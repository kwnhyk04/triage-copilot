import { setTimeout as sleep } from 'node:timers/promises';

import { GetQueueUrlCommand, SendMessageCommand, SQSClient } from '@aws-sdk/client-sqs';
import type { FastifyBaseLogger } from 'fastify';
import type { Sql } from 'postgres';

/** Failed sends after which a message is parked (failed_at) until `pnpm outbox:replay`. */
export const MAX_ATTEMPTS = 10;
const BATCH = 10;

/** An SQS client bounded like every external call: 3 s to connect, two attempts, requestTimeout. */
export const sqsClient = (
  config: { AWS_REGION: string; SQS_ENDPOINT_URL?: string | undefined },
  requestTimeout: number,
) =>
  new SQSClient({
    region: config.AWS_REGION,
    ...(config.SQS_ENDPOINT_URL === undefined ? {} : { endpoint: config.SQS_ENDPOINT_URL }),
    maxAttempts: 2,
    requestHandler: { connectionTimeout: 3000, requestTimeout },
  });

export type Queue = 'investigations' | 'actions';
export interface OutboxMessage {
  queue: Queue;
  groupId: string;
  deduplicationId: string;
  body: string;
}
export type Send = (message: OutboxMessage) => Promise<void>;
export interface Relay {
  /** Relays at once instead of at the next interval. */
  nudge: () => void;
  stop: () => Promise<void>;
}

/** Sends to the named FIFO queue, looking up its URL once; the message's own queue is ignored. */
export function sqsSender(sqs: SQSClient, queueName: string): Send {
  let queueUrl: string | undefined;
  return async ({ groupId, deduplicationId, body }) => {
    queueUrl ??= (await sqs.send(new GetQueueUrlCommand({ QueueName: queueName }))).QueueUrl;
    await sqs.send(
      new SendMessageCommand({
        QueueUrl: queueUrl,
        MessageBody: body,
        MessageGroupId: groupId,
        MessageDeduplicationId: deduplicationId,
      }),
    );
  };
}

/**
 * Sends the due messages, oldest first, deleting each once SQS has it; returns how many it sent.
 * A failed send is retried after 2^attempts seconds (at most 5 minutes) and parked after
 * MAX_ATTEMPTS; until then, later messages of its FIFO group wait, so none overtakes it. SKIP LOCKED keeps two relays from sending one row. A crash between the send and
 * the delete sends it again, which SQS drops within its 5-minute deduplication window.
 */
export async function relayOnce(sql: Sql, send: Send): Promise<number> {
  return sql.begin(async (tx) => {
    const due = await tx<
      {
        id: string;
        queue: Queue;
        message_group_id: string;
        deduplication_id: string;
        body: unknown;
        attempts: number;
      }[]
    >`
      SELECT id, queue, message_group_id, deduplication_id, body, attempts FROM gateway.outbox o
      WHERE failed_at IS NULL AND next_attempt_at <= now()
        -- FIFO per group: a row waits while an earlier one of its group is still to be sent.
        AND NOT EXISTS (
          SELECT FROM gateway.outbox e
          WHERE e.queue = o.queue AND e.message_group_id = o.message_group_id AND e.id < o.id
            AND e.failed_at IS NULL)
      ORDER BY id LIMIT ${BATCH} FOR UPDATE SKIP LOCKED`;
    for (const row of due) {
      try {
        await send({
          queue: row.queue,
          groupId: row.message_group_id,
          deduplicationId: row.deduplication_id,
          body: JSON.stringify(row.body),
        });
      } catch (error) {
        const attempts = row.attempts + 1;
        await tx`
          UPDATE gateway.outbox
          SET attempts = ${attempts}, last_error = ${String(error).slice(0, 500)},
              next_attempt_at = now() + make_interval(secs => ${Math.min(2 ** attempts, 300)}),
              failed_at = ${attempts >= MAX_ATTEMPTS ? tx`now()` : null}
          WHERE id = ${row.id}`;
        return 0; // SQS is likely down; the other rows wait for the next round.
      }
      await tx`DELETE FROM gateway.outbox WHERE id = ${row.id}`;
    }
    return due.length;
  });
}

/** Relays in the background: when nudged after a commit, and every intervalMs for retries. */
export function startRelay(sql: Sql, send: Send, log: FastifyBaseLogger, intervalMs = 1000): Relay {
  const stopped = new AbortController();
  let wake = new AbortController();
  const loop = (async () => {
    while (!stopped.signal.aborted) {
      let sent = 0;
      try {
        sent = await relayOnce(sql, send);
      } catch (error) {
        log.error({ err: error }, 'outbox relay failed');
      }
      // A full batch means more may be due, so the next round starts at once.
      if (sent < BATCH) {
        await sleep(intervalMs, undefined, { signal: wake.signal }).catch(() => undefined);
        wake = new AbortController();
      }
    }
  })();
  return {
    nudge: () => {
      wake.abort();
    },
    stop: async () => {
      stopped.abort();
      wake.abort();
      await loop;
    },
  };
}

/** The gateway's DLQ replay: puts every parked message back in line for the relay. */
export async function replayParked(sql: Sql): Promise<number> {
  const rows = await sql`
    UPDATE gateway.outbox
    SET failed_at = NULL, attempts = 0, next_attempt_at = now(), last_error = NULL
    WHERE failed_at IS NOT NULL
    RETURNING id`;
  return rows.length;
}
