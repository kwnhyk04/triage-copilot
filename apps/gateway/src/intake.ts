import { createHash } from 'node:crypto';

import type { components } from '@triage/ts-clients/gateway';
import { parseMessage } from '@triage/ts-clients/messages';
import { z } from '@triage/ts-config';
import type { Sql } from 'postgres';

/** Most alerts one webhook may carry; Alertmanager's max_alerts should match. */
export const MAX_ALERTS = 200;

const labels = z.record(z.string(), z.string());
const status = z.enum(['firing', 'resolved']);

/** Alertmanager webhook payload v4 (contract AlertmanagerWebhook); unknown members are dropped. */
export const webhookSchema = z.object({
  version: z.literal('4'),
  groupKey: z.string().min(1),
  status,
  receiver: z.string(),
  groupLabels: labels,
  commonLabels: labels,
  commonAnnotations: labels,
  externalURL: z.string(),
  alerts: z
    .array(
      z.object({
        status,
        labels,
        annotations: labels,
        startsAt: z.iso.datetime({ offset: true }),
        endsAt: z.string(),
        generatorURL: z.string(),
        fingerprint: z.string().min(1),
      }),
    )
    .min(1)
    .max(MAX_ALERTS),
});

export type Webhook = z.infer<typeof webhookSchema>;
export type AlertAccepted = components['schemas']['AlertAccepted'];
/** Stores one tenant's webhook payload. */
export type Intake = (tenantId: string, webhook: Webhook) => Promise<AlertAccepted>;
/** The tenant whose webhook token has this SHA-256, if any. */
export type FindTenant = (tokenSha256: string) => Promise<string | undefined>;

export const sha256 = (value: string) => createHash('sha256').update(value).digest('hex');

const SEVERITIES = ['critical', 'warning', 'info'] as const;

const one = <T>(rows: readonly T[]): T => {
  const [row] = rows;
  if (row === undefined) throw new Error('the query returned no row');
  return row;
};

export const createFindTenant =
  (sql: Sql): FindTenant =>
  async (tokenSha256) => {
    const [tenant] = await sql<{ id: string }[]>`
      SELECT id FROM gateway.tenants WHERE webhook_token_sha256 = ${tokenSha256}`;
    return tenant?.id;
  };

/**
 * Stores each firing once, keyed by (tenant, fingerprint, startsAt), and records a known alert's
 * new status. New alerts join the group of their groupKey whose 5-minute window opened last, or
 * open a group. A new group gets an investigation when it has a firing alert and the tenant opened
 * fewer than `maxNewInvestigations` in the last 5 minutes; otherwise it stays
 * queued_not_investigated, so a storm over many group keys starts only a few. The investigation's
 * InvestigationRequested message goes to the outbox in the same transaction; `onQueued` runs after
 * that commit to wake the relay.
 */
export function createIntake(sql: Sql, onQueued: () => void, maxNewInvestigations: number): Intake {
  return async (tenantId, { groupKey, alerts }) => {
    // A jsonb parameter, so postgres.js serializes it once.
    const rows = sql.json(
      alerts.map((alert) => ({
        fingerprint: alert.fingerprint,
        labels: alert.labels,
        starts_at: alert.startsAt,
        status: alert.status,
      })),
    );
    const severity = SEVERITIES.find((s) => alerts.some((a) => a.labels.severity === s));
    const groupHash = sha256(`${tenantId}\n${groupKey}`);
    const outcome = await sql.begin(async (tx) => {
      const seen = one(
        await tx<
          { fresh: number; fresh_firing: number; changed: number; known_group: string | null }[]
        >`
          WITH incoming AS (
            SELECT DISTINCT ON (fingerprint, starts_at) fingerprint, starts_at, status
            FROM jsonb_to_recordset(${rows})
              AS i(fingerprint text, starts_at timestamptz, status gateway.alert_status)
          ), changed AS (
            UPDATE gateway.alerts a SET status = i.status FROM incoming i
            WHERE a.tenant_id = ${tenantId} AND a.fingerprint = i.fingerprint
              AND a.starts_at = i.starts_at AND a.status <> i.status
            RETURNING a.id
          ), known AS (
            SELECT a.group_id, i.fingerprint, i.starts_at FROM gateway.alerts a JOIN incoming i
              ON a.fingerprint = i.fingerprint AND a.starts_at = i.starts_at
            WHERE a.tenant_id = ${tenantId}
          ), fresh AS (
            SELECT status FROM incoming i WHERE NOT EXISTS (
              SELECT FROM known k WHERE k.fingerprint = i.fingerprint AND k.starts_at = i.starts_at)
          )
          SELECT (SELECT count(*) FROM fresh)::int AS fresh,
                 (SELECT count(*) FROM fresh WHERE status = 'firing')::int AS fresh_firing,
                 (SELECT count(*) FROM changed)::int AS changed,
                 (SELECT group_id FROM known LIMIT 1) AS known_group`,
      );
      if (seen.fresh === 0) {
        return { group_id: seen.known_group ?? '', deduped: seen.changed === 0, queued: false };
      }

      const openGroup = () => tx<{ id: string }[]>`
        SELECT id FROM gateway.alert_groups
        WHERE tenant_id = ${tenantId} AND group_key = ${groupKey}
          AND window_start > now() - interval '5 minutes'
        ORDER BY window_start DESC LIMIT 1`;
      let [group] = await openGroup();
      if (group === undefined) {
        // Only opening a window takes this lock, so a tenant opens one group at a time: no two
        // requests open the same window, and the count of its new investigations stays exact.
        await tx`SELECT pg_advisory_xact_lock(hashtextextended(${`tenant\n${tenantId}`}, 0))`;
        [group] = await openGroup();
      }
      let queued = false;
      if (group === undefined) {
        const recent = one(
          await tx<{ count: number }[]>`
            SELECT count(*)::int AS count FROM gateway.alert_groups
            WHERE tenant_id = ${tenantId} AND investigation_id IS NOT NULL
              AND window_start > now() - interval '5 minutes'`,
        );
        const investigate = seen.fresh_firing > 0 && recent.count < maxNewInvestigations;
        const created = one(
          await tx<{ id: string; investigation_id: string | null; window_start: Date }[]>`
            INSERT INTO gateway.alert_groups
              (tenant_id, group_key, window_start, severity, status, investigation_id)
            VALUES (${tenantId}, ${groupKey}, now(), ${severity ?? 'warning'},
                    ${investigate ? 'queued' : 'queued_not_investigated'},
                    ${investigate ? tx`gen_random_uuid()` : null})
            RETURNING id, investigation_id, window_start`,
        );
        if (created.investigation_id !== null) {
          const windowStart = created.window_start.toISOString();
          const message = parseMessage('investigation-requested.v2', {
            schema_version: 2,
            investigation_id: created.investigation_id,
            tenant_id: tenantId,
            group_key: groupKey,
            requested_at: windowStart,
          });
          await tx`
            INSERT INTO gateway.outbox (message_group_id, deduplication_id, body)
            VALUES (${groupHash}, ${sha256(`${tenantId}\n${groupKey}\n${windowStart}`)},
                    ${tx.json({ ...message })})`;
          queued = true;
        }
        group = created;
      }
      await tx`
        INSERT INTO gateway.alerts (tenant_id, group_id, fingerprint, labels, starts_at, status)
        SELECT ${tenantId}, ${group.id}, fingerprint, labels, starts_at, status
        FROM jsonb_to_recordset(${rows})
          AS i(fingerprint text, labels jsonb, starts_at timestamptz, status gateway.alert_status)
        ON CONFLICT DO NOTHING`;
      return { group_id: group.id, deduped: false, queued };
    });
    if (outcome.queued) onQueued();
    return { group_id: outcome.group_id, deduped: outcome.deduped };
  };
}
