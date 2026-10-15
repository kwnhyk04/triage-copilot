import type { Claim, Execution, Report } from './executions.ts';
import { buildInternalApp } from './internal.ts';

const TOKEN = 'executor-token-for-these-tests-000000';
const ID = '9f8b6c1e-3d2a-4f5b-8e7d-6c5b4a392817';
const EXECUTION: Execution = {
  action_id: ID,
  kind: 'rollback_deploy',
  target: 'checkout',
  parameters: { to_version: 'v1.7.3' },
  approved_by: 'oncall@example.com',
  approved_at: '2026-10-15T12:03:12.000Z',
  state: 'claimed',
  dry_run: true,
  detail: null,
  claimed_at: '2026-10-15T12:03:13.000Z',
  finished_at: null,
};
const executor = { authorization: `Bearer ${TOKEN}` };
const claimUrl = `/internal/actions/${ID}/execution`;
const resultUrl = `${claimUrl}/result`;

describe('internal API', () => {
  const unused = () => Promise.reject(new Error('unused by this test'));
  const start = (claim: Claim = unused, report: Report = unused) =>
    buildInternalApp({
      config: { LOG_LEVEL: 'error', EXECUTOR_SERVICE_TOKEN: TOKEN },
      claim,
      report,
    });

  it('takes the executor token only; the probe takes none', async () => {
    const app = start();
    try {
      for (const headers of [
        {},
        { authorization: `Bearer wrong-${TOKEN}` },
        { authorization: `Basic ${TOKEN}` },
        { 'x-api-key': TOKEN },
      ]) {
        const response = await app.inject({ method: 'POST', url: claimUrl, headers, payload: {} });
        expect(response.statusCode).toBe(401);
        expect(response.headers['www-authenticate']).toBe('Bearer');
        expect(response.headers['content-type']).toMatch(/^application\/problem\+json/);
      }
      expect((await app.inject({ url: '/healthz' })).statusCode).toBe(200);
    } finally {
      await app.close();
    }
  });

  it('answers 201 for a new claim, 200 for an earlier one and 404 without an approval', async () => {
    const answers: Awaited<ReturnType<Claim>>[] = [
      { created: true, execution: EXECUTION },
      { created: false, execution: EXECUTION },
      'not_found',
    ];
    const asked: [string, boolean][] = [];
    const app = start((id, dryRun) => {
      asked.push([id, dryRun]);
      return Promise.resolve(answers[asked.length - 1] ?? 'not_found');
    });
    try {
      const claim = () =>
        app.inject({
          method: 'POST',
          url: claimUrl,
          headers: executor,
          payload: { dry_run: true },
        });
      expect((await claim()).statusCode).toBe(201);
      const again = await claim();
      expect(again.statusCode).toBe(200);
      expect(again.json()).toEqual(EXECUTION);
      expect((await claim()).statusCode).toBe(404);
      expect(asked).toEqual([
        [ID, true],
        [ID, true],
        [ID, true],
      ]);
    } finally {
      await app.close();
    }
  });

  it('records a result once, and refuses a malformed request', async () => {
    const answers: Awaited<ReturnType<Report>>[] = [
      { ...EXECUTION, state: 'succeeded' },
      'already_finished',
      'not_found',
    ];
    let reported = 0;
    const app = start(unused, () => Promise.resolve(answers[reported++] ?? 'not_found'));
    try {
      const report = (payload: object, url = resultUrl) =>
        app.inject({ method: 'POST', url, headers: executor, payload });
      expect((await report({ state: 'succeeded' })).statusCode).toBe(200);
      expect((await report({ state: 'failed', detail: 'rollback timed out' })).statusCode).toBe(
        409,
      );
      expect((await report({ state: 'succeeded' })).statusCode).toBe(404);
      for (const bad of [{}, { state: 'claimed' }, { state: 'failed', extra: 1 }]) {
        expect((await report(bad)).statusCode).toBe(400);
      }
      expect((await report({ state: 'failed', detail: 'x'.repeat(2001) })).statusCode).toBe(400);
      const badId = '/internal/actions/not-a-uuid/execution/result';
      expect((await report({ state: 'succeeded' }, badId)).statusCode).toBe(400);
      expect(reported).toBe(3);
    } finally {
      await app.close();
    }
  });
});
