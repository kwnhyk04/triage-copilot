import type { components } from '@triage/ts-clients/gateway';
import type { Sql, TransactionSql } from 'postgres';

type Schemas = components['schemas'];
export type Execution = Schemas['Execution'];
/** Claims an approved action for the executor; `created` is false when it was claimed before. */
export type Claim = (
  actionId: string,
  dryRun: boolean,
) => Promise<{ created: boolean; execution: Execution } | 'not_found'>;
/** Records how a claimed execution ended. */
export type Report = (
  actionId: string,
  result: Schemas['ExecutionResult'],
) => Promise<Execution | 'not_found' | 'already_finished'>;

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

/** The execution with the action as approved. */
async function read(tx: TransactionSql, actionId: string): Promise<Execution | undefined> {
  const [row] = await tx<Row[]>`
    SELECT e.action_id, p.kind, p.target, p.parameters, a.approver AS approved_by,
           a.decided_at AS approved_at, e.state, e.dry_run, e.detail, e.claimed_at, e.finished_at
    FROM gateway.executions e
    JOIN gateway.approvals a ON a.action_id = e.action_id
    JOIN gateway.proposed_actions p ON p.id = e.action_id
    WHERE e.action_id = ${actionId}`;
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
 * Only an approval whose decision is approved yields a row, and the primary key turns a second
 * claim into a no-op that reads the first claim. A concurrent second claim waits for the first to
 * commit, so exactly one caller gets `created`.
 */
export const createClaim =
  (sql: Sql): Claim =>
  (actionId, dryRun) =>
    sql.begin(async (tx) => {
      const inserted = await tx`
        INSERT INTO gateway.executions (action_id, dry_run)
        SELECT action_id, ${dryRun} FROM gateway.approvals
        WHERE action_id = ${actionId} AND decision = 'approved'
        ON CONFLICT (action_id) DO NOTHING
        RETURNING action_id`;
      const execution = await read(tx, actionId);
      return execution === undefined ? 'not_found' : { created: inserted.length > 0, execution };
    });

/** Only a claimed execution takes a result, once; a finished one is never reopened. */
export const createReport =
  (sql: Sql): Report =>
  (actionId, { state, detail }) =>
    sql.begin(async (tx) => {
      const updated = await tx`
        UPDATE gateway.executions SET state = ${state}, detail = ${detail ?? null}, finished_at = now()
        WHERE action_id = ${actionId} AND state = 'claimed'
        RETURNING action_id`;
      const execution = await read(tx, actionId);
      if (execution === undefined) return 'not_found';
      return updated.length > 0 ? execution : 'already_finished';
    });

/** Executions claimed over `minutes` ago without a result: never retried, so a human must look. */
export async function stuckExecutions(sql: Sql, minutes: number): Promise<string[]> {
  const rows = await sql<{ action_id: string }[]>`
    SELECT action_id FROM gateway.executions
    WHERE state = 'claimed' AND claimed_at < now() - make_interval(mins => ${minutes})
    ORDER BY claimed_at`;
  return rows.map((row) => row.action_id);
}
