import { randomUUID } from 'node:crypto';

import type { FastifyInstance } from 'fastify';

import { buildApp, createUpdates } from './app.ts';
import { connectDb } from './db.ts';
import { createDecidePlan } from './decisions.ts';
import { storeEvent } from './events.ts';
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
  files: [],
  rationale: 'The worker exited with 137 while its dependencies stay healthy [tc_01J9ZK3Q7M].',
};

describe('plans against Postgres', () => {
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
      decidePlan: createDecidePlan(db.sql),
      findTenant: unused,
      intake: unused,
      listInvestigations: unused,
      getInvestigation: createGetInvestigation(db.sql, { liveTenants: [], stuckAfterMinutes: 10 }),
    });
  }, 60_000);
  afterAll(async () => {
    await app.close();
    await db.sql.end();
    await scratch.drop();
  });

  /** A live triage's alert group with its report, then its plan as the agent sent it. */
  const planned = async (proposal: typeof PROPOSAL | null = PROPOSAL) => {
    const { tenantId } = await newTenant(db.sql);
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
    const plan = message(investigationId, proposal);
    expect(await storeEvent(db.sql, 'plan-recorded', JSON.stringify(plan))).toBe('stored');
    return { investigationId, planId: plan.plan_id, plan };
  };
  const message = (investigationId: string, proposal: typeof PROPOSAL | null) => ({
    schema_version: 1,
    plan_id: randomUUID(),
    investigation_id: investigationId,
    plan: proposal,
    problem: proposal === null ? 'No model gave a plan: claude-opus-5-5: Refused' : null,
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

  it('stores a plan once and serves it, its models after the report', async () => {
    const { investigationId, planId, plan } = await planned();
    const changed = { ...plan, plan: { ...PROPOSAL, target: 'api' } };
    expect(await storeEvent(db.sql, 'plan-recorded', JSON.stringify(changed))).toBe('stored');

    const investigation = await detail(investigationId);

    expect(investigation.plan).toEqual({
      id: planId,
      proposal: PROPOSAL,
      problem: null,
      status: 'pending',
      recorded_at: '2026-10-26T10:03:10.000Z',
      decided_by: null,
      decided_at: null,
      decision_reason: null,
    });
    expect(investigation.models.map((use) => use.part)).toEqual(['triage', 'plan']);
  });

  it('serves a plan without a proposal with its problem, and drops an unknown one', async () => {
    const { investigationId } = await planned(null);
    expect((await detail(investigationId)).plan).toMatchObject({
      proposal: null,
      problem: 'No model gave a plan: claude-opus-5-5: Refused',
    });

    const unknown = message(randomUUID(), PROPOSAL);
    expect(await storeEvent(db.sql, 'plan-recorded', JSON.stringify(unknown))).toBe(
      'unknown_investigation',
    );
  });

  it('records one decision per plan and queues nothing; a plan without a proposal takes none', async () => {
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
    // Nothing runs from a plan in phase 2: no message is queued for the executor.
    expect(await queued()).toBe(before);

    const empty = await planned(null);
    const refused = await decide(empty.planId, 'approve');
    expect(refused.statusCode).toBe(409);
    expect(refused.json<{ detail: string }>().detail).toMatch(/no proposal/);
    expect((await decide(randomUUID(), 'reject')).statusCode).toBe(404);
  });
});
