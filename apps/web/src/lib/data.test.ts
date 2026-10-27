import { APPROVER, readInvestigation, readInvestigations, recordPlanDecision } from './data.ts';
import { signSession } from './session.ts';
import { WORKER_OUT_OF_MEMORY as LIVE_TRIAGE } from '../demo/live-samples.ts';

// Hoisted above the imports: the data layer reads the cookie and redirects through Next.
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
jest.mock('next/navigation', () => ({
  redirect: (url: string) => {
    throw Object.assign(new Error('NEXT_REDIRECT'), { url });
  },
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
const ID = LIVE_TRIAGE.id;
const PLAN_ID = '5b1c9d2e-7f3a-4e8b-9c6d-1a2b3c4d5e6f';
const decision = (fields: Record<string, string>) => {
  const form = new FormData();
  for (const [name, value] of Object.entries(fields)) form.set(name, value);
  return form;
};
/** Where the call redirected to, or how it ended without a redirect. */
const ending = async (call: () => Promise<unknown>) => {
  try {
    await call();
  } catch (error) {
    return (error as { url?: string }).url ?? `threw ${(error as Error).message}`;
  }
  return 'no redirect';
};
const calls = () => [
  () => readInvestigations('live'),
  () => readInvestigation(ID),
  () => recordPlanDecision(ID, PLAN_ID, decision({ verdict: 'approve' })),
];
const sent = (spy: jest.SpiedFunction<typeof fetch>, i = 0) => spy.mock.calls[i]?.[0] as Request;

describe('data access', () => {
  let gateway: jest.SpiedFunction<typeof fetch>;
  beforeAll(() => {
    Object.assign(process.env, ENV);
  });
  beforeEach(() => {
    mockCookie.value = signSession(SECRET);
    gateway = jest.spyOn(globalThis, 'fetch');
  });
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('sends nothing to the gateway, and goes to sign-in, without a valid session', async () => {
    for (const cookie of [undefined, 'v1.1.forged', signSession(`${SECRET}-other`)]) {
      mockCookie.value = cookie;
      for (const call of calls()) expect(await ending(call)).toBe('/login');
    }
    expect(gateway).not.toHaveBeenCalled();
  });

  it('reads a page of one source, and one report, for a signed-in reader', async () => {
    gateway
      .mockResolvedValueOnce(Response.json({ items: [], next_cursor: null }))
      .mockResolvedValueOnce(Response.json(LIVE_TRIAGE));
    await expect(readInvestigations('sim', 'abc')).resolves.toEqual({
      items: [],
      next_cursor: null,
    });
    const url = new URL(sent(gateway).url);
    expect([url.searchParams.get('source'), url.searchParams.get('cursor')]).toEqual([
      'sim',
      'abc',
    ]);
    expect(sent(gateway).headers.get('X-API-Key')).toBe(ENV.GATEWAY_API_KEY);
    await expect(readInvestigation(ID)).resolves.toEqual(LIVE_TRIAGE);
  });

  it('records a decision as the operator, whatever the form names, then shows the report', async () => {
    gateway.mockResolvedValue(Response.json({ status: 'rejected' }));
    const form = decision({ verdict: 'reject', approver: 'Mallory', reason: 'Too risky' });
    expect(await ending(() => recordPlanDecision(ID, PLAN_ID, form))).toBe(`/investigations/${ID}`);
    const request = sent(gateway);
    expect(new URL(request.url).pathname).toBe(`/v1/plans/${PLAN_ID}/reject`);
    expect(request.headers.get('X-Approver-Key')).toBe(ENV.APPROVER_API_KEY);
    expect(await request.json()).toEqual({ approver: 'operator', reason: 'Too risky' });
    expect(APPROVER).toBe('operator');
  });

  it('shows the report when a decision stands already, and fails on any other refusal', async () => {
    const answer = (status: number) => Response.json({ title: 'x', status }, { status });
    gateway.mockResolvedValueOnce(answer(409)).mockResolvedValueOnce(answer(400));
    const approve = () => recordPlanDecision(ID, PLAN_ID, decision({ verdict: 'approve' }));
    expect(await ending(approve)).toBe(`/investigations/${ID}`);
    expect(await ending(approve)).toBe('threw gateway answered 400');
    // A form or ids it cannot read are refused before anything is sent.
    gateway.mockClear();
    for (const call of [
      () => recordPlanDecision(ID, PLAN_ID, decision({ verdict: 'execute' })),
      () => recordPlanDecision(ID, '../actions', decision({ verdict: 'approve' })),
    ]) {
      expect(await ending(call)).toMatch(/^threw /);
    }
    expect(gateway).not.toHaveBeenCalled();
  });

  it('sends a reader to the demo when the gateway does not answer', async () => {
    for (const answer of [
      () => Promise.reject(new TypeError('fetch failed')),
      () => Promise.reject(new DOMException('The operation timed out.', 'TimeoutError')),
      ...[502, 503, 504].map((status) => () => Promise.resolve(new Response(null, { status }))),
    ]) {
      gateway.mockImplementation(answer);
      for (const call of calls()) expect(await ending(call)).toBe('/demo?offline=1');
    }
  });

  it('fails on a gateway error that is not about reaching it', async () => {
    gateway.mockImplementation(() =>
      Promise.resolve(
        Response.json({ title: 'Internal Server Error', status: 500 }, { status: 500 }),
      ),
    );
    expect(await ending(() => readInvestigations('live'))).toBe('threw gateway answered 500');
  });
});
