# Triage Copilot

Investigates production alerts with read-only tools (metrics, logs, deploys, runbooks), returns ranked root-cause hypotheses with cited evidence, and proposes fixes that only run after human approval.

## Layout

| Path | What | Port |
| --- | --- | --- |
| `apps/gateway` | Fastify + GraphQL Yoga (subscriptions over SSE) | 4200 |
| `apps/web` | Next.js UI and backend-for-frontend | 3200 |
| `services/agent` | SQS consumer + internal FastAPI | 4210 |
| `services/executor` | Runs approved, allowlisted actions only | 4220 |
| `sim/` | Scenario engine for scripted incidents | 4230 |
| `contracts/` | OpenAPI, GraphQL and SQS message schemas | |
| `packages/ts-*`, `packages/py-*` | Shared config, telemetry and generated clients | |
| `infra/` | Docker Compose and service config | |
| `runbooks/`, `evals/` | Runbooks the agent searches; eval scenarios and reports | |

## Setup

Needs Node 24 (pnpm 12 via corepack), uv, Docker and GNU make.

```sh
cp .env.example .env
make install
make dev
```

`make install` also installs the gitleaks pre-commit hook, which blocks commits that stage secrets. `make dev` starts Postgres, ElasticMQ (SQS) and Toxiproxy, then all five apps. Set `COMPOSE_PROFILES=observability` in `.env` to add the OpenTelemetry Collector, Jaeger (16688), Prometheus (9092) and Grafana (3201), and uncomment `OTEL_EXPORTER_OTLP_ENDPOINT` to export traces.

## Usage

| Target | Does |
| --- | --- |
| `make dev` | Infra plus every app with reload |
| `make build` | Next.js build and Python wheels |
| `make test` | Jest and pytest unit tests; for repeated runs use `pnpm turbo run test --force`, since Turbo replays cached output |
| `make test-int` | Integration tests against the infra, about 3 minutes (the partitioning test seeds 2,000,000 steps). Each test gets a scratch database `triage_test_*`, dropped afterwards; tests never touch the dev database |
| `make lint` | ESLint, Prettier, ruff |
| `make typecheck` | tsc and pyright |
| `make codegen` | Regenerates clients from `contracts/` |
| `make contracts-check` | Regenerates clients; fails if any generated file changed |
| `make contracts-breaking` | oasdiff breaking-change check of both OpenAPI specs against `CONTRACTS_BASE` (default `HEAD`; needs Docker) |
| `make migrate` | Drizzle (gateway) and Alembic (agent) migrations; see `docs/migrations.md` |
| `make seed` | The same fake history every run in both schemas; `SEED_ARGS=--reset` replaces existing rows, `SEED_ARGS=--large` tops the agent up to 2,000,000 steps |
| `make eval` | Placeholder until build step 6 |
| `make dlq-replay` | Re-queues failed messages: parked gateway outbox rows, `investigations-dlq.fifo`, and the agent's `dead_letters` |
| `pnpm --filter @triage/gateway tenant:token <name>` | Issues a tenant's Alertmanager webhook token (creates the tenant if new); prints it once, stores only its SHA-256 |
| `make infra-down` | Removes this project's containers; data volume stays |

Production runs the same compose file plus an override that unpublishes the database and queue ports and adds restart policies and memory limits:

```sh
docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml up -d
```

See `docs/context.md` for the design and `docs/todo.md` for the build plan.
