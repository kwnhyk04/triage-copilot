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
| `make test` | Jest and pytest unit tests |
| `make test-int` | Integration tests against the infra |
| `make lint` | ESLint, Prettier, ruff |
| `make typecheck` | tsc and pyright |
| `make codegen` | Regenerates clients from `contracts/` |
| `make migrate` | Drizzle (gateway) and Alembic (agent) migrations |
| `make seed`, `make eval` | Placeholders until build steps 3 and 6 |
| `make infra-down` | Removes this project's containers; data volume stays |

Production runs the same compose file plus an override that unpublishes the database and queue ports and adds restart policies and memory limits:

```sh
docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml up -d
```

See `docs/context.md` for the design and `docs/todo.md` for the build plan.
