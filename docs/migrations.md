# Migrations

Schema changes follow expand, migrate, contract. Record each change here, newest first.

## 2026-10-14: Baseline

- `infra/postgres-init.sql` runs once on an empty data volume. It creates roles `gateway` and `agent`, schemas `gateway` and `agent` owned by their roles, and the `vector` extension. `gateway` also gets CREATE on the database for drizzle-kit.
- Version tracking: `gateway.__drizzle_migrations` (Drizzle, `apps/gateway/drizzle/`) and `agent.alembic_version` (Alembic, `services/agent/migrations/`). No tables yet.
