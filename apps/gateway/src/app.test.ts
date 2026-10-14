import type { FastifyInstance } from 'fastify';

import { type AppDeps, buildApp, createUpdates } from './app.ts';

const config = { LOG_LEVEL: 'error' } as const;
const up = () => Promise.resolve();

describe('gateway', () => {
  let app: FastifyInstance;
  const start = (deps: Partial<AppDeps> = {}) => {
    app = buildApp({ config, ping: up, updates: createUpdates(), ...deps });
    return app;
  };
  afterEach(() => app.close());

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
        query: 'subscription { investigationUpdated(id: "inv-1") { investigationId status } }',
      }),
      signal: abort.signal,
    });
    if (!response.body) throw new Error('subscription returned no body');

    const update = { investigationId: 'inv-1', status: 'RUNNING' } as const;
    // SSE has no "subscribed" signal, so publish until the first event arrives.
    const publisher = setInterval(() => {
      updates.publish('investigation:updated', 'inv-1', update);
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
      expect(text).toContain(JSON.stringify({ data: { investigationUpdated: update } }));
    } finally {
      clearInterval(publisher);
      abort.abort();
    }
  });
});
