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
- `contracts/` (spec-first; code is generated from it, never the reverse):
  - `openapi/gateway.yaml` (OpenAPI 3.1): `POST /v1/alerts` (Alertmanager webhook v4, 202 `{group_id, deduped}`), `GET /v1/investigations` (keyset: `limit`, opaque `cursor`, `next_cursor`), `GET /v1/investigations/{id}`, `POST /v1/actions/{id}/approve|reject` (body `{approver, reason?}`, 409 once decided). Errors are RFC 9457 problem details; 401 carries `WWW-Authenticate`; 429 carries `RateLimit-Limit`, `RateLimit-Remaining`, `Retry-After`; 503 carries `Retry-After`. Security: the webhook takes its own bearer token, approve and reject take `X-Approver-Key` (`APPROVER_API_KEY`, held only by the web server), other `/v1` routes and `/graphql` take `X-API-Key`, health probes none. `openapi/examples/alertmanager-webhook.json` is a sample payload.
  - `openapi/agent.yaml` (internal): `POST /internal/investigations/{id}/cancel`, `GET /internal/investigations/{id}/state`, behind a separate service bearer token.
  - `messages/`: one strict JSON Schema per message version (`<name>.v<N>.schema.json`, `schema_version` const, embedded `examples`): InvestigationRequested v1, StepRecorded v1 and v2, ActionProposed v1, ActionApproved v1. `step-recorded.schema.json` is the v1-or-v2 union consumers validate against. The `investigations.fifo` MessageGroupId is the gateway's alert group id (a UUID), not the Alertmanager `group_key`, which SQS rejects (spaces, over 128 characters).
  - `graphql/schema.graphql`: reads and live updates only. Investigation, Hypothesis, Evidence, ProposedAction, Step, ToolCall; Relay connection `investigations(first, after)`; `investigation(id)`; subscription `investigationUpdated(id): Investigation!`.
- Codegen (`make codegen`): `packages/ts-clients/scripts/codegen.ts` writes `packages/ts-clients/src/generated/` (openapi-typescript, json-schema-to-typescript plus the embedded schemas, graphql-codegen resolver types) and `apps/web/src/generated/graphql.ts` (typed document strings for `apps/web/src/graphql/*.graphql`). `packages/py-clients/scripts/codegen.py` writes `packages/py-clients/src/py_clients/generated/` (datamodel-codegen). Both wipe their output first.
- Clients: `@triage/ts-clients/gateway` has `createGatewayClient` (openapi-fetch over the X-API-Key routes only; the webhook and decision routes are excluded from its type) and `createDecisionClient` (approve and reject with `X-Approver-Key`; only `apps/web/src/lib/decisions.ts` calls it). `/agent` uses the service token. All have a 10 s default timeout; `/messages` (`parseMessage(name, data)`, ajv 2020 validation). `py_clients.clients` (httpx2, 10 s default timeout): `AlertsClient`, a read-only `GatewayClient` (no approve or reject: only the web backend-for-frontend records human decisions), `AgentClient`, and `ApiError` (status, problem, Retry-After).
- `make contracts-check` (`scripts/contracts_check.py`): hashes the generated dirs, runs codegen, fails if anything changed. `make contracts-breaking` compares against the git ref `CONTRACTS_BASE` (default `HEAD`). It runs oasdiff (`tufin/oasdiff:v1.33.0` in Docker, no network, only `.git` and `contracts/` mounted) on both OpenAPI specs and fails on ERR-level changes. It also fails when a `contracts/messages/*.v*.schema.json` that exists at the base was edited or deleted.
- `infra/docker-compose.prod.yml`: production override. Postgres and ElasticMQ publish no ports, every service restarts unless stopped and has a memory limit, and Toxiproxy starts only with `--profile chaos`.
- `packages/ts-config` (zod), `packages/py-config` (pydantic-settings): env validation at startup. A missing or bad variable stops startup with one line naming it, never its value.
- `.pre-commit-config.yaml`: gitleaks hook (v8.30.1) that blocks commits staging secrets; `make install` installs it.
- `packages/ts-telemetry`, `packages/py-telemetry`: JSON logs; OTLP tracing when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.
- `Makefile`: one entry point for both toolchains; recipes run under cmd.exe and sh.

Still planned: gateway intake and grouping, checkpoints, agent tools, executor safety checks, scenario engine, eval harness.

## Implemented Functionality

Step 1 scaffolding and step 2 contracts:

- Contracts and generated clients for both languages (see Important Components). Contract tests cover both sides:
  - Every message schema example validates in TypeScript (ajv) and Python (pydantic, which must also dump it back unchanged).
  - A v1 StepRecorded validates as StepRecorded in both.
  - Action kinds outside the allowlist are rejected in both.
  - The Alertmanager sample parses.
  - The clients send the right credential header.
  - A type-level check in `contracts.test.ts` fails `make typecheck` when an enum copied across REST, messages and GraphQL drifts.
- gateway: `/healthz`, `/readyz` (Postgres), `/graphql` with `Query.health` and `Subscription.investigationUpdated` (returns the whole `Investigation`) over SSE (in-process pub/sub, no publisher yet).
- gateway: `POST /v1/actions/{id}/approve` and `/reject`. They take only `X-Approver-Key`, compared in constant time against `APPROVER_API_KEY`:
  - `X-API-Key` or a bearer token instead: 403.
  - Missing or wrong key: 401 with `WWW-Authenticate`.
  - Then the id and body are validated (400), and an injected decision store gives 200, 404 or 409.
  - Errors, including Fastify's own, are problem+json.
- Approver key: `APPROVER_API_KEY` (at least 32 characters) is required by the gateway and the web server and checked at startup. The Makefile unexports it and passes it only to `dev-ts` (gateway and web), so the agent, executor, sim, tests and tools never receive it. `apps/web/src/lib/decisions.ts` (`decideAction`) is the only code that sends it.
- web: home page shows gateway status through a server-side GraphQL call; `/healthz`, `/readyz` (gateway reachable).
- agent: `/healthz`, `/readyz` (Postgres and queue). A consumer thread validates `InvestigationRequested`, logs and deletes valid messages, and leaves invalid ones for the DLQ.
- executor and sim: `/healthz` and `/readyz` only.
- Migrations wired, no tables yet: Drizzle (`gateway.__drizzle_migrations`) and Alembic (`agent.alembic_version`).

## Current Development Status

Steps 1 (scaffolding) and 2 (contracts) done on 2026-10-14. Next: step 3, data.

## Known Limitations

- All targets are design goals and have not been measured.
- The contracts are ahead of the code:
  - Approve and reject are the only `/v1` routes served. They find no action yet: the gateway's decision store answers not found until step 3 stores actions.
  - X-API-Key, the Alertmanager token and the agent service token are not enforced yet.
  - The GraphQL `investigations` and `investigation` queries have no resolvers (step 9).
- `RootCauseClass` and `ActionKind` are initial enums; the scenario engine and eval steps may extend them. A new value in a response breaks older generated Python clients (pydantic enums reject unknown values), so ship the gateway and the clients together.
- Action `parameters` are free-form; per-kind parameter schemas are open (docs/todo.md). No message carries the structured report (hypotheses, evidence) from agent to gateway yet; decide before step 6.
- `make seed` and `make eval` are placeholders until steps 3 and 6.
- Apps run natively in dev; Dockerfiles and the prod compose override come with steps 7 and 10.
- Gateway subscriptions use in-process pub/sub, so they work with one gateway instance only.
- Toxiproxy runs without proxies; fault-injection tests will create them.
- ElasticMQ messages survive container restarts (H2 on the `elasticmq-data` volume), but losing that volume loses queued investigations until the recovery sweeper (step 4) exists.

## Planned Features

See `docs/todo.md` for the 10-step build plan (8 build steps, review UI, deploy).
