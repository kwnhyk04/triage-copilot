import { signSession, validSession } from '../../lib/session.ts';
import { signIn } from './actions.ts';

// Hoisted above the imports: the action reads cookies and headers, and redirects, through Next.
const mockCookies = new Map<string, { value: string; options: unknown }>();
const mockVerified: string[] = [];
jest.mock('next/headers', () => ({
  cookies: () =>
    Promise.resolve({
      set: (name: string, value: string, options: unknown) =>
        mockCookies.set(name, { value, options }),
    }),
  headers: () => Promise.resolve(new Headers()),
}));
jest.mock('next/navigation', () => ({
  redirect: (url: string) => {
    throw Object.assign(new Error('NEXT_REDIRECT'), { url });
  },
}));
// The password check is password.test.ts's; here it counts the attempts that reach it.
jest.mock('../../lib/password.ts', () => ({
  ...jest.requireActual<typeof import('../../lib/password.ts')>('../../lib/password.ts'),
  verifyPassword: (password: string) => {
    mockVerified.push(password);
    return Promise.resolve(password === 'the right password');
  },
}));

const SECRET = 'session-secret-for-these-tests-0000';
const ENV = {
  GATEWAY_URL: 'http://127.0.0.1:4200',
  GATEWAY_API_KEY: 'gateway-key-for-these-tests-000000000',
  APPROVER_API_KEY: 'approver-key-for-these-tests-00000000',
  LOGIN_PASSWORD_HASH:
    '$argon2id$v=19$m=65536,t=2,p=1$c29tZXNhbHQ$CTFhFdXPJO1aFaMaO6Mm5c8y7cJHAph8ArZWb2GRPPc',
  SESSION_SECRET: SECRET,
};
const form = (fields: Record<string, string>) => {
  const data = new FormData();
  for (const [name, value] of Object.entries(fields)) data.set(name, value);
  return data;
};
const attempt = async (password: string, next = '/investigations?source=sim') => {
  try {
    return await signIn({}, form({ password, next }));
  } catch (error) {
    return { redirect: (error as { url?: string }).url };
  }
};

// The tests share the action's one limiter, so they run in this order.
describe('signIn', () => {
  beforeAll(() => {
    Object.assign(process.env, ENV);
  });
  beforeEach(() => {
    mockCookies.clear();
    mockVerified.length = 0;
  });

  it('says a wrong password is wrong, and sets no cookie', async () => {
    await expect(attempt('a wrong password')).resolves.toEqual({ error: 'wrong' });
    expect(mockCookies.size).toBe(0);
  });

  it('signs in with the right password: an 8-hour session cookie, then back to next', async () => {
    await expect(attempt('the right password')).resolves.toEqual({
      redirect: '/investigations?source=sim',
    });
    const cookie = mockCookies.get('__Host-triage_session');
    expect(cookie?.options).toEqual({
      httpOnly: true,
      secure: true,
      sameSite: 'lax',
      path: '/',
      maxAge: 8 * 60 * 60,
    });
    expect(validSession(cookie?.value, SECRET)).toBe(true);
    // A next off this site goes to the list instead.
    await expect(attempt('the right password', 'https://evil.example/')).resolves.toEqual({
      redirect: '/investigations',
    });
  });

  it('refuses the 6th attempt in 15 minutes before checking the password', async () => {
    for (let i = 0; i < 5; i++) {
      await expect(attempt(`wrong ${String(i)}`)).resolves.toEqual({ error: 'wrong' });
    }
    await expect(attempt('the right password')).resolves.toEqual({ error: 'limit' });
    expect(mockVerified).toHaveLength(5);
    expect(mockCookies.size).toBe(0);
  });

  it('is off without the two settings', async () => {
    delete process.env.LOGIN_PASSWORD_HASH;
    delete process.env.SESSION_SECRET;
    await expect(attempt('the right password')).resolves.toEqual({ error: 'off' });
    expect(mockVerified).toHaveLength(0);
    // A token signed under the old secret means nothing now.
    expect(validSession(signSession(SECRET), process.env.SESSION_SECRET)).toBe(false);
  });
});
