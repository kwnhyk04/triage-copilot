// A stand-in gateway for the browser tests: node:http on 127.0.0.1 with no dependency. It serves the
// demo's samples, whose pending plan the review flagged, plus one investigation that gains a step
// on each read until its report lands, one real incident whose plan lands on its third read, one
// whose plan the review refused, and one whose plan no model reviewed. Older fillers give each list
// several pages. It takes the same
// keys as the real one, numbers its pages as the real one does, and records plan decisions.
// /__e2e/* lets the tests reset and read its state; it is never part of the real gateway.
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';

import { EVAL_RUNS, LIVE_SAMPLES, WORKER_OUT_OF_MEMORY } from '../src/demo/index.ts';
import type { Investigation } from '../src/lib/investigations.ts';
import { E2E } from './shared.ts';

type Plan = NonNullable<Investigation['plan']>;
const RUNNING_STEPS = 4;
const PLANNING_READS = 2;
let requests = 0;
let reads = 0;
let planningReads = 0;
// The planning incident's report lands at the start, so the web's 10-minute wait has begun.
let planningSince = new Date().toISOString();
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
    plan_review: null,
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

/** The real incident after `count` reads: its report is in, and its plan lands on read 3. */
function planning(count: number): Investigation {
  const landed = count > PLANNING_READS;
  const plan = { ...WORKER_OUT_OF_MEMORY.plan, id: 'c0ffee00-0000-4000-8000-000000000004' };
  return {
    ...WORKER_OUT_OF_MEMORY,
    id: E2E.planningId,
    group_id: 'c0ffee00-0000-4000-8000-000000000005',
    title: 'ReadinessCheckFailed on api',
    created_at: planningSince,
    updated_at: planningSince,
    plan_status: landed ? 'pending' : null,
    plan_review: landed ? plan.review.status : null,
    plan: landed ? plan : null,
  };
}

/** A real incident whose plan the review refused: it stays pending and takes no decision. */
const REFUSED: Investigation = {
  ...WORKER_OUT_OF_MEMORY,
  id: E2E.refusedId,
  group_id: 'c0ffee00-0000-4000-8000-000000000007',
  title: 'QueueBacklog on worker',
  plan_status: 'pending',
  plan_review: 'refuse',
  plan: {
    ...WORKER_OUT_OF_MEMORY.plan,
    id: 'c0ffee00-0000-4000-8000-000000000008',
    proposal: {
      kind: 'restart_service',
      target: 'worker',
      files: [],
      rationale:
        'The worker exited with 137 [tc_01JB2SNAP01]. Restart it, then flush the queue in ' +
        'redis to rule it out.',
    },
    review: {
      status: 'refuse',
      by: 'model',
      model: 'claude-sonnet-5-5',
      issues: [
        {
          kind: 'forbidden_step',
          quote: 'flush the queue in redis to rule it out',
          reason: 'A change made as a test to redis, whose health check passes [tc_01JB2SNAP01].',
        },
      ],
      reason: null,
      new_files: [],
    },
  },
};

/** A real incident whose plan no model reviewed: it takes no approval. */
const UNREVIEWED: Investigation = {
  ...WORKER_OUT_OF_MEMORY,
  id: E2E.unreviewedId,
  group_id: 'c0ffee00-0000-4000-8000-00000000000a',
  title: 'WorkerStalled on worker',
  plan_status: 'pending',
  plan_review: 'not_reviewed',
  plan: {
    ...WORKER_OUT_OF_MEMORY.plan,
    id: 'c0ffee00-0000-4000-8000-00000000000b',
    review: {
      status: 'not_reviewed',
      by: null,
      model: null,
      issues: [],
      reason: 'its credits ended on 2026-11-10 (UTC)',
      new_files: [],
    },
  },
};

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

/** Settled investigations older than every other, `count` of one source, newest first. */
const fillers = (source: 'live' | 'sim', count: number): Investigation[] =>
  Array.from({ length: count }, (_, i) => {
    const [name, service] = E2E.fillerTitles[i % E2E.fillerTitles.length] ?? ['Alert', 'api'];
    const base = source === 'live' ? WORKER_OUT_OF_MEMORY : (EVAL_RUNS[0] ?? WORKER_OUT_OF_MEMORY);
    const at = new Date(Date.parse('2026-10-01T00:00:00Z') - i * 3_600_000).toISOString();
    return {
      ...base,
      id: E2E.fillerId(source, i),
      group_id: E2E.fillerId(source, i + 500),
      title: `${name} on ${service}`,
      created_at: at,
      updated_at: at,
      source,
      plan: null,
      plan_status: null,
      plan_review: null,
    };
  });
const FILLERS = [...fillers('live', E2E.liveFillers), ...fillers('sim', E2E.simFillers)];

const all = () =>
  [
    running(reads),
    planning(planningReads),
    REFUSED,
    UNREVIEWED,
    ...LIVE_SAMPLES,
    ...EVAL_RUNS,
    ...FILLERS,
  ].map(withDecision);
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
      planningReads = 0;
      planningSince = new Date().toISOString();
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
      // As the real gateway: one decision per plan, none on a plan its review refused, and no
      // approval of a plan no model reviewed.
      const unreviewed = verdict === 'approve' && found.review?.by !== 'model';
      if (found.status !== 'pending' || found.review?.status === 'refuse' || unreviewed)
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
      const offset = Number(url.searchParams.get('offset') ?? 0);
      const limit = Number(url.searchParams.get('limit') ?? 25);
      const items = all()
        .filter((item) => source === null || item.source === source)
        .sort((a, b) => b.created_at.localeCompare(a.created_at));
      return send(response, 200, {
        items: items.slice(offset, offset + limit),
        next_cursor: null,
        total: items.length,
      });
    }
    const id = /^\/v1\/investigations\/([^/]+)$/.exec(url.pathname)?.[1];
    if (id === E2E.runningId) reads++;
    if (id === E2E.planningId) planningReads++;
    const investigation = all().find((item) => item.id === id);
    return investigation
      ? send(response, 200, investigation)
      : send(response, 404, { title: 'Not Found', status: 404 });
  })();
}).listen(E2E.gatewayPort, '127.0.0.1');
