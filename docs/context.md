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

Design (steps 1 to 4 built intake, queueing and the resumable job; the model providers and tools come in step 6):

1. `apps/gateway` (TS): webhook intake, dedupe and grouping, approvals API, GraphQL with subscriptions.
2. SQS FIFO `investigations.fifo`, one MessageGroupId per Alertmanager group (a hash of tenant and group key); redrive to DLQ after 5 receives.
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
  - `messages/`: one strict JSON Schema per message version (`<name>.v<N>.schema.json`, `schema_version` const, embedded `examples`): InvestigationRequested v1 and v2, StepRecorded v1, v2 and v3, ActionProposed v1, ActionApproved v1. `step-recorded.schema.json` is the union of v1, v2 and v3 that consumers validate against; it marks v2 deprecated. Producers emit v3, whose payload is the agent's stored StepRecorded v2 event unchanged. Both require a summary of 1 to 2,000 characters, and latency and token counts that are not negative.
  - InvestigationRequested v2 adds `tenant_id`; the union `investigation-requested.schema.json` accepts v1 (deprecated) and v2, and the gateway emits v2. Its MessageGroupId is the hex SHA-256 of tenant and Alertmanager `group_key`, which SQS cannot take raw (spaces, over 128 characters); its MessageDeduplicationId hashes tenant, group key and window start.
  - `graphql/schema.graphql`: reads and live updates only. Investigation, Hypothesis, Evidence, ProposedAction, Step, ToolCall; Relay connection `investigations(first, after)`; `investigation(id)`; subscription `investigationUpdated(id): Investigation!`.
- Codegen (`make codegen`): `packages/ts-clients/scripts/codegen.ts` writes `packages/ts-clients/src/generated/` (openapi-typescript, json-schema-to-typescript plus the embedded schemas, graphql-codegen resolver types) and `apps/web/src/generated/graphql.ts` (typed document strings for `apps/web/src/graphql/*.graphql`). `packages/py-clients/scripts/codegen.py` writes `packages/py-clients/src/py_clients/generated/` (datamodel-codegen). Both wipe their output first.
- Clients: `@triage/ts-clients/gateway` has `createGatewayClient` (openapi-fetch over the X-API-Key routes only; the webhook and decision routes are excluded from its type) and `createDecisionClient` (approve and reject with `X-Approver-Key`; only `apps/web/src/lib/decisions.ts` calls it). `/agent` uses the service token. All have a 10 s default timeout; `/messages` (`parseMessage(name, data)`, ajv 2020 validation). `py_clients.clients` (httpx2, 10 s default timeout): `AlertsClient`, a read-only `GatewayClient` (no approve or reject: only the web backend-for-frontend records human decisions), `AgentClient`, and `ApiError` (status, problem, Retry-After).
- `make contracts-check` (`scripts/contracts_check.py`): hashes the generated dirs, runs codegen, fails if anything changed. `make contracts-breaking` compares against the git ref `CONTRACTS_BASE` (default `HEAD`). It runs oasdiff (`tufin/oasdiff:v1.33.0` in Docker, no network, only `.git` and `contracts/` mounted) on both OpenAPI specs and fails on ERR-level changes. It also fails when a `contracts/messages/*.v*.schema.json` that exists at the base was edited or deleted.
- `infra/docker-compose.prod.yml`: production override. Postgres and ElasticMQ publish no ports, every service restarts unless stopped and has a memory limit, and Toxiproxy starts only with `--profile chaos`.
- `packages/ts-config` (zod), `packages/py-config` (pydantic-settings): env validation at startup. A missing or bad variable stops startup with one line naming it, never its value.
- `.pre-commit-config.yaml`: gitleaks hook (v8.30.1) that blocks commits staging secrets; `make install` installs it.
- `packages/ts-telemetry`, `packages/py-telemetry`: JSON logs; OTLP tracing when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.
- `Makefile`: one entry point for both toolchains; recipes run under cmd.exe and sh.

- Gateway schema (`apps/gateway/src/schema.ts`, SQL migrations in `apps/gateway/drizzle/`, written by `pnpm --filter @triage/gateway db:generate`):
  - `tenants`, with `webhook_token_sha256`; `services` and `service_dependencies` (the dependency graph; writers keep it acyclic).
  - `outbox`: InvestigationRequested messages waiting for the relay; parked rows (`failed_at`) are the gateway's failure store.
  - `alert_groups`: `group_key` and `window_start` are unique per tenant. `status` holds the API's investigation statuses plus `queued_not_investigated`, and a CHECK ties a null `investigation_id` to exactly that status.
  - `alerts`: one row per firing, unique on `(tenant_id, fingerprint, starts_at)`.
  - `proposed_actions`: a copy of the agent's proposals for the UI. Its status comes from `approvals`, whose primary key `action_id` allows one decision per action.
  - Keyset indexes on `(tenant_id, created_at DESC NULLS FIRST, id DESC NULLS FIRST)` for alerts, groups and actions.
- Agent schema:
  - Models in `services/agent/src/agent/db.py`; plain-SQL Alembic revisions 0001 to 0005 in `services/agent/migrations/versions/` (`docs/migrations.md`).
  - Tables: `investigations` (status adds `queued_budget`), `investigation_steps` (partitioned by month on `created_at`), `tool_calls`, `runbook_chunks` (`vector(768)`, no ANN index yet), `proposed_actions` (unique `idempotency_key`), `dead_letters` (poison messages), `ai_usage` (model tokens per UTC day).
- Gateway intake (`apps/gateway/src/intake.ts`, `outbox.ts`, `cli.ts`):
  - `POST /v1/alerts` checks the tenant's bearer token after the rate limit and before reading the body, then validates the payload (1 MB, 200 alerts, 10 s to send the request).
  - It stores each firing once per (tenant, fingerprint, startsAt) and records status changes. New alerts join the group of their group key whose 5-minute window opened last, or open a group. A new group with a firing alert gets an investigation, whose InvestigationRequested v2 goes to `outbox` in the same transaction, unless the tenant already started `MAX_NEW_INVESTIGATIONS` (5) in the last 5 minutes; otherwise it stays `queued_not_investigated`. It answers 202 `{group_id, deduped}`; the request path never calls a model.
  - The relay sends due outbox rows to `investigations.fifo` (nudged after each commit, else every second), retries a failure after 2^attempts seconds up to 5 minutes, and parks a row after 10 failures.
  - `cli.ts`: `tenant:token <name>` issues a webhook token (stored as SHA-256); `outbox:replay` re-queues parked rows.
  - @fastify/rate-limit, in memory, `RATE_LIMIT_PER_MINUTE` (600) per credential hash or client address; probes are exempt; 429 is problem+json with `Retry-After` and `RateLimit-*` headers.
- Agent worker (`services/agent/src/agent/consumer.py`, `job.py`, `model.py`, `dlq.py`):
  - `Consumer`: asyncio task in the FastAPI lifespan. 20 s long polls ask only for free slots (`AGENT_MAX_IN_FLIGHT`, 5). A heartbeat extends visibility to 60 s every 30 s and stops at shutdown. Poison messages go to `dead_letters` and are deleted. A message is deleted only once its report commits; a failure leaves it to redelivery, and the 5th failed receive marks the investigation `failed`.
  - `Job`: one Postgres session per run, holding an advisory lock per investigation and one of `AGENT_TENANT_CAP` (5) slots per tenant ("busy" returns the message after 60 to 120 s). It replays the recorded steps and continues after the last: a model turn, or the tool call the last turn asked for.
  - Each model turn and each tool result is one transaction: a turn that asks for a tool writes the call's `tool_calls` row with its arguments, and the result step completes it. As StepRecorded v3 defines them, only result steps carry `result` and `latency_ms`. One message per FIFO group runs from each receive; later ones of that group go back at once. `failed` is not final: a message replayed from the DLQ resumes its investigation. Tools get a 10 s timeout and 3 retries with full jitter; a call that still fails is recorded as an error and the investigation goes on. Model calls get 60 s and 3 retries, waiting the provider's Retry-After when given.
  - Budget guard: new investigations wait as `queued_budget` while `AI_ENABLED` is false or today's `ai_usage` reaches `DAILY_AI_TOKEN_BUDGET`; `AI_ENABLED=false` also holds running ones at their next model turn.
  - `model.py`: the provider layer (`Model`, `ModelError.retry_after`, the `Report` of summary and hypotheses, `ScriptedModel`). Until step 6 the agent runs a placeholder `ScriptedModel` that reports at once.
  - `python -m agent.dlq replay` moves `investigations-dlq.fifo` back to the queue and re-sends unreplayed `dead_letters`; `make dlq-replay` runs it after the gateway's `outbox:replay`.
- `agent.steps`: the stored StepRecorded v1 and v2 event models, the upcasters, `replay` (always v2), `step_message`, and `record_step`. `record_step` numbers steps from `investigations.step_count`, dual-writes to `investigation_steps_p` while `STEPS_DUAL_WRITE` is on, and returns the StepRecorded v3 message to publish after its transaction commits.
- `agent.partitions`: creates monthly partitions (the agent runs it at startup and then daily) and runs the resumable backfill. CLI: `uv run python -m agent.partitions backfill|ensure`.
- `scripts/seed.py` (`make seed`): Faker with a fixed seed.
  - 5 tenants, 50 services, 79 dependency edges, 10,000 alerts in 2,523 groups, 2,000 investigations (21,867 steps: v1 before 2026-06-01, v2 after), 17,486 tool calls, 784 actions, 714 decisions.
  - Each schema is written by its own role. It refuses to overwrite data without `--reset`; `--large` tops the agent up to 2,000,000 steps.
- Migration tests (`services/agent/tests/conftest.py`) create a throwaway database `triage_test_*` per test as the Postgres superuser (`POSTGRES_PASSWORD`) and drop it afterwards.

Still planned: the recovery sweeper, agent tools and model providers (step 6), executor safety checks, scenario engine, eval harness.

## Implemented Functionality

Steps 1 (scaffolding), 2 (contracts), 3 (data) and 4 (workers):

- Contracts and generated clients for both languages (see Important Components). Contract tests cover both sides:
  - Every message schema example validates in TypeScript (ajv) and Python (pydantic, which must also dump it back unchanged).
  - Every StepRecorded version (v1, v2, v3) validates as StepRecorded in both, and only v2 is marked deprecated.
  - InvestigationRequested v1 and v2 validate through their union in both; only v1 is marked deprecated, and v2 requires `tenant_id`.
  - The v3 payload fields equal the stored v2 event's, and every stored event becomes a v3 message unchanged (`services/agent/tests/test_steps.py`).
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
- agent: `/healthz`, `/readyz` (Postgres and queue), and the worker (consumer and resumable job, see Important Components).
- Step 4 (workers), checked by tests:
  - Restart: an agent stopped mid-investigation finishes and records its running tool call, leaves the message, and the next agent resumes at the next step; every tool call ran once. After a hard kill only the interrupted call runs again, since it had no recorded result (`services/agent/tests/test_workers_int.py`).
  - DLQ: a message whose job fails 5 times lands in the DLQ, and its investigation is `failed`; replayed with the cause fixed, it resolves. A poison message goes to `dead_letters` without using receives. Both replay commands move messages back.
  - The heartbeat keeps a job that outlasts the visibility timeout from redelivering; at shutdown it stops.
  - Budget guard: a new investigation waits as `queued_budget` with no model call until the day's usage is under budget; resumed ones pass the budget but not `AI_ENABLED=false`; tokens add up per UTC day and per investigation. The tenant cap holds a third run of a tenant capped at 2 while another tenant proceeds.
  - Storm: 500 webhook posts from 10 concurrent clients (400 alerts in 8 groups plus 100 re-sends) are all acknowledged with p99 under 200 ms (about 86 ms measured), store every alert, open 8 groups and queue only 5 investigations, one message per FIFO group (`apps/gateway/src/intake.int.test.ts`). A new alert that arrives resolved opens a group without an investigation.
  - Rate limit: 429 with `Retry-After` and `RateLimit-*` headers once a credential uses its minute, a separate count per credential, and probes never limited. Limits: 201 alerts get 400, a body over 1 MB gets 413, and the request timeout is 10 s (`apps/gateway/src/app.test.ts`).
- executor and sim: `/healthz` and `/readyz` only.
- Both schemas, migrated by Drizzle (`gateway.__drizzle_migrations`) and Alembic (`agent.alembic_version`). Tests apply both chains to an empty database, run the seed, and check the models against the result (`alembic check`). They also downgrade the agent chain to empty and back.
- Story A (online partitioning of `investigation_steps`): expand, dual-write flag, resumable backfill, verified swap, contract, and the partition job. A test runs the whole sequence on 2,000,000 seeded steps while a writer records steps: zero errors, writes in every phase, slowest write under 70 ms. Two guard tests confirm that the swap refuses before the backfill finishes, and the drop refuses while the old table holds a row the new one lacks.
- Story B (event versioning): replaying stored v1 steps returns v2 objects; a golden snapshot pins the result, and stored rows are never rewritten.

## Current Development Status

Steps 1 (scaffolding), 2 (contracts) and 3 (data) done on 2026-10-14, step 4 (workers) on 2026-10-15 except the recovery sweeper. Next: the sweeper, then step 5, tests.

## Known Limitations

- All targets are design goals and have not been measured.
- The contracts are ahead of the code:
  - Approve, reject and the alert webhook are the only `/v1` routes served. Their decision store still answers not found. The `proposed_actions` and `approvals` tables exist, but recording a decision must also queue ActionApproved, which needs an outbox (step 4).
  - X-API-Key and the agent service token are not enforced yet; the webhook enforces per-tenant tokens.
  - The GraphQL `investigations` and `investigation` queries have no resolvers (step 9).
- `RootCauseClass` and `ActionKind` are initial enums; the scenario engine and eval steps may extend them. A new value in a response breaks older generated Python clients (pydantic enums reject unknown values), so ship the gateway and the clients together.
- Action `parameters` are free-form; per-kind parameter schemas are open (docs/todo.md). No message carries the structured report (hypotheses, evidence) from agent to gateway yet; decide before step 6.
- `make eval` is a placeholder until step 6.
- StepRecorded v3, like the stored v2 event, has no tool call status, and its `latency_ms` may be null. The API's `Step.tool_call` needs `status` and `duration_ms`, so the gateway must derive them or a later version must add them (docs/todo.md).
- The job records StepRecorded events but publishes none: no queue carries them to the gateway yet (step 6). A tool call is two steps (the model's request with tokens, then the result), so the API's `Step.tool_call` must pair them.
- Agent tables carry no tenant id; the agent reads the tenant from InvestigationRequested v2 only to cap concurrency.
- Until step 6 the agent's model is a placeholder that ends each investigation with a "not investigated" report, and it has no tools.
- Rate limits count in memory, so they hold for one gateway instance only. Requests without a credential are counted per client address, which is the proxy's behind Tailscale Funnel unless `trustProxy` is set (step 10).
- With several agents, messages that wait for a tenant slot use up receives and can dead-letter (docs/memory.md). Budget waits hold up to `AGENT_MAX_IN_FLIGHT` messages in flight, and while every slot waits, resumed investigations wait in the queue too. Real SQS ends a message's visibility 12 hours after its receive, so a longer wait costs one receive.
- A group opened without an investigation (cap reached, or only resolved alerts) stays without one for its 5-minute window, even if firing alerts join it.
- The storm test sends its 500 posts within seconds rather than over 2 minutes, which is harsher on latency.
- `STEPS_DUAL_WRITE` and the dual-write code stay for the partitioning test; past revision 0004 the flag must stay false.
- `make seed SEED_ARGS=--large` adds agent rows only: its extra investigations have no gateway alert groups.
- Embeddings are `vector(768)`; another dimension needs a new column and a re-embed.
- Apps run natively in dev; Dockerfiles and the prod compose override come with steps 7 and 10.
- Gateway subscriptions use in-process pub/sub, so they work with one gateway instance only.
- Toxiproxy runs without proxies; fault-injection tests will create them.
- ElasticMQ messages survive container restarts (H2 on the `elasticmq-data` volume), but losing that volume loses queued investigations until the recovery sweeper (step 4) exists.

## Planned Features

See `docs/todo.md` for the 10-step build plan (8 build steps, review UI, deploy).
