import { randomUUID } from 'node:crypto';
import { setTimeout as sleep } from 'node:timers/promises';

import type { components } from '@triage/ts-clients/gateway';
import type { FastifyInstance } from 'fastify';

import { buildApp, createUpdates } from './app.ts';
import { connectDb } from './db.ts';
import { storeEvent } from './events.ts';
import { createGetInvestigation, createListInvestigations } from './investigations.ts';
import { APP_CONFIG, newTenant, scratchDb } from './testing.ts';

type Page = components['schemas']['InvestigationPage'];
type Investigation = components['schemas']['Investigation'];
const SNAPSHOT = 'tc_01JA0SNAP0';
const DEPENDENCIES = 'tc_01JA0DEPS0';
const MODELS = [
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
    calls: 3,
    failed: 0,
    reason: null,
  },
] as const;

describe('investigation list against Postgres', () => {
  let scratch: Awaited<ReturnType<typeof scratchDb>>;
  let db: ReturnType<typeof connectDb>;
  let app: FastifyInstance;
  const unused = () => Promise.reject(new Error('unused by this test'));

  beforeAll(async () => {
    scratch = await scratchDb();
    db = connectDb(scratch.url);
    app = await buildApp({
      config: APP_CONFIG,
      ping: db.ping,
      updates: createUpdates(),
      decide: unused,
      findTenant: unused,
      intake: unused,
      listInvestigations: createListInvestigations(db.sql),
      getInvestigation: createGetInvestigation(db.sql),
    });
  }, 60_000);
  afterAll(async () => {
    await app.close();
    await db.sql.end();
    await scratch.drop();
  });
  beforeEach(async () => {
    await db.sql`DELETE FROM gateway.approvals`;
    await db.sql`DELETE FROM gateway.proposed_actions`;
    await db.sql`DELETE FROM gateway.alerts`;
    await db.sql`DELETE FROM gateway.alert_groups`;
  });

  /** Investigated groups; rows of one statement share created_at, so only the id orders them. */
  const insert = async (tenantId: string, count: number, createdAt: string | null = null) =>
    (
      await db.sql<{ investigation_id: string }[]>`
        INSERT INTO gateway.alert_groups
          (tenant_id, group_key, window_start, severity, status, investigation_id, created_at)
        SELECT ${tenantId}, 'group-' || gen_random_uuid(), now(), 'warning', 'queued',
               gen_random_uuid(), coalesce(${createdAt}::text::timestamptz, now())
        FROM generate_series(1, ${count})
        RETURNING investigation_id`
    ).map((row) => row.investigation_id);
  const page = async (query: string) => {
    const response = await app.inject({
      url: `/v1/investigations?${query}`,
      headers: { 'x-api-key': APP_CONFIG.GATEWAY_API_KEY },
    });
    expect(response.statusCode).toBe(200);
    return response.json<Page>();
  };
  /** Every page from the first, `limit` items each. */
  const walk = async (limit: number) => {
    const ids: string[] = [];
    let cursor: string | null = null;
    let pages = 0;
    do {
      const query: string = cursor === null ? '' : `&cursor=${cursor}`;
      const body = await page(`limit=${String(limit)}${query}`);
      ids.push(...body.items.map((item) => item.id));
      cursor = body.next_cursor;
      pages++;
    } while (cursor !== null);
    return { ids, pages };
  };
  /** The ids in the order the list promises: created_at, then id, newest first. */
  const ordered = async (ids: string[]) =>
    (
      await db.sql<{ investigation_id: string }[]>`
        SELECT investigation_id FROM gateway.alert_groups WHERE investigation_id = ANY(${ids})
        ORDER BY created_at DESC, investigation_id DESC`
    ).map((row) => row.investigation_id);

  it('lists 25 per page by default, newest first', async () => {
    const { tenantId } = await newTenant(db.sql);
    await insert(tenantId, 30);
    const first = await page('');
    expect(first.items).toHaveLength(25);
    expect(first.next_cursor).not.toBeNull();
    expect(first.items.map((item) => item.id)).toEqual(
      await ordered(first.items.map((item) => item.id)),
    );
  });

  it('keeps rows created within one millisecond apart, by their microseconds and ids', async () => {
    const { tenantId } = await newTenant(db.sql);
    const before = [
      ...(await insert(tenantId, 1, '2026-01-01T00:00:00.123999Z')),
      ...(await insert(tenantId, 1, '2026-01-01T00:00:00.123456Z')),
      ...(await insert(tenantId, 3, '2026-01-01T00:00:00.123455Z')),
      ...(await insert(tenantId, 1, '2026-01-01T00:00:00.123001Z')),
      ...(await insert(tenantId, 2, '2026-01-01T00:00:00.123000Z')),
    ];
    // Stored with their microseconds: five instants, all within one millisecond.
    expect(await db.sql`SELECT DISTINCT created_at FROM gateway.alert_groups`).toHaveLength(5);
    // Two per page, so pages end inside the millisecond and inside each shared microsecond.
    const { ids, pages } = await walk(2);
    expect(pages).toBe(4);
    expect(ids).toEqual(await ordered(before));
  });

  it('pages through concurrent inserts with no duplicate and no gap', async () => {
    const { tenantId } = await newTenant(db.sql);
    const before = [
      ...(await insert(tenantId, 12)),
      ...(await insert(tenantId, 10, '2026-01-01T00:00:00Z')),
    ];
    for (let i = 0; i < 25; i++) before.push(...(await insert(tenantId, 1)));
    // New investigations arrive while the pages are read, both before and behind the cursor.
    const stop = new AbortController();
    let written = 0;
    const writer = (async () => {
      while (!stop.signal.aborted) {
        await insert(tenantId, 1, written % 2 === 0 ? null : '2026-06-01T00:00:00Z');
        written++;
        await sleep(1);
      }
    })();
    let walked;
    try {
      walked = await walk(7);
    } finally {
      stop.abort();
      await writer;
    }
    expect(written).toBeGreaterThan(0);
    expect(new Set(walked.ids).size).toBe(walked.ids.length);
    expect(walked.ids).toEqual(expect.arrayContaining(before));
    expect(walked.ids).toEqual(await ordered(walked.ids));
    expect(walked.pages).toBeGreaterThanOrEqual(Math.ceil(before.length / 7));
  });

  /** One alert in an investigated group, as watch posts a failed readiness check. */
  const alert = (tenantId: string, investigationId: string) => {
    const labels = db.sql.json({ alertname: 'ApiReadinessFailing', service: 'api' });
    return db.sql`
      INSERT INTO gateway.alerts (tenant_id, group_id, fingerprint, labels, starts_at, status)
      SELECT ${tenantId}, id, 'f0', ${labels}, now(), 'firing'
      FROM gateway.alert_groups WHERE investigation_id = ${investigationId}`;
  };
  /** A message stored as the events consumer stores it. */
  const store = async (type: 'step-recorded' | 'report-recorded', message: object) => {
    expect(await storeEvent(db.sql, type, JSON.stringify(message))).toBe('stored');
  };
  /** A StepRecorded v3: by default the model asking for the snapshot. */
  const step = (investigationId: string, seq: number, change: object) =>
    store('step-recorded', {
      schema_version: 3,
      investigation_id: investigationId,
      seq,
      kind: 'tool_call',
      summary: 'Read the snapshot of alert 1.',
      tool_name: 'read_snapshot',
      tool_call_id: SNAPSHOT,
      result: null,
      latency_ms: null,
      tokens: null,
      recorded_at: '2026-10-25T09:53:00Z',
      ...change,
    });
  const get = (id: string) =>
    app.inject({
      url: `/v1/investigations/${id}`,
      headers: { 'x-api-key': APP_CONFIG.GATEWAY_API_KEY },
    });
  const detail = async (id: string) => {
    const response = await get(id);
    expect(response.statusCode).toBe(200);
    return response.json<Investigation>();
  };

  it('serves a live triage as its report and steps hold it, and lists its verdict', async () => {
    const { tenantId } = await newTenant(db.sql);
    const [id = '', other = ''] = await insert(tenantId, 2);
    await alert(tenantId, id);
    const snapshot = `postgres: restarting (exit 137)\n${'log line\n'.repeat(400)}`;
    const dependencies = { tool_name: 'get_dependencies', tool_call_id: DEPENDENCIES };
    await step(id, 1, { tokens: { input: 5000, output: 120 } });
    await step(id, 2, { summary: 'read_snapshot returned', result: snapshot, latency_ms: 12 });
    const asked = { summary: 'What the api needs.', tokens: { input: 6000, output: 40 } };
    await step(id, 3, { ...dependencies, ...asked });
    await step(id, 4, {
      ...dependencies,
      summary: 'get_dependencies failed: TimeoutError: ',
      latency_ms: 10_000,
    });
    const summary = "freight-audit's api fails its readiness check: postgres restarts.";
    await step(id, 5, {
      kind: 'report',
      summary,
      tool_name: null,
      tool_call_id: null,
      tokens: { input: 7000, output: 300 },
    });
    const database = 'The readiness check fails on postgres, whose container restarts.';
    await store('report-recorded', {
      schema_version: 2,
      investigation_id: id,
      status: 'resolved',
      report: {
        summary,
        hypotheses: [
          {
            root_cause_class: 'database',
            service: 'postgres',
            confidence: 0.8,
            summary: database,
            evidence_ids: [SNAPSHOT],
          },
          {
            root_cause_class: 'dependency_failure',
            service: 'api',
            confidence: 0.3,
            summary: 'A dependency of the api may be down.',
            evidence_ids: [DEPENDENCIES],
          },
        ],
        missing_evidence: ['get_dependencies failed: it timed out.'],
        proposed_actions: [],
        partial: false,
      },
      verdict: 'real',
      models: MODELS,
      recorded_at: '2026-10-25T09:54:10Z',
    });

    const snapshotCall = { id: SNAPSHOT, name: 'read_snapshot', status: 'ok', duration_ms: 12 };
    const timedOut = {
      id: DEPENDENCIES,
      name: 'get_dependencies',
      status: 'timeout',
      duration_ms: 10_000,
    };
    const at = '2026-10-25T09:53:00.000Z';
    const [group] = await db.sql<{ id: string; created_at: Date; updated_at: Date }[]>`
      SELECT id, created_at, updated_at FROM gateway.alert_groups WHERE investigation_id = ${id}`;
    if (group === undefined) throw new Error('no group');
    expect(await detail(id)).toEqual({
      id,
      group_id: group.id,
      title: 'ApiReadinessFailing on api',
      status: 'resolved',
      verdict: 'real',
      created_at: group.created_at.toISOString(),
      updated_at: group.updated_at.toISOString(),
      summary,
      // The turns' tokens, the report's included.
      tokens_used: 18_460,
      hypotheses: [
        {
          rank: 1,
          root_cause_class: 'database',
          service: 'postgres',
          confidence: 0.8,
          summary: database,
          evidence: [{ tool_call_id: SNAPSHOT, excerpt: snapshot.slice(0, 2000) }],
        },
        {
          rank: 2,
          root_cause_class: 'dependency_failure',
          service: 'api',
          confidence: 0.3,
          summary: 'A dependency of the api may be down.',
          // A call that never returned has no result to quote.
          evidence: [{ tool_call_id: DEPENDENCIES, excerpt: '' }],
        },
      ],
      missing_evidence: ['get_dependencies failed: it timed out.'],
      partial: false,
      models: MODELS,
      proposed_actions: [],
      steps: [
        { seq: 1, kind: 'tool_call', summary: 'Read the snapshot of alert 1.' },
        { seq: 2, kind: 'tool_call', summary: 'read_snapshot returned' },
        { seq: 3, kind: 'tool_call', summary: 'What the api needs.' },
        { seq: 4, kind: 'tool_call', summary: 'get_dependencies failed: TimeoutError: ' },
        { seq: 5, kind: 'report', summary },
      ].map((s, i) => ({
        ...s,
        tool_call: [snapshotCall, snapshotCall, timedOut, timedOut, null][i],
        recorded_at: at,
      })),
    });
    const listed = await page('');
    expect(listed.items.map((item) => [item.id, item.verdict])).toEqual(
      expect.arrayContaining([
        [id, 'real'],
        [other, null],
      ]),
    );
  });

  it('serves an investigation before its report, an older report with its decision, and 404', async () => {
    const { tenantId } = await newTenant(db.sql);
    const [running = '', old = ''] = await insert(tenantId, 2);
    await step(running, 1, { tokens: { input: 5000, output: 120 } });
    expect(await detail(running)).toMatchObject({
      status: 'queued',
      verdict: null,
      summary: null,
      tokens_used: 5120,
      hypotheses: [],
      missing_evidence: [],
      partial: false,
      models: [],
      proposed_actions: [],
      // The call has not returned yet.
      steps: [{ seq: 1, kind: 'tool_call', tool_call: null }],
    });

    const action = randomUUID();
    const rationale = 'Roll back to the version before the errors.';
    await store('report-recorded', {
      schema_version: 1,
      investigation_id: old,
      status: 'awaiting_approval',
      report: {
        summary: 'checkout v1.8.0 raised the 5xx rate.',
        hypotheses: [
          {
            root_cause_class: 'bad_deploy',
            service: 'checkout',
            confidence: 0.6,
            summary: 'Errors began after v1.8.0.',
            evidence_ids: [],
          },
        ],
        missing_evidence: [],
        proposed_actions: [
          {
            action_id: action,
            kind: 'rollback_deploy',
            target: 'checkout',
            parameters: { to_version: 'v1.7.3' },
            rationale,
            evidence_ids: [SNAPSHOT],
          },
        ],
        partial: true,
      },
      recorded_at: '2026-10-25T10:00:00Z',
    });
    await db.sql`
      INSERT INTO gateway.approvals (action_id, approver, decision, reason, decided_at)
      VALUES (${action}, 'oncall@example.com', 'rejected', 'Too risky at peak',
              '2026-10-25T10:05:00Z')`;
    // A v1 report carries no verdict and no models.
    expect(await detail(old)).toMatchObject({
      status: 'awaiting_approval',
      verdict: null,
      models: [],
      partial: true,
      proposed_actions: [
        {
          id: action,
          investigation_id: old,
          kind: 'rollback_deploy',
          target: 'checkout',
          parameters: { to_version: 'v1.7.3' },
          rationale,
          status: 'rejected',
          proposed_at: '2026-10-25T10:00:00.000Z',
          decided_by: 'oncall@example.com',
          decided_at: '2026-10-25T10:05:00.000Z',
          decision_reason: 'Too risky at peak',
        },
      ],
    });
    expect((await get(randomUUID())).statusCode).toBe(404);
  });
});
