import { randomUUID } from 'node:crypto';

import type { FastifyInstance } from 'fastify';

import { connectDb } from './db.ts';
import { createClaim, createReport, stuckExecutions } from './executions.ts';
import { buildInternalApp } from './internal.ts';
import { createAlertContext } from './investigations.ts';
import { newTenant, scratchDb } from './testing.ts';

const TOKEN = 'executor-token-for-these-tests-000000';
const AGENT_SERVICE_TOKEN = 'agent-token-for-these-tests-0000000000';

describe('executions against Postgres', () => {
  let scratch: Awaited<ReturnType<typeof scratchDb>>;
  let db: ReturnType<typeof connectDb>;
  let internal: FastifyInstance;
  // The agent's actions switch, as the claim route asks for it.
  let actions = true;

  beforeAll(async () => {
    scratch = await scratchDb();
    db = connectDb(scratch.url);
    internal = buildInternalApp({
      config: { LOG_LEVEL: 'error', EXECUTOR_SERVICE_TOKEN: TOKEN, AGENT_SERVICE_TOKEN },
      claim: createClaim(db.sql),
      report: createReport(db.sql),
      alertContext: createAlertContext(db.sql),
      actionsOn: () => Promise.resolve(actions),
    });
  }, 60_000);
  afterAll(async () => {
    await internal.close();
    await db.sql.end();
    await scratch.drop();
  });

  /** A proposed rollback, decided as given, or still pending. */
  const action = async (decision?: 'approved' | 'rejected') => {
    const { tenantId } = await newTenant(db.sql);
    const investigationId = randomUUID();
    await db.sql`
      INSERT INTO gateway.alert_groups
        (tenant_id, group_key, window_start, severity, status, investigation_id)
      VALUES (${tenantId}, ${randomUUID()}, now(), 'critical', 'awaiting_approval',
              ${investigationId})`;
    const id = randomUUID();
    await db.sql`
      INSERT INTO gateway.proposed_actions
        (id, tenant_id, investigation_id, kind, target, parameters, rationale, proposed_at)
      VALUES (${id}, ${tenantId}, ${investigationId}, 'rollback_deploy', 'checkout',
              ${db.sql.json({ to_version: 'v1.7.3' })}, 'Errors began with v1.8.0.', now())`;
    if (decision !== undefined) {
      await db.sql`
        INSERT INTO gateway.approvals (action_id, approver, decision)
        VALUES (${id}, 'oncall@example.com', ${decision})`;
    }
    return id;
  };
  /** A live triage's restart plan (or another kind), decided as given, or still pending. */
  const plan = async (decision?: 'approved' | 'rejected', kind = 'restart_service') => {
    const { tenantId } = await newTenant(db.sql);
    const investigationId = randomUUID();
    await db.sql`
      INSERT INTO gateway.alert_groups
        (tenant_id, group_key, window_start, severity, status, investigation_id)
      VALUES (${tenantId}, ${randomUUID()}, now(), 'critical', 'resolved', ${investigationId})`;
    const id = randomUUID();
    const files = kind === 'code_fix' ? ['apps/worker/src/queue.ts'] : [];
    await db.sql`
      INSERT INTO gateway.plans
        (id, tenant_id, investigation_id, kind, target, files, rationale, models, recorded_at)
      VALUES (${id}, ${tenantId}, ${investigationId}, ${kind}, 'worker', ${db.sql.json(files)},
              'Restart the worker, then check its queue [tc_01J9ZK3Q7M].', ${db.sql.json([])}, now())`;
    if (decision !== undefined) {
      await db.sql`
        INSERT INTO gateway.plan_decisions (plan_id, approver, decision)
        VALUES (${id}, 'operator', ${decision})`;
    }
    return id;
  };
  const post = (url: string, payload: object) =>
    internal.inject({
      method: 'POST',
      url,
      headers: { authorization: `Bearer ${TOKEN}` },
      payload,
    });
  const claim = (id: string, dryRun = false) =>
    post(`/internal/actions/${id}/execution`, { dry_run: dryRun });
  const report = (id: string, result: object) =>
    post(`/internal/actions/${id}/execution/result`, result);

  it('claims an approved action once: a second claim returns the first and never runs it again', async () => {
    const id = await action('approved');
    const first = await claim(id);
    expect(first.statusCode).toBe(201);
    expect(first.json()).toMatchObject({
      action_id: id,
      kind: 'rollback_deploy',
      target: 'checkout',
      parameters: { to_version: 'v1.7.3' },
      approved_by: 'oncall@example.com',
      state: 'claimed',
      dry_run: false,
      detail: null,
      finished_at: null,
    });
    const again = await claim(id, true);
    expect(again.statusCode).toBe(200);
    expect(again.json()).toEqual(first.json());
  });

  it('refuses an action without an approval row, a pending one and a rejected one', async () => {
    const ids = [randomUUID(), await action(), await action('rejected')];
    for (const id of ids) expect((await claim(id)).statusCode).toBe(404);
    const rows = await db.sql`SELECT FROM gateway.executions WHERE action_id = ANY(${ids})`;
    expect(rows).toHaveLength(0);
  });

  it('lets the database refuse an execution without an approved decision, whatever the code does', async () => {
    const pending = await action();
    const rejected = await action('rejected');
    for (const id of [pending, rejected, randomUUID()]) {
      await expect(
        db.sql`INSERT INTO gateway.executions (action_id, dry_run) VALUES (${id}, false)`,
      ).rejects.toThrow(/executions_approval_fk/);
    }
    await expect(
      db.sql`
        INSERT INTO gateway.executions (action_id, decision, dry_run)
        VALUES (${rejected}, 'rejected', false)`,
    ).rejects.toThrow(/executions_approved_only/);
  });

  it('gives exactly one of 10 concurrent claims the right to run the action', async () => {
    const id = await action('approved');
    const codes = (await Promise.all(Array.from({ length: 10 }, () => claim(id)))).map(
      (response) => response.statusCode,
    );
    expect(codes.filter((code) => code === 201)).toHaveLength(1);
    expect(codes.filter((code) => code === 200)).toHaveLength(9);
  });

  it('claims an approved restart plan once, as an action of its kind and target alone', async () => {
    const id = await plan('approved');
    const first = await claim(id, true);
    expect(first.statusCode).toBe(201);
    expect(first.json()).toMatchObject({
      action_id: id,
      kind: 'restart_service',
      target: 'worker',
      parameters: {},
      approved_by: 'operator',
      state: 'claimed',
      dry_run: true,
    });
    expect((await claim(id)).statusCode).toBe(200);
    const dry = 'dry run: docker compose -p freightaudit restart worker';
    const done = await report(id, { state: 'succeeded', detail: dry });
    expect(done.json()).toMatchObject({ state: 'succeeded', detail: dry });
    expect((await report(id, { state: 'failed' })).statusCode).toBe(409);
  });

  it('claims no pending, rejected or code-fix plan, and the database refuses an unapproved one', async () => {
    const pending = await plan();
    const rejected = await plan('rejected');
    const fix = await plan('approved', 'code_fix');
    for (const id of [pending, rejected, fix]) expect((await claim(id)).statusCode).toBe(404);
    const ids = [pending, rejected, fix];
    const rows = await db.sql`SELECT FROM gateway.plan_executions WHERE plan_id = ANY(${ids})`;
    expect(rows).toHaveLength(0);
    for (const id of [pending, rejected]) {
      await expect(
        db.sql`INSERT INTO gateway.plan_executions (plan_id, dry_run) VALUES (${id}, false)`,
      ).rejects.toThrow(/plan_executions_decision_fk/);
    }
  });

  it('claims nothing while the actions switch is off', async () => {
    const ids = [await plan('approved'), await action('approved')];
    actions = false;
    try {
      for (const id of ids) expect((await claim(id)).statusCode).toBe(409);
    } finally {
      actions = true;
    }
    expect(
      await db.sql`SELECT FROM gateway.plan_executions WHERE plan_id = ANY(${ids})`,
    ).toHaveLength(0);
    expect(await db.sql`SELECT FROM gateway.executions WHERE action_id = ANY(${ids})`).toHaveLength(
      0,
    );
  });

  it('takes one result per execution and reports the ones stuck in claimed', async () => {
    const done = await action('approved');
    const stuck = await action('approved');
    const stuckPlan = await plan('approved');
    await claim(done, true);
    await claim(stuck);
    await claim(stuckPlan);
    await db.sql`
      UPDATE gateway.plan_executions SET claimed_at = claimed_at - interval '20 minutes'
      WHERE plan_id = ${stuckPlan}`;
    const dry = 'dry run: rollback_deploy checkout {"to_version":"v1.7.3"}';
    const finished = await report(done, { state: 'succeeded', detail: dry });
    expect(finished.statusCode).toBe(200);
    expect(finished.json()).toMatchObject({ state: 'succeeded', detail: dry, dry_run: true });
    expect(finished.json<{ finished_at: string | null }>().finished_at).not.toBeNull();
    expect((await report(done, { state: 'failed' })).statusCode).toBe(409);
    expect((await report(randomUUID(), { state: 'failed' })).statusCode).toBe(404);

    // Never answered, it stays claimed: a claim replays it unchanged, and once it is old the
    // sweeper's query lists it for a human.
    await db.sql`
      UPDATE gateway.executions SET claimed_at = claimed_at - interval '30 minutes'
      WHERE action_id = ${stuck}`;
    expect(await stuckExecutions(db.sql, 10)).toEqual([stuck, stuckPlan]);
    const replay = await claim(stuck);
    expect(replay.statusCode).toBe(200);
    expect(replay.json()).toMatchObject({ state: 'claimed' });
  });
});
