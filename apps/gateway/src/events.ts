import { setTimeout as sleep } from 'node:timers/promises';

import {
  ChangeMessageVisibilityCommand,
  DeleteMessageCommand,
  GetQueueUrlCommand,
  type Message,
  ReceiveMessageCommand,
  SendMessageCommand,
  type SQSClient,
} from '@aws-sdk/client-sqs';
import {
  parseMessage,
  type ReportRecordedV1,
  type StepRecorded,
  type ToolCall,
} from '@triage/ts-clients/messages';
import type { FastifyBaseLogger } from 'fastify';
import postgres, { type Sql, type TransactionSql } from 'postgres';

// A received message stays hidden this long, renewed before each retry of a transient failure.
const VISIBILITY_SECONDS = 60;
// A transient failure is retried after 1 s, doubling up to 30 s between tries, for 15 minutes.
const FIRST_RETRY_MS = 1000;
const MAX_RETRY_MS = 30_000;
const RETRY_FOR_MS = 15 * 60_000;
// The longest reason a quarantined message carries in its error attribute.
const MAX_REASON_LENGTH = 256;
// SQLSTATE classes no retry fixes: 22 data exception, 23 integrity constraint, 42 syntax or name.
const PERMANENT_SQLSTATE = /^(?:22|23|42)/;
// The replay takes as many as SQS hands out per receive, and is done once a short poll finds none.
const REPLAY_BATCH = 10;
const REPLAY_WAIT_SECONDS = 1;

export type ToolCallStatus = ToolCall['status'];
/** stored: the mirror holds the message now (or held it already). */
export type EventOutcome = 'stored' | 'unknown_investigation';
/** Stores one message, given its message_type attribute and its body. */
export type Store = (type: string | undefined, body: string) => Promise<EventOutcome>;
/** The agent events queue and the DLQ beside it, by name. */
export interface EventQueues {
  queue: string;
  dlq: string;
}
type QueueUrls = Record<keyof EventQueues, string | undefined>;
export interface EventsConsumer {
  stop: () => Promise<void>;
}

/** A message no retry can store: not JSON, against its contract, or of an unknown message_type. */
export class InvalidEvent extends Error {}

/** Whether retrying cannot store the message: it is invalid, or Postgres refuses its data. */
export function permanent(error: unknown): boolean {
  if (error instanceof postgres.PostgresError) return PERMANENT_SQLSTATE.test(error.code);
  return error instanceof InvalidEvent;
}

/** One investigation_steps row. */
export interface StepRow {
  investigation_id: string;
  seq: number;
  kind: StepRecorded['kind'];
  summary: string;
  tool_name: string | null;
  tool_call_id: string | null;
  result: string | null;
  latency_ms: number | null;
  tokens_input: number | null;
  tokens_output: number | null;
  tool_status: ToolCallStatus | null;
  duration_ms: number | null;
  recorded_at: string;
}

/** Any StepRecorded version as a mirror row; v2 steps and v3 result steps carry a call's outcome. */
export function stepRow(step: StepRecorded): StepRow {
  const { investigation_id, seq, kind, summary, recorded_at } = step;
  const base = {
    investigation_id,
    seq,
    kind,
    summary,
    recorded_at,
    result: null,
    latency_ms: null,
  };
  switch (step.schema_version) {
    case 1:
      return {
        ...base,
        tool_name: step.tool_name,
        tool_call_id: step.tool_call_id,
        tokens_input: null,
        tokens_output: null,
        tool_status: null,
        duration_ms: null,
      };
    case 2:
      return {
        ...base,
        tool_name: step.tool_call?.name ?? null,
        tool_call_id: step.tool_call?.id ?? null,
        tokens_input: step.usage.input_tokens,
        tokens_output: step.usage.output_tokens,
        tool_status: step.tool_call?.status ?? null,
        duration_ms: step.tool_call?.duration_ms ?? null,
      };
    case 3: {
      const row: StepRow = {
        ...base,
        tool_name: step.tool_name,
        tool_call_id: step.tool_call_id,
        result: step.result,
        latency_ms: step.latency_ms,
        tokens_input: step.tokens?.input ?? null,
        tokens_output: step.tokens?.output ?? null,
        tool_status: null,
        duration_ms: null,
      };
      const outcome = toolOutcome(row);
      return outcome === undefined
        ? row
        : { ...row, tool_status: outcome.status, duration_ms: outcome.duration_ms };
    }
  }
}

/**
 * The outcome of a tool call from its result step: a v3 tool_call step without tokens. The model's
 * request is the step with tokens; both share the tool_call_id.
 */
export function toolOutcome(
  row: Pick<
    StepRow,
    'kind' | 'tool_call_id' | 'tokens_input' | 'result' | 'summary' | 'latency_ms'
  >,
): { status: ToolCallStatus; duration_ms: number | null } | undefined {
  if (row.kind !== 'tool_call' || row.tool_call_id === null || row.tokens_input !== null) {
    return undefined;
  }
  // ponytail: StepRecorded v3 has no status, so a timeout is read from the agent's summary,
  // "<tool> failed: <ErrorType>: ..."; add a status field in StepRecorded v4 if that format changes.
  const timedOut = /\sfailed: \w*Timeout\w*:/.test(row.summary);
  const status = row.result !== null ? 'ok' : timedOut ? 'timeout' : 'error';
  return { status, duration_ms: row.latency_ms };
}

/** A message body checked against its contract; any failure is an InvalidEvent. */
function parse<K extends 'step-recorded' | 'report-recorded.v1'>(name: K, body: string) {
  try {
    return parseMessage(name, JSON.parse(body));
  } catch (error) {
    throw new InvalidEvent((error as Error).message);
  }
}

async function tenantOf(tx: TransactionSql, investigationId: string) {
  const [group] = await tx<{ tenant_id: string }[]>`
    SELECT tenant_id FROM gateway.alert_groups WHERE investigation_id = ${investigationId}`;
  return group?.tenant_id;
}

/**
 * Copies one message into the mirror tables in one transaction. Every write is keyed, so a
 * redelivered message changes nothing. Throws InvalidEvent on an unknown message type or an invalid
 * body. The agent sets message_type on each message: step-recorded or report-recorded.
 */
export async function storeEvent(
  sql: Sql,
  type: string | undefined,
  body: string,
): Promise<EventOutcome> {
  if (type === 'step-recorded') {
    const row = stepRow(parse('step-recorded', body));
    return sql.begin(async (tx) => {
      if ((await tenantOf(tx, row.investigation_id)) === undefined) return 'unknown_investigation';
      await tx`
        INSERT INTO gateway.investigation_steps ${tx(row)}
        ON CONFLICT (investigation_id, seq) DO NOTHING`;
      if (row.tool_call_id !== null) {
        // Either step of a call may come first (a DLQ replay, a parked outbox row), so the call's
        // other rows take its result row's outcome each time, and rows that hold it stay untouched.
        await tx`
          UPDATE gateway.investigation_steps s
          SET tool_status = r.tool_status, duration_ms = r.duration_ms
          FROM (
            SELECT seq, tool_status, duration_ms FROM gateway.investigation_steps
            WHERE investigation_id = ${row.investigation_id} AND tool_call_id = ${row.tool_call_id}
              AND tokens_input IS NULL AND tool_status IS NOT NULL
            ORDER BY seq DESC LIMIT 1) r
          WHERE s.investigation_id = ${row.investigation_id} AND s.tool_call_id = ${row.tool_call_id}
            AND s.seq <> r.seq
            AND (s.tool_status, s.duration_ms) IS DISTINCT FROM (r.tool_status, r.duration_ms)`;
      }
      return 'stored';
    });
  }
  if (type === 'report-recorded') {
    const message: ReportRecordedV1 = parse('report-recorded.v1', body);
    const { investigation_id: id, status, report, recorded_at: at } = message;
    return sql.begin(async (tx) => {
      const tenantId = await tenantOf(tx, id);
      if (tenantId === undefined) return 'unknown_investigation';
      // Text cast to jsonb: postgres.js would store a string parameter typed jsonb as one string.
      const inserted = await tx`
        INSERT INTO gateway.investigation_reports (investigation_id, status, report, recorded_at)
        VALUES (${id}, ${status}, ${JSON.stringify(report)}::text::jsonb, ${at})
        ON CONFLICT (investigation_id) DO NOTHING
        RETURNING investigation_id`;
      // The first report stands: a redelivered or later one adds no actions and moves no group.
      if (inserted.length === 0) return 'stored';
      for (const action of report.proposed_actions) {
        await tx`
          INSERT INTO gateway.proposed_actions
            (id, tenant_id, investigation_id, kind, target, parameters, rationale, proposed_at)
          VALUES (${action.action_id}, ${tenantId}, ${id}, ${action.kind}, ${action.target},
                  ${tx.json(action.parameters)}, ${action.rationale}, ${at})
          ON CONFLICT (id) DO NOTHING`;
      }
      // Only an unfinished group moves, so a report never undoes a later status.
      // SKIP LOCKED: a group the sweeper holds takes the agent's finished status on its next pass.
      await tx`
        UPDATE gateway.alert_groups SET status = ${status}, updated_at = now()
        WHERE id IN (
          SELECT id FROM gateway.alert_groups
          WHERE investigation_id = ${id} AND status IN ('queued', 'running', 'failed')
          FOR NO KEY UPDATE SKIP LOCKED)`;
      return 'stored';
    });
  }
  throw new InvalidEvent(`unknown message_type ${JSON.stringify(type ?? null)}`);
}

const queueUrl = async (sqs: SQSClient, name: string) =>
  (await sqs.send(new GetQueueUrlCommand({ QueueName: name }))).QueueUrl;

/** A received message for another queue: the same body, group and message_type, plus an error. */
function copyTo(
  queue: string | undefined,
  message: Message,
  deduplicationId: string,
  error?: string,
) {
  const type = message.MessageAttributes?.message_type?.StringValue;
  const text = (value: string) => ({ DataType: 'String', StringValue: value });
  return new SendMessageCommand({
    QueueUrl: queue,
    MessageBody: message.Body,
    MessageGroupId: message.Attributes?.MessageGroupId,
    MessageDeduplicationId: deduplicationId,
    MessageAttributes: {
      ...(type === undefined ? {} : { message_type: text(type) }),
      ...(error === undefined ? {} : { error: text(error) }),
    },
  });
}

/**
 * Stores one received message and deletes it once its transaction commits. One that can never be
 * stored goes to the DLQ at once. Any other failure is retried here, the message kept hidden, for
 * up to RETRY_FOR_MS; after that, or on stop, the message returns after its visibility timeout.
 */
async function handle(
  sqs: SQSClient,
  urls: QueueUrls,
  message: Message,
  store: Store,
  log: FastifyBaseLogger,
  stopped: AbortSignal,
): Promise<void> {
  const type = message.MessageAttributes?.message_type?.StringValue;
  const fields = {
    message_id: message.MessageId,
    message_type: type,
    investigation_id: message.Attributes?.MessageGroupId,
    receives: message.Attributes?.ApproximateReceiveCount,
  };
  const deadline = Date.now() + RETRY_FOR_MS;
  try {
    for (let delay = FIRST_RETRY_MS; ; delay = Math.min(2 * delay, MAX_RETRY_MS)) {
      try {
        if ((await store(type, message.Body ?? '')) === 'unknown_investigation') {
          log.warn(fields, 'agent event for an investigation the gateway does not know; dropped');
        }
        break;
      } catch (error) {
        if (permanent(error)) {
          const reason = (error as Error).message.slice(0, MAX_REASON_LENGTH);
          await sqs.send(
            copyTo(urls.dlq, message, `quarantine-${message.MessageId ?? ''}`, reason),
          );
          log.error({ err: error, ...fields }, 'agent event cannot be stored; moved to the DLQ');
          break;
        }
        if (stopped.aborted || Date.now() + delay > deadline) throw error;
        log.warn({ err: error, ...fields, retry_in_ms: delay }, 'agent event not stored; retrying');
        // Stopping ends the wait and leaves the message, as the deadline does.
        if (!(await sleep(delay, true, { signal: stopped }).catch(() => false))) throw error;
        await sqs.send(
          new ChangeMessageVisibilityCommand({
            QueueUrl: urls.queue,
            ReceiptHandle: message.ReceiptHandle,
            VisibilityTimeout: VISIBILITY_SECONDS,
          }),
        );
      }
    }
  } catch (error) {
    log.error(
      { err: error, ...fields },
      'agent event not stored; it returns after its visibility timeout',
    );
    return;
  }
  try {
    await sqs.send(
      new DeleteMessageCommand({ QueueUrl: urls.queue, ReceiptHandle: message.ReceiptHandle }),
    );
  } catch (error) {
    log.error({ err: error, ...fields }, 'agent event not deleted; its redelivery changes nothing');
  }
}

/**
 * Long-polls the agent events queue into the mirror tables until stopped. It takes one message at
 * a time, so a failing message holds back only the later messages of its own FIFO group. Stopping
 * aborts the long poll on this side only: a message the server still hands to it returns after its
 * visibility timeout, like any receive that was never deleted.
 */
export function startEventsConsumer(
  sqs: SQSClient,
  queues: EventQueues,
  store: Store,
  log: FastifyBaseLogger,
): EventsConsumer {
  const stopped = new AbortController();
  const loop = (async () => {
    let urls: QueueUrls | undefined;
    while (!stopped.signal.aborted) {
      try {
        urls ??= { queue: await queueUrl(sqs, queues.queue), dlq: await queueUrl(sqs, queues.dlq) };
        const { Messages = [] } = await sqs.send(
          new ReceiveMessageCommand({
            QueueUrl: urls.queue,
            MaxNumberOfMessages: 1,
            WaitTimeSeconds: 20,
            VisibilityTimeout: VISIBILITY_SECONDS,
            MessageAttributeNames: ['message_type'],
            MessageSystemAttributeNames: ['MessageGroupId', 'ApproximateReceiveCount'],
          }),
          { abortSignal: stopped.signal },
        );
        for (const message of Messages) {
          await handle(sqs, urls, message, store, log, stopped.signal);
        }
      } catch (error) {
        // Stopping aborts the long poll, which is no failure.
        if (error instanceof Error && error.name === 'AbortError') break;
        log.error({ err: error }, 'agent events receive failed');
        await sleep(5000, undefined, { signal: stopped.signal }).catch(() => undefined);
      }
    }
  })();
  return {
    stop: async () => {
      stopped.abort();
      await loop;
    },
  };
}

/**
 * Moves every message of the events DLQ back to the events queue with its body, group and
 * message_type, deleting each once sent; returns how many it moved. A failed send stops the
 * replay, so the rest of that FIFO group stays behind it in order.
 */
export async function replayDeadLetters(sqs: SQSClient, queues: EventQueues): Promise<number> {
  const queue = await queueUrl(sqs, queues.queue);
  const dlq = await queueUrl(sqs, queues.dlq);
  let moved = 0;
  for (;;) {
    const { Messages = [] } = await sqs.send(
      new ReceiveMessageCommand({
        QueueUrl: dlq,
        MaxNumberOfMessages: REPLAY_BATCH,
        WaitTimeSeconds: REPLAY_WAIT_SECONDS,
        VisibilityTimeout: VISIBILITY_SECONDS,
        MessageAttributeNames: ['message_type'],
        MessageSystemAttributeNames: ['MessageGroupId'],
      }),
    );
    if (Messages.length === 0) return moved;
    for (const message of Messages) {
      await sqs.send(copyTo(queue, message, `replay-${message.MessageId ?? ''}`));
      await sqs.send(
        new DeleteMessageCommand({ QueueUrl: dlq, ReceiptHandle: message.ReceiptHandle }),
      );
      moved++;
    }
  }
}
