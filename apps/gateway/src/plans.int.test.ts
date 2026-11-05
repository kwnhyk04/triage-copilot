import { randomUUID } from 'node:crypto';

import { parseMessage } from '@triage/ts-clients/messages';
import type { FastifyInstance } from 'fastify';

import { buildApp, createUpdates } from './app.ts';
import { connectDb } from './db.ts';
import { createDecidePlan } from './decisions.ts';
import { storeEvent } from './events.ts';
import { sha256 } from './intake.ts';
import { createGetInvestigation } from './investigations.ts';
import { APP_CONFIG, newTenant, scratchDb } from './testing.ts';

const model = (part: string, calls: number) => ({
  part,
  provider: 'anthropic',
  model: part === 'plan' ? 'claude-opus-5-5' : 'claude-haiku-5-5',
  calls,
  failed: calls === 0 ? 1 : 0,
  reason: calls === 0 ? 'Refused: anthropic declined the request (category cyber)' : null,
});
const PROPOSAL = {
  kind: 'restart_service',
  target: 'worker',
  files: [] as string[],
  rationale:
    'The worker exited with 137 while its dependencies stay healthy [tc_01J9ZK3Q7M]. Restart ' +
    'it. If it exits again, raise its memory limit.',
};
/** PlanRecorded v2's review. */
interface Review {
  status: string;
  by: string | null;
  model: string | null;
  issues: { kind: string; quote: string; reason: string }[];
  reason: string | null;
  new_files: string[];
}
/** The reviewer's model refused the plan. */
const REFUSED: Review = {
  status: 'refuse',
  by: 'model',
  model: 'claude-sonnet-5-5',
  issues: [
    {
      kind: 'forbidden_step',
      quote: 'Restart it.',
      reason: 'Nothing cited shows the worker failing now [tc_01J9ZK3Q7M].',
    },
  ],
  reason: null,
  new_files: [],
};
const FLAGGED: Review = {
  ...REFUSED,
  status: 'flag',
  issues: [
    {
      kind: 'fallback_step',
      quote: 'If it exits again, raise its memory limit.',
      reason: 'Nothing cited shows that the memory limit is too low [tc_01J9ZK3Q7M].',
    },
  ],
};
const OK: Review = { ...REFUSED, status: 'ok', issues: [] };
const NOT_REVIEWED: Review = {
  ...OK,
  status: 'not_reviewed',
  by: null,
  reason: "the reviewer's daily cap is spent",
};
/** A plan from before reviews: PlanRecorded v1, which carries none. */
const V1 = 'v1';
const WINDOW_HOURS = 6;

describe('plans against Postgres', () => {
  let scratch: Awaited<ReturnType<typeof scratchDb>>;
  let db: ReturnType<typeof connectDb>;
  let app: FastifyInstance;
  let nudges = 0;
  // LIVE_TENANTS: each plan's tenant is live, as only live triage makes plans.
  const apps: Record<string, 'freight-audit' | 'filing-lens'> = {};
  const unused = () => Promise.reject(new Error('unused by this test'));

  beforeAll(async () => {
    scratch = await scratchDb();
    db = connectDb(scratch.url);
    app = await buildApp({
      config: APP_CONFIG,
      ping: db.ping,
      updates: createUpdates(),
      decide: unused,
      decidePlan: createDecidePlan(
        db.sql,
        () => {
          nudges++;
        },
        apps,
        WINDOW_HOURS,
      ),
      findTenant: unused,
      intake: unused,
      listInvestigations: unused,
      getInvestigation: createGetInvestigation(db.sql, {
        liveTenants: [],
        stuckAfterMinutes: 10,
        approvalWindowHours: WINDOW_HOURS,
      }),
    });
  }, 60_000);
  afterAll(async () => {
    await app.close();
    await db.sql.end();
    await scratch.drop();
  });

  /** A live triage's alert group with its report, then its plan: v2 with a review, or v1. */
  const planned = async (
    proposal: typeof PROPOSAL | null = PROPOSAL,
    review: Review | null | typeof V1 = OK,
  ) => {
    const { tenantId } = await newTenant(db.sql);
    apps[tenantId] = 'freight-audit';
    const investigationId = randomUUID();
    await db.sql`
      INSERT INTO gateway.alert_groups
        (tenant_id, group_key, window_start, severity, status, investigation_id)
      VALUES (${tenantId}, ${randomUUID()}, now(), 'critical', 'running', ${investigationId})`;
    const report = {
      schema_version: 2,
      investigation_id: investigationId,
      status: 'resolved',
      report: {
        summary: "The worker's container exited with 137.",
        hypotheses: [],
        missing_evidence: [],
        proposed_actions: [],
        partial: false,
      },
      verdict: 'real',
      models: [model('triage', 3)],
      recorded_at: '2026-10-26T10:01:30Z',
    };
    expect(await storeEvent(db.sql, 'report-recorded', JSON.stringify(report))).toBe('stored');
    const plan = message(investigationId, proposal, review);
    expect(await storeEvent(db.sql, 'plan-recorded', JSON.stringify(plan))).toBe('stored');
    return { tenantId, investigationId, planId: plan.plan_id, plan };
  };
  const message = (
    investigationId: string,
    proposal: typeof PROPOSAL | null,
    review: Review | null | typeof V1,
  ) => ({
    schema_version: review === V1 ? 1 : 2,
    plan_id: randomUUID(),
    investigation_id: investigationId,
    plan: proposal,
    problem: proposal === null ? 'No model gave a plan: claude-opus-5-5: Refused' : null,
    ...(review === V1 ? {} : { review }),
    models: [model('plan', proposal === null ? 0 : 1)],
    recorded_at: '2026-10-26T10:03:10Z',
  });
  const detail = async (investigationId: string) =>
    (
      await app.inject({
        method: 'GET',
        url: `/v1/investigations/${investigationId}`,
        headers: { 'x-api-key': APP_CONFIG.GATEWAY_API_KEY },
      })
    ).json<{ plan: Record<string, unknown> | null; models: { part: string }[] }>();
  const decide = (planId: string, route: 'approve' | 'reject', approver = 'oncall@example.com') =>
    app.inject({
      method: 'POST',
      url: `/v1/plans/${planId}/${route}`,
      headers: { 'x-approver-key': APP_CONFIG.APPROVER_API_KEY },
      payload: { approver },
    });
  const queued = async () =>
    (await db.sql<{ count: number }[]>`SELECT count(*)::int AS count FROM gateway.outbox`)[0]
      ?.count;
  const sent = async (planId: string) => [
    ...(await db.sql<{ queue: string; message_group_id: string; body: unknown }[]>`
      SELECT queue, message_group_id, body FROM gateway.outbox WHERE deduplication_id = ${planId}`),
  ];

  it('stores a plan once and serves it, its models after the report', async () => {
    const { investigationId, planId, plan } = await planned(PROPOSAL, V1);
    const changed = { ...plan, plan: { ...PROPOSAL, target: 'api' } };
    expect(await storeEvent(db.sql, 'plan-recorded', JSON.stringify(changed))).toBe('stored');

    const investigation = await detail(investigationId);

    expect(investigation.plan).toEqual({
      id: planId,
      proposal: PROPOSAL,
      problem: null,
      // PlanRecorded v1 carries no review.
      review: null,
      status: 'pending',
      recorded_at: '2026-10-26T10:03:10.000Z',
      decided_by: null,
      decided_at: null,
      decision_reason: null,
      approval_window_hours: WINDOW_HOURS,
    });
    expect(investigation.models.map((use) => use.part)).toEqual(['triage', 'plan']);
  });

  it('serves a v2 plan with its review, and one without a proposal with none', async () => {
    const { investigationId, planId } = await planned(PROPOSAL, FLAGGED);
    expect((await detail(investigationId)).plan).toEqual({
      id: planId,
      proposal: PROPOSAL,
      problem: null,
      review: FLAGGED,
      status: 'pending',
      recorded_at: '2026-10-26T10:03:10.000Z',
      decided_by: null,
      decided_at: null,
      decision_reason: null,
      approval_window_hours: WINDOW_HOURS,
    });
    const empty = await planned(null, null);
    expect((await detail(empty.investigationId)).plan).toMatchObject({
      proposal: null,
      review: null,
    });
  });

  it('takes no decision on a refused plan: approve and reject get 409 and record nothing', async () => {
    const { investigationId, planId } = await planned(PROPOSAL, REFUSED);
    const before = await queued();
    // No decision is recorded, so it reads pending; its review says it is refused.
    expect(await detail(investigationId)).toMatchObject({
      plan_status: 'pending',
      plan_review: 'refuse',
      plan: { review: REFUSED, status: 'pending' },
    });
    for (const route of ['approve', 'reject'] as const) {
      const answer = await decide(planId, route);
      expect([route, answer.statusCode]).toEqual([route, 409]);
      expect(answer.json<{ detail: string }>().detail).toBe('A refused plan takes no decision.');
    }
    const decisions = await db.sql`SELECT * FROM gateway.plan_decisions WHERE plan_id = ${planId}`;
    expect([...decisions]).toEqual([]);
    expect(await queued()).toBe(before);
    expect((await detail(investigationId)).plan).toMatchObject({
      status: 'pending',
      decided_by: null,
      decided_at: null,
    });
  });

  it('decides a flagged and an unflagged plan like any other', async () => {
    for (const review of [FLAGGED, OK]) {
      const { planId } = await planned(PROPOSAL, review);
      const approved = await decide(planId, 'approve');
      expect([review.status, approved.statusCode]).toEqual([review.status, 200]);
      expect(approved.json()).toMatchObject({
        id: planId,
        review,
        status: 'approved',
        approval_window_hours: WINDOW_HOURS,
      });
      // An approved restart is queued for the executor.
      expect(await sent(planId)).toHaveLength(1);
    }
  });

  it('refuses to approve a plan no model reviewed: 409, a clear error, nothing recorded or queued', async () => {
    // The reviewer could not answer, or the plan is from before reviews: either way no model read it.
    for (const review of [NOT_REVIEWED, V1] as const) {
      const { investigationId, planId } = await planned(PROPOSAL, review);
      const before = await queued();
      const answer = await decide(planId, 'approve');
      expect(answer.statusCode).toBe(409);
      expect(answer.headers['content-type']).toMatch(/^application\/problem\+json/);
      expect(answer.json<{ detail: string }>().detail).toBe(
        'This plan has no model review, so it cannot be approved.',
      );
      const decisions =
        await db.sql`SELECT * FROM gateway.plan_decisions WHERE plan_id = ${planId}`;
      expect([...decisions]).toEqual([]);
      expect(await queued()).toBe(before);
      expect(await sent(planId)).toEqual([]);
      expect((await detail(investigationId)).plan).toMatchObject({ status: 'pending' });
    }
  });

  it('still lets a human reject a plan no model reviewed', async () => {
    const { planId } = await planned(PROPOSAL, NOT_REVIEWED);
    const rejected = await decide(planId, 'reject');
    expect(rejected.statusCode).toBe(200);
    expect(rejected.json()).toMatchObject({ id: planId, status: 'rejected' });
    expect(await sent(planId)).toEqual([]);
  });

  it('serves a plan without a proposal with its problem, and drops an unknown one', async () => {
    const { investigationId } = await planned(null, null);
    expect((await detail(investigationId)).plan).toMatchObject({
      proposal: null,
      problem: 'No model gave a plan: claude-opus-5-5: Refused',
    });

    const unknown = message(randomUUID(), PROPOSAL, OK);
    expect(await storeEvent(db.sql, 'plan-recorded', JSON.stringify(unknown))).toBe(
      'unknown_investigation',
    );
  });

  it("queues an approved restart's kind and target for the executor, never its written steps", async () => {
    const { tenantId, investigationId, planId } = await planned();
    const nudged = nudges;
    const approved = await decide(planId, 'approve', 'operator');
    expect(approved.statusCode).toBe(200);

    const [row, ...more] = await sent(planId);
    expect(more).toEqual([]);
    expect(row).toMatchObject({
      queue: 'actions',
      message_group_id: sha256(`${tenantId}\nworker`),
    });
    expect(parseMessage('action-approved.v2', row?.body)).toEqual({
      schema_version: 2,
      action_id: planId,
      investigation_id: investigationId,
      tenant_id: tenantId,
      app: 'freight-audit',
      kind: 'restart_service',
      target: 'worker',
      parameters: {},
      approved_by: 'operator',
      approved_at: approved.json<{ decided_at: string }>().decided_at,
    });
    expect(JSON.stringify(row?.body)).not.toContain('exited with');
    expect(nudges).toBe(nudged + 1);
  });

  it('queues nothing for a rejected restart or an approved code fix', async () => {
    const rejected = await planned();
    expect((await decide(rejected.planId, 'reject')).statusCode).toBe(200);
    const fix = await planned({
      ...PROPOSAL,
      kind: 'code_fix',
      files: ['apps/worker/src/queue.ts'],
    });
    expect((await decide(fix.planId, 'approve')).statusCode).toBe(200);
    expect([...(await sent(rejected.planId)), ...(await sent(fix.planId))]).toEqual([]);
  });

  it('records one decision per plan, and one ActionApproved; a plan without a proposal takes none', async () => {
    const before = await queued();
    const { investigationId, planId } = await planned();

    const answers = await Promise.all(
      Array.from({ length: 10 }, (_, i) => decide(planId, 'approve', `approver-${String(i)}`)),
    );

    const codes = answers.map((answer) => answer.statusCode).sort();
    expect(codes).toEqual([200, ...Array.from({ length: 9 }, () => 409)]);
    const approved = answers
      .find((answer) => answer.statusCode === 200)
      ?.json<{ id: string; status: string }>();
    expect(approved).toMatchObject({ id: planId, status: 'approved' });
    expect((await decide(planId, 'reject')).statusCode).toBe(409);
    expect((await detail(investigationId)).plan).toMatchObject({ status: 'approved' });
    // Ten concurrent approvals, one decision: one message for the executor.
    expect(await queued()).toBe((before ?? 0) + 1);

    const empty = await planned(null, null);
    const refused = await decide(empty.planId, 'approve');
    expect(refused.statusCode).toBe(409);
    expect(refused.json<{ detail: string }>().detail).toMatch(/no proposal/);
    expect((await decide(randomUUID(), 'reject')).statusCode).toBe(404);
  });
});
