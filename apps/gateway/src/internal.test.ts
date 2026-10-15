import type { components } from '@triage/ts-clients/gateway';

import type { Claim, Execution, Report } from './executions.ts';
import { buildInternalApp } from './internal.ts';
import type { GetAlertContext } from './investigations.ts';

const TOKEN = 'executor-token-for-these-tests-000000';
const AGENT_TOKEN = 'agent-token-for-these-tests-0000000000';
const ID = '9f8b6c1e-3d2a-4f5b-8e7d-6c5b4a392817';
const INVESTIGATION = '1b4e28ba-2fa1-41d2-883f-0016d3cca427';
const CONTEXT: components['schemas']['AlertContext'] = {
  investigation_id: INVESTIGATION,
  group_id: '6f1c2a9e-8b3d-4e5f-9a7b-2c4d6e8f0a1b',
  tenant_id: '7c9e6679-7425-40de-944b-e07fc1f90ae7',
  group_key: '{}:{alertname="CheckoutHighErrorRate"}',
  window_start: '2026-10-15T12:00:05.000Z',
  severity: 'critical',
  alert_count: 1,
  alerts: [
    {
      fingerprint: '8a0461e5e216f2b5',
      labels: { alertname: 'CheckoutHighErrorRate', service: 'checkout' },
      starts_at: '2026-10-15T11:59:58.000Z',
      status: 'firing',
    },
  ],
};
const alertsUrl = `/internal/investigations/${INVESTIGATION}/alerts`;
const agent = { authorization: `Bearer ${AGENT_TOKEN}` };
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
  const start = (
    claim: Claim = unused,
    report: Report = unused,
    alertContext: GetAlertContext = unused,
  ) =>
    buildInternalApp({
      config: {
        LOG_LEVEL: 'error',
        EXECUTOR_SERVICE_TOKEN: TOKEN,
        AGENT_SERVICE_TOKEN: AGENT_TOKEN,
      },
      claim,
      report,
      alertContext,
    });

  it('takes the executor token only on its routes; the probe takes none', async () => {
    const app = start();
    try {
      for (const headers of [
        {},
        { authorization: `Bearer wrong-${TOKEN}` },
        { authorization: `Basic ${TOKEN}` },
        { 'x-api-key': TOKEN },
        agent,
      ]) {
        for (const url of [claimUrl, resultUrl]) {
          const response = await app.inject({ method: 'POST', url, headers, payload: {} });
          expect(response.statusCode).toBe(401);
          expect(response.headers['www-authenticate']).toBe('Bearer');
          expect(response.headers['content-type']).toMatch(/^application\/problem\+json/);
        }
      }
      expect((await app.inject({ url: '/healthz' })).statusCode).toBe(200);
    } finally {
      await app.close();
    }
  });

  it('takes the agent token only on the alert context', async () => {
    const app = start();
    try {
      for (const headers of [{}, executor, { authorization: `Bearer wrong-${AGENT_TOKEN}` }]) {
        const response = await app.inject({ url: alertsUrl, headers });
        expect(response.statusCode).toBe(401);
        expect(response.headers['www-authenticate']).toBe('Bearer');
        expect(response.json()).toMatchObject({ detail: 'Send the agent service token.' });
      }
    } finally {
      await app.close();
    }
  });

  it('serves the alert context, 404 for an unknown investigation and 400 for a bad id', async () => {
    const asked: string[] = [];
    const app = start(unused, unused, (id) => {
      asked.push(id);
      return Promise.resolve(id === INVESTIGATION ? CONTEXT : undefined);
    });
    try {
      const found = await app.inject({ url: alertsUrl, headers: agent });
      expect(found.statusCode).toBe(200);
      expect(found.json()).toEqual(CONTEXT);
      const unknown = await app.inject({
        url: `/internal/investigations/${ID}/alerts`,
        headers: agent,
      });
      expect(unknown.statusCode).toBe(404);
      expect(unknown.headers['content-type']).toMatch(/^application\/problem\+json/);
      const bad = await app.inject({ url: '/internal/investigations/nope/alerts', headers: agent });
      expect(bad.statusCode).toBe(400);
      expect(asked).toEqual([INVESTIGATION, ID]);
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
