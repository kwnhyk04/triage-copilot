import { readFileSync } from 'node:fs';

import type { Investigation } from '@triage/ts-clients/graphql';
import type { FastifyInstance } from 'fastify';

import { type AppDeps, buildApp, createUpdates, type Decide, REQUEST_TIMEOUT_MS } from './app.ts';
import { type FindTenant, type Intake, sha256, type Webhook } from './intake.ts';

const APPROVER_KEY = 'approver-test-key-000000000000000';
const config = {
  LOG_LEVEL: 'error',
  APPROVER_API_KEY: APPROVER_KEY,
  RATE_LIMIT_PER_MINUTE: 600,
} as const;
const up = () => Promise.resolve();
const ACTION_ID = '9f8b6c1e-3d2a-4f5b-8e7d-6c5b4a392817';
const TOKEN = 'tenant-webhook-token-00000000000000';
const TENANT_ID = '6f1c2a9e-8b3d-4e5f-9a7b-2c4d6e8f0a1b';
const ACCEPTED = { group_id: '1b4e28ba-2fa1-41d2-883f-0016d3cca427', deduped: false };
const WEBHOOK = JSON.parse(
  readFileSync(
    new URL('../../../contracts/openapi/examples/alertmanager-webhook.json', import.meta.url),
    'utf8',
  ),
) as Webhook;
const HEALTH_QUERY = {
  method: 'POST',
  url: '/graphql',
  payload: { query: '{ health { status } }' },
} as const;

/** Fake store in which every action is pending, so any decision is recorded. */
const record: Decide = (actionId, verdict, decision) =>
  Promise.resolve({
    id: actionId,
    investigation_id: '1b4e28ba-2fa1-41d2-883f-0016d3cca427',
    kind: 'rollback_deploy',
    target: 'checkout',
    parameters: { to_version: 'v1.7.3' },
    rationale: 'Errors started two minutes after v1.8.0 shipped.',
    status: verdict,
    proposed_at: '2026-10-14T12:01:30Z',
    decided_by: decision.approver,
    decided_at: '2026-10-14T12:03:12Z',
    decision_reason: decision.reason ?? null,
  });
const findTenant: FindTenant = (hash) =>
  Promise.resolve(hash === sha256(TOKEN) ? TENANT_ID : undefined);

describe('gateway', () => {
  let app: FastifyInstance;
  let stored: [string, Webhook][];
  const intake: Intake = (tenantId, webhook) => {
    stored.push([tenantId, webhook]);
    return Promise.resolve(ACCEPTED);
  };
  const start = async (deps: Partial<AppDeps> = {}) => {
    stored = [];
    app = await buildApp({
      config,
      ping: up,
      updates: createUpdates(),
      decide: record,
      findTenant,
      intake,
      ...deps,
    });
    return app;
  };
  afterEach(() => app.close());

  describe('approve and reject', () => {
    const post = (
      headers: Record<string, string>,
      { route = 'approve', id = ACTION_ID, payload = { approver: 'oncall@example.com' } } = {},
    ) => app.inject({ method: 'POST', url: `/v1/actions/${id}/${route}`, headers, payload });
    const approver = { 'x-approver-key': APPROVER_KEY };

    it('refuse the API key and the Alertmanager token with 403', async () => {
      await start();
      for (const headers of [{ 'x-api-key': 'gateway-api-key' }, { authorization: 'Bearer am' }]) {
        const response = await post(headers);
        expect(response.statusCode).toBe(403);
        expect(response.headers['content-type']).toMatch(/^application\/problem\+json/);
      }
    });

    it('answer 401 with a challenge when the approver key is missing or wrong', async () => {
      await start();
      for (const headers of [{}, { 'x-approver-key': 'not-the-approver-key' }]) {
        const response = await post(headers);
        expect(response.statusCode).toBe(401);
        expect(response.headers['www-authenticate']).toBe('ApiKey header="X-Approver-Key"');
      }
    });

    it('record the decision with the approver key', async () => {
      await start();
      const approved = await post(approver);
      expect(approved.statusCode).toBe(200);
      expect(approved.json()).toMatchObject({
        status: 'approved',
        decided_by: 'oncall@example.com',
      });
      const payload = { approver: 'oncall@example.com', reason: 'Too risky at peak' };
      const rejected = await post(approver, { route: 'reject', payload });
      expect(rejected.json()).toMatchObject({
        status: 'rejected',
        decision_reason: payload.reason,
      });
    });

    it('validate the request and map unknown and decided actions', async () => {
      await start({
        decide: (id) => Promise.resolve(id === ACTION_ID ? 'already_decided' : 'not_found'),
      });
      const extraField = { approver: 'oncall@example.com', extra: true };
      expect((await post(approver, { payload: extraField })).statusCode).toBe(400);
      expect((await post(approver, { id: 'not-a-uuid' })).statusCode).toBe(400);
      expect(
        (await post(approver, { id: '1b4e28ba-2fa1-41d2-883f-0016d3cca427' })).statusCode,
      ).toBe(404);
      expect((await post(approver)).statusCode).toBe(409);
    });
  });

  describe('alert webhook', () => {
    const post = (headers: Record<string, string>, payload: object = WEBHOOK) =>
      app.inject({ method: 'POST', url: '/v1/alerts', headers, payload });
    const tenant = { authorization: `Bearer ${TOKEN}` };

    it("stores the payload under the token's tenant and answers 202", async () => {
      await start();
      const response = await post(tenant);
      expect(response.statusCode).toBe(202);
      expect(response.json()).toEqual(ACCEPTED);
      const fingerprints = stored.map(([id, webhook]) => [id, webhook.alerts[0]?.fingerprint]);
      expect(fingerprints).toEqual([[TENANT_ID, '3f9c2a7b1e5d8c40']]);
    });

    it('answers 401 for a missing or unknown token and 403 for other credentials', async () => {
      await start();
      for (const headers of [{}, { authorization: 'Bearer not-a-tenant-token' }]) {
        const response = await post(headers);
        expect(response.statusCode).toBe(401);
        expect(response.headers['www-authenticate']).toBe('Bearer');
      }
      for (const headers of [{ 'x-api-key': 'gateway-api-key' }, { 'x-approver-key': 'key' }]) {
        expect((await post(headers)).statusCode).toBe(403);
      }
      expect(stored).toEqual([]);
    });

    it('refuses more than 200 alerts and bodies over 1 MB, and sets the 10 s timeout', async () => {
      await start();
      const [alert] = WEBHOOK.alerts;
      const tooMany = await post(tenant, {
        ...WEBHOOK,
        alerts: Array.from({ length: 201 }, () => alert),
      });
      expect(tooMany.statusCode).toBe(400);
      expect(tooMany.json()).toMatchObject({ title: 'Bad Request' });
      const atLimit = await post(tenant, {
        ...WEBHOOK,
        alerts: Array.from({ length: 200 }, () => alert),
      });
      expect(atLimit.statusCode).toBe(202);

      const huge = { ...WEBHOOK, commonAnnotations: { summary: 'x'.repeat(1024 * 1024) } };
      const tooLarge = await post(tenant, huge);
      expect(tooLarge.statusCode).toBe(413);
      expect(tooLarge.headers['content-type']).toMatch(/^application\/problem\+json/);

      expect((await post(tenant, { ...WEBHOOK, version: '3' })).statusCode).toBe(400);
      expect(stored).toHaveLength(1);
      expect(app.server.requestTimeout).toBe(REQUEST_TIMEOUT_MS);
    });

    it('answers 503 with Retry-After when the alerts cannot be stored', async () => {
      await start({ intake: () => Promise.reject(new Error('database down')) });
      const response = await post(tenant);
      expect(response.statusCode).toBe(503);
      expect(response.headers['retry-after']).toBe('5');
    });
  });

  describe('rate limit', () => {
    const limited = { ...config, RATE_LIMIT_PER_MINUTE: 2 };

    it('answers 429 with Retry-After and RateLimit headers when a credential uses its minute', async () => {
      await start({ config: limited });
      const post = (token: string) =>
        app.inject({
          method: 'POST',
          url: '/v1/alerts',
          headers: { authorization: `Bearer ${token}` },
          payload: WEBHOOK,
        });
      const first = await post(TOKEN);
      expect(first.statusCode).toBe(202);
      expect(first.headers['ratelimit-limit']).toBe('2');
      expect(first.headers['ratelimit-remaining']).toBe('1');
      expect((await post(TOKEN)).statusCode).toBe(202);

      const refused = await post(TOKEN);
      expect(refused.statusCode).toBe(429);
      expect(refused.headers['content-type']).toMatch(/^application\/problem\+json/);
      expect(refused.json()).toMatchObject({ status: 429, title: 'Too Many Requests' });
      expect(Number(refused.headers['retry-after'])).toBeGreaterThan(0);
      expect(refused.headers['ratelimit-limit']).toBe('2');
      expect(refused.headers['ratelimit-remaining']).toBe('0');
      expect(stored).toHaveLength(2);

      // Each credential has its own count: this unknown one gets 401, not 429.
      expect((await post('another-token')).statusCode).toBe(401);
    });

    it('counts requests without a credential per client and never limits probes', async () => {
      await start({ config: limited });
      const answers = [];
      for (let i = 0; i < 3; i++) answers.push((await app.inject(HEALTH_QUERY)).statusCode);
      expect(answers).toEqual([200, 200, 429]);
      const withKey = await app.inject({ ...HEALTH_QUERY, headers: { 'x-api-key': 'key' } });
      expect(withKey.statusCode).toBe(200);
      for (let i = 0; i < 5; i++) {
        expect((await app.inject({ url: '/healthz' })).statusCode).toBe(200);
        expect((await app.inject({ url: '/readyz' })).statusCode).toBe(200);
      }
    });
  });

  it('answers /healthz', async () => {
    const response = await (await start()).inject({ url: '/healthz' });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: 'ok' });
  });

  it('answers /readyz with 503 when a dependency is down', async () => {
    await start({ ping: () => Promise.reject(new Error('down')) });
    const response = await app.inject({ url: '/readyz' });
    expect(response.statusCode).toBe(503);
    expect(response.json()).toEqual({ status: 'unavailable' });
  });

  it('serves the GraphQL health query', async () => {
    const response = await (await start()).inject(HEALTH_QUERY);
    expect(response.json()).toEqual({ data: { health: { status: 'ok' } } });
  });

  it('grants no cross-origin access, so browsers cannot call it directly', async () => {
    await start();
    const response = await app.inject({
      ...HEALTH_QUERY,
      headers: { origin: 'https://evil.example' },
    });
    expect(response.headers['access-control-allow-origin']).toBeUndefined();
  });

  it('streams investigation updates over server-sent events', async () => {
    const updates = createUpdates();
    const address = await (await start({ updates })).listen({ host: '127.0.0.1', port: 0 });
    const abort = new AbortController();
    const response = await fetch(`${address}/graphql`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
      body: JSON.stringify({
        query: 'subscription { investigationUpdated(id: "inv-1") { id status } }',
      }),
      signal: abort.signal,
    });
    if (!response.body) throw new Error('subscription returned no body');

    const investigation: Investigation = {
      id: 'inv-1',
      groupId: 'group-1',
      title: 'Checkout error rate above 5%',
      status: 'RUNNING',
      summary: null,
      tokensUsed: 0,
      createdAt: '2026-10-14T12:00:05Z',
      updatedAt: '2026-10-14T12:00:41Z',
      hypotheses: [],
      proposedActions: [],
      steps: [],
    };
    // SSE has no "subscribed" signal, so publish until the first event arrives.
    const publisher = setInterval(() => {
      updates.publish('investigation:updated', 'inv-1', investigation);
    }, 20);
    try {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let text = '';
      while (!/data: .*\n/.test(text)) {
        const { value, done } = await reader.read();
        if (done) break;
        text += decoder.decode(value, { stream: true });
      }
      expect(text).toContain(
        JSON.stringify({ data: { investigationUpdated: { id: 'inv-1', status: 'RUNNING' } } }),
      );
    } finally {
      clearInterval(publisher);
      abort.abort();
    }
  });
});
