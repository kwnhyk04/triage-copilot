CREATE TYPE "gateway"."report_verdict" AS ENUM('real', 'not_real');--> statement-breakpoint
ALTER TABLE "gateway"."investigation_reports" ADD COLUMN "verdict" "gateway"."report_verdict";--> statement-breakpoint
ALTER TABLE "gateway"."investigation_reports" ADD COLUMN "models" jsonb;