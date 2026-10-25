import type { components } from '@triage/ts-clients/gateway';
import type { ReportRecordedV2 } from '@triage/ts-clients/messages';
import { z } from '@triage/ts-config';
import type { Sql } from 'postgres';

type Schemas = components['schemas'];
/** One page, newest first; bad_cursor when the cursor is not one this gateway issued. */
export type ListInvestigations = (query: {
  limit: number;
  cursor?: string | undefined;
}) => Promise<Schemas['InvestigationPage'] | 'bad_cursor'>;
/** One investigation; undefined when no alert group has its id. */
export type GetInvestigation = (id: string) => Promise<Schemas['Investigation'] | undefined>;

interface Group {
  id: string;
  group_id: string;
  group_key: string;
  status: Schemas['InvestigationStatus'];
  verdict: Schemas['Verdict'] | null;
  created_at: Date;
  updated_at: Date;
  labels: Record<string, string> | null;
}
type Row = Group & { cursor_at: string };
// The contract's Evidence.excerpt cap: the start of the cited result.
const EXCERPT_CHARS = 2000;

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
const title = ({ labels, group_key }: Group) => {
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
      SELECT g.investigation_id AS id, g.id AS group_id, g.group_key, g.status, r.verdict,
             g.created_at, g.updated_at,
             to_char(g.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS cursor_at,
             (SELECT a.labels FROM gateway.alerts a WHERE a.group_id = g.id
              ORDER BY a.created_at, a.id LIMIT 1) AS labels
      FROM gateway.alert_groups g
      LEFT JOIN gateway.investigation_reports r ON r.investigation_id = g.investigation_id
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
      items: page.map(investigationSummary),
      next_cursor:
        rows.length > limit && last !== undefined ? encodeCursor(last.cursor_at, last.id) : null,
    };
  };

const investigationSummary = (group: Group): Schemas['InvestigationSummary'] => ({
  id: group.id,
  group_id: group.group_id,
  title: title(group),
  status: group.status,
  verdict: group.verdict,
  created_at: group.created_at.toISOString(),
  updated_at: group.updated_at.toISOString(),
});

/**
 * The investigation as its alert group, report and mirrored steps hold it. Evidence quotes the start
 * of each cited result, so a result the gateway no longer keeps (live steps go after 30 days, see
 * retention.ts) leaves an empty excerpt; the report itself stays.
 */
export const createGetInvestigation =
  (sql: Sql): GetInvestigation =>
  async (id) => {
    const [group] = await sql<
      (Group & {
        report: ReportRecordedV2['report'] | null;
        models: ReportRecordedV2['models'] | null;
      })[]
    >`
      SELECT g.investigation_id AS id, g.id AS group_id, g.group_key, g.status, r.verdict,
             g.created_at, g.updated_at, r.report, r.models,
             (SELECT a.labels FROM gateway.alerts a WHERE a.group_id = g.id
              ORDER BY a.created_at, a.id LIMIT 1) AS labels
      FROM gateway.alert_groups g
      LEFT JOIN gateway.investigation_reports r ON r.investigation_id = g.investigation_id
      WHERE g.investigation_id = ${id}`;
    if (group === undefined) return undefined;
    const steps = await sql<
      {
        seq: number;
        kind: Schemas['StepKind'];
        summary: string;
        tool_name: string | null;
        tool_call_id: string | null;
        excerpt: string | null;
        tokens: number;
        tool_status: Schemas['ToolCallStatus'] | null;
        duration_ms: number | null;
        recorded_at: Date;
      }[]
    >`
      SELECT seq, kind, summary, tool_name, tool_call_id, left(result, ${EXCERPT_CHARS}) AS excerpt,
             coalesce(tokens_input, 0) + coalesce(tokens_output, 0) AS tokens, tool_status,
             duration_ms, recorded_at
      FROM gateway.investigation_steps WHERE investigation_id = ${id} ORDER BY seq`;
    const actions = await sql<
      {
        id: string;
        investigation_id: string;
        kind: Schemas['ActionKind'];
        target: string;
        parameters: Schemas['ProposedAction']['parameters'];
        rationale: string;
        proposed_at: Date;
        decision: 'approved' | 'rejected' | null;
        approver: string | null;
        decided_at: Date | null;
        reason: string | null;
      }[]
    >`
      SELECT a.id, a.investigation_id, a.kind, a.target, a.parameters, a.rationale, a.proposed_at,
             p.decision, p.approver, p.decided_at, p.reason
      FROM gateway.proposed_actions a LEFT JOIN gateway.approvals p ON p.action_id = a.id
      WHERE a.investigation_id = ${id} ORDER BY a.proposed_at, a.id`;
    const results = new Map<string, string>();
    for (const step of steps) {
      if (step.tool_call_id !== null && step.excerpt !== null) {
        results.set(step.tool_call_id, step.excerpt);
      }
    }
    const { report } = group;
    return {
      ...investigationSummary(group),
      summary: report?.summary ?? null,
      tokens_used: steps.reduce((sum, step) => sum + step.tokens, 0),
      hypotheses: (report?.hypotheses ?? []).map((hypothesis, i) => ({
        rank: i + 1,
        root_cause_class: hypothesis.root_cause_class,
        service: hypothesis.service,
        summary: hypothesis.summary,
        confidence: hypothesis.confidence,
        evidence: hypothesis.evidence_ids.map((cited) => ({
          tool_call_id: cited,
          excerpt: results.get(cited) ?? '',
        })),
      })),
      missing_evidence: report?.missing_evidence ?? [],
      partial: report?.partial ?? false,
      models: group.models ?? [],
      proposed_actions: actions.map((action) => ({
        id: action.id,
        investigation_id: action.investigation_id,
        kind: action.kind,
        target: action.target,
        parameters: action.parameters,
        rationale: action.rationale,
        status: action.decision ?? 'pending',
        proposed_at: action.proposed_at.toISOString(),
        decided_by: action.approver,
        decided_at: action.decided_at?.toISOString() ?? null,
        decision_reason: action.reason,
      })),
      steps: steps.map((step) => ({
        seq: step.seq,
        kind: step.kind,
        summary: step.summary,
        // A call is shown once it has returned: its status and duration come from its result.
        tool_call:
          step.tool_call_id === null ||
          step.tool_name === null ||
          step.tool_status === null ||
          step.duration_ms === null
            ? null
            : {
                id: step.tool_call_id,
                name: step.tool_name,
                status: step.tool_status,
                duration_ms: step.duration_ms,
              },
        recorded_at: step.recorded_at.toISOString(),
      })),
    };
  };
