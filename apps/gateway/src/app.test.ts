import type { Investigation } from '@triage/ts-clients/graphql';
import type { FastifyInstance } from 'fastify';

import { type AppDeps, buildApp, createUpdates, type Decide } from './app.ts';

const APPROVER_KEY = 'approver-test-key-000000000000000';
const config = { LOG_LEVEL: 'error', APPROVER_API_KEY: APPROVER_KEY } as const;
const up = () => Promise.resolve();
const ACTION_ID = '9f8b6c1e-3d2a-4f5b-8e7d-6c5b4a392817';

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

describe('gateway', () => {
  let app: FastifyInstance;
  const start = (deps: Partial<AppDeps> = {}) => {
    app = buildApp({ config, ping: up, updates: createUpdates(), decide: record, ...deps });
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
      start();
      for (const headers of [{ 'x-api-key': 'gateway-api-key' }, { authorization: 'Bearer am' }]) {
        const response = await post(headers);
        expect(response.statusCode).toBe(403);
        expect(response.headers['content-type']).toMatch(/^application\/problem\+json/);
      }
    });

    it('answer 401 with a challenge when the approver key is missing or wrong', async () => {
      start();
      for (const headers of [{}, { 'x-approver-key': 'not-the-approver-key' }]) {
        const response = await post(headers);
        expect(response.statusCode).toBe(401);
        expect(response.headers['www-authenticate']).toBe('ApiKey header="X-Approver-Key"');
      }
    });

    it('record the decision with the approver key', async () => {
      start();
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
      start({
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

  it('answers /healthz', async () => {
    const response = await start().inject({ url: '/healthz' });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: 'ok' });
  });

  it('answers /readyz with 503 when a dependency is down', async () => {
    const response = await start({ ping: () => Promise.reject(new Error('down')) }).inject({
      url: '/readyz',
    });
    expect(response.statusCode).toBe(503);
    expect(response.json()).toEqual({ status: 'unavailable' });
  });

  it('serves the GraphQL health query', async () => {
    const response = await start().inject({
      method: 'POST',
      url: '/graphql',
      payload: { query: '{ health { status } }' },
    });
    expect(response.json()).toEqual({ data: { health: { status: 'ok' } } });
  });

  it('grants no cross-origin access, so browsers cannot call it directly', async () => {
    const response = await start().inject({
      method: 'POST',
      url: '/graphql',
      headers: { origin: 'https://evil.example' },
      payload: { query: '{ health { status } }' },
    });
    expect(response.headers['access-control-allow-origin']).toBeUndefined();
  });

  it('streams investigation updates over server-sent events', async () => {
    const updates = createUpdates();
    const address = await start({ updates }).listen({ host: '127.0.0.1', port: 0 });
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
