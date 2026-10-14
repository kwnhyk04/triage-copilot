-- The webhook writes alert_groups; never queue behind it for long. Drizzle runs migrations in one transaction.
SET LOCAL lock_timeout = '3s';--> statement-breakpoint
ALTER TABLE "gateway"."alert_groups" ADD COLUMN "requeued_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "gateway"."alert_groups" ADD COLUMN "requeue_count" integer DEFAULT 0 NOT NULL;