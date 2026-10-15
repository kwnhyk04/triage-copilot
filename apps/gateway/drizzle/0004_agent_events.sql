-- The foreign keys lock alert_groups, which the webhook writes; never queue behind it for long.
-- Drizzle runs migrations in one transaction.
SET LOCAL lock_timeout = '3s';--> statement-breakpoint
CREATE TYPE "gateway"."step_kind" AS ENUM('tool_call', 'hypothesis', 'action', 'report');--> statement-breakpoint
CREATE TYPE "gateway"."tool_call_status" AS ENUM('ok', 'error', 'timeout');--> statement-breakpoint
CREATE TABLE "gateway"."investigation_reports" (
	"investigation_id" uuid PRIMARY KEY NOT NULL,
	"status" "gateway"."alert_group_status" NOT NULL,
	"report" jsonb NOT NULL,
	"recorded_at" timestamp with time zone NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "investigation_reports_status_final" CHECK ("gateway"."investigation_reports"."status" in ('resolved', 'awaiting_approval'))
);
--> statement-breakpoint
CREATE TABLE "gateway"."investigation_steps" (
	"investigation_id" uuid NOT NULL,
	"seq" integer NOT NULL,
	"kind" "gateway"."step_kind" NOT NULL,
	"summary" text NOT NULL,
	"tool_name" text,
	"tool_call_id" text,
	"result" text,
	"latency_ms" integer,
	"tokens_input" integer,
	"tokens_output" integer,
	"tool_status" "gateway"."tool_call_status",
	"duration_ms" integer,
	"recorded_at" timestamp with time zone NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "investigation_steps_investigation_id_seq_pk" PRIMARY KEY("investigation_id","seq")
);
--> statement-breakpoint
ALTER TABLE "gateway"."investigation_reports" ADD CONSTRAINT "investigation_reports_investigation_id_fk" FOREIGN KEY ("investigation_id") REFERENCES "gateway"."alert_groups"("investigation_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "gateway"."investigation_steps" ADD CONSTRAINT "investigation_steps_investigation_id_fk" FOREIGN KEY ("investigation_id") REFERENCES "gateway"."alert_groups"("investigation_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "investigation_steps_tool_call_idx" ON "gateway"."investigation_steps" USING btree ("investigation_id","tool_call_id");