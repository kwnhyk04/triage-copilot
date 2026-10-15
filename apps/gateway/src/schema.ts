import type { ActionApprovedV1, InvestigationRequestedV2 } from '@triage/ts-clients/messages';
import { sql } from 'drizzle-orm';
import {
  bigint,
  boolean,
  check,
  foreignKey,
  index,
  integer,
  jsonb,
  pgSchema,
  primaryKey,
  text,
  timestamp,
  unique,
  uuid,
} from 'drizzle-orm/pg-core';

// Gateway tables. `pnpm db:generate` turns an edit here into a SQL migration under drizzle/.
export const gateway = pgSchema('gateway');

export const alertStatus = gateway.enum('alert_status', ['firing', 'resolved']);
export const alertSeverity = gateway.enum('alert_severity', ['critical', 'warning', 'info']);
// The API's investigation statuses, plus groups that backpressure kept from being investigated.
export const alertGroupStatus = gateway.enum('alert_group_status', [
  'queued',
  'running',
  'awaiting_approval',
  'resolved',
  'failed',
  'cancelled',
  'queued_not_investigated',
]);
export const actionKind = gateway.enum('action_kind', [
  'rollback_deploy',
  'restart_service',
  'scale_service',
  'disable_feature_flag',
]);
export const decision = gateway.enum('decision', ['approved', 'rejected']);
// The queue each outbox message goes to (INVESTIGATIONS_QUEUE or ACTIONS_QUEUE).
export const outboxQueue = gateway.enum('outbox_queue', ['investigations', 'actions']);
export const executionState = gateway.enum('execution_state', ['claimed', 'succeeded', 'failed']);

const timestamptz = (name: string) => timestamp(name, { withTimezone: true });
const createdAt = () => timestamptz('created_at').notNull().defaultNow();

export const tenants = gateway.table('tenants', {
  id: uuid('id').primaryKey().defaultRandom(),
  name: text('name').notNull().unique(),
  // Hex SHA-256 of the tenant's Alertmanager webhook token, set by `pnpm tenant:token`.
  webhookTokenSha256: text('webhook_token_sha256').unique(),
  createdAt: createdAt(),
});

export const services = gateway.table(
  'services',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    tenantId: uuid('tenant_id')
      .notNull()
      .references(() => tenants.id),
    name: text('name').notNull(),
    createdAt: createdAt(),
  },
  (t) => [unique().on(t.tenantId, t.name)],
);

// An edge from a service to one it calls. Writers keep the graph acyclic; no constraint checks it.
export const serviceDependencies = gateway.table(
  'service_dependencies',
  {
    serviceId: uuid('service_id')
      .notNull()
      .references(() => services.id, { onDelete: 'cascade' }),
    dependsOnId: uuid('depends_on_id')
      .notNull()
      .references(() => services.id, { onDelete: 'cascade' }),
  },
  (t) => [
    primaryKey({ columns: [t.serviceId, t.dependsOnId] }),
    index('service_dependencies_depends_on_id_idx').on(t.dependsOnId),
    check('service_dependencies_not_self', sql`${t.serviceId} <> ${t.dependsOnId}`),
  ],
);

export const alertGroups = gateway.table(
  'alert_groups',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    tenantId: uuid('tenant_id')
      .notNull()
      .references(() => tenants.id),
    // Alertmanager's groupKey; alerts with the same key in one grouping window share a group.
    groupKey: text('group_key').notNull(),
    windowStart: timestamptz('window_start').notNull(),
    severity: alertSeverity('severity').notNull(),
    status: alertGroupStatus('status').notNull(),
    // Set when an investigation is requested; the agent's investigation row has the same id.
    investigationId: uuid('investigation_id').unique(),
    createdAt: createdAt(),
    updatedAt: timestamptz('updated_at').notNull().defaultNow(),
    // The recovery sweeper's last re-queue of the investigation (src/sweeper.ts), and how many.
    requeuedAt: timestamptz('requeued_at'),
    requeueCount: integer('requeue_count').notNull().default(0),
  },
  (t) => [
    unique().on(t.tenantId, t.groupKey, t.windowStart),
    index('alert_groups_keyset_idx').on(
      t.tenantId,
      t.createdAt.desc().nullsFirst(),
      t.id.desc().nullsFirst(),
    ),
    check(
      'alert_groups_investigation_matches_status',
      sql`(${t.investigationId} is null) = (${t.status} = 'queued_not_investigated')`,
    ),
  ],
);

export const alerts = gateway.table(
  'alerts',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    tenantId: uuid('tenant_id')
      .notNull()
      .references(() => tenants.id),
    groupId: uuid('group_id')
      .notNull()
      .references(() => alertGroups.id),
    fingerprint: text('fingerprint').notNull(),
    labels: jsonb('labels').$type<Record<string, string>>().notNull(),
    startsAt: timestamptz('starts_at').notNull(),
    status: alertStatus('status').notNull(),
    createdAt: createdAt(),
  },
  (t) => [
    // One row per firing: webhook repeats dedupe on this key, a re-fire brings a new starts_at.
    unique().on(t.tenantId, t.fingerprint, t.startsAt),
    index('alerts_keyset_idx').on(
      t.tenantId,
      t.createdAt.desc().nullsFirst(),
      t.id.desc().nullsFirst(),
    ),
    index('alerts_group_id_idx').on(t.groupId),
  ],
);

// Copy of the agent's proposals (ActionProposed messages) for the UI; status comes from approvals.
export const proposedActions = gateway.table(
  'proposed_actions',
  {
    id: uuid('id').primaryKey(),
    tenantId: uuid('tenant_id')
      .notNull()
      .references(() => tenants.id),
    investigationId: uuid('investigation_id').notNull(),
    kind: actionKind('kind').notNull(),
    target: text('target').notNull(),
    parameters: jsonb('parameters').$type<Record<string, string | number | boolean>>().notNull(),
    rationale: text('rationale').notNull(),
    proposedAt: timestamptz('proposed_at').notNull(),
    createdAt: createdAt(),
  },
  (t) => [
    index('proposed_actions_keyset_idx').on(
      t.tenantId,
      t.createdAt.desc().nullsFirst(),
      t.id.desc().nullsFirst(),
    ),
    index('proposed_actions_investigation_id_idx').on(t.investigationId),
    // Named by hand: the generated name is over Postgres's 63-character limit.
    foreignKey({
      name: 'proposed_actions_investigation_id_fk',
      columns: [t.investigationId],
      foreignColumns: [alertGroups.investigationId],
    }),
  ],
);

// Messages committed together with the rows that caused them. The relay (src/outbox.ts) sends
// each to SQS and deletes it; a row that keeps failing gets failed_at until `pnpm outbox:replay`.
export const outbox = gateway.table(
  'outbox',
  {
    id: bigint('id', { mode: 'number' }).primaryKey().generatedAlwaysAsIdentity(),
    queue: outboxQueue('queue').notNull().default('investigations'),
    messageGroupId: text('message_group_id').notNull(),
    deduplicationId: text('deduplication_id').notNull(),
    body: jsonb('body').$type<InvestigationRequestedV2 | ActionApprovedV1>().notNull(),
    attempts: integer('attempts').notNull().default(0),
    nextAttemptAt: timestamptz('next_attempt_at').notNull().defaultNow(),
    lastError: text('last_error'),
    failedAt: timestamptz('failed_at'),
    createdAt: createdAt(),
  },
  (t) => [
    index('outbox_due_idx')
      .on(t.nextAttemptAt)
      .where(sql`${t.failedAt} is null`),
  ],
);

// One human decision per action: a second approve or reject hits the primary key.
export const approvals = gateway.table(
  'approvals',
  {
    actionId: uuid('action_id')
      .primaryKey()
      .references(() => proposedActions.id),
    approver: text('approver').notNull(),
    decision: decision('decision').notNull(),
    reason: text('reason'),
    decidedAt: timestamptz('decided_at').notNull().defaultNow(),
  },
  // The target of executions' foreign key, which accepts approved decisions only.
  (t) => [unique('approvals_action_id_decision_key').on(t.actionId, t.decision)],
);

// The executor's claim on an approved action, keyed by ActionApproved's action_id, its idempotency
// key: a second claim finds this row instead of running the action again. The foreign key and the
// check let no row exist without an approval whose decision is approved. A row left in claimed is
// never retried; the sweeper reports it for a human.
export const executions = gateway.table(
  'executions',
  {
    actionId: uuid('action_id').primaryKey(),
    decision: decision('decision').notNull().default('approved'),
    state: executionState('state').notNull().default('claimed'),
    dryRun: boolean('dry_run').notNull(),
    // What the executor reported: a dry run's plan, or why the action failed.
    detail: text('detail'),
    claimedAt: timestamptz('claimed_at').notNull().defaultNow(),
    finishedAt: timestamptz('finished_at'),
  },
  (t) => [
    foreignKey({
      name: 'executions_approval_fk',
      columns: [t.actionId, t.decision],
      foreignColumns: [approvals.actionId, approvals.decision],
    }),
    check('executions_approved_only', sql`${t.decision} = 'approved'`),
    check(
      'executions_finished_matches_state',
      sql`(${t.finishedAt} is null) = (${t.state} = 'claimed')`,
    ),
  ],
);
