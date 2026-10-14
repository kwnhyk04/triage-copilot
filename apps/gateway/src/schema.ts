import { sql } from 'drizzle-orm';
import {
  check,
  foreignKey,
  index,
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

const timestamptz = (name: string) => timestamp(name, { withTimezone: true });
const createdAt = () => timestamptz('created_at').notNull().defaultNow();

export const tenants = gateway.table('tenants', {
  id: uuid('id').primaryKey().defaultRandom(),
  name: text('name').notNull().unique(),
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

// One human decision per action: a second approve or reject hits the primary key.
export const approvals = gateway.table('approvals', {
  actionId: uuid('action_id')
    .primaryKey()
    .references(() => proposedActions.id),
  approver: text('approver').notNull(),
  decision: decision('decision').notNull(),
  reason: text('reason'),
  decidedAt: timestamptz('decided_at').notNull().defaultNow(),
});
