CREATE TABLE "gateway"."outbox" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "gateway"."outbox_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"message_group_id" text NOT NULL,
	"deduplication_id" text NOT NULL,
	"body" jsonb NOT NULL,
	"attempts" integer DEFAULT 0 NOT NULL,
	"next_attempt_at" timestamp with time zone DEFAULT now() NOT NULL,
	"last_error" text,
	"failed_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "gateway"."tenants" ADD COLUMN "webhook_token_sha256" text;--> statement-breakpoint
CREATE INDEX "outbox_due_idx" ON "gateway"."outbox" USING btree ("next_attempt_at") WHERE "gateway"."outbox"."failed_at" is null;--> statement-breakpoint
ALTER TABLE "gateway"."tenants" ADD CONSTRAINT "tenants_webhook_token_sha256_unique" UNIQUE("webhook_token_sha256");