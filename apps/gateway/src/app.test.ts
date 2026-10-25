import { readFileSync } from 'node:fs';

import type { Investigation } from '@triage/ts-clients/graphql';
import type { FastifyInstance } from 'fastify';

import { type AppDeps, buildApp, createUpdates, type Decide, REQUEST_TIMEOUT_MS } from './app.ts';
import { loadGatewayConfig } from './config.ts';
import { type FindTenant, type Intake, sha256, type Webhook } from './intake.ts';
import type { GetInvestigation, ListInvestigations } from './investigations.ts';

const APPROVER_KEY = 'approver-test-key-000000000000000';
const API_KEY = 'gateway-api-test-key-0000000000000';
const config = {
  LOG_LEVEL: 'error',
  APPROVER_API_KEY: APPROVER_KEY,
  GATEWAY_API_KEY: API_KEY,
  RATE_LIMIT_PER_MINUTE: 600,
} as const;
const PAGE = { items: [], next_cursor: null };
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
  headers: { 'x-api-key': API_KEY },
  payload: { query: '{ health { status } }' },
} as const;
/** A live triage as the store returns it: Claude skipped, Gemini answered. */
const DETAIL: NonNullable<Awaited<ReturnType<GetInvestigation>>> = {
  id: '3d6f9a2c-1b4e-4c8d-9f7a-5e2b1c0d4a68',
  group_id: ACCEPTED.group_id,
  title: 'ApiReadinessFailing on api',
  status: 'resolved',
  verdict: 'real',
  created_at: '2026-10-25T09:52:30.000Z',
  updated_at: '2026-10-25T09:54:10.000Z',
  summary: "freight-audit's api fails its readiness check: postgres restarts.",
  tokens_used: 6240,
  hypotheses: [
    {
      rank: 1,
      root_cause_class: 'database',
      service: 'postgres',
      summary: 'The readiness check fails on postgres, whose container restarts.',
      confidence: 0.8,
      evidence: [{ tool_call_id: 'tc_01J9ZK3Q7M', excerpt: 'postgres: restarting (exit 137)' }],
    },
  ],
  missing_evidence: [],
  partial: false,
  models: [
    {
      part: 'triage',
      provider: 'anthropic',
      model: 'claude-haiku-5-5',
      calls: 0,
      failed: 1,
      reason: 'claude-haiku-5-5 skipped: Claude is switched off (agent.switches)',
    },
    {
      part: 'triage',
      provider: 'gemini',
      model: 'gemini-3.5-flash-lite',
      calls: 2,
      failed: 0,
      reason: null,
    },
  ],
  proposed_actions: [],
  steps: [
    {
      seq: 1,
      kind: 'tool_call',
      summary: 'read_snapshot of alert 1',
      tool_call: { id: 'tc_01J9ZK3Q7M', name: 'read_snapshot', status: 'ok', duration_ms: 12 },
      recorded_at: '2026-10-25T09:53:02.000Z',
    },
  ],
};
const noInvestigation: GetInvestigation = () => Promise.resolve(undefined);

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
  let listed: Parameters<ListInvestigations>[0][];
  const intake: Intake = (tenantId, webhook) => {
    stored.push([tenantId, webhook]);
    return Promise.resolve(ACCEPTED);
  };
  const listInvestigations: ListInvestigations = (query) => {
    listed.push(query);
    return Promise.resolve(PAGE);
  };
  const start = async (deps: Partial<AppDeps> = {}) => {
    stored = [];
    listed = [];
    app = await buildApp({
      config,
      ping: up,
      updates: createUpdates(),
      decide: record,
      findTenant,
      intake,
      listInvestigations,
      getInvestigation: noInvestigation,
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
      expect(fingerprints).toEqual([[TENANT_ID, '8a0461e5e216f2b5']]);
    });

    it('deduplicates on the fingerprint its labels give, and never refuses a different one', async () => {
      await start();
      const [alert] = WEBHOOK.alerts;
      if (alert === undefined) throw new Error('the sample has no alert');
      const forged = { ...WEBHOOK, alerts: [{ ...alert, fingerprint: 'not-what-labels-give' }] };
      expect((await post(tenant, forged)).statusCode).toBe(202);
      expect(stored.map(([, webhook]) => webhook.alerts[0]?.fingerprint)).toEqual([
        '8a0461e5e216f2b5',
      ]);
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
      const anonymous = { ...HEALTH_QUERY, headers: {} };
      for (let i = 0; i < 3; i++) answers.push((await app.inject(anonymous)).statusCode);
      // Refused for want of a key, and counted per client all the same.
      expect(answers).toEqual([401, 401, 429]);
      expect((await app.inject(HEALTH_QUERY)).statusCode).toBe(200);
      for (let i = 0; i < 5; i++) {
        expect((await app.inject({ url: '/healthz' })).statusCode).toBe(200);
        expect((await app.inject({ url: '/readyz' })).statusCode).toBe(200);
      }
    });
  });

  describe('API key', () => {
    const routes = [
      ['GET', '/v1/investigations'],
      ['GET', `/v1/investigations/${ACTION_ID}`],
      ['GET', '/graphql?query=%7Bhealth%7Bstatus%7D%7D'],
      ['POST', '/graphql'],
    ] as const;

    it('is not needed by the webhook; every other /v1 route and /graphql answer 401 without it', async () => {
      await start();
      const alerts = await app.inject({
        method: 'POST',
        url: '/v1/alerts',
        headers: { authorization: `Bearer ${TOKEN}` },
        payload: WEBHOOK,
      });
      expect(alerts.statusCode).toBe(202);
      // Approve and reject want their own key, and refuse a request without one the same way.
      const decisions = [
        ['POST', `/v1/actions/${ACTION_ID}/approve`],
        ['POST', `/v1/actions/${ACTION_ID}/reject`],
      ] as const;
      for (const [method, url] of [...routes, ...decisions]) {
        const response = await app.inject({ method, url, payload: {} });
        expect([method, url, response.statusCode]).toEqual([method, url, 401]);
        expect(response.headers['www-authenticate']).toMatch(
          /^ApiKey header="X-(API|Approver)-Key"$/,
        );
        expect(response.headers['content-type']).toMatch(/^application\/problem\+json/);
      }
    });

    it('answers 401 with the challenge for a wrong key', async () => {
      await start();
      for (const [method, url] of routes) {
        const headers = { 'x-api-key': `wrong-${API_KEY}` };
        const response = await app.inject({ method, url, headers, payload: {} });
        expect([url, response.statusCode]).toEqual([url, 401]);
        expect(response.headers['www-authenticate']).toBe('ApiKey header="X-API-Key"');
      }
    });
  });

  describe('investigation list', () => {
    const get = (query: string) =>
      app.inject({ url: `/v1/investigations${query}`, headers: { 'x-api-key': API_KEY } });

    it('pages 25 by default and refuses a limit outside 1 to 100', async () => {
      await start();
      expect((await get('')).json()).toEqual(PAGE);
      expect((await get('?limit=100&cursor=abc')).statusCode).toBe(200);
      for (const query of ['?limit=0', '?limit=101', '?limit=ten', '?limit=2.5', '?cursor=']) {
        expect([query, (await get(query)).statusCode]).toEqual([query, 400]);
      }
      expect((await get(`?cursor=${'c'.repeat(513)}`)).statusCode).toBe(400);
      expect(listed).toEqual([{ limit: 25 }, { limit: 100, cursor: 'abc' }]);
    });

    it('refuses a cursor it never issued', async () => {
      await start({ listInvestigations: () => Promise.resolve('bad_cursor') });
      const response = await get('?cursor=forged');
      expect(response.statusCode).toBe(400);
      expect(response.json<{ detail: string }>().detail).toContain('cursor');
    });
  });

  describe('investigation detail', () => {
    const get = (id: string) =>
      app.inject({ url: `/v1/investigations/${id}`, headers: { 'x-api-key': API_KEY } });

    it('serves one investigation, 404 for an id it does not know, 400 for one that is no uuid', async () => {
      const asked: string[] = [];
      await start({
        getInvestigation: (id) => {
          asked.push(id);
          return Promise.resolve(id === DETAIL.id ? DETAIL : undefined);
        },
      });
      const found = await get(DETAIL.id);
      expect(found.statusCode).toBe(200);
      expect(found.json()).toEqual(DETAIL);
      const unknown = await get(ACTION_ID);
      expect(unknown.statusCode).toBe(404);
      expect(unknown.headers['content-type']).toMatch(/^application\/problem\+json/);
      expect((await get('not-a-uuid')).statusCode).toBe(400);
      // A bad id never reaches the store.
      expect(asked).toEqual([DETAIL.id, ACTION_ID]);
    });
  });

  describe('executor routes', () => {
    it('are never served by the public listener, whatever the credentials', async () => {
      await start();
      const headers = { 'x-api-key': API_KEY, authorization: `Bearer ${'executor'.repeat(5)}` };
      for (const url of [
        `/internal/actions/${ACTION_ID}/execution`,
        `/internal/actions/${ACTION_ID}/execution/result`,
      ]) {
        const response = await app.inject({ method: 'POST', url, headers, payload: {} });
        expect([url, response.statusCode]).toEqual([url, 404]);
      }
    });

    it('have their own listener, bound to 127.0.0.1 on another port by default', () => {
      const secret = (name: string) => `${name}-for-this-test-000000000000000`;
      const defaults = loadGatewayConfig({
        GATEWAY_DATABASE_URL: 'postgres://gateway@127.0.0.1:5434/triage',
        APPROVER_API_KEY: secret('approver'),
        GATEWAY_API_KEY: secret('gateway'),
        EXECUTOR_SERVICE_TOKEN: secret('executor'),
        AGENT_SERVICE_TOKEN: secret('agent'),
      });
      expect(defaults.GATEWAY_INTERNAL_HOST).toBe('127.0.0.1');
      expect(defaults.GATEWAY_INTERNAL_PORT).not.toBe(defaults.GATEWAY_PORT);
      // The executor's token may not be another service's: the agent could then claim actions.
      const shared = secret('shared');
      const load = () =>
        loadGatewayConfig({
          GATEWAY_DATABASE_URL: 'postgres://gateway@127.0.0.1:5434/triage',
          APPROVER_API_KEY: secret('approver'),
          GATEWAY_API_KEY: secret('gateway'),
          EXECUTOR_SERVICE_TOKEN: shared,
          AGENT_SERVICE_TOKEN: shared,
        });
      expect(load).toThrow('EXECUTOR_SERVICE_TOKEN: must differ from the others');
      expect(load).not.toThrow(shared);
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
      headers: { ...HEALTH_QUERY.headers, origin: 'https://evil.example' },
    });
    expect(response.headers['access-control-allow-origin']).toBeUndefined();
  });

  it('streams investigation updates over server-sent events', async () => {
    const updates = createUpdates();
    const address = await (await start({ updates })).listen({ host: '127.0.0.1', port: 0 });
    const abort = new AbortController();
    const response = await fetch(`${address}/graphql`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        accept: 'text/event-stream',
        'x-api-key': API_KEY,
      },
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
      verdict: null,
      models: [],
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
