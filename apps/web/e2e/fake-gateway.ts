// A stand-in gateway for the browser tests: node:http on 127.0.0.1 with no dependency. It serves the
// demo's samples, plus one investigation that gains a step on each read until its report lands,
// takes the same keys as the real one, and records plan decisions. /__e2e/* lets the tests reset
// and read its state; it is never part of the real gateway.
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';

import { EVAL_RUNS, LIVE_SAMPLES, WORKER_OUT_OF_MEMORY } from '../src/demo/index.ts';
import type { Investigation } from '../src/lib/investigations.ts';
import { E2E } from './shared.ts';

type Plan = NonNullable<Investigation['plan']>;
const RUNNING_STEPS = 4;
let requests = 0;
let reads = 0;
let decisions: { plan_id: string; verdict: string; approver: string; reason?: string }[] = [];
let decided = new Map<
  string,
  Pick<Plan, 'status' | 'decided_by' | 'decided_at' | 'decision_reason'>
>();

/** The running investigation after `count` reads: one more step each, then the report. */
function running(count: number): Investigation {
  const done = count > RUNNING_STEPS;
  return {
    ...WORKER_OUT_OF_MEMORY,
    id: E2E.runningId,
    group_id: 'c0ffee00-0000-4000-8000-000000000002',
    title: 'HighLatency on api',
    status: done ? 'resolved' : 'running',
    verdict: done ? 'not_real' : null,
    created_at: '2026-10-26T10:00:00.000Z',
    updated_at: '2026-10-26T10:00:00.000Z',
    cause: done ? { root_cause_class: 'unknown', service: 'api', confidence: 0.6 } : null,
    plan_status: null,
    summary: done ? 'The latency came back on its own; nothing failed.' : null,
    hypotheses: [],
    missing_evidence: [],
    models: [],
    plan: null,
    steps: Array.from({ length: Math.min(count, RUNNING_STEPS) }, (_, i) => ({
      seq: i + 1,
      kind: 'tool_call' as const,
      summary: `Step ${String(i + 1)} of the running triage.`,
      tool_call: null,
      recorded_at: new Date(Date.parse('2026-10-26T10:00:01Z') + i * 1000).toISOString(),
    })),
  };
}

/** An investigation with any recorded decision on its plan. */
function withDecision(investigation: Investigation): Investigation {
  const decision = investigation.plan && decided.get(investigation.plan.id);
  if (!investigation.plan || !decision) return investigation;
  return {
    ...investigation,
    plan_status: decision.status,
    plan: { ...investigation.plan, ...decision },
  };
}

const all = () => [running(reads), ...LIVE_SAMPLES, ...EVAL_RUNS].map(withDecision);
const send = (response: ServerResponse, status: number, body: unknown) =>
  response.writeHead(status, { 'content-type': 'application/json' }).end(JSON.stringify(body));
const body = async (request: IncomingMessage) => {
  let text = '';
  for await (const chunk of request) text += String(chunk);
  return text === '' ? {} : (JSON.parse(text) as Record<string, unknown>);
};

createServer((request, response) => {
  void (async () => {
    const url = new URL(request.url ?? '/', E2E.gatewayUrl);
    if (url.pathname === '/healthz') return send(response, 200, { status: 'ok' });
    if (url.pathname === '/__e2e/reset' && request.method === 'POST') {
      reads = 0;
      decisions = [];
      decided = new Map();
      return send(response, 200, {});
    }
    if (url.pathname === '/__e2e/state') return send(response, 200, { requests, reads, decisions });
    requests++;
    const plan = /^\/v1\/plans\/([^/]+)\/(approve|reject)$/.exec(url.pathname);
    if (plan !== null && request.method === 'POST') {
      if (request.headers['x-approver-key'] !== E2E.approverKey) return send(response, 401, {});
      const [, id = '', verdict = ''] = plan;
      const found = all().find((item) => item.plan?.id === id)?.plan;
      if (!found) return send(response, 404, { title: 'Not Found', status: 404 });
      if (found.status !== 'pending')
        return send(response, 409, { title: 'Conflict', status: 409 });
      const { approver, reason } = (await body(request)) as { approver: string; reason?: string };
      decisions.push({
        plan_id: id,
        verdict,
        approver,
        ...(reason === undefined ? {} : { reason }),
      });
      const decision = {
        status: verdict === 'approve' ? ('approved' as const) : ('rejected' as const),
        decided_by: approver,
        decided_at: new Date().toISOString(),
        decision_reason: reason ?? null,
      };
      decided.set(id, decision);
      return send(response, 200, { ...found, ...decision });
    }
    if (request.headers['x-api-key'] !== E2E.apiKey) return send(response, 401, {});
    if (url.pathname === '/graphql')
      return send(response, 200, { data: { health: { status: 'ok' } } });
    if (url.pathname === '/v1/investigations') {
      const source = url.searchParams.get('source');
      const items = all()
        .filter((item) => source === null || item.source === source)
        .sort((a, b) => b.created_at.localeCompare(a.created_at));
      return send(response, 200, { items, next_cursor: null });
    }
    const id = /^\/v1\/investigations\/([^/]+)$/.exec(url.pathname)?.[1];
    if (id === E2E.runningId) reads++;
    const investigation = all().find((item) => item.id === id);
    return investigation
      ? send(response, 200, investigation)
      : send(response, 404, { title: 'Not Found', status: 404 });
  })();
}).listen(E2E.gatewayPort, '127.0.0.1');
