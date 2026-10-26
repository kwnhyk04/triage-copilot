import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
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
import fc from 'fast-check';

import { buildApp, createUpdates } from './app.ts';
import { connectDb } from './db.ts';
import { createFindTenant, createIntake, fingerprint, sha256 } from './intake.ts';
import {
  MAX_ATTEMPTS,
  type OutboxMessage,
  relayOnce,
  type Send,
  sqsSender,
  startRelay,
} from './outbox.ts';
import { APP_CONFIG, env, eventually, newTenant, scratchDb, webhook } from './testing.ts';

const GATEWAY_DIR = fileURLToPath(new URL('..', import.meta.url));

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
  const unused = () => Promise.reject(new Error('unused by this test'));

  it('dedupes by fingerprint and opens one group and investigation per 5-minute window', async () => {
    const { tenantId } = await newTenant(db.sql);
    let queued = 0;
    const intake = createIntake(
      db.sql,
      () => {
        queued++;
      },
      5,
    );
    const key = '{}:{alertname="DiskFull", service="checkout"}';
    const store = (alerts: { alert: string; status?: string }[]) =>
      intake(tenantId, webhook(key, alerts));

    const first = await store([{ alert: 'a' }]);
    expect(first.deduped).toBe(false);
    expect(await store([{ alert: 'a' }])).toEqual({ ...first, deduped: true });
    expect(await store([{ alert: 'a', status: 'resolved' }])).toEqual(first);
    expect(await store([{ alert: 'b' }, { alert: 'b' }])).toEqual(first);
    expect(queued).toBe(1);

    // Once the window is 5 minutes old, new alerts open the next group and investigation.
    await db.sql`
      UPDATE gateway.alert_groups SET window_start = window_start - interval '5 minutes'
      WHERE id = ${first.group_id}`;
    const next = await store([{ alert: 'c' }]);
    expect(next.group_id).not.toBe(first.group_id);
    expect(queued).toBe(2);

    // A new alert that arrives already resolved opens a group, but no investigation.
    await db.sql`
      UPDATE gateway.alert_groups SET window_start = window_start - interval '5 minutes'
      WHERE id = ${next.group_id}`;
    const late = await store([{ alert: 'd', status: 'resolved' }]);
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

  it('investigates 30 groups a tenant a UTC day, records why the rest are not, and digests them', async () => {
    const { tenantId } = await newTenant(db.sql);
    const intake = createIntake(db.sql, () => undefined, 100, 30);
    const key = '{}:{app="freight-audit"}';
    const store = async (alert: string, status = 'firing') => {
      const { group_id } = await intake(tenantId, webhook(key, [{ alert, status }]));
      // Five minutes pass, so the next alert opens the next group.
      await db.sql`
        UPDATE gateway.alert_groups SET window_start = window_start - interval '5 minutes'
        WHERE id = ${group_id}`;
    };
    for (let n = 0; n < 32; n++) await store(`storm-${String(n)}`);
    await store('late', 'resolved');
    const groups = () => db.sql<{ status: string; reason: string | null; alerts: number }[]>`
      SELECT status, not_investigated AS reason,
             (SELECT count(*)::int FROM gateway.alerts a WHERE a.group_id = g.id) AS alerts
      FROM gateway.alert_groups g WHERE tenant_id = ${tenantId} ORDER BY created_at, id`;
    const today = await groups();
    expect(today.slice(0, 30).every((g) => g.status === 'queued' && g.reason === null)).toBe(true);
    expect(today.slice(30).map((g) => [g.status, g.reason])).toEqual([
      ['queued_not_investigated', 'daily_cap'],
      ['queued_not_investigated', 'daily_cap'],
      ['queued_not_investigated', 'resolved'],
    ]);
    // Every alert is recorded, investigated or not.
    expect(today.map((g) => g.alerts)).toEqual(Array.from({ length: 33 }, () => 1));

    // The digest lists the day's uninvestigated groups, with why and what they hold.
    const day = new Date().toISOString().slice(0, 10);
    const [tenant] = await db.sql<{ name: string }[]>`
      SELECT name FROM gateway.tenants WHERE id = ${tenantId}`;
    const digest = JSON.parse((await cli('digest', day)).stdout) as {
      day: string;
      tenants: {
        tenant: string;
        investigated: number;
        not_investigated: { reason: string; alerts: number; names: string[] }[];
      }[];
    };
    const listed = digest.tenants.find((t) => t.tenant === tenant?.name);
    expect(digest.day).toBe(day);
    expect(listed?.investigated).toBe(30);
    expect(listed?.not_investigated.map((g) => [g.reason, g.alerts, g.names])).toEqual([
      ['daily_cap', 1, ['HighErrorRate']],
      ['daily_cap', 1, ['HighErrorRate']],
      ['resolved', 1, ['HighErrorRate']],
    ]);

    // A new UTC day starts a new count; a tenant past its 5-minute cap keeps that reason.
    await db.sql`
      UPDATE gateway.alert_groups SET created_at = created_at - interval '1 day'
      WHERE tenant_id = ${tenantId}`;
    await intake(tenantId, webhook(key, [{ alert: 'tomorrow' }]));
    const burst = createIntake(db.sql, () => undefined, 1, 30);
    await burst(tenantId, webhook('{}:{app="other"}', [{ alert: 'busy' }]));
    expect((await groups()).slice(-2).map((g) => [g.status, g.reason])).toEqual([
      ['queued', null],
      ['queued_not_investigated', 'burst'],
    ]);
  });

  it('puts the alerts of one window in one group per group key, whatever order their labels are in', async () => {
    const app = await buildApp({
      config: APP_CONFIG,
      ping: db.ping,
      updates: createUpdates(),
      decide: unused,
      findTenant: createFindTenant(db.sql),
      intake: createIntake(db.sql, () => undefined, 5),
      listInvestigations: unused,
      getInvestigation: unused,
      decidePlan: unused,
    });
    // Up to 6 alerts over up to 4 group keys (each alert's key is one of its labels, as with
    // Alertmanager's group_by). Each is sent once, then any of them again, labels in either order.
    const name = fc.string({ unit: fc.constantFrom('a', 'b', 'c'), minLength: 1, maxLength: 2 });
    const alert = fc.record({ key: fc.nat(3), labels: fc.dictionary(name, name, { maxKeys: 3 }) });
    const labelsOf = ({ key, labels }: { key: number; labels: Record<string, string> }) => ({
      ...labels,
      alertname: 'Storm',
      group: `k${String(key)}`,
    });
    const run = fc.record({
      alerts: fc.uniqueArray(alert, {
        minLength: 1,
        maxLength: 6,
        selector: (a) => fingerprint(labelsOf(a)),
      }),
      resends: fc.array(fc.tuple(fc.nat(), fc.boolean()), { maxLength: 10 }),
    });
    try {
      await fc.assert(
        fc.asyncProperty(run, async ({ alerts, resends }) => {
          const { tenantId, token } = await newTenant(db.sql);
          const sends = [
            ...alerts.map((a) => [a, false] as const),
            ...resends.map(([n, reversed]) => [alerts[n % alerts.length], reversed] as const),
          ];
          for (const [sent, reversed] of sends) {
            if (sent === undefined) continue;
            const entries = Object.entries(labelsOf(sent));
            const labels = Object.fromEntries(reversed ? entries.reverse() : entries);
            const payload = {
              ...webhook(`{}:{group="k${String(sent.key)}"}`, [{ alert: 'unused' }]),
              alerts: [
                {
                  status: 'firing',
                  labels,
                  annotations: {},
                  startsAt: '2026-10-14T11:54:12.481Z',
                  endsAt: '0001-01-01T00:00:00Z',
                  generatorURL: 'http://prometheus.example:9090/graph',
                  // Never trusted: deduplication uses the fingerprint the labels give.
                  fingerprint: 'what-the-sender-claims',
                },
              ],
            };
            const response = await app.inject({
              method: 'POST',
              url: '/v1/alerts',
              headers: { authorization: `Bearer ${token}` },
              payload,
            });
            expect(response.statusCode).toBe(202);
          }
          const groups = await db.sql<{ group_key: string; alerts: number }[]>`
            SELECT g.group_key, count(a.id)::int AS alerts
            FROM gateway.alert_groups g LEFT JOIN gateway.alerts a ON a.group_id = g.id
            WHERE g.tenant_id = ${tenantId} GROUP BY g.id, g.group_key`;
          const expected = new Map<string, number>();
          for (const { key } of alerts) {
            const groupKey = `{}:{group="k${String(key)}"}`;
            expected.set(groupKey, (expected.get(groupKey) ?? 0) + 1);
          }
          expect(new Map(groups.map((g) => [g.group_key, g.alerts]))).toEqual(expected);
          const stored = await db.sql<{ fingerprint: string }[]>`
            SELECT fingerprint FROM gateway.alerts WHERE tenant_id = ${tenantId}`;
          expect(stored.map((row) => row.fingerprint).sort()).toEqual(
            alerts.map((a) => fingerprint(labelsOf(a))).sort(),
          );
        }),
        { numRuns: 25 },
      );
    } finally {
      await app.close();
    }
  }, 60_000);

  it('retries a failed send, parks it after the last attempt and sends it after a replay', async () => {
    const { tenantId } = await newTenant(db.sql);
    const intake = createIntake(db.sql, () => undefined, 5);
    await intake(tenantId, webhook('{}:{alertname="Relay"}', [{ alert: 'r' }]));
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

  it('keeps each FIFO group in order when a send fails', async () => {
    for (const id of ['first', 'second']) {
      await db.sql`
        INSERT INTO gateway.outbox (queue, message_group_id, deduplication_id, body)
        VALUES ('actions', 'one-target', ${id}, ${db.sql.json({ id })})`;
    }
    const sent: string[] = [];
    const failFirst: Send = (message) => {
      if (message.deduplicationId === 'first') return Promise.reject(new Error('SQS down'));
      sent.push(message.deduplicationId);
      return Promise.resolve();
    };
    expect(await relayOnce(db.sql, failFirst)).toBe(0);
    // The first waits out its backoff, and the second may not overtake it meanwhile.
    expect(await relayOnce(db.sql, failFirst)).toBe(0);
    expect(sent).toEqual([]);
    await db.sql`UPDATE gateway.outbox SET next_attempt_at = now()`;
    const working: Send = (message) => {
      sent.push(message.deduplicationId);
      return Promise.resolve();
    };
    await relayOnce(db.sql, working);
    await relayOnce(db.sql, working);
    expect(sent).toEqual(['first', 'second']);
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
    const { tenantId, token } = await newTenant(db.sql);
    const sqs = new SQSClient({ region: env.AWS_REGION, endpoint: env.SQS_ENDPOINT_URL });
    const queueName = `storm-${randomUUID().slice(0, 8)}.fifo`;
    const { QueueUrl } = await sqs.send(
      new CreateQueueCommand({ QueueName: queueName, Attributes: { FifoQueue: 'true' } }),
    );
    let nudge: () => void = () => undefined;
    const app = await buildApp({
      config: APP_CONFIG,
      ping: db.ping,
      updates: createUpdates(),
      decide: unused,
      listInvestigations: unused,
      getInvestigation: unused,
      decidePlan: unused,
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
        return JSON.stringify(webhook(keys[n % 8] ?? '', [{ alert: `storm-${String(n)}` }]));
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
