import { setTimeout as sleep } from 'node:timers/promises';

import { GetQueueUrlCommand, SendMessageCommand, type SQSClient } from '@aws-sdk/client-sqs';
import type { FastifyBaseLogger } from 'fastify';
import type { Sql } from 'postgres';

/** Failed sends after which a message is parked (failed_at) until `pnpm outbox:replay`. */
export const MAX_ATTEMPTS = 10;
const BATCH = 10;

export interface OutboxMessage {
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

/** Sends to the named FIFO queue, looking up its URL once. */
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
 * MAX_ATTEMPTS. SKIP LOCKED keeps two relays from sending one row. A crash between the send and
 * the delete sends it again, which SQS drops within its 5-minute deduplication window.
 */
export async function relayOnce(sql: Sql, send: Send): Promise<number> {
  return sql.begin(async (tx) => {
    const due = await tx<
      {
        id: string;
        message_group_id: string;
        deduplication_id: string;
        body: unknown;
        attempts: number;
      }[]
    >`
      SELECT id, message_group_id, deduplication_id, body, attempts FROM gateway.outbox
      WHERE failed_at IS NULL AND next_attempt_at <= now()
      ORDER BY id LIMIT ${BATCH} FOR UPDATE SKIP LOCKED`;
    for (const row of due) {
      try {
        await send({
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
