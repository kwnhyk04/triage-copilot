CREATE SCHEMA IF NOT EXISTS "gateway";
--> statement-breakpoint
CREATE TYPE "gateway"."action_kind" AS ENUM('rollback_deploy', 'restart_service', 'scale_service', 'disable_feature_flag');--> statement-breakpoint
CREATE TYPE "gateway"."alert_group_status" AS ENUM('queued', 'running', 'awaiting_approval', 'resolved', 'failed', 'cancelled', 'queued_not_investigated');--> statement-breakpoint
CREATE TYPE "gateway"."alert_severity" AS ENUM('critical', 'warning', 'info');--> statement-breakpoint
CREATE TYPE "gateway"."alert_status" AS ENUM('firing', 'resolved');--> statement-breakpoint
CREATE TYPE "gateway"."decision" AS ENUM('approved', 'rejected');--> statement-breakpoint
CREATE TABLE "gateway"."alert_groups" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"tenant_id" uuid NOT NULL,
	"group_key" text NOT NULL,
	"window_start" timestamp with time zone NOT NULL,
	"severity" "gateway"."alert_severity" NOT NULL,
	"status" "gateway"."alert_group_status" NOT NULL,
	"investigation_id" uuid,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "alert_groups_investigation_id_unique" UNIQUE("investigation_id"),
	CONSTRAINT "alert_groups_tenant_id_group_key_window_start_unique" UNIQUE("tenant_id","group_key","window_start"),
	CONSTRAINT "alert_groups_investigation_matches_status" CHECK (("gateway"."alert_groups"."investigation_id" is null) = ("gateway"."alert_groups"."status" = 'queued_not_investigated'))
);
--> statement-breakpoint
CREATE TABLE "gateway"."alerts" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"tenant_id" uuid NOT NULL,
	"group_id" uuid NOT NULL,
	"fingerprint" text NOT NULL,
	"labels" jsonb NOT NULL,
	"starts_at" timestamp with time zone NOT NULL,
	"status" "gateway"."alert_status" NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "alerts_tenant_id_fingerprint_starts_at_unique" UNIQUE("tenant_id","fingerprint","starts_at")
);
--> statement-breakpoint
CREATE TABLE "gateway"."approvals" (
	"action_id" uuid PRIMARY KEY NOT NULL,
	"approver" text NOT NULL,
	"decision" "gateway"."decision" NOT NULL,
	"reason" text,
	"decided_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "gateway"."proposed_actions" (
	"id" uuid PRIMARY KEY NOT NULL,
	"tenant_id" uuid NOT NULL,
	"investigation_id" uuid NOT NULL,
	"kind" "gateway"."action_kind" NOT NULL,
	"target" text NOT NULL,
	"parameters" jsonb NOT NULL,
	"rationale" text NOT NULL,
	"proposed_at" timestamp with time zone NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "gateway"."service_dependencies" (
	"service_id" uuid NOT NULL,
	"depends_on_id" uuid NOT NULL,
	CONSTRAINT "service_dependencies_service_id_depends_on_id_pk" PRIMARY KEY("service_id","depends_on_id"),
	CONSTRAINT "service_dependencies_not_self" CHECK ("gateway"."service_dependencies"."service_id" <> "gateway"."service_dependencies"."depends_on_id")
);
--> statement-breakpoint
CREATE TABLE "gateway"."services" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"tenant_id" uuid NOT NULL,
	"name" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "services_tenant_id_name_unique" UNIQUE("tenant_id","name")
);
--> statement-breakpoint
CREATE TABLE "gateway"."tenants" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "tenants_name_unique" UNIQUE("name")
);
--> statement-breakpoint
ALTER TABLE "gateway"."alert_groups" ADD CONSTRAINT "alert_groups_tenant_id_tenants_id_fk" FOREIGN KEY ("tenant_id") REFERENCES "gateway"."tenants"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "gateway"."alerts" ADD CONSTRAINT "alerts_tenant_id_tenants_id_fk" FOREIGN KEY ("tenant_id") REFERENCES "gateway"."tenants"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "gateway"."alerts" ADD CONSTRAINT "alerts_group_id_alert_groups_id_fk" FOREIGN KEY ("group_id") REFERENCES "gateway"."alert_groups"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "gateway"."approvals" ADD CONSTRAINT "approvals_action_id_proposed_actions_id_fk" FOREIGN KEY ("action_id") REFERENCES "gateway"."proposed_actions"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "gateway"."proposed_actions" ADD CONSTRAINT "proposed_actions_tenant_id_tenants_id_fk" FOREIGN KEY ("tenant_id") REFERENCES "gateway"."tenants"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "gateway"."proposed_actions" ADD CONSTRAINT "proposed_actions_investigation_id_fk" FOREIGN KEY ("investigation_id") REFERENCES "gateway"."alert_groups"("investigation_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "gateway"."service_dependencies" ADD CONSTRAINT "service_dependencies_service_id_services_id_fk" FOREIGN KEY ("service_id") REFERENCES "gateway"."services"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "gateway"."service_dependencies" ADD CONSTRAINT "service_dependencies_depends_on_id_services_id_fk" FOREIGN KEY ("depends_on_id") REFERENCES "gateway"."services"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "gateway"."services" ADD CONSTRAINT "services_tenant_id_tenants_id_fk" FOREIGN KEY ("tenant_id") REFERENCES "gateway"."tenants"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "alert_groups_keyset_idx" ON "gateway"."alert_groups" USING btree ("tenant_id","created_at" DESC NULLS FIRST,"id" DESC NULLS FIRST);--> statement-breakpoint
CREATE INDEX "alerts_keyset_idx" ON "gateway"."alerts" USING btree ("tenant_id","created_at" DESC NULLS FIRST,"id" DESC NULLS FIRST);--> statement-breakpoint
CREATE INDEX "alerts_group_id_idx" ON "gateway"."alerts" USING btree ("group_id");--> statement-breakpoint
CREATE INDEX "proposed_actions_keyset_idx" ON "gateway"."proposed_actions" USING btree ("tenant_id","created_at" DESC NULLS FIRST,"id" DESC NULLS FIRST);--> statement-breakpoint
CREATE INDEX "proposed_actions_investigation_id_idx" ON "gateway"."proposed_actions" USING btree ("investigation_id");--> statement-breakpoint
CREATE INDEX "service_dependencies_depends_on_id_idx" ON "gateway"."service_dependencies" USING btree ("depends_on_id");