-- The webhook writes outbox rows; never queue behind it for long. Drizzle runs migrations in one transaction.
SET LOCAL lock_timeout = '3s';--> statement-breakpoint
CREATE TYPE "gateway"."execution_state" AS ENUM('claimed', 'succeeded', 'failed');--> statement-breakpoint
CREATE TYPE "gateway"."outbox_queue" AS ENUM('investigations', 'actions');--> statement-breakpoint
CREATE TABLE "gateway"."executions" (
	"action_id" uuid PRIMARY KEY NOT NULL,
	"decision" "gateway"."decision" DEFAULT 'approved' NOT NULL,
	"state" "gateway"."execution_state" DEFAULT 'claimed' NOT NULL,
	"dry_run" boolean NOT NULL,
	"detail" text,
	"claimed_at" timestamp with time zone DEFAULT now() NOT NULL,
	"finished_at" timestamp with time zone,
	CONSTRAINT "executions_approved_only" CHECK ("gateway"."executions"."decision" = 'approved'),
	CONSTRAINT "executions_finished_matches_state" CHECK (("gateway"."executions"."finished_at" is null) = ("gateway"."executions"."state" = 'claimed'))
);
--> statement-breakpoint
ALTER TABLE "gateway"."outbox" ADD COLUMN "queue" "gateway"."outbox_queue" DEFAULT 'investigations' NOT NULL;--> statement-breakpoint
-- Moved by hand above the foreign key that needs it; drizzle-kit wrote it last.
ALTER TABLE "gateway"."approvals" ADD CONSTRAINT "approvals_action_id_decision_key" UNIQUE("action_id","decision");--> statement-breakpoint
ALTER TABLE "gateway"."executions" ADD CONSTRAINT "executions_approval_fk" FOREIGN KEY ("action_id","decision") REFERENCES "gateway"."approvals"("action_id","decision") ON DELETE no action ON UPDATE no action;
