import { randomBytes, randomUUID } from 'node:crypto';

import {
  CreateQueueCommand,
  DeleteMessageCommand,
  DeleteQueueCommand,
  type Message,
  PurgeQueueCommand,
  ReceiveMessageCommand,
  SQSClient,
} from '@aws-sdk/client-sqs';
import { parseMessage } from '@triage/ts-clients/messages';
import Fastify from 'fastify';

import { connectDb } from './db.ts';
import { createIntake, sha256 } from './intake.ts';
import { relayOnce, sqsSender } from './outbox.ts';
import { type GetState, type State, sweepOnce } from './sweeper.ts';
import { env, scratchDb, webhook } from './testing.ts';

const STUCK_AFTER_MINUTES = 10;
const { log } = Fastify({ logger: false });
const investigationOf = (message: Message) =>
  parseMessage('investigation-requested.v2', JSON.parse(message.Body ?? '')).investigation_id;

describe('recovery sweeper against Postgres and ElasticMQ', () => {
  let scratch: Awaited<ReturnType<typeof scratchDb>>;
  let db: ReturnType<typeof connectDb>;
  const sqs = new SQSClient({ region: env.AWS_REGION, endpoint: env.SQS_ENDPOINT_URL });
  const queueName = `sweep-${randomUUID().slice(0, 8)}.fifo`;
  let queueUrl = '';

  beforeAll(async () => {
    scratch = await scratchDb();
    db = connectDb(scratch.url);
    const attributes = { FifoQueue: 'true' };
    queueUrl =
      (await sqs.send(new CreateQueueCommand({ QueueName: queueName, Attributes: attributes })))
        .QueueUrl ?? '';
  }, 60_000);
  afterAll(async () => {
    await sqs.send(new DeleteQueueCommand({ QueueUrl: queueUrl }));
    sqs.destroy();
    await db.sql.end();
    await scratch.drop();
  });

  /** Receives and deletes every message, as an agent would. */
  const drain = async () => {
    const received: Message[] = [];
    for (;;) {
      const { Messages = [] } = await sqs.send(
        new ReceiveMessageCommand({
          QueueUrl: queueUrl,
          MaxNumberOfMessages: 10,
          WaitTimeSeconds: 1,
        }),
      );
      if (Messages.length === 0) return received;
      for (const { ReceiptHandle } of Messages) {
        await sqs.send(new DeleteMessageCommand({ QueueUrl: queueUrl, ReceiptHandle }));
      }
      received.push(...Messages);
    }
  };
  const clock = async () => {
    const [row] = await db.sql<{ now: Date }[]>`SELECT clock_timestamp() AS now`;
    if (row === undefined) throw new Error('no clock');
    return row.now;
  };

  it('re-queues each lost, resumable or stuck investigation once, and a second sweep nothing', async () => {
    const [tenant] = await db.sql<{ id: string }[]>`
      INSERT INTO gateway.tenants (name, webhook_token_sha256)
      VALUES (${randomUUID()}, ${sha256(randomBytes(32).toString('base64url'))}) RETURNING id`;
    if (tenant === undefined) throw new Error('no tenant');
    const intake = createIntake(db.sql, () => undefined, 20);
    const names = ['lost', 'resumable', 'parked', 'budget', 'stuck', 'busy', 'done', 'failed'];
    for (const name of names) {
      await intake(tenant.id, webhook(`{}:{alertname="${name}"}`, [{ alert: name }]));
    }
    // Resolved alerts only: the group has no investigation, and no sweep touches it.
    const calm = webhook('{}:{alertname="calm"}', [{ alert: 'calm', status: 'resolved' }]);
    await intake(tenant.id, calm);

    // The relay sends the 8 investigations, then the queue loses them.
    const send = sqsSender(sqs, queueName);
    expect(await relayOnce(db.sql, send)).toBe(8);
    await sqs.send(new PurgeQueueCommand({ QueueUrl: queueUrl }));
    await db.sql`UPDATE gateway.alert_groups SET created_at = created_at - interval '30 minutes'`;

    const groups = await db.sql<{ name: string; investigation_id: string; window_start: Date }[]>`
      SELECT substring(group_key from 'alertname="(.*)"') AS name, investigation_id, window_start
      FROM gateway.alert_groups WHERE investigation_id IS NOT NULL`;
    const id = (name: string) => groups.find((g) => g.name === name)?.investigation_id ?? '';
    const now = await clock();
    const ago = (minutes: number) => new Date(now.getTime() - minutes * 60_000).toISOString();
    const state = (name: string, change: Partial<State>): State => ({
      investigation_id: id(name),
      status: 'queued',
      cancel_requested: false,
      steps_taken: 0,
      tool_calls: 0,
      tokens_used: 0,
      updated_at: ago(30),
      waiting_for: null,
      waiting_since: null,
      resumable: false,
      ...change,
    });
    const parked = { waiting_for: 'tenant_cap', waiting_since: ago(1) } as const;
    const states = new Map<string, State | undefined>([
      [id('lost'), undefined], // The agent never received it.
      [id('resumable'), state('resumable', { ...parked, resumable: true })],
      [id('parked'), state('parked', parked)],
      [id('budget'), state('budget', { waiting_for: 'budget', waiting_since: ago(30) })],
      [id('stuck'), state('stuck', { status: 'running', steps_taken: 2, updated_at: ago(30) })],
      [id('busy'), state('busy', { status: 'running', steps_taken: 2, updated_at: ago(1) })],
      [id('done'), state('done', { status: 'resolved', steps_taken: 3 })],
      [id('failed'), state('failed', { status: 'failed' })], // Its message is in the DLQ.
    ]);
    const asked: string[] = [];
    const getState: GetState = (investigationId) => {
      asked.push(investigationId);
      return Promise.resolve(states.get(investigationId));
    };
    const sweep = () => sweepOnce(db.sql, getState, STUCK_AFTER_MINUTES, log);
    // An approved action's message names the investigation too; it must not pass for a re-queue.
    await db.sql`
      INSERT INTO gateway.outbox (queue, message_group_id, deduplication_id, body)
      VALUES ('actions', 'g', 'd', ${db.sql.json({ investigation_id: id('lost') })})`;
    const rows = async () =>
      (
        await db.sql<{ name: string; status: string; requeue_count: number }[]>`
          SELECT substring(group_key from 'alertname="(.*)"') AS name, status, requeue_count
          FROM gateway.alert_groups ORDER BY 1`
      ).map(({ name, status, requeue_count }) => [name, status, requeue_count]);

    expect(await sweep()).toBe(3);
    expect(asked).toHaveLength(8);
    await db.sql`DELETE FROM gateway.outbox WHERE queue = 'actions'`;
    expect(await rows()).toEqual([
      ['budget', 'queued', 0],
      ['busy', 'queued', 0],
      ['calm', 'queued_not_investigated', 0],
      ['done', 'resolved', 0], // Finished: it leaves the sweep.
      ['failed', 'queued', 0],
      ['lost', 'queued', 1],
      ['parked', 'queued', 0],
      ['resumable', 'queued', 1],
      ['stuck', 'queued', 1],
    ]);
    const outbox = await db.sql<{ message_group_id: string; deduplication_id: string }[]>`
      SELECT message_group_id, deduplication_id FROM gateway.outbox`;
    expect(outbox).toHaveLength(3);
    for (const name of ['lost', 'resumable', 'stuck']) {
      const key = `{}:{alertname="${name}"}`;
      const windowStart = groups.find((g) => g.name === name)?.window_start.toISOString() ?? '';
      const row = outbox.find((r) => r.message_group_id === sha256(`${tenant.id}\n${key}`));
      // A re-queue's own deduplication id: SQS cannot drop it as a repeat of the first send.
      expect(row?.deduplication_id).not.toBe(sha256(`${tenant.id}\n${key}\n${windowStart}`));
    }

    // A second sweep re-queues nothing: those messages are on their way.
    expect(await sweep()).toBe(0);
    expect(await relayOnce(db.sql, send)).toBe(3);
    const delivered = (await drain()).map(investigationOf).sort();
    expect(delivered).toEqual([id('lost'), id('resumable'), id('stuck')].sort());
    expect(await sweep()).toBe(0);

    // Parked again after the re-queue (another run took the slot), it goes back once it can run.
    const parkedAgain = (await clock()).toISOString();
    states.set(
      id('resumable'),
      state('resumable', { ...parked, waiting_since: parkedAgain, resumable: true }),
    );
    expect(await sweep()).toBe(1);
    expect(await relayOnce(db.sql, send)).toBe(1);
    expect((await drain()).map(investigationOf)).toEqual([id('resumable')]);
  }, 30_000);
});
