CREATE TYPE "gateway"."plan_kind" AS ENUM('none', 'restart_service', 'code_fix');--> statement-breakpoint
CREATE TABLE "gateway"."plan_decisions" (
	"plan_id" uuid PRIMARY KEY NOT NULL,
	"approver" text NOT NULL,
	"decision" "gateway"."decision" NOT NULL,
	"reason" text,
	"decided_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "gateway"."plans" (
	"id" uuid PRIMARY KEY NOT NULL,
	"tenant_id" uuid NOT NULL,
	"investigation_id" uuid NOT NULL,
	"kind" "gateway"."plan_kind",
	"target" text,
	"files" jsonb NOT NULL,
	"rationale" text,
	"problem" text,
	"models" jsonb NOT NULL,
	"recorded_at" timestamp with time zone NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "plans_investigation_id_unique" UNIQUE("investigation_id"),
	CONSTRAINT "plans_proposal_or_problem" CHECK (("gateway"."plans"."kind" is null) = ("gateway"."plans"."problem" is not null))
);
--> statement-breakpoint
ALTER TABLE "gateway"."plan_decisions" ADD CONSTRAINT "plan_decisions_plan_id_plans_id_fk" FOREIGN KEY ("plan_id") REFERENCES "gateway"."plans"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "gateway"."plans" ADD CONSTRAINT "plans_tenant_id_tenants_id_fk" FOREIGN KEY ("tenant_id") REFERENCES "gateway"."tenants"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "gateway"."plans" ADD CONSTRAINT "plans_investigation_id_fk" FOREIGN KEY ("investigation_id") REFERENCES "gateway"."alert_groups"("investigation_id") ON DELETE no action ON UPDATE no action;