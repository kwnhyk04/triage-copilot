import { setTimeout as sleep } from 'node:timers/promises';

import type { FastifyBaseLogger } from 'fastify';
import type { Sql } from 'postgres';

/** Days live data is kept (docs/memory.md, 2026-10-24); the agent purges its own copy too. */
export const RETENTION_DAYS = 30;
const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;

/**
 * Purges what live tenants stored over `days` ago: the watch snapshot in each alert's annotations
 * (its summary and description stay) and the mirrored steps, tool results included. Reports, their
 * verdicts and models stay, so every live report can still be read. Returns the rows it changed.
 */
export async function purgeLive(sql: Sql, tenants: readonly string[], days = RETENTION_DAYS) {
  if (tenants.length === 0) return { snapshots: 0, steps: 0 };
  return sql.begin(async (tx) => {
    const snapshots = await tx`
      UPDATE gateway.alerts SET annotations = annotations - 'snapshot'
      WHERE tenant_id = ANY(${tenants}::uuid[]) AND annotations ? 'snapshot'
        AND created_at < now() - make_interval(days => ${days})`;
    const steps = await tx`
      DELETE FROM gateway.investigation_steps s USING gateway.alert_groups g
      WHERE g.investigation_id = s.investigation_id AND g.tenant_id = ANY(${tenants}::uuid[])
        AND s.created_at < now() - make_interval(days => ${days})`;
    return { snapshots: snapshots.count, steps: steps.count };
  });
}

/** Purges at once, then daily (an hour after a failure) until stopped. */
export function startRetention(sql: Sql, tenants: readonly string[], log: FastifyBaseLogger) {
  const stopped = new AbortController();
  const loop = (async () => {
    while (!stopped.signal.aborted) {
      let wait = DAY_MS;
      try {
        const purged = await purgeLive(sql, tenants);
        if (purged.snapshots + purged.steps > 0) log.info(purged, 'live data purged');
      } catch (error) {
        log.error({ err: error }, 'live data purge failed; retrying in an hour');
        wait = HOUR_MS;
      }
      await sleep(wait, undefined, { signal: stopped.signal }).catch(() => undefined);
    }
  })();
  return {
    stop: async () => {
      stopped.abort();
      await loop;
    },
  };
}
