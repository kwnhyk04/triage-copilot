ALTER TABLE "gateway"."plan_decisions" ADD CONSTRAINT "plan_decisions_plan_id_decision_key" UNIQUE("plan_id","decision");--> statement-breakpoint
CREATE TABLE "gateway"."plan_executions" (
	"plan_id" uuid PRIMARY KEY NOT NULL,
	"decision" "gateway"."decision" DEFAULT 'approved' NOT NULL,
	"state" "gateway"."execution_state" DEFAULT 'claimed' NOT NULL,
	"dry_run" boolean NOT NULL,
	"detail" text,
	"claimed_at" timestamp with time zone DEFAULT now() NOT NULL,
	"finished_at" timestamp with time zone,
	CONSTRAINT "plan_executions_approved_only" CHECK ("gateway"."plan_executions"."decision" = 'approved'),
	CONSTRAINT "plan_executions_finished_matches_state" CHECK (("gateway"."plan_executions"."finished_at" is null) = ("gateway"."plan_executions"."state" = 'claimed'))
);
--> statement-breakpoint
ALTER TABLE "gateway"."plan_executions" ADD CONSTRAINT "plan_executions_decision_fk" FOREIGN KEY ("plan_id","decision") REFERENCES "gateway"."plan_decisions"("plan_id","decision") ON DELETE no action ON UPDATE no action;
