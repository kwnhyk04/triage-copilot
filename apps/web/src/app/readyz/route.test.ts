import { GET } from './route.ts';

const ENV = {
  GATEWAY_URL: 'http://gateway.test',
  GATEWAY_API_KEY: 'gateway-key-for-these-tests-000000000',
  APPROVER_API_KEY: 'approver-key-for-these-tests-00000000',
};
const healthy = () => Promise.resolve(Response.json({ data: { health: { status: 'ok' } } }));

describe('GET /readyz', () => {
  beforeAll(() => {
    Object.assign(process.env, ENV);
  });
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("keeps the gateway's answer for 10 s, since every hit spends its rate limit", async () => {
    let now = Date.parse('2026-10-27T10:00:00Z');
    jest.spyOn(Date, 'now').mockImplementation(() => now);
    const gateway = jest.spyOn(globalThis, 'fetch').mockImplementation(healthy);

    expect((await GET()).status).toBe(200);
    now += 9_999;
    expect((await GET()).status).toBe(200);
    expect(gateway).toHaveBeenCalledTimes(1);

    now += 1;
    gateway.mockImplementation(() => Promise.reject(new TypeError('fetch failed')));
    const down = await GET();
    expect([down.status, await down.json()]).toEqual([503, { status: 'unavailable' }]);
    expect(gateway).toHaveBeenCalledTimes(2);
  });

  it('asks the gateway once however many probes arrive while it answers', async () => {
    const now = Date.parse('2026-10-27T11:00:00Z');
    jest.spyOn(Date, 'now').mockImplementation(() => now);
    const gateway = jest.spyOn(globalThis, 'fetch').mockImplementation(
      () =>
        new Promise((resolve) => {
          setTimeout(() => {
            resolve(healthy());
          }, 20);
        }),
    );

    const answers = await Promise.all(Array.from({ length: 50 }, () => GET()));

    expect(answers.map((answer) => answer.status)).toEqual(Array.from({ length: 50 }, () => 200));
    expect(gateway).toHaveBeenCalledTimes(1);
  });
});
