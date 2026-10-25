import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

import {
  CreateQueueCommand,
  DeleteMessageCommand,
  DeleteQueueCommand,
  GetQueueAttributesCommand,
  type Message,
  ReceiveMessageCommand,
  SendMessageCommand,
  SQSClient,
} from '@aws-sdk/client-sqs';
import Fastify, { type FastifyBaseLogger } from 'fastify';

import { connectDb } from './db.ts';
import { startEventsConsumer, type StepRow, type Store, storeEvent } from './events.ts';
import { buildInternalApp } from './internal.ts';
import { createIntake } from './intake.ts';
import { createAlertContext } from './investigations.ts';
import { env, eventually, newTenant, scratchDb, webhook } from './testing.ts';

// A silent logger that keeps the consumer's warnings and errors for the tests to read. Its other
// methods come from Fastify's no-op logger through the prototype, which a spread would not copy.
interface Entry {
  level: 'warn' | 'error';
  message: string;
  fields: Record<string, unknown>;
}
const logs: Entry[] = [];
const keep =
  (level: Entry['level']) =>
  (fields: Record<string, unknown>, message = '') => {
    logs.push({ level, message, fields });
  };
const log = Object.assign(Object.create(Fastify({ logger: false }).log) as FastifyBaseLogger, {
  warn: keep('warn'),
  error: keep('error'),
});
const AGENT_TOKEN = 'agent-token-for-these-tests-0000000000';
const METRICS = 'tc_01J9ZK3Q7M';
const LOGS = 'tc_01J9ZK4B2X';
const ACTION = '9f8b6c1e-3d2a-4f5b-8e7d-6c5b4a392817';

/** A StepRecorded v3 of one investigation. */
const step = (investigationId: string, seq: number, change: object) => ({
  schema_version: 3,
  investigation_id: investigationId,
  seq,
  kind: 'tool_call',
  summary: 'query_metrics on checkout',
  tool_name: 'query_metrics',
  tool_call_id: METRICS,
  result: null,
  latency_ms: null,
  tokens: null,
  recorded_at: '2026-10-15T12:00:41Z',
  ...change,
});

/** Two tool calls, one answered and one timed out, then a report proposing a rollback. */
const investigation = (id: string, actionId = randomUUID()) =>
  [
    ['step-recorded', step(id, 1, { tokens: { input: 5120, output: 310 } })],
    ['step-recorded', step(id, 2, { result: 'Error rate rose to 14%.', latency_ms: 812 })],
    [
      'step-recorded',
      step(id, 3, {
        tool_name: 'query_logs',
        tool_call_id: LOGS,
        tokens: { input: 6000, output: 90 },
      }),
    ],
    [
      'step-recorded',
      step(id, 4, {
        tool_name: 'query_logs',
        tool_call_id: LOGS,
        summary: 'query_logs failed: TimeoutError: ',
        latency_ms: 10_000,
      }),
    ],
    [
      'report-recorded',
      {
        schema_version: 1,
        investigation_id: id,
        status: 'awaiting_approval',
        report: {
          summary: 'checkout v1.8.0 raised the 5xx rate.',
          hypotheses: [
            {
              root_cause_class: 'bad_deploy',
              service: 'checkout',
              confidence: 0.6,
              summary: 'Errors began after v1.8.0.',
              evidence_ids: [METRICS],
            },
          ],
          missing_evidence: ['query_logs failed: the log backend timed out.'],
          proposed_actions: [
            {
              action_id: actionId,
              kind: 'rollback_deploy',
              target: 'checkout',
              parameters: { to_version: 'v1.7.3' },
              rationale: 'Roll back to the version before the errors.',
              evidence_ids: [METRICS],
            },
          ],
          partial: false,
        },
        recorded_at: '2026-10-15T12:02:10Z',
      },
    ],
  ] as const;

describe('agent events against Postgres and ElasticMQ', () => {
  let scratch: Awaited<ReturnType<typeof scratchDb>>;
  let db: ReturnType<typeof connectDb>;
  const sqs = new SQSClient({ region: env.AWS_REGION, endpoint: env.SQS_ENDPOINT_URL });
  const suffix = randomUUID().slice(0, 8);
  const queues = { queue: `events-${suffix}.fifo`, dlq: `events-dlq-${suffix}.fifo` };
  const created: string[] = [];
  let queueUrl = '';
  let dlqUrl = '';
  // One consumer for the whole suite: a stopped consumer's aborted long poll stays open on the
  // server for its 20 s and takes the next message it sees, which then waits out its visibility.
  let consumer: ReturnType<typeof startEventsConsumer>;
  // Store calls to fail as on a lost database connection before the real store runs again.
  let failures = 0;
  const store: Store = (type, body) => {
    if (failures === 0) return storeEvent(db.sql, type, body);
    failures--;
    const lost = Object.assign(new Error('write CONNECTION_CLOSED'), { code: 'CONNECTION_CLOSED' });
    return Promise.reject(lost);
  };

  /** A FIFO queue; given a DLQ, a message received 5 times moves there, as in elasticmq.conf. */
  const createQueue = async (name: string, dlq?: string) => {
    const attributes: Record<string, string> = { FifoQueue: 'true' };
    if (dlq !== undefined) {
      const { Attributes = {} } = await sqs.send(
        new GetQueueAttributesCommand({ QueueUrl: dlq, AttributeNames: ['All'] }),
      );
      const redrive = { deadLetterTargetArn: Attributes.QueueArn, maxReceiveCount: 5 };
      attributes.RedrivePolicy = JSON.stringify(redrive);
    }
    const command = new CreateQueueCommand({ QueueName: name, Attributes: attributes });
    const url = (await sqs.send(command)).QueueUrl ?? '';
    created.push(url);
    return url;
  };

  beforeAll(async () => {
    scratch = await scratchDb();
    db = connectDb(scratch.url);
    dlqUrl = await createQueue(queues.dlq);
    queueUrl = await createQueue(queues.queue, dlqUrl);
    consumer = startEventsConsumer(sqs, queues, store, log);
  }, 60_000);
  afterAll(async () => {
    await consumer.stop();
    for (const url of created) await sqs.send(new DeleteQueueCommand({ QueueUrl: url }));
    sqs.destroy();
    await db.sql.end();
    await scratch.drop();
  });

  /** A tenant and an alert group whose investigation the agent reports on. */
  const group = async () => {
    const { tenantId } = await newTenant(db.sql);
    const investigationId = randomUUID();
    const [row] = await db.sql<{ id: string }[]>`
      INSERT INTO gateway.alert_groups
        (tenant_id, group_key, window_start, severity, status, investigation_id)
      VALUES (${tenantId}, ${randomUUID()}, now(), 'critical', 'running', ${investigationId})
      RETURNING id`;
    if (row === undefined) throw new Error('no group');
    return { tenantId, investigationId, groupId: row.id };
  };
  const send = async (
    messages: readonly (readonly [string, object])[],
    groupId: string,
    attempt = 'first',
    url = queueUrl,
  ) => {
    for (const [type, body] of messages) {
      const key = 'seq' in body ? `step-${String(body.seq)}` : 'report';
      await sqs.send(
        new SendMessageCommand({
          QueueUrl: url,
          MessageBody: JSON.stringify(body),
          MessageGroupId: groupId,
          MessageDeduplicationId: `${groupId}-${key}-${attempt}`,
          MessageAttributes: { message_type: { DataType: 'String', StringValue: type } },
        }),
      );
    }
  };
  /** Waits until the running consumer has stored and deleted every message. */
  const drained = () =>
    eventually(async () => {
      const { Attributes = {} } = await sqs.send(
        // ElasticMQ returns the counts for All, not when they are named.
        new GetQueueAttributesCommand({ QueueUrl: queueUrl, AttributeNames: ['All'] }),
      );
      return (
        Attributes.ApproximateNumberOfMessages === '0' &&
        Attributes.ApproximateNumberOfMessagesNotVisible === '0'
      );
    }, 20_000).catch((error: unknown) => {
      const errors = logs.filter((entry) => entry.level === 'error');
      const shown = errors.map((entry) => `${entry.message}: ${String(entry.fields.err)}`);
      throw new Error(`${String(error)}; consumer errors: ${shown.join(' | ') || 'none'}`);
    });
  /** Takes every message off a queue no consumer reads. */
  const takeAll = async (url: string) => {
    const taken: Message[] = [];
    for (;;) {
      const { Messages = [] } = await sqs.send(
        new ReceiveMessageCommand({
          QueueUrl: url,
          MaxNumberOfMessages: 10,
          WaitTimeSeconds: 1,
          MessageAttributeNames: ['All'],
          MessageSystemAttributeNames: ['MessageGroupId'],
        }),
      );
      if (Messages.length === 0) return taken;
      for (const message of Messages) {
        await sqs.send(
          new DeleteMessageCommand({ QueueUrl: url, ReceiptHandle: message.ReceiptHandle }),
        );
        taken.push(message);
      }
    }
  };
  // version is the row's xmin, which every rewrite changes, even to equal values.
  const mirror = async (investigationId: string) => ({
    steps: await db.sql<(StepRow & { created_at: Date; version: string })[]>`
      SELECT *, xmin::text AS version FROM gateway.investigation_steps
      WHERE investigation_id = ${investigationId} ORDER BY seq`,
    reports: await db.sql<{ status: string; report: unknown; created_at: Date }[]>`
      SELECT * FROM gateway.investigation_reports WHERE investigation_id = ${investigationId}`,
    actions: await db.sql<Record<string, unknown>[]>`
      SELECT * FROM gateway.proposed_actions WHERE investigation_id = ${investigationId}`,
    groups: await db.sql<{ status: string; updated_at: Date }[]>`
      SELECT status, updated_at FROM gateway.alert_groups WHERE investigation_id = ${investigationId}`,
  });
  const put = ([type, body]: readonly [string, object]) =>
    storeEvent(db.sql, type, JSON.stringify(body));

  it('mirrors steps, pairs tool calls, stores the report and its actions, in order, once', async () => {
    const { tenantId, investigationId } = await group();
    const messages = investigation(investigationId, ACTION);
    await send(messages, investigationId);
    await drained();

    const stored = await mirror(investigationId);
    expect(
      stored.steps.map((row) => [
        row.seq,
        row.tool_call_id,
        row.tokens_input,
        row.result,
        row.tool_status,
        row.duration_ms,
      ]),
    ).toEqual([
      [1, METRICS, 5120, null, 'ok', 812],
      [2, METRICS, null, 'Error rate rose to 14%.', 'ok', 812],
      [3, LOGS, 6000, null, 'timeout', 10_000],
      [4, LOGS, null, null, 'timeout', 10_000],
    ]);
    const [report] = stored.reports;
    expect(report).toMatchObject({ status: 'awaiting_approval', report: messages[4][1].report });
    expect(stored.actions).toEqual([
      expect.objectContaining({
        id: ACTION,
        tenant_id: tenantId,
        kind: 'rollback_deploy',
        target: 'checkout',
        parameters: { to_version: 'v1.7.3' },
      }),
    ]);
    expect(stored.groups).toEqual([expect.objectContaining({ status: 'awaiting_approval' })]);
    // FIFO per investigation: every step was stored before its report.
    const stepTimes = stored.steps.map((row) => row.created_at.getTime());
    expect(report?.created_at.getTime()).toBeGreaterThanOrEqual(Math.max(...stepTimes));

    // A redelivery (the same messages under new deduplication ids) changes nothing.
    await send(messages, investigationId, 'again');
    await drained();
    expect(await mirror(investigationId)).toEqual(stored);
  }, 60_000);

  it('drops events of an investigation the gateway does not know', async () => {
    const unknown = randomUUID();
    await send(investigation(unknown), unknown);
    await drained();
    const stored = await mirror(unknown);
    expect([stored.steps, stored.reports, stored.actions]).toEqual([[], [], []]);
  }, 60_000);

  it('moves a message it can never store to the DLQ at once, and stores the rest of its group', async () => {
    const { investigationId: id } = await group();
    const [request, ...rest] = investigation(id);
    const poison = step(id, 5, {});
    await send([request, ['step-exploded', poison], ...rest], id);
    // Drained within seconds: no follower waits out a visibility timeout behind the poison.
    await drained();
    const stored = await mirror(id);
    expect(stored.steps.map((row) => [row.seq, row.tool_status])).toEqual([
      [1, 'ok'],
      [2, 'ok'],
      [3, 'timeout'],
      [4, 'timeout'],
    ]);
    expect([stored.reports.length, stored.actions.length]).toEqual([1, 1]);
    expect(stored.groups).toEqual([expect.objectContaining({ status: 'awaiting_approval' })]);
    expect(await takeAll(dlqUrl)).toEqual([
      expect.objectContaining({
        Body: JSON.stringify(poison),
        Attributes: expect.objectContaining({ MessageGroupId: id }) as unknown,
        MessageAttributes: {
          message_type: expect.objectContaining({ StringValue: 'step-exploded' }) as unknown,
          error: expect.objectContaining({
            StringValue: 'unknown message_type "step-exploded"',
          }) as unknown,
        },
      }),
    ]);
  }, 60_000);

  it('retries a transient failure in process, without another receive', async () => {
    const { investigationId: id } = await group();
    const first = logs.length;
    failures = 2;
    await send(investigation(id).slice(0, 1), id);
    await drained();
    expect(failures).toBe(0);
    // Every try comes from the one receive; a second receive would count 2.
    const retries = logs
      .slice(first)
      .filter((entry) => entry.message === 'agent event not stored; retrying');
    expect(retries.map((entry) => entry.fields.receives)).toEqual(['1', '1']);
    expect((await mirror(id)).steps.map((row) => row.seq)).toEqual([1]);
    expect(await takeAll(dlqUrl)).toEqual([]);
  }, 60_000);

  it('moves a message Postgres refuses to the DLQ without retrying it', async () => {
    const { investigationId: id } = await group();
    const first = logs.length;
    // Valid by its contract, but out of range for the integer column.
    const huge = step(id, 1, { result: 'rows', latency_ms: 3_000_000_000 });
    await send(
      [
        ['step-recorded', huge],
        ['step-recorded', step(id, 2, { tokens: { input: 1, output: 1 } })],
      ],
      id,
    );
    await drained();
    const dead = await takeAll(dlqUrl);
    expect(dead.map((message) => message.Body)).toEqual([JSON.stringify(huge)]);
    expect(dead[0]?.MessageAttributes?.error?.StringValue).toMatch(/out of range/);
    expect(logs.slice(first).map((entry) => entry.message)).toEqual([
      'agent event cannot be stored; moved to the DLQ',
    ]);
    expect((await mirror(id)).steps.map((row) => row.seq)).toEqual([2]);
  }, 60_000);

  it('pairs a tool call whichever of its steps comes first, and rewrites no row again', async () => {
    const { investigationId: id } = await group();
    const [request, result] = investigation(id);
    await put(result);
    await put(request);
    const { steps } = await mirror(id);
    expect(steps.map((row) => [row.seq, row.tool_status, row.duration_ms])).toEqual([
      [1, 'ok', 812],
      [2, 'ok', 812],
    ]);
    await put(result);
    await put(request);
    expect((await mirror(id)).steps).toEqual(steps);
  });

  it('stores a v2 report with its verdict and models, and a v1 report with neither', async () => {
    const { investigationId: live } = await group();
    const [type, v1] = investigation(live)[4];
    const models = [
      {
        part: 'triage',
        provider: 'anthropic',
        model: 'claude-haiku-5-5',
        calls: 0,
        failed: 1,
        reason: "QuotaExhausted: claude-haiku-5-5 skipped: today's triage cap is spent",
      },
      { part: 'triage', provider: 'gemini', model: 'm', calls: 4, failed: 0, reason: null },
    ];
    await put([type, { ...v1, schema_version: 2, verdict: 'real', models }]);
    const { investigationId: old } = await group();
    const v1Report = investigation(old)[4];
    await put(v1Report);
    const stored = async (id: string) => {
      const [row] = await db.sql<{ verdict: string | null; models: unknown; report: unknown }[]>`
        SELECT verdict, models, report FROM gateway.investigation_reports
        WHERE investigation_id = ${id}`;
      return row;
    };
    expect(await stored(live)).toEqual({ verdict: 'real', models, report: v1.report });
    expect(await stored(old)).toEqual({ verdict: null, models: null, report: v1Report[1].report });
    expect((await mirror(live)).actions).toHaveLength(1);
  });

  it('keeps the first report: a second, different one adds no actions and moves no group', async () => {
    const { investigationId: id } = await group();
    const [, , , , report] = investigation(id);
    await put(report);
    // Unfinished again, as when the sweeper held the group while the first report was stored.
    await db.sql`UPDATE gateway.alert_groups SET status = 'running' WHERE investigation_id = ${id}`;
    const [type, first] = report;
    const [action] = first.report.proposed_actions;
    const proposed_actions = [{ ...action, action_id: randomUUID() }];
    await put([
      type,
      { ...first, report: { ...first.report, summary: 'Again.', proposed_actions } },
    ]);
    const stored = await mirror(id);
    expect(stored.reports).toEqual([expect.objectContaining({ report: first.report })]);
    expect(stored.actions.map((row) => row.id)).toEqual([action.action_id]);
    expect(stored.groups).toEqual([expect.objectContaining({ status: 'running' })]);
  });

  it('stores a report at once while the sweeper holds its group, and leaves the status to it', async () => {
    const { investigationId: id, groupId } = await group();
    const locked = Promise.withResolvers<undefined>();
    const release = Promise.withResolvers<undefined>();
    // The sweeper locks queued groups like this across its agent calls.
    const sweep = db.sql.begin(async (tx) => {
      await tx`SELECT id FROM gateway.alert_groups WHERE id = ${groupId} FOR NO KEY UPDATE`;
      locked.resolve(undefined);
      await release.promise;
    });
    try {
      await locked.promise;
      const started = Date.now();
      await put(investigation(id)[4]);
      expect(Date.now() - started).toBeLessThan(2000);
    } finally {
      release.resolve(undefined);
      await sweep;
    }
    const stored = await mirror(id);
    expect([stored.reports.length, stored.actions.length]).toEqual([1, 1]);
    // The sweeper's next pass copies the agent's finished status instead.
    expect(stored.groups).toEqual([expect.objectContaining({ status: 'running' })]);
  });

  it('replays the events DLQ into the events queue with each message type and group', async () => {
    const replay = { queue: `replay-${suffix}.fifo`, dlq: `replay-dlq-${suffix}.fifo` };
    const target = await createQueue(replay.queue);
    const dlq = await createQueue(replay.dlq);
    const [a, b] = [randomUUID(), randomUUID()];
    const [request, result] = investigation(a);
    const report = investigation(b)[4];
    await send([request, result], a, 'first', dlq);
    await send([report], b, 'first', dlq);
    const cli = fileURLToPath(new URL('./cli.ts', import.meta.url));
    const { stdout } = await promisify(execFile)(process.execPath, [cli, 'events-replay'], {
      env: { ...process.env, AGENT_EVENTS_QUEUE: replay.queue, AGENT_EVENTS_DLQ: replay.dlq },
    });
    expect(JSON.parse(stdout)).toMatchObject({ msg: 'agent events re-queued', count: 3 });
    const moved = await takeAll(target);
    const of = (groupId: string) =>
      moved
        .filter((message) => message.Attributes?.MessageGroupId === groupId)
        .map((message) => [message.MessageAttributes?.message_type?.StringValue, message.Body]);
    const expected = (...messages: (readonly [string, object])[]) =>
      messages.map(([type, body]) => [type, JSON.stringify(body)]);
    expect(of(a)).toEqual(expected(request, result));
    expect(of(b)).toEqual(expected(report));
    expect(await takeAll(dlq)).toEqual([]);
  }, 60_000);

  it('serves an alert group to the agent, oldest alert first', async () => {
    const { tenantId, investigationId, groupId } = await group();
    for (const [alert, startsAt] of [
      ['later', '2026-10-15T12:01:00Z'],
      ['first', '2026-10-15T12:00:00Z'],
    ] as const) {
      await db.sql`
        INSERT INTO gateway.alerts (tenant_id, group_id, fingerprint, labels, starts_at, status)
        VALUES (${tenantId}, ${groupId}, ${alert}, ${db.sql.json({ alertname: alert })},
                ${startsAt}, 'firing')`;
    }
    const internal = buildInternalApp({
      config: {
        LOG_LEVEL: 'error',
        EXECUTOR_SERVICE_TOKEN: 'executor-token-for-these-tests-000000',
        AGENT_SERVICE_TOKEN: AGENT_TOKEN,
      },
      claim: () => Promise.reject(new Error('unused')),
      report: () => Promise.reject(new Error('unused')),
      alertContext: createAlertContext(db.sql),
    });
    try {
      const headers = { authorization: `Bearer ${AGENT_TOKEN}` };
      const found = await internal.inject({
        url: `/internal/investigations/${investigationId}/alerts`,
        headers,
      });
      expect(found.statusCode).toBe(200);
      expect(found.json()).toMatchObject({
        investigation_id: investigationId,
        group_id: groupId,
        tenant_id: tenantId,
        severity: 'critical',
        alert_count: 2,
        alerts: [
          {
            fingerprint: 'first',
            labels: { alertname: 'first' },
            starts_at: '2026-10-15T12:00:00.000Z',
          },
          { fingerprint: 'later', status: 'firing' },
        ],
      });
      // Rows stored before annotations existed read as before.
      expect(found.json<{ alerts: object[] }>().alerts[0]).not.toHaveProperty('annotations');
      const unknown = await internal.inject({
        url: `/internal/investigations/${randomUUID()}/alerts`,
        headers,
      });
      expect(unknown.statusCode).toBe(404);
    } finally {
      await internal.close();
    }
  });

  it('keeps the annotations an alert arrived with and serves them to the agent', async () => {
    const { tenantId } = await newTenant(db.sql);
    const payload = webhook('{}:{alertname="HighErrorRate"}', [{ alert: 'noted' }]);
    const annotations = {
      summary: 'checkout 5xx ratio above 5% for 2 minutes',
      runbook_url: 'https://runbooks.example/5xx',
    };
    payload.alerts = payload.alerts.map((alert) => ({ ...alert, annotations }));
    const accepted = await createIntake(db.sql, () => undefined, 5)(tenantId, payload);
    const [group] = await db.sql<{ investigation_id: string }[]>`
      SELECT investigation_id FROM gateway.alert_groups WHERE id = ${accepted.group_id}`;
    const internal = buildInternalApp({
      config: {
        LOG_LEVEL: 'error',
        EXECUTOR_SERVICE_TOKEN: 'executor-token-for-these-tests-000000',
        AGENT_SERVICE_TOKEN: AGENT_TOKEN,
      },
      claim: () => Promise.reject(new Error('unused')),
      report: () => Promise.reject(new Error('unused')),
      alertContext: createAlertContext(db.sql),
    });
    try {
      const found = await internal.inject({
        url: `/internal/investigations/${group?.investigation_id ?? ''}/alerts`,
        headers: { authorization: `Bearer ${AGENT_TOKEN}` },
      });
      expect(found.statusCode).toBe(200);
      expect(found.json()).toMatchObject({ alerts: [{ annotations }] });
    } finally {
      await internal.close();
    }
  });
});
