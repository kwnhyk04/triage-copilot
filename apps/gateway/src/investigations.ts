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
