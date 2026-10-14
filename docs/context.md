# Context

Current state of the project. Document only confirmed information. Update after meaningful technical or functional changes.

Source: design doc "Three AI-Native Portfolio Projects" (2026-10-13).

## Overview and Objectives

AI-native portfolio project showing a tool-using agent loop with a human approval gate for incident triage (SRE / incident response domain).

## Scope and Requirements

- Alertmanager-compatible webhook intake with fingerprint dedupe and grouping.
- Agent loop with checkpoints, step and token budgets, and paginated tool results.
- Human approval UI and an allowlisted, idempotent executor.
- Scenario engine with 40 scripted incidents; optional live mode on the OpenTelemetry Demo.
- Must demonstrate: backpressure, retries, pagination, partial failures, schema migrations.

Targets (design goals, not yet measured):

| Metric | Target |
| --- | --- |
| Root cause top-1 | 0.75 |
| Root cause top-3 | 0.90 |
| Consistency (correct in all 3 runs) | 0.65 |
| Evidence grounding | 0.95 |
| Action correctness | 0.80 |
| Forbidden action proposals | 0 (hard gate) |
| Unapproved executions | 0 (hard gate, proven by tests) |
| Tool-call validity | 0.99 |
| Degraded-tool scenarios | 0.60 correct, 100% name missing evidence |
| Tool calls per investigation | median 8, hard cap 15 |
| Time to first hypothesis | p95 45 s |
| Investigation time | p95 3 min |
| Webhook ack during 500-alert storm | p99 200 ms, 0 dropped, 5 or fewer investigations |
| Tokens per investigation | median 60k, hard cap 150k |
| Cost per investigation | $0.25 |

## Technical Stack

TypeScript 6.0 strict on Node 24 LTS (pnpm 12, Turborepo 2, Fastify 5, GraphQL Yoga 5, Next.js 16, Drizzle, zod 4, Jest 30, ESLint 10, Prettier) and Python 3.12+ (uv workspace, FastAPI, SQLAlchemy 2.1, Alembic, pydantic 2, pydantic-settings, boto3, pytest, ruff, pyright strict). Postgres 18 + pgvector, SQS (ElasticMQ in dev), OpenTelemetry, Docker, gitleaks (pre-commit hook), GitHub Actions.

LLM providers (switchable by env vars): Gemini Flash (free tier) primary, NVIDIA NIM fallback, Ollama for local runs. Deployment: UI on Vercel, backend on the PC via Tailscale Funnel.

Local ports (bound to 127.0.0.1; chosen so all three projects can run at once; compose project name `triagecopilot`). Set in `.env`; template in `.env.example`. Checked against `docker compose ps` under `make dev` on 2026-10-14.

| Service | Host port |
| --- | --- |
| Postgres (pgvector) | 5434 |
| SQS (ElasticMQ; container port 9324, stats UI 9325 not published) | 4566 |
| gateway / agent / executor / sim | 4200 / 4210 / 4220 / 4230 |
| web (Next.js) | 3200 |
| Grafana | 3201 |
| Jaeger UI | 16688 |
| Prometheus | 9092 |
| OTel Collector (gRPC / HTTP) | 4337 / 4338 |
| Toxiproxy API | 8476 (proxies in 15500-15599; 15500-15509 published) |

## Architecture and System Design

Design (scaffolded in step 1; behavior beyond health checks is not built yet):

1. `apps/gateway` (TS): webhook intake, dedupe and grouping, approvals API, GraphQL with subscriptions.
2. SQS FIFO `investigations.fifo` with MessageGroupId per alert group; redrive to DLQ after 5 receives.
3. `services/agent` (Python): SQS consumer, agent loop, checkpoint after every step, visibility heartbeat.
4. `services/executor` (Python): runs approved, allowlisted actions only, with idempotency keys.
5. `sim/` (Python): metrics, paginated logs, and deploy history for scripted incidents.
6. `apps/web` (Next.js): live timeline, hypotheses, approve and reject.

## Important Components

- `infra/docker-compose.yml`: Postgres 18 + pgvector, ElasticMQ, Toxiproxy; OTel Collector, Jaeger, Prometheus and Grafana behind the `observability` profile. Images pinned.
- `infra/postgres-init.sql`: roles `gateway` and `agent`, each owning only its schema.
- `infra/elasticmq.conf`: `investigations.fifo`, redriven to `investigations-dlq.fifo` after 5 receives. Messages persist in H2 on the `elasticmq-data` volume.
- `contracts/`: OpenAPI (gateway, agent), GraphQL schema, SQS message JSON Schemas. `make codegen` generates `packages/ts-clients` and `packages/py-clients`.
- `infra/docker-compose.prod.yml`: production override. Postgres and ElasticMQ publish no ports, every service restarts unless stopped and has a memory limit, and Toxiproxy starts only with `--profile chaos`.
- `packages/ts-config` (zod), `packages/py-config` (pydantic-settings): env validation at startup. A missing or bad variable stops startup with one line naming it, never its value.
- `.pre-commit-config.yaml`: gitleaks hook (v8.30.1) that blocks commits staging secrets; `make install` installs it.
- `packages/ts-telemetry`, `packages/py-telemetry`: JSON logs; OTLP tracing when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.
- `Makefile`: one entry point for both toolchains; recipes run under cmd.exe and sh.

Still planned: gateway intake and grouping, checkpoints, agent tools, executor safety checks, scenario engine, eval harness.

## Implemented Functionality

Step 1 scaffolding only:

- gateway: `/healthz`, `/readyz` (Postgres), `/graphql` with `Query.health` and `Subscription.investigationUpdated` over SSE (in-process pub/sub, no publisher yet).
- web: home page shows gateway status through a server-side GraphQL call; `/healthz`, `/readyz` (gateway reachable).
- agent: `/healthz`, `/readyz` (Postgres and queue). A consumer thread validates `InvestigationRequested`, logs and deletes valid messages, and leaves invalid ones for the DLQ.
- executor and sim: `/healthz` and `/readyz` only.
- Migrations wired, no tables yet: Drizzle (`gateway.__drizzle_migrations`) and Alembic (`agent.alembic_version`).

## Current Development Status

Step 1 (scaffolding) done on 2026-10-14. Next: step 2, contracts.

## Known Limitations

- All targets are design goals and have not been measured.
- `make seed` and `make eval` are placeholders until steps 3 and 6.
- Apps run natively in dev; Dockerfiles and the prod compose override come with steps 7 and 10.
- Gateway subscriptions use in-process pub/sub, so they work with one gateway instance only.
- Toxiproxy runs without proxies; fault-injection tests will create them.
- ElasticMQ messages survive container restarts (H2 on the `elasticmq-data` volume), but losing that volume loses queued investigations until the recovery sweeper (step 4) exists.

## Planned Features

See `docs/todo.md` for the 10-step build plan (8 build steps, review UI, deploy).
