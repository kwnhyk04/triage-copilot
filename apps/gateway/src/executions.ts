import { createAgentClient } from '@triage/ts-clients/agent';
import type { components } from '@triage/ts-clients/gateway';
import type { Sql, TransactionSql } from 'postgres';

type Schemas = components['schemas'];
export type Execution = Schemas['Execution'];
/**
 * Claims an approved action for the executor: a proposed action, or a restart plan a human
 * approved, whose id ActionApproved v2 carries as its action id. `created` is false when it was
 * claimed before.
 */
export type Claim = (
  actionId: string,
  dryRun: boolean,
) => Promise<{ created: boolean; execution: Execution } | 'not_found'>;
/** Records how a claimed execution ended. */
export type Report = (
  actionId: string,
  result: Schemas['ExecutionResult'],
) => Promise<Execution | 'not_found' | 'already_finished'>;
/** Whether the agent's actions switch is on; throws when the agent does not say. */
export type ActionsOn = () => Promise<boolean>;

interface Row {
  action_id: string;
  kind: Execution['kind'];
  target: string;
  parameters: Execution['parameters'];
  approved_by: string;
  approved_at: Date;
  state: Execution['state'];
  dry_run: boolean;
  detail: string | null;
  claimed_at: Date;
  finished_at: Date | null;
}

/** The execution with the action as approved: a proposed action's, or a restart plan's, which has
 * no parameters and whose rationale never reaches the executor. */
async function read(tx: TransactionSql, actionId: string): Promise<Execution | undefined> {
  const [row] = await tx<Row[]>`
    SELECT e.action_id, p.kind::text AS kind, p.target, p.parameters, a.approver AS approved_by,
           a.decided_at AS approved_at, e.state, e.dry_run, e.detail, e.claimed_at, e.finished_at
    FROM gateway.executions e
    JOIN gateway.approvals a ON a.action_id = e.action_id
    JOIN gateway.proposed_actions p ON p.id = e.action_id
    WHERE e.action_id = ${actionId}
    UNION ALL
    SELECT x.plan_id, p.kind::text, p.target, '{}'::jsonb, d.approver, d.decided_at, x.state,
           x.dry_run, x.detail, x.claimed_at, x.finished_at
    FROM gateway.plan_executions x
    JOIN gateway.plan_decisions d ON d.plan_id = x.plan_id
    JOIN gateway.plans p ON p.id = x.plan_id
    WHERE x.plan_id = ${actionId}`;
  return (
    row && {
      ...row,
      approved_at: row.approved_at.toISOString(),
      claimed_at: row.claimed_at.toISOString(),
      finished_at: row.finished_at?.toISOString() ?? null,
    }
  );
}

/**
 * Only an approval whose decision is approved yields a row: of a proposed action, or of a plan
 * whose kind is restart_service (phase 3 runs no other). The primary keys turn a second claim into
 * a no-op that reads the first, and a concurrent second claim waits for the first to commit, so
 * exactly one caller gets `created`.
 */
export const createClaim =
  (sql: Sql): Claim =>
  (actionId, dryRun) =>
    sql.begin(async (tx) => {
      const action = await tx`
        INSERT INTO gateway.executions (action_id, dry_run)
        SELECT action_id, ${dryRun} FROM gateway.approvals
        WHERE action_id = ${actionId} AND decision = 'approved'
        ON CONFLICT (action_id) DO NOTHING
        RETURNING action_id`;
      const plan = await tx`
        INSERT INTO gateway.plan_executions (plan_id, dry_run)
        SELECT d.plan_id, ${dryRun} FROM gateway.plan_decisions d
        JOIN gateway.plans p ON p.id = d.plan_id
        WHERE d.plan_id = ${actionId} AND d.decision = 'approved' AND p.kind = 'restart_service'
        ON CONFLICT (plan_id) DO NOTHING
        RETURNING plan_id`;
      const execution = await read(tx, actionId);
      if (execution === undefined) return 'not_found';
      return { created: action.length + plan.length > 0, execution };
    });

/** Only a claimed execution takes a result, once; a finished one is never reopened. */
export const createReport =
  (sql: Sql): Report =>
  (actionId, { state, detail }) =>
    sql.begin(async (tx) => {
      const action = await tx`
        UPDATE gateway.executions SET state = ${state}, detail = ${detail ?? null}, finished_at = now()
        WHERE action_id = ${actionId} AND state = 'claimed'
        RETURNING action_id`;
      const plan = await tx`
        UPDATE gateway.plan_executions
        SET state = ${state}, detail = ${detail ?? null}, finished_at = now()
        WHERE plan_id = ${actionId} AND state = 'claimed'
        RETURNING plan_id`;
      const execution = await read(tx, actionId);
      if (execution === undefined) return 'not_found';
      return action.length + plan.length > 0 ? execution : 'already_finished';
    });

/** Executions claimed over `minutes` ago without a result: never retried, so a human must look. */
export async function stuckExecutions(sql: Sql, minutes: number): Promise<string[]> {
  const rows = await sql<{ action_id: string }[]>`
    SELECT action_id FROM (
      SELECT action_id, claimed_at FROM gateway.executions WHERE state = 'claimed'
      UNION ALL
      SELECT plan_id, claimed_at FROM gateway.plan_executions WHERE state = 'claimed'
    ) claims
    WHERE claimed_at < now() - make_interval(mins => ${minutes})
    ORDER BY claimed_at`;
  return rows.map((row) => row.action_id);
}

/** The agent's kill switch `name` (GET /internal/switches/{name}), asked with the service token. */
export function agentSwitch(baseUrl: string, serviceToken: string, name: 'actions'): ActionsOn {
  const agent = createAgentClient({ baseUrl, serviceToken, timeoutMs: 3000 });
  return async () => {
    const { data, response } = await agent.GET('/internal/switches/{name}', {
      params: { path: { name } },
    });
    if (data === undefined)
      throw new Error(`agent switch ${name}: HTTP ${String(response.status)}`);
    return data.on;
  };
}
