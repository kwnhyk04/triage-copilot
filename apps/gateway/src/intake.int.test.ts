import { execFile } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

import {
  CreateQueueCommand,
  DeleteQueueCommand,
  type Message,
  ReceiveMessageCommand,
  SQSClient,
} from '@aws-sdk/client-sqs';
import { parseMessage } from '@triage/ts-clients/messages';
import { loadConfig, z } from '@triage/ts-config';
import { drizzle } from 'drizzle-orm/postgres-js';
import { migrate } from 'drizzle-orm/postgres-js/migrator';
import postgres from 'postgres';

import { buildApp, createUpdates } from './app.ts';
import { connectDb } from './db.ts';
import { createFindTenant, createIntake, sha256, webhookSchema } from './intake.ts';
import {
  MAX_ATTEMPTS,
  type OutboxMessage,
  relayOnce,
  type Send,
  sqsSender,
  startRelay,
} from './outbox.ts';

const env = loadConfig(
  z.object({
    GATEWAY_DATABASE_URL: z.url(),
    POSTGRES_PASSWORD: z.string().min(1),
    SQS_ENDPOINT_URL: z.url(),
    AWS_REGION: z.string().default('us-east-1'),
  }),
);
const GATEWAY_DIR = fileURLToPath(new URL('..', import.meta.url));
const quiet = { onnotice: () => undefined };

/** An empty database set up like infra/postgres-init.sql, with the gateway migrations applied. */
async function scratchDb() {
  const name = `triage_test_${randomUUID().replaceAll('-', '').slice(0, 12)}`;
  const at = (database: string, superuser = false) => {
    const url = new URL(env.GATEWAY_DATABASE_URL);
    url.pathname = `/${database}`;
    if (superuser) {
      url.username = 'postgres';
      url.password = env.POSTGRES_PASSWORD;
    }
    return url.href;
  };
  const admin = postgres(at('postgres', true), { max: 1, ...quiet });
  await admin.unsafe(`CREATE DATABASE ${name}`);
  const setup = postgres(at(name, true), { max: 1, ...quiet });
  await setup.unsafe(`
    REVOKE ALL ON DATABASE ${name} FROM PUBLIC;
    GRANT CONNECT ON DATABASE ${name} TO gateway, agent;
    GRANT CREATE ON DATABASE ${name} TO gateway;
    CREATE SCHEMA gateway AUTHORIZATION gateway;
    CREATE SCHEMA agent AUTHORIZATION agent;`);
  await setup.end();
  const migrator = postgres(at(name), { max: 1, ...quiet });
  const migrationsFolder = fileURLToPath(new URL('../drizzle', import.meta.url));
  await migrate(drizzle(migrator), { migrationsFolder, migrationsSchema: 'gateway' });
  await migrator.end();
  return {
    url: at(name),
    drop: async () => {
      await admin.unsafe(`DROP DATABASE ${name} WITH (FORCE)`);
      await admin.end();
    },
  };
}

const webhook = (groupKey: string, alerts: { fingerprint: string; status?: string }[]) =>
  webhookSchema.parse({
    version: '4',
    groupKey,
    status: 'firing',
    receiver: 'triage-copilot',
    groupLabels: {},
    commonLabels: {},
    commonAnnotations: {},
    externalURL: 'http://alertmanager.example:9093',
    alerts: alerts.map(({ fingerprint, status = 'firing' }) => ({
      status,
      labels: { alertname: 'HighErrorRate', severity: 'critical' },
      annotations: {},
      startsAt: '2026-10-14T11:54:12.481516234Z',
      endsAt: '0001-01-01T00:00:00Z',
      generatorURL: 'http://prometheus.example:9090/graph',
      fingerprint,
    })),
  });

async function eventually(condition: () => Promise<boolean>, ms = 10_000) {
  const deadline = Date.now() + ms;
  while (!(await condition())) {
    if (Date.now() > deadline) throw new Error('timed out');
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

describe('alert intake against Postgres and ElasticMQ', () => {
  let scratch: Awaited<ReturnType<typeof scratchDb>>;
  let db: ReturnType<typeof connectDb>;
  const cli = (...args: string[]) =>
    promisify(execFile)(process.execPath, ['src/cli.ts', ...args], {
      cwd: GATEWAY_DIR,
      env: { ...process.env, GATEWAY_DATABASE_URL: scratch.url },
    });

  beforeAll(async () => {
    scratch = await scratchDb();
    db = connectDb(scratch.url);
  }, 60_000);
  afterAll(async () => {
    await db.sql.end();
    await scratch.drop();
  });
  beforeEach(() => db.sql`DELETE FROM gateway.outbox`);

  const newTenant = async () => {
    const token = randomBytes(32).toString('base64url');
    const [tenant] = await db.sql<{ id: string }[]>`
      INSERT INTO gateway.tenants (name, webhook_token_sha256)
      VALUES (${randomUUID()}, ${sha256(token)}) RETURNING id`;
    if (tenant === undefined) throw new Error('no tenant');
    return { tenantId: tenant.id, token };
  };

  it('dedupes by fingerprint and opens one group and investigation per 5-minute window', async () => {
    const { tenantId } = await newTenant();
    let queued = 0;
    const intake = createIntake(
      db.sql,
      () => {
        queued++;
      },
      5,
    );
    const key = '{}:{alertname="DiskFull", service="checkout"}';
    const store = (alerts: { fingerprint: string; status?: string }[]) =>
      intake(tenantId, webhook(key, alerts));

    const first = await store([{ fingerprint: 'a' }]);
    expect(first.deduped).toBe(false);
    expect(await store([{ fingerprint: 'a' }])).toEqual({ ...first, deduped: true });
    expect(await store([{ fingerprint: 'a', status: 'resolved' }])).toEqual(first);
    expect(await store([{ fingerprint: 'b' }, { fingerprint: 'b' }])).toEqual(first);
    expect(queued).toBe(1);

    // Once the window is 5 minutes old, new alerts open the next group and investigation.
    await db.sql`
      UPDATE gateway.alert_groups SET window_start = window_start - interval '5 minutes'
      WHERE id = ${first.group_id}`;
    const next = await store([{ fingerprint: 'c' }]);
    expect(next.group_id).not.toBe(first.group_id);
    expect(queued).toBe(2);

    // A new alert that arrives already resolved opens a group, but no investigation.
    await db.sql`
      UPDATE gateway.alert_groups SET window_start = window_start - interval '5 minutes'
      WHERE id = ${next.group_id}`;
    const late = await store([{ fingerprint: 'd', status: 'resolved' }]);
    expect(late.group_id).not.toBe(next.group_id);
    expect(queued).toBe(2);

    const groups = await db.sql<{ status: string; severity: string; alerts: number }[]>`
      SELECT status, severity,
             (SELECT count(*)::int FROM gateway.alerts a WHERE a.group_id = g.id) AS alerts
      FROM gateway.alert_groups g WHERE tenant_id = ${tenantId} ORDER BY window_start`;
    expect(groups.map((g) => [g.status, g.severity, g.alerts])).toEqual([
      ['queued', 'critical', 2],
      ['queued', 'critical', 1],
      ['queued_not_investigated', 'critical', 1],
    ]);
    const messages = await db.sql<
      { message_group_id: string; deduplication_id: string; body: unknown }[]
    >`SELECT message_group_id, deduplication_id, body FROM gateway.outbox ORDER BY id`;
    // One FIFO group per Alertmanager group, one deduplication id per window.
    const group = sha256(`${tenantId}\n${key}`);
    expect(messages.map((m) => m.message_group_id)).toEqual([group, group]);
    expect(new Set(messages.map((m) => m.deduplication_id)).size).toBe(2);
    for (const { body } of messages) {
      expect(parseMessage('investigation-requested.v2', body).tenant_id).toBe(tenantId);
    }
  });

  it('retries a failed send, parks it after the last attempt and sends it after a replay', async () => {
    const { tenantId } = await newTenant();
    const intake = createIntake(db.sql, () => undefined, 5);
    await intake(tenantId, webhook('{}:{alertname="Relay"}', [{ fingerprint: 'r' }]));
    const failing: Send = () => Promise.reject(new Error('SQS unreachable'));
    const state = async () => {
      const [row] = await db.sql<
        { attempts: number; parked: boolean; due: boolean; last_error: string }[]
      >`SELECT attempts, failed_at IS NOT NULL AS parked, next_attempt_at <= now() AS due,
               last_error FROM gateway.outbox`;
      return row;
    };

    expect(await relayOnce(db.sql, failing)).toBe(0);
    expect(await state()).toEqual({
      attempts: 1,
      parked: false,
      due: false,
      last_error: 'Error: SQS unreachable',
    });
    await db.sql`UPDATE gateway.outbox SET attempts = ${MAX_ATTEMPTS - 1}, next_attempt_at = now()`;
    await relayOnce(db.sql, failing);
    expect(await state()).toMatchObject({ attempts: MAX_ATTEMPTS, parked: true });

    const sent: OutboxMessage[] = [];
    const working: Send = (message) => {
      sent.push(message);
      return Promise.resolve();
    };
    await relayOnce(db.sql, working);
    expect(sent).toEqual([]); // Parked messages wait for a replay.
    const { stdout } = await cli('outbox-replay');
    expect(JSON.parse(stdout)).toMatchObject({ count: 1 });
    expect(await relayOnce(db.sql, working)).toBe(1);
    const bodies = sent.map((m) => parseMessage('investigation-requested.v2', JSON.parse(m.body)));
    expect(bodies.map((body) => body.tenant_id)).toEqual([tenantId]);
    expect(await state()).toBeUndefined();
  });

  it('issues a webhook token per tenant and replaces it when issued again', async () => {
    const findTenant = createFindTenant(db.sql);
    const first = (await cli('tenant-token', 'acme')).stdout.trim();
    const tenantId = await findTenant(sha256(first));
    expect(tenantId).toBeDefined();
    const second = (await cli('tenant-token', 'acme')).stdout.trim();
    expect(await findTenant(sha256(second))).toBe(tenantId);
    expect(await findTenant(sha256(first))).toBeUndefined();
  });

  it('acks a 500-alert storm with p99 under 200 ms and queues 5 or fewer investigations', async () => {
    const { tenantId, token } = await newTenant();
    const sqs = new SQSClient({ region: env.AWS_REGION, endpoint: env.SQS_ENDPOINT_URL });
    const queueName = `storm-${randomUUID().slice(0, 8)}.fifo`;
    const { QueueUrl } = await sqs.send(
      new CreateQueueCommand({ QueueName: queueName, Attributes: { FifoQueue: 'true' } }),
    );
    let nudge: () => void = () => undefined;
    const app = await buildApp({
      config: {
        LOG_LEVEL: 'error',
        APPROVER_API_KEY: 'unused-by-this-test-0000000000000',
        RATE_LIMIT_PER_MINUTE: 600,
      },
      ping: db.ping,
      updates: createUpdates(),
      decide: () => Promise.resolve('not_found'),
      findTenant: createFindTenant(db.sql),
      intake: createIntake(
        db.sql,
        () => {
          nudge();
        },
        5,
      ),
    });
    const relay = startRelay(db.sql, sqsSender(sqs, queueName), app.log, 100);
    nudge = relay.nudge;
    const address = await app.listen({ host: '127.0.0.1', port: 0 });
    try {
      // A cascading failure across eight Alertmanager groups: 400 alerts, then 100 re-sends.
      const services = ['checkout', 'payments', 'inventory', 'cart', 'search', 'auth', 'ads', 'db'];
      const keys = services.map(
        (service) => `{}:{alertname="HighErrorRate", service="${service}"}`,
      );
      const bodies = Array.from({ length: 500 }, (_, i) => {
        const n = i % 400;
        return JSON.stringify(webhook(keys[n % 8] ?? '', [{ fingerprint: `storm-${String(n)}` }]));
      });
      const latencies: number[] = [];
      const statuses = new Set<number>();
      let next = 0;
      // Ten clients at once, so the 500 requests arrive within seconds instead of 2 minutes.
      await Promise.all(
        Array.from({ length: 10 }, async () => {
          for (let body = bodies[next++]; body !== undefined; body = bodies[next++]) {
            const started = performance.now();
            const response = await fetch(`${address}/v1/alerts`, {
              method: 'POST',
              headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
              body,
            });
            await response.arrayBuffer();
            latencies.push(performance.now() - started);
            statuses.add(response.status);
          }
        }),
      );
      latencies.sort((a, b) => a - b);
      const p99 = latencies[Math.ceil(latencies.length * 0.99) - 1] ?? Infinity;
      // Jest hides console output from this suite; stderr shows the measurement.
      process.stderr.write(`storm: 500 requests, p99 ${p99.toFixed(1)} ms\n`);
      expect([...statuses]).toEqual([202]);
      expect(p99).toBeLessThan(200);

      const [stored] = await db.sql<{ alerts: number; groups: number; investigated: number }[]>`
        SELECT count(*)::int AS groups, count(investigation_id)::int AS investigated,
               (SELECT count(*)::int FROM gateway.alerts WHERE tenant_id = ${tenantId}) AS alerts
        FROM gateway.alert_groups WHERE tenant_id = ${tenantId}`;
      // No alert dropped; 8 groups, but the cap starts only 5 investigations.
      expect(stored).toEqual({ alerts: 400, groups: 8, investigated: 5 });

      // The relay delivers each InvestigationRequested in its Alertmanager group's FIFO group.
      await eventually(async () => (await db.sql`SELECT FROM gateway.outbox`).length === 0);
      const received: Message[] = [];
      await eventually(async () => {
        const { Messages = [] } = await sqs.send(
          new ReceiveMessageCommand({
            QueueUrl,
            MaxNumberOfMessages: 10,
            WaitTimeSeconds: 1,
            MessageSystemAttributeNames: ['MessageGroupId'],
          }),
        );
        received.push(...Messages);
        return received.length >= 5;
      });
      const { Messages: more = [] } = await sqs.send(
        new ReceiveMessageCommand({ QueueUrl, MaxNumberOfMessages: 10, WaitTimeSeconds: 1 }),
      );
      expect(more).toEqual([]);
      const groupIds = new Set(received.map((m) => m.Attributes?.MessageGroupId ?? ''));
      const keyHashes = keys.map((key) => sha256(`${tenantId}\n${key}`));
      expect(groupIds.size).toBe(5);
      expect([...groupIds].every((id) => keyHashes.includes(id))).toBe(true);
      for (const { Body = '' } of received) {
        const message = parseMessage('investigation-requested.v2', JSON.parse(Body));
        expect(message.tenant_id).toBe(tenantId);
      }
    } finally {
      await relay.stop();
      await app.close();
      await sqs.send(new DeleteQueueCommand({ QueueUrl }));
      sqs.destroy();
    }
  }, 60_000);
});
