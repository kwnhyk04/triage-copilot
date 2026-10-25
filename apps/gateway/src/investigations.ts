import type { components } from '@triage/ts-clients/gateway';
import { z } from '@triage/ts-config';
import type { Sql } from 'postgres';

type Schemas = components['schemas'];
/** One page, newest first; bad_cursor when the cursor is not one this gateway issued. */
export type ListInvestigations = (query: {
  limit: number;
  cursor?: string | undefined;
}) => Promise<Schemas['InvestigationPage'] | 'bad_cursor'>;

interface Row {
  id: string;
  group_id: string;
  group_key: string;
  status: Schemas['InvestigationStatus'];
  created_at: Date;
  updated_at: Date;
  cursor_at: string;
  labels: Record<string, string> | null;
}

// A position is (created_at, id). created_at travels as text with its microseconds: a JS Date keeps
// only milliseconds, so rows created within one millisecond would be skipped or repeated.
// A real instant: Date rolls February 31 over into March (or gives up), so its round trip differs.
const isInstant = (at: string) => {
  const date = new Date(`${at.slice(0, 23)}Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().startsWith(at.slice(0, 23));
};
const position = z.tuple([
  z
    .string()
    .regex(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/)
    .refine(isInstant),
  z.guid(),
]);

export const encodeCursor = (createdAt: string, id: string) =>
  Buffer.from(JSON.stringify([createdAt, id])).toString('base64url');

export function decodeCursor(cursor: string): [string, string] | undefined {
  try {
    const parsed = position.safeParse(JSON.parse(Buffer.from(cursor, 'base64url').toString()));
    return parsed.success ? parsed.data : undefined;
  } catch {
    return undefined;
  }
}

// The first alert's name and service, as people say it; the group key when it has no alertname.
const title = ({ labels, group_key }: Row) => {
  const name = labels?.alertname;
  if (name === undefined) return group_key;
  const service = labels?.service;
  return service === undefined ? name : `${name} on ${service}`;
};

/** An investigation's alert group for the agent's get_alert_context; undefined when unknown. */
export type GetAlertContext = (
  investigationId: string,
) => Promise<Schemas['AlertContext'] | undefined>;

/** The group and its 200 oldest alerts, with how many it holds in all. */
export const createAlertContext =
  (sql: Sql): GetAlertContext =>
  async (investigationId) => {
    const [group] = await sql<
      {
        id: string;
        tenant_id: string;
        group_key: string;
        window_start: Date;
        severity: Schemas['AlertContext']['severity'];
        alert_count: number;
      }[]
    >`
      SELECT id, tenant_id, group_key, window_start, severity,
             (SELECT count(*)::int FROM gateway.alerts a WHERE a.group_id = g.id) AS alert_count
      FROM gateway.alert_groups g WHERE investigation_id = ${investigationId}`;
    if (group === undefined) return undefined;
    const alerts = await sql<
      {
        fingerprint: string;
        labels: Record<string, string>;
        annotations: Record<string, string> | null;
        starts_at: Date;
        status: Schemas['AlertStatus'];
      }[]
    >`
      SELECT fingerprint, labels, annotations, starts_at, status FROM gateway.alerts
      WHERE group_id = ${group.id}
      ORDER BY starts_at, created_at, id LIMIT 200`;
    return {
      investigation_id: investigationId,
      group_id: group.id,
      tenant_id: group.tenant_id,
      group_key: group.group_key,
      window_start: group.window_start.toISOString(),
      severity: group.severity,
      alert_count: group.alert_count,
      // Rows stored before 0005 have no annotations, and their alerts leave the field out.
      alerts: alerts.map(({ annotations, ...alert }) => ({
        ...alert,
        ...(annotations === null ? {} : { annotations }),
        starts_at: alert.starts_at.toISOString(),
      })),
    };
  };

/** The groups a UTC day kept from investigation, per tenant, with why and what they hold. */
export interface Digest {
  day: string;
  tenants: {
    tenant: string;
    investigated: number;
    not_investigated: {
      group_id: string;
      group_key: string;
      window_start: string;
      reason: string | null;
      alerts: number;
      names: string[];
    }[];
  }[];
}

/** A day's digest: every tenant with an alert group that day, oldest group first. */
export const createDigest =
  (sql: Sql) =>
  async (day: string): Promise<Digest> => {
    const rows = await sql<
      {
        tenant: string;
        group_id: string;
        group_key: string;
        window_start: Date;
        investigated: boolean;
        reason: string | null;
        alerts: number;
        names: string[];
      }[]
    >`
      SELECT t.name AS tenant, g.id AS group_id, g.group_key, g.window_start,
             g.investigation_id IS NOT NULL AS investigated, g.not_investigated::text AS reason,
             (SELECT count(*)::int FROM gateway.alerts a WHERE a.group_id = g.id) AS alerts,
             (SELECT coalesce(array_agg(DISTINCT concat_ws(' on ', a.labels->>'alertname',
                                                          a.labels->>'service')), '{}')
              FROM gateway.alerts a WHERE a.group_id = g.id) AS names
      FROM gateway.alert_groups g JOIN gateway.tenants t ON t.id = g.tenant_id
      WHERE g.created_at >= ${day}::date AT TIME ZONE 'UTC'
        AND g.created_at < (${day}::date + 1) AT TIME ZONE 'UTC'
      ORDER BY t.name, g.created_at, g.id`;
    const tenants = new Map<string, Digest['tenants'][number]>();
    for (const { tenant, investigated, window_start, ...group } of rows) {
      const entry = tenants.get(tenant) ?? { tenant, investigated: 0, not_investigated: [] };
      tenants.set(tenant, entry);
      if (investigated) entry.investigated++;
      else entry.not_investigated.push({ ...group, window_start: window_start.toISOString() });
    }
    return { day, tenants: [...tenants.values()] };
  };

/** Keyset pagination over investigated alert groups, ordered by (created_at, id) descending. */
export const createListInvestigations =
  (sql: Sql): ListInvestigations =>
  async ({ limit, cursor }) => {
    const after = cursor === undefined ? undefined : decodeCursor(cursor);
    if (cursor !== undefined && after === undefined) return 'bad_cursor';
    // ponytail: sorts every tenant's investigated groups; index (created_at, investigation_id)
    // past ~100k groups.
    const rows = await sql<Row[]>`
      SELECT g.investigation_id AS id, g.id AS group_id, g.group_key, g.status, g.created_at,
             g.updated_at,
             to_char(g.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS cursor_at,
             (SELECT a.labels FROM gateway.alerts a WHERE a.group_id = g.id
              ORDER BY a.created_at, a.id LIMIT 1) AS labels
      FROM gateway.alert_groups g
      WHERE g.investigation_id IS NOT NULL ${
        after === undefined
          ? sql``
          : // A text parameter: postgres.js turns a timestamptz one into a JS Date, which drops
            // the microseconds and skips the rest of a millisecond.
            sql`AND (g.created_at, g.investigation_id)
                    < (${after[0]}::text::timestamptz, ${after[1]}::uuid)`
      }
      ORDER BY g.created_at DESC, g.investigation_id DESC
      LIMIT ${limit + 1}`;
    const page = rows.slice(0, limit);
    const last = page.at(-1);
    return {
      items: page.map((row) => ({
        id: row.id,
        group_id: row.group_id,
        title: title(row),
        status: row.status,
        created_at: row.created_at.toISOString(),
        updated_at: row.updated_at.toISOString(),
      })),
      next_cursor:
        rows.length > limit && last !== undefined ? encodeCursor(last.cursor_at, last.id) : null,
    };
  };
