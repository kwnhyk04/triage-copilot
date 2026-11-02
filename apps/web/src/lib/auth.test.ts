import { sessionValid, signedIn } from './auth.ts';
import { signSession } from './session.ts';

// Hoisted above the imports: signedIn() reads the cookie through Next.
const mockCookie: { value: string | undefined } = { value: undefined };
jest.mock('next/headers', () => ({
  cookies: () =>
    Promise.resolve({
      get: (name: string) =>
        name === '__Host-triage_session' && mockCookie.value !== undefined
          ? { name, value: mockCookie.value }
          : undefined,
    }),
}));

const SECRET = 'session-secret-for-these-tests-0000';
const ENV = {
  GATEWAY_URL: 'http://gateway.test',
  GATEWAY_API_KEY: 'gateway-key-for-these-tests-000000000',
  APPROVER_API_KEY: 'approver-key-for-these-tests-00000000',
  LOGIN_PASSWORD_HASH:
    '$argon2id$v=19$m=65536,t=2,p=1$c29tZXNhbHQ$CTFhFdXPJO1aFaMaO6Mm5c8y7cJHAph8ArZWb2GRPPc',
  SESSION_SECRET: SECRET,
};
const HOUR = 60 * 60 * 1000;

describe('the session check', () => {
  beforeAll(() => {
    Object.assign(process.env, ENV);
  });

  it('signs in only a session this server signed that has not expired', async () => {
    const cases: [string, string | undefined, boolean][] = [
      ['fresh', signSession(SECRET), true],
      ['expired an hour ago', signSession(SECRET, Date.now() - 9 * HOUR), false],
      ["another server's", signSession(`${SECRET}-other`), false],
      ['forged', 'v1.1.forged', false],
      ['none', undefined, false],
    ];
    for (const [name, token, expected] of cases) {
      mockCookie.value = token;
      // The proxy asks with the token; pages, the header and the data layer ask for the request.
      expect([name, sessionValid(token), await signedIn()]).toEqual([name, expected, expected]);
    }
  });
});
