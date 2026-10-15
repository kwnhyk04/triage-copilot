import type { components } from '@triage/ts-clients/gateway';
import { parseMessage } from '@triage/ts-clients/messages';
import type { Sql } from 'postgres';

import type { Decide } from './app.ts';
import { sha256 } from './intake.ts';

type ProposedAction = components['schemas']['ProposedAction'];
interface ActionRow {
  id: string;
  tenant_id: string;
  investigation_id: string;
  kind: ProposedAction['kind'];
  target: string;
  parameters: ProposedAction['parameters'];
  rationale: string;
  proposed_at: Date;
}

/**
 * Records a human decision in gateway.approvals, whose primary key allows one per action: a second
 * decision inserts nothing and gets already_decided. An approval queues ActionApproved through the
 * outbox in the same transaction, in one FIFO group per tenant and target, so one service's
 * actions run in order; the action id is its deduplication id. `onQueued` wakes the relay after
 * the commit.
 */
export function createDecide(sql: Sql, onQueued: () => void): Decide {
  return async (actionId, verdict, { approver, reason }) => {
    const outcome = await sql.begin(async (tx) => {
      const [action] = await tx<ActionRow[]>`
        SELECT id, tenant_id, investigation_id, kind, target, parameters, rationale, proposed_at
        FROM gateway.proposed_actions WHERE id = ${actionId}`;
      if (action === undefined) return 'not_found';
      // No conflict target: every unique index arbitrates, so a concurrent second decision waits
      // for the first and then inserts nothing instead of failing on approvals_action_id_decision_key.
      const [decided] = await tx<{ decided_at: Date }[]>`
        INSERT INTO gateway.approvals (action_id, approver, decision, reason)
        VALUES (${actionId}, ${approver}, ${verdict}, ${reason ?? null})
        ON CONFLICT DO NOTHING
        RETURNING decided_at`;
      if (decided === undefined) return 'already_decided';
      if (verdict === 'approved') {
        const message = parseMessage('action-approved.v1', {
          schema_version: 1,
          action_id: action.id,
          investigation_id: action.investigation_id,
          kind: action.kind,
          target: action.target,
          parameters: action.parameters,
          approved_by: approver,
          approved_at: decided.decided_at.toISOString(),
        });
        await tx`
          INSERT INTO gateway.outbox (queue, message_group_id, deduplication_id, body)
          VALUES ('actions', ${sha256(`${action.tenant_id}\n${action.target}`)}, ${action.id},
                  ${tx.json({ ...message })})`;
      }
      return { action, decidedAt: decided.decided_at };
    });
    if (typeof outcome === 'string') return outcome;
    if (verdict === 'approved') onQueued();
    const { action, decidedAt } = outcome;
    return {
      id: action.id,
      investigation_id: action.investigation_id,
      kind: action.kind,
      target: action.target,
      parameters: action.parameters,
      rationale: action.rationale,
      status: verdict,
      proposed_at: action.proposed_at.toISOString(),
      decided_by: approver,
      decided_at: decidedAt.toISOString(),
      decision_reason: reason ?? null,
    };
  };
}
