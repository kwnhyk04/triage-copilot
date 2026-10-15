import { randomUUID } from 'node:crypto';

import type { FastifyInstance } from 'fastify';

import { connectDb } from './db.ts';
import { createClaim, createReport, stuckExecutions } from './executions.ts';
import { buildInternalApp } from './internal.ts';
import { newTenant, scratchDb } from './testing.ts';

const TOKEN = 'executor-token-for-these-tests-000000';

describe('executions against Postgres', () => {
  let scratch: Awaited<ReturnType<typeof scratchDb>>;
  let db: ReturnType<typeof connectDb>;
  let internal: FastifyInstance;

  beforeAll(async () => {
    scratch = await scratchDb();
    db = connectDb(scratch.url);
    internal = buildInternalApp({
      config: { LOG_LEVEL: 'error', EXECUTOR_SERVICE_TOKEN: TOKEN },
      claim: createClaim(db.sql),
      report: createReport(db.sql),
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

  it('takes one result per execution and reports the ones stuck in claimed', async () => {
    const done = await action('approved');
    const stuck = await action('approved');
    await claim(done, true);
    await claim(stuck);
    const plan = 'dry run: rollback_deploy checkout {"to_version":"v1.7.3"}';
    const finished = await report(done, { state: 'succeeded', detail: plan });
    expect(finished.statusCode).toBe(200);
    expect(finished.json()).toMatchObject({ state: 'succeeded', detail: plan, dry_run: true });
    expect(finished.json<{ finished_at: string | null }>().finished_at).not.toBeNull();
    expect((await report(done, { state: 'failed' })).statusCode).toBe(409);
    expect((await report(randomUUID(), { state: 'failed' })).statusCode).toBe(404);

    // Never answered, it stays claimed: a claim replays it unchanged, and once it is old the
    // sweeper's query lists it for a human.
    await db.sql`
      UPDATE gateway.executions SET claimed_at = claimed_at - interval '30 minutes'
      WHERE action_id = ${stuck}`;
    expect(await stuckExecutions(db.sql, 10)).toEqual([stuck]);
    const replay = await claim(stuck);
    expect(replay.statusCode).toBe(200);
    expect(replay.json()).toMatchObject({ state: 'claimed' });
  });
});
