import { randomUUID } from 'node:crypto';

import { parseMessage } from '@triage/ts-clients/messages';
import type { FastifyInstance } from 'fastify';

import { buildApp, createUpdates } from './app.ts';
import { connectDb } from './db.ts';
import { createDecide } from './decisions.ts';
import { sha256 } from './intake.ts';
import { APP_CONFIG, newTenant, scratchDb } from './testing.ts';

describe('decision store against Postgres', () => {
  let scratch: Awaited<ReturnType<typeof scratchDb>>;
  let db: ReturnType<typeof connectDb>;
  let app: FastifyInstance;
  let nudges = 0;
  const unused = () => Promise.reject(new Error('unused by this test'));

  beforeAll(async () => {
    scratch = await scratchDb();
    db = connectDb(scratch.url);
    app = await buildApp({
      config: APP_CONFIG,
      ping: db.ping,
      updates: createUpdates(),
      decide: createDecide(db.sql, () => {
        nudges++;
      }),
      findTenant: unused,
      intake: unused,
      listInvestigations: unused,
    });
  }, 60_000);
  afterAll(async () => {
    await app.close();
    await db.sql.end();
    await scratch.drop();
  });

  /** A pending rollback the agent proposed for a new investigation of a new tenant. */
  const propose = async () => {
    const { tenantId } = await newTenant(db.sql);
    const investigationId = randomUUID();
    await db.sql`
      INSERT INTO gateway.alert_groups
        (tenant_id, group_key, window_start, severity, status, investigation_id)
      VALUES (${tenantId}, ${randomUUID()}, now(), 'critical', 'awaiting_approval',
              ${investigationId})`;
    const actionId = randomUUID();
    await db.sql`
      INSERT INTO gateway.proposed_actions
        (id, tenant_id, investigation_id, kind, target, parameters, rationale, proposed_at)
      VALUES (${actionId}, ${tenantId}, ${investigationId}, 'rollback_deploy', 'checkout',
              ${db.sql.json({ to_version: 'v1.7.3' })}, 'Errors began with v1.8.0.', now())`;
    return { tenantId, investigationId, actionId };
  };
  const decide = (actionId: string, route: 'approve' | 'reject', approver = 'oncall@example.com') =>
    app.inject({
      method: 'POST',
      url: `/v1/actions/${actionId}/${route}`,
      headers: { 'x-approver-key': APP_CONFIG.APPROVER_API_KEY },
      payload: { approver },
    });
  const decisions = async (actionId: string) =>
    (
      await db.sql<{ decision: string; approver: string }[]>`
        SELECT decision, approver FROM gateway.approvals WHERE action_id = ${actionId}`
    ).map(({ decision, approver }) => [decision, approver]);
  const queued = async (actionId: string) => [
    ...(await db.sql<{ queue: string; message_group_id: string; body: unknown }[]>`
      SELECT queue, message_group_id, body FROM gateway.outbox WHERE deduplication_id = ${actionId}`),
  ];

  it('approves once: a second approval gets 409, and only the first queues ActionApproved', async () => {
    const { tenantId, investigationId, actionId } = await propose();
    const nudged = nudges;
    const first = await decide(actionId, 'approve');
    expect(first.statusCode).toBe(200);
    expect(first.json()).toMatchObject({
      id: actionId,
      status: 'approved',
      decided_by: 'oncall@example.com',
      decision_reason: null,
    });
    expect((await decide(actionId, 'approve', 'someone-else@example.com')).statusCode).toBe(409);
    expect(await decisions(actionId)).toEqual([['approved', 'oncall@example.com']]);

    const [message, ...more] = await queued(actionId);
    expect(more).toEqual([]);
    // One FIFO group per tenant and target, so one service's actions run in order.
    expect(message).toMatchObject({
      queue: 'actions',
      message_group_id: sha256(`${tenantId}\ncheckout`),
    });
    expect(parseMessage('action-approved.v1', message?.body)).toEqual({
      schema_version: 1,
      action_id: actionId,
      investigation_id: investigationId,
      kind: 'rollback_deploy',
      target: 'checkout',
      parameters: { to_version: 'v1.7.3' },
      approved_by: 'oncall@example.com',
      approved_at: first.json<{ decided_at: string }>().decided_at,
    });
    expect(nudges).toBe(nudged + 1);
  });

  it('answers 409 to an approval after a rejection, and queues nothing', async () => {
    const { actionId } = await propose();
    expect((await decide(actionId, 'reject')).statusCode).toBe(200);
    expect((await decide(actionId, 'approve')).statusCode).toBe(409);
    expect((await decide(actionId, 'reject')).statusCode).toBe(409);
    expect(await decisions(actionId)).toEqual([['rejected', 'oncall@example.com']]);
    expect(await queued(actionId)).toEqual([]);
  });

  it('records exactly one of 20 concurrent decisions', async () => {
    const { actionId } = await propose();
    const responses = await Promise.all(
      Array.from({ length: 20 }, (_, i) =>
        decide(actionId, i % 2 === 0 ? 'approve' : 'reject', `approver-${String(i)}@example.com`),
      ),
    );
    const winners = responses.filter((response) => response.statusCode === 200);
    expect(winners).toHaveLength(1);
    expect(responses.filter((response) => response.statusCode === 409)).toHaveLength(19);
    const winner = winners[0]?.json<{ status: string; decided_by: string }>();
    expect(await decisions(actionId)).toEqual([[winner?.status, winner?.decided_by]]);
    expect(await queued(actionId)).toHaveLength(winner?.status === 'approved' ? 1 : 0);
  });

  it('commits the decision and its ActionApproved together', async () => {
    const { actionId } = await propose();
    // A constraint the outbox insert breaks, standing in for any failure inside the transaction.
    // NOT VALID: earlier tests' rows stay; only new rows are checked.
    await db.sql`
      ALTER TABLE gateway.outbox ADD CONSTRAINT no_actions CHECK (queue <> 'actions') NOT VALID`;
    try {
      const failed = await decide(actionId, 'approve');
      expect(failed.statusCode).toBe(503);
      expect(failed.headers['retry-after']).toBe('5');
      expect(await decisions(actionId)).toEqual([]);
    } finally {
      await db.sql`ALTER TABLE gateway.outbox DROP CONSTRAINT no_actions`;
    }
    // Nothing was recorded, so the approval can still be made, and queues its message.
    expect((await decide(actionId, 'approve')).statusCode).toBe(200);
    expect(await queued(actionId)).toHaveLength(1);
  });

  it('answers 404 for an action that was never proposed', async () => {
    expect((await decide(randomUUID(), 'approve')).statusCode).toBe(404);
  });
});
