# Migrations

Schema changes follow expand, migrate, contract. Record each change here, newest first.

## 2026-10-14: Message contracts

No database change. Queue messages follow the same three phases:

- StepRecorded v2 is in the expand phase. `contracts/messages/step-recorded.schema.json` accepts v1 and v2, and consumers validate against it.
- Migrate: producers switch to v2 (the agent loop, build step 6).
- Contract: once no v1 producer runs and the queues hold no v1 messages, drop v1 from the union, then delete `step-recorded.v1.schema.json`.
- `investigation-requested.schema.json` became `investigation-requested.v1.schema.json` (generated type `InvestigationRequestedV1`). The wire format is unchanged.

## 2026-10-14: Baseline

- `infra/postgres-init.sql` runs once on an empty data volume. It creates roles `gateway` and `agent`, schemas `gateway` and `agent` owned by their roles, and the `vector` extension. `gateway` also gets CREATE on the database for drizzle-kit.
- Version tracking: `gateway.__drizzle_migrations` (Drizzle, `apps/gateway/drizzle/`) and `agent.alembic_version` (Alembic, `services/agent/migrations/`). No tables yet.
