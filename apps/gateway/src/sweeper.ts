import { setTimeout as sleep } from 'node:timers/promises';

import { type components, createAgentClient } from '@triage/ts-clients/agent';
import type { FastifyBaseLogger } from 'fastify';
import type { Sql } from 'postgres';

import { investigationRequest } from './intake.ts';

/** The agent's checkpoint of an investigation (its internal API's InvestigationState). */
export type State = components['schemas']['InvestigationState'];
/** Asks the agent about an investigation; undefined when the agent never received it. */
export type GetState = (investigationId: string) => Promise<State | undefined>;
export interface Sweeper {
  stop: () => Promise<void>;
}

/** A queued group as the sweep reads it, with the database's clock. */
export interface QueuedGroup {
  id: string;
  tenant_id: string;
  group_key: string;
  window_start: Date;
  investigation_id: string;
  created_at: Date;
  requeued_at: Date | null;
  requeue_count: number;
  now: Date;
}

// The group takes one of these statuses and leaves the sweep.
const FINISHED: readonly State['status'][] = ['awaiting_approval', 'resolved', 'cancelled'];

/** Reads states through the agent's internal API with the service token, 3 s per call. */
export function agentStates(baseUrl: string, serviceToken: string): GetState {
  const agent = createAgentClient({ baseUrl, serviceToken, timeoutMs: 3000 });
  return async (id) => {
    const { data, response } = await agent.GET('/internal/investigations/{id}/state', {
      params: { path: { id } },
    });
    if (response.status === 404) return undefined;
    if (data === undefined) throw new Error(`agent state: HTTP ${String(response.status)}`);
    return data;
  };
}

/**
 * What the sweep does with a queued group: re-queue its investigation, record that it finished,
 * or leave it. A parked investigation goes back once the agent reports it resumable. Stuck ones go
 * back too: unknown to the agent, or in progress without a step, for longer than stuckAfterMs.
 */
export function decide(
  group: QueuedGroup,
  state: State | undefined,
  stuckAfterMs: number,
): 'requeue' | 'finished' | 'none' {
  const age = (at: Date | string) => group.now.getTime() - new Date(at).getTime();
  const stale = (at: Date | string) => age(at) > stuckAfterMs;
  const requeued = group.requeued_at;
  // Never received: the message was lost on its way.
  if (state === undefined) return stale(requeued ?? group.created_at) ? 'requeue' : 'none';
  if (FINISHED.includes(state.status)) return 'finished';
  // Its message is in the DLQ, which `make dlq-replay` owns.
  if (state.status === 'failed') return 'none';
  if (state.waiting_for !== null) {
    // Re-queued since it parked: that message is on its way, unless it was sent long ago.
    const parkedAt = new Date(state.waiting_since ?? 0).getTime();
    const onItsWay = requeued !== null && requeued.getTime() > parkedAt && !stale(requeued);
    return state.resumable && !onItsWay ? 'requeue' : 'none';
  }
  return stale(state.updated_at) && (requeued === null || stale(requeued)) ? 'requeue' : 'none';
}

/**
 * One pass over the queued groups; returns how many investigations it re-queued through the
 * outbox. A group whose investigation finished takes its status, so the sweep stays as small as
 * the work in progress. SKIP LOCKED keeps two gateways from sweeping one group, and a group with
 * an outbox row is on its way already.
 */
export async function sweepOnce(
  sql: Sql,
  getState: GetState,
  stuckAfterMinutes: number,
  log: FastifyBaseLogger,
): Promise<number> {
  return sql.begin(async (tx) => {
    // ponytail: one agent call per queued group per sweep; batch them past a few thousand.
    const groups = await tx<QueuedGroup[]>`
      SELECT id, tenant_id, group_key, window_start, investigation_id, created_at, requeued_at,
             requeue_count, now() AS now
      FROM gateway.alert_groups g
      WHERE status = 'queued' AND NOT EXISTS (
        SELECT FROM gateway.outbox o WHERE o.body->>'investigation_id' = g.investigation_id::text)
      ORDER BY created_at
      FOR NO KEY UPDATE SKIP LOCKED`;
    let requeued = 0;
    let unreachable: { error: unknown; groups: number } | undefined;
    for (const group of groups) {
      let state: State | undefined;
      try {
        state = await getState(group.investigation_id);
      } catch (error) {
        unreachable = { error, groups: (unreachable?.groups ?? 0) + 1 };
        continue;
      }
      const action = decide(group, state, stuckAfterMinutes * 60_000);
      if (action === 'finished' && state !== undefined) {
        await tx`
          UPDATE gateway.alert_groups SET status = ${state.status}, updated_at = now()
          WHERE id = ${group.id}`;
      } else if (action === 'requeue') {
        const count = group.requeue_count + 1;
        const message = investigationRequest(
          {
            tenantId: group.tenant_id,
            groupKey: group.group_key,
            windowStart: group.window_start,
            investigationId: group.investigation_id,
          },
          count,
        );
        await tx`
          INSERT INTO gateway.outbox (message_group_id, deduplication_id, body)
          VALUES (${message.groupId}, ${message.deduplicationId}, ${tx.json({ ...message.body })})`;
        // The clock, not the transaction start: compared with when the agent parks it next.
        await tx`
          UPDATE gateway.alert_groups
          SET requeued_at = clock_timestamp(), requeue_count = ${count}, updated_at = now()
          WHERE id = ${group.id}`;
        requeued++;
      }
    }
    if (unreachable !== undefined) {
      const { error, groups: count } = unreachable;
      log.warn({ err: error, groups: count }, 'agent state unavailable; those groups wait');
    }
    return requeued;
  });
}

/** Sweeps every intervalMs until stopped; `onQueued` wakes the relay after re-queues commit. */
export function startSweeper(
  sql: Sql,
  getState: GetState,
  { stuckAfterMinutes, intervalMs }: { stuckAfterMinutes: number; intervalMs: number },
  log: FastifyBaseLogger,
  onQueued: () => void,
): Sweeper {
  const stopped = new AbortController();
  const loop = (async () => {
    while (!stopped.signal.aborted) {
      try {
        if ((await sweepOnce(sql, getState, stuckAfterMinutes, log)) > 0) onQueued();
      } catch (error) {
        log.error({ err: error }, 'recovery sweep failed');
      }
      await sleep(intervalMs, undefined, { signal: stopped.signal }).catch(() => undefined);
    }
  })();
  return {
    stop: async () => {
      stopped.abort();
      await loop;
    },
  };
}
