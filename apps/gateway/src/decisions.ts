import type { components } from '@triage/ts-clients/gateway';
import { parseMessage } from '@triage/ts-clients/messages';
import type { Sql } from 'postgres';

import type { Decide, DecidePlan } from './app.ts';
import type { App } from './config.ts';
import { sha256 } from './intake.ts';
import { type PlanRow, toPlan } from './investigations.ts';

type ProposedAction = components['schemas']['ProposedAction'];
/** LIVE_TENANTS: each live tenant's app, which ActionApproved v2 names; a sim tenant has none. */
type LiveApps = Readonly<Partial<Record<string, App>>>;
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
export function createDecide(sql: Sql, onQueued: () => void, apps: LiveApps): Decide {
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
        const message = parseMessage('action-approved.v2', {
          schema_version: 2,
          action_id: action.id,
          investigation_id: action.investigation_id,
          tenant_id: action.tenant_id,
          app: apps[action.tenant_id] ?? null,
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

/**
 * Records a human decision on a plan in gateway.plan_decisions, whose primary key allows one per
 * plan: a second decision inserts nothing and gets already_decided. A plan without a proposal takes
 * none, and neither does one its review refused (PlanRecorded v2); the review never changes once
 * stored, and this is the only writer of plan_decisions. Only a plan a model reviewed takes an
 * approval: one that went unreviewed, or a v1 plan from before reviews, can still be rejected. An
 * approved restart (phase 3) queues ActionApproved v2 in the same transaction, as the plan's kind
 * and target alone: its rationale, the steps it writes for a human, never reaches the executor. A
 * code fix's approval queues nothing until the coder exists (phase 4).
 */
export function createDecidePlan(
  sql: Sql,
  onQueued: () => void,
  apps: LiveApps,
  approvalWindowHours: number,
): DecidePlan {
  return async (planId, verdict, { approver, reason }) => {
    const outcome = await sql.begin(async (tx) => {
      const [plan] = await tx<(PlanRow & { tenant_id: string; investigation_id: string })[]>`
        SELECT id, tenant_id, investigation_id, kind, target, files, rationale, problem, models,
               review, recorded_at, NULL AS decision, NULL AS approver, NULL AS decided_at,
               NULL AS reason
        FROM gateway.plans WHERE id = ${planId}`;
      if (plan === undefined) return 'not_found';
      if (plan.kind === null) return 'no_proposal';
      if (plan.review?.status === 'refuse') return 'refused';
      if (verdict === 'approved' && plan.review?.by !== 'model') return 'not_reviewed';
      // No conflict target, as for actions: a concurrent second decision inserts nothing.
      const [decided] = await tx<{ decided_at: Date }[]>`
        INSERT INTO gateway.plan_decisions (plan_id, approver, decision, reason)
        VALUES (${planId}, ${approver}, ${verdict}, ${reason ?? null})
        ON CONFLICT DO NOTHING
        RETURNING decided_at`;
      if (decided === undefined) return 'already_decided';
      const restart =
        verdict === 'approved' && plan.kind === 'restart_service' ? plan.target : null;
      if (restart !== null) {
        const message = parseMessage('action-approved.v2', {
          schema_version: 2,
          action_id: plan.id,
          investigation_id: plan.investigation_id,
          tenant_id: plan.tenant_id,
          app: apps[plan.tenant_id] ?? null,
          kind: 'restart_service',
          target: restart,
          parameters: {},
          approved_by: approver,
          approved_at: decided.decided_at.toISOString(),
        });
        await tx`
          INSERT INTO gateway.outbox (queue, message_group_id, deduplication_id, body)
          VALUES ('actions', ${sha256(`${plan.tenant_id}\n${restart}`)}, ${plan.id},
                  ${tx.json({ ...message })})`;
      }
      const decidedPlan = toPlan(
        {
          ...plan,
          decision: verdict,
          approver,
          decided_at: decided.decided_at,
          reason: reason ?? null,
        },
        approvalWindowHours,
      );
      return { decidedPlan, queued: restart !== null };
    });
    if (typeof outcome === 'string') return outcome;
    if (outcome.queued) onQueued();
    return outcome.decidedPlan;
  };
}
