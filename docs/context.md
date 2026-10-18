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

Targets (design goals; `make eval` measures them, and no counted eval run exists yet):

| Metric | Target |
| --- | --- |
| Root cause top-1 | 0.75 |
| Root cause top-3 | 0.90 |
| Consistency (correct in all 3 runs) | 0.65 |
| Evidence grounding | 0.95 |
| Action correctness (reported beside its do-nothing baseline) | 0.80 |
| Action correctness, scenarios with an acceptable action | 0.80 |
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

LLM providers (switchable by env vars): Gemini Flash (free tier) primary, then optionally Claude Haiku 5.5 on Anthropic API credits (`LLM_CLAUDE_FALLBACK`, off by default, until the credits expire on 2026-11-10), then the NVIDIA NIM fallback; Ollama for local runs. Deployment: UI on Vercel, backend on the PC via Tailscale Funnel.

Local ports (bound to 127.0.0.1; chosen so all three projects can run at once; compose project name `triagecopilot`). Set in `.env`; template in `.env.example`. Checked against `docker compose ps` under `make dev` on 2026-10-14.

| Service | Host port |
| --- | --- |
| Postgres (pgvector) | 5434 |
| SQS (ElasticMQ; container port 9324, stats UI 9325 not published) | 4566 |
| gateway / agent / executor / sim | 4200 / 4210 / 4220 / 4230 |
| gateway internal listener (executor only) | 4201 |
| sim admin listener (eval harness only) | 4231 |
| web (Next.js) | 3200 |
| Grafana | 3201 |
| Jaeger UI | 16688 |
| Prometheus | 9092 |
| OTel Collector (gRPC / HTTP) | 4337 / 4338 |
| Toxiproxy API | 8476 (proxies in 15500-15599; 15500-15509 published) |

## Architecture and System Design

Design (steps 1 to 4 built intake, queueing and the resumable job; the model providers and tools come in step 6):

1. `apps/gateway` (TS): webhook intake, dedupe and grouping, recovery sweeper, approvals API, GraphQL with subscriptions.
2. SQS FIFO `investigations.fifo`, one MessageGroupId per Alertmanager group (a hash of tenant and group key); redrive to DLQ after 5 receives.
3. `services/agent` (Python): SQS consumer, agent loop, checkpoint after every step, visibility heartbeat. Work that must wait for a tenant slot or model budget parks in Postgres and gives up its message; the gateway's sweeper re-queues it.
4. `services/executor` (Python): runs approved, allowlisted actions only, with idempotency keys.
5. `sim/` (Python): metrics, paginated logs, and deploy history for scripted incidents.
6. `apps/web` (Next.js): live timeline, hypotheses, approve and reject.

## Important Components

- `infra/docker-compose.yml`: Postgres 18 + pgvector, ElasticMQ, Toxiproxy; OTel Collector, Jaeger, Prometheus and Grafana behind the `observability` profile. Images pinned.
- `infra/postgres-init.sql`: roles `gateway` and `agent`, each owning only its schema.
- `infra/elasticmq.conf`: `investigations.fifo`, redriven to `investigations-dlq.fifo` after 5 receives. Messages persist in H2 on the `elasticmq-data` volume.
- `contracts/` (spec-first; code is generated from it, never the reverse):
  - `openapi/gateway.yaml` (OpenAPI 3.1): `POST /v1/alerts` (Alertmanager webhook v4, 202 `{group_id, deduped}`), `GET /v1/investigations` (keyset: `limit`, opaque `cursor`, `next_cursor`), `GET /v1/investigations/{id}`, `POST /v1/actions/{id}/approve|reject` (body `{approver, reason?}`, 409 once decided). Errors are RFC 9457 problem details; 401 carries `WWW-Authenticate`; 429 carries `RateLimit-Limit`, `RateLimit-Remaining`, `Retry-After`; 503 carries `Retry-After`. Security: the webhook takes its own bearer token, approve and reject take `X-Approver-Key` (`APPROVER_API_KEY`, held only by the web server), other `/v1` routes and `/graphql` take `X-API-Key`, health probes none. `openapi/examples/alertmanager-webhook.json` is a sample payload.
  - `openapi/agent.yaml` (internal, 0.3.0): `POST /internal/investigations/{id}/cancel`, `GET /internal/investigations/{id}/state`, behind a separate service bearer token (`AGENT_SERVICE_TOKEN`). The state adds `waiting_for` (`tenant_cap` or `budget`), `waiting_since` and `resumable`. Only state is served; cancel is not built.
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
  - `outbox`: messages waiting for the relay, each for its `queue` (`investigations`: InvestigationRequested; `actions`: ActionApproved); parked rows (`failed_at`) are the gateway's failure store.
  - `executions`: the executor's claims (`action_id` primary key, the idempotency key; states claimed, succeeded, failed). A foreign key on `(action_id, decision)` to `approvals` and a check accept only approved decisions.
  - `alert_groups`: `group_key` and `window_start` are unique per tenant. `status` holds the API's investigation statuses plus `queued_not_investigated`, and a CHECK ties a null `investigation_id` to exactly that status.
  - `alerts`: one row per firing, unique on `(tenant_id, fingerprint, starts_at)`.
  - `proposed_actions`: a copy of the agent's proposals for the UI. Its status comes from `approvals`, whose primary key `action_id` allows one decision per action.
  - Keyset indexes on `(tenant_id, created_at DESC NULLS FIRST, id DESC NULLS FIRST)` for alerts, groups and actions.
- Agent schema:
  - Models in `services/agent/src/agent/db.py`; plain-SQL Alembic revisions 0001 to 0009 in `services/agent/migrations/versions/` (`docs/migrations.md`).
  - Tables: `investigations` (while parked: `waiting_reason`, `waiting_since` and the `tenant_id` from the message; `queued_budget` is no longer written), `investigation_steps` (partitioned by month on `created_at`), `tool_calls`, `runbook_chunks` (`vector(768)`, no ANN index yet), `proposed_actions` (unique `idempotency_key`), `dead_letters` (poison messages), `ai_usage` (model tokens per UTC day).
- Gateway intake (`apps/gateway/src/intake.ts`, `outbox.ts`, `cli.ts`):
  - `POST /v1/alerts` checks the tenant's bearer token after the rate limit and before reading the body, then validates the payload (1 MB, 200 alerts, 10 s to send the request).
  - It stores each firing once per (tenant, fingerprint, startsAt) and records status changes. New alerts join the group of their group key whose 5-minute window opened last, or open a group. A new group with a firing alert gets an investigation, whose InvestigationRequested v2 goes to `outbox` in the same transaction, unless the tenant already started `MAX_NEW_INVESTIGATIONS` (5) in the last 5 minutes; otherwise it stays `queued_not_investigated`. It answers 202 `{group_id, deduped}`; the request path never calls a model.
  - The relay sends due outbox rows to `investigations.fifo` (nudged after each commit, else every second), retries a failure after 2^attempts seconds up to 5 minutes, and parks a row after 10 failures.
  - `cli.ts`: `tenant:token <name>` issues a webhook token (stored as SHA-256); `outbox:replay` re-queues parked rows.
  - @fastify/rate-limit, in memory, `RATE_LIMIT_PER_MINUTE` (600) per credential hash or client address; probes are exempt; 429 is problem+json with `Retry-After` and `RateLimit-*` headers.
- Gateway recovery sweeper (`apps/gateway/src/sweeper.ts`), every `SWEEPER_INTERVAL_SECONDS` (15) in the gateway process:
  - One transaction over the `queued` groups that have no outbox row, `FOR NO KEY UPDATE SKIP LOCKED`, so two gateways never sweep one group and webhook foreign-key checks are not blocked.
  - For each, it asks the agent's state (`AGENT_URL`, service token, 3 s timeout); an unreachable agent skips the group.
  - Re-queues through the outbox: a parked investigation the agent reports `resumable` (unless a re-queue since it parked is still on its way), and stuck ones: unknown to the agent, or `queued`/`running` without a checkpoint, for `SWEEPER_STUCK_AFTER_MINUTES` (10), counted from the last re-queue too. `failed` is left to the DLQ and `make dlq-replay`.
  - A re-queue writes the outbox row and sets `requeued_at` and `requeue_count` in that transaction, then wakes the relay. Its MessageDeduplicationId is the window's hash input plus `\nrequeue N`, so SQS never drops it as a repeat of an earlier send. `investigationRequest` in `intake.ts` builds the message for both.
  - A group whose investigation reached `awaiting_approval`, `resolved` or `cancelled` takes that status and leaves the sweep.
- Agent worker (`services/agent/src/agent/consumer.py`, `job.py`, `model.py`, `dlq.py`):
  - `Consumer`: asyncio task in the FastAPI lifespan. 20 s long polls ask only for free slots (`AGENT_MAX_IN_FLIGHT`, 5). A heartbeat extends visibility to 60 s every 30 s and stops at shutdown. Poison messages go to `dead_letters` and are deleted. A message is deleted once its report commits or its investigation parks; a failure leaves it to redelivery, and the 5th failed receive marks the investigation `failed`.
  - `Job`: one Postgres session per run, holding an advisory lock per investigation and one of `AGENT_TENANT_CAP` (5) slots per tenant. A duplicate delivery ("busy": the investigation runs elsewhere) returns after 60 to 120 s. It replays the recorded steps and continues after the last: a model turn, or the tool call the last turn asked for.
  - Parking: a tenant at its cap, or no model budget, marks the investigation waiting (status `queued`, `waiting_reason` `tenant_cap` or `budget`, `waiting_since`, `tenant_id`) and the consumer deletes its message, so a wait holds no worker slot and uses up no receives. The budget check comes before the tenant slot; with `AI_ENABLED=false` a running investigation parks at its next model turn and frees its slot. Taking a slot clears the mark.
  - `Job.resumable`: budget waiters, once model calls are available again (resumed ones need only `AI_ENABLED`); tenant-cap waiters, while fewer of the tenant's investigations that parked earlier still wait than slots are free. Free slots are the cap minus the tenant's slot locks in `pg_locks`. A re-queued investigation stays parked until it takes its slot, so it keeps its place in line.
- Agent internal API (`services/agent/src/agent/api.py`): `GET /internal/investigations/{id}/state` behind `AGENT_SERVICE_TOKEN` (32+ characters), compared as SHA-256 digests in constant time. Missing or wrong: 401 problem details with `WWW-Authenticate: Bearer`. Every error is problem details (400 for a bad id, 404 for an unknown one). It maps `queued_budget` to `queued` and always reports `cancel_requested` false.
  - Each model turn and each tool result is one transaction: a turn that asks for a tool writes the call's `tool_calls` row with its arguments, and the result step completes it. As StepRecorded v3 defines them, only result steps carry `result` and `latency_ms`. One message per FIFO group runs from each receive; later ones of that group go back at once. `failed` is not final: a message replayed from the DLQ resumes its investigation. Tools get a 10 s timeout and 3 retries with full jitter; a call that still fails is recorded as an error and the investigation goes on. Model calls get 60 s and 3 retries, waiting the provider's Retry-After when given.
  - Budget guard: new investigations park (`budget`) while `AI_ENABLED` is false or today's `ai_usage` reaches `DAILY_AI_TOKEN_BUDGET`; `AI_ENABLED=false` also parks running ones at their next model turn.
  - `model.py`: provider-neutral types (`TurnRequest`, `TurnBudget`, `ModelTurn`, `ModelCall`, `ReportDraft`, errors) and `ScriptedModel` for tests. The production model is `llm.Router` (step 6a, below).
  - `python -m agent.dlq replay` moves `investigations-dlq.fifo` back to the queue and re-sends unreplayed `dead_letters`; `make dlq-replay` runs it after the gateway's `outbox:replay`.
- `agent.steps`: the stored StepRecorded v1 and v2 event models, the upcasters, `replay` (always v2), `step_message`, and `record_step`. `record_step` numbers steps from `investigations.step_count`, dual-writes to `investigation_steps_p` while `STEPS_DUAL_WRITE` is on, and returns the StepRecorded v3 message to publish after its transaction commits.
- `agent.partitions`: creates monthly partitions (the agent runs it at startup and then daily) and runs the resumable backfill. CLI: `uv run python -m agent.partitions backfill|ensure`.
- `scripts/seed.py` (`make seed`): Faker with a fixed seed.
  - 5 tenants, 50 services, 79 dependency edges, 10,000 alerts in 2,523 groups, 2,000 investigations (21,867 steps: v1 before 2026-06-01, v2 after), 17,486 tool calls, 784 actions, 714 decisions.
  - Each schema is written by its own role. It refuses to overwrite data without `--reset`; `--large` tops the agent up to 2,000,000 steps.
- Integration tests (`services/agent/tests/conftest.py`, `apps/gateway/src/testing.ts`) create a scratch database per test (Python) or suite (Jest), `triage_test_<unix seconds>_<run>_<id>`, as the Postgres superuser (`POSTGRES_PASSWORD`), and drop it afterwards. Each run first drops prefixed databases over an hour old. Both helpers repoint the database URL variables at `tests_never_use_the_dev_database`, so no test reaches the dev database, and refuse to run if the dev database name has the prefix.
- Gateway decisions (`apps/gateway/src/decisions.ts`): approve and reject insert into `approvals` (`ON CONFLICT DO NOTHING`; a second decision gets 409) and, for an approval, queue ActionApproved v1 through the outbox in the same transaction. Its FIFO group is the hash of tenant and target, so one service's actions run in order; its deduplication id is the action id. A store failure answers 503 with `Retry-After`.
- Gateway list (`apps/gateway/src/investigations.ts`): `GET /v1/investigations`, investigated alert groups of every tenant, newest first by (created_at, investigation id), with an opaque base64url cursor holding created_at to the microsecond. `limit` defaults to 25; outside 1 to 100, or with a cursor the gateway never issued, it answers 400. The title is the first alert's alertname and service.
- Gateway credentials (`apps/gateway/src/app.ts`): a `preParsing` hook asks for `GATEWAY_API_KEY` as X-API-Key (constant-time) on every route, unknown ones included, except the probes, the webhook and approve or reject; 401 problem details with `WWW-Authenticate: ApiKey header="X-API-Key"`. The web helpers send it.
- Gateway internal listener (`apps/gateway/src/internal.ts`, `executions.ts`), on `GATEWAY_INTERNAL_HOST`:`GATEWAY_INTERNAL_PORT` (127.0.0.1:4201), behind `EXECUTOR_SERVICE_TOKEN`: `POST /internal/actions/{id}/execution` claims an approved action (201 new, 200 an earlier claim, 404 no approved decision); `.../execution/result` records succeeded or failed once (409 after). The public listener has no such route. The sweeper logs executions stuck in claimed past `SWEEPER_STUCK_AFTER_MINUTES`.
- Webhook fingerprints (`apps/gateway/src/intake.ts`): `fingerprint()` ports Prometheus's `LabelSet.Fingerprint`; the webhook replaces each alert's fingerprint with it before deduplication and logs how many differed.
- Executor (`services/executor/src/executor/run.py`): `run_action(message, client, dry_run, effectors)` validates ActionApproved v1, checks `ALLOWED_KINDS` (and the effector) before any call, claims through `py_clients.ExecutionsClient`, checks the approved kind again, then runs the effector or, in a dry run, records the plan, and reports the result. Gateway unreachable or any error: refused. A claim found claimed is never retried. No consumer runs it yet.
- AI pipeline (step 6a), `services/agent/src/agent/`:
  - `providers.py`: `GeminiProvider` (google-genai 2.27.0 on an httpx2 client; explicit key sent as `x-goog-api-key`, `vertexai=False`; function calling mode ANY, thinking LOW, thought signatures kept in `tool_calls.provider_state`), `AnthropicProvider` and `OpenAICompatibleProvider` (NVIDIA NIM, OpenRouter, Ollama). All return the tool call, usage (input with cached tokens, output with thinking, cache reads and writes) and raise `RateLimited`, `QuotaExhausted` (a per-day 429), `CreditsExhausted` or `ModelError`; none retries.
  - `AnthropicProvider` (anthropic 1.15.0, native tool use, `claude-haiku-5-5`): a forced `tool_choice` (`any`, or the report tool) with `disable_parallel_tool_use`, which skips thinking, so no thinking block is ever replayed; summaries send `thinking: disabled`. One cache breakpoint on the system prompt caches the tool definitions and the system prompt (5,036 tokens). The explicit key and base URL win over `ANTHROPIC_*` variables, the SDK never retries, and its spans stay `metadata_only`. A 402 `billing_error`, a 400 naming a spend or usage limit, or a 429 without retry-after is `CreditsExhausted`; a 429 with retry-after is `RateLimited`; a refusal is not retried.
  - `llm.py`: `Router` (the primary; on a 429 or a spent quota Claude when `LLM_CLAUDE_FALLBACK` is on, then the free fallback; with `LLM_FALLBACK_ON_RATE_LIMIT=false`, for evals, the primary alone. Claude's 429 with a retry-after goes back to the job, which waits it out and asks again. The small role summarizes log pages, with Claude after it on a 429 or a spent quota) and `Limiter` (RPM in a sliding minute per process; before each request it refuses a role that `spent()` reports, so no request reaches a spent model). `spent()`: Claude from `LLM_CLAUDE_UNTIL` (UTC) on; RPD from `agent.model_calls` since midnight Pacific; a provider's "daily quota spent" since midnight Pacific; a `credits_exhausted` call since the month began (UTC), with a warning log line when Claude's credits run out. Parked investigations stay parked while every model of the chain is spent. `LLM_PRIMARY_RPD` defaults to 260, under the 275 requests measured on 2026-10-16 (not a documented limit). Every model call records `prompt_sha`, the sha256 of the prompt its process runs (`job.prompt_sha`); the eval refuses a run whose calls name another prompt.
  - Roles (`settings.py`): `LLM_{PRIMARY,FALLBACK,SMALL}_{PROVIDER,MODEL,BASE_URL,RPM,RPD}`, each provider's key from its own variable; keys and `FASTEMBED_CACHE_PATH` are required only when `AI_ENABLED=true`, which now defaults to false. Pinned 2026-10-15: `gemini-3.5-flash-lite`, fallback `deepseek-ai/deepseek-v4.1-flash`, small `gemini-3.1-flash-lite`. Claude: `LLM_CLAUDE_FALLBACK` (false), `LLM_CLAUDE_MODEL` (`claude-haiku-5-5`), `LLM_CLAUDE_RPM` (50), `LLM_CLAUDE_UNTIL` (2026-11-10), `ANTHROPIC_API_KEY`; any role may also run on `anthropic`, as the Haiku eval does.
  - `tools.py`: pydantic argument models and the definitions the model sees (plain JSON schema, a required `reason`): `get_alert_context` (the gateway's internal route, agent token), `query_metrics` (Prometheus selectors against the sim), `query_logs` (100 raw lines on page 1; later pages of 200 summarized by the small model), `list_deploys`, `get_dependencies`, `search_runbooks`, `propose_action` and `submit_report` (both handled by the job). Results are capped at 24,000 bytes and cited by their `tc_` id; 4xx is the model's to fix, 5xx and timeouts are missing evidence.
  - `prompt.py`: the system prompt lists the sim's 21-metric catalog (a test keeps the two equal) and the root-cause classes with a labelling convention: name the first cause in the chain, and the service where it lives (`evals/prompt-log.md` records every change).
  - `job.py`: the loop. Stops at the report, 15 tool calls, 300 s since `started_at` (moved forward by any time parked, so a run resumed after a quota stop keeps the time it had left), or when another turn would leave too little for a forced report; then a forced `submit_report` turn, or the job's own partial report. Hard 150,000-token cap through `TurnBudget` (see docs/memory.md). Every model request is recorded in `model_calls` with its tokens before the step it produced. `build_report` enforces evidence ids, missing evidence, the 0.95 and 0.6 confidence ceilings, and proposals from `proposed_actions`; status `awaiting_approval` when there are proposals.
  - `outbox.py`: StepRecorded v3 and ReportRecorded v1 committed with their step, relayed to `agent-events.fifo` (group = investigation, `message_type` attribute); 10 failed sends park a row.
  - `runbooks.py`: fastembed `nomic-ai/nomic-embed-text-v1.5` from `FASTEMBED_CACHE_PATH` (H:\.cache\fastembed, about 0.5 GB), `search_document: ` and `search_query: ` prefixes, pgvector cosine search. CLI `python -m agent.runbooks download|ingest|info`.
- Gateway events (`apps/gateway/src/events.ts`): consumes `agent-events.fifo` one message at a time into `investigation_steps` and `investigation_reports`, copies the report's proposals into `proposed_actions` and moves the alert group to the report's status; idempotent and in order per investigation.
  - Tool-call status and duration are paired by `tool_call_id` whichever step arrives first; a redelivery rewrites no row.
  - Only a newly inserted report adds actions and moves the status, and the status update skips a row the sweeper has locked (the sweeper copies the status on its next pass).
  - Permanent failures (bad JSON, contract, unknown `message_type`, Postgres classes 22, 23, 42) go to `agent-events-dlq.fifo` at once with their attributes and an `error` attribute. Transient ones retry in-process for up to 15 minutes, extending the message's visibility, so they cost no receives. `pnpm --filter @triage/gateway events:replay` (part of `make dlq-replay`) moves the DLQ back.
  - `GET /internal/investigations/{id}/alerts` on the internal listener serves the alert context to the agent's token only.
- Sim (`sim/src/sim/`): scenarios in `evals/scenarios/v1/*.yaml` (`SIM_SCENARIOS_DIR`; world and labels; times are offsets from the anchor, the alert's start), loaded with libyaml. Two apps over one state: the data app on `SIM_PORT` (4230) serves a Prometheus subset (`/api/v1/query_range`, `/query`, `/label/{name}/values`; selectors only), Loki-style logs with keyset cursor pages of at most 200, keyset deploys and dependencies, deterministic from seed and anchor (crc32 per sample). The admin app on `SIM_ADMIN_PORT` (4231) serves `POST /admin/scenarios/{id}/activate`, `PUT /admin/down {tool}` (503, answered by a middleware before parameter validation) and `GET /admin/state`.
  - The data app has no admin route. Only `make eval` and `make smoke` get `SIM_ADMIN_URL`; the agent's settings have no admin field. Tests send hostile arguments through every agent tool and see only the data port and data routes.
  - Activation sets the scenario's own `down` backend and returns the file's sha256. `run()` serves both ports from one process; `--reload` restarts on code or scenario changes (watchfiles).
  - `sim.validate` (`uv run python -m sim.validate <dirs>`) checks:
    - the alert rule against its metric, worst case with noise;
    - log streams' `asserts` against the metrics;
    - `version=` mentions against the deploys, and deploy chains;
    - cause before symptoms (`labels.onset`);
    - every required signal shows before the alert from a backend that is up;
    - acceptable and forbidden actions do not overlap, and rollbacks go to the right version.
- Scenario set v1 (`evals/scenarios/v1/`):
  - 40 scenarios, 8 failure families x 5 variants, written by `generate.py` and pinned by `manifest.json` (sha256, family, split, ci flag, down backend). Frozen on 2026-10-16, unfrozen the same day for the config timing fix and frozen again: a test pins the manifest's own sha256.
  - 15 dev and 25 test; the ci subset is one dev scenario per family; 6 scenarios mark a backend down.
  - Every scenario carries noise: an unrelated deploy about 2 hours before, a loud irrelevant error log, routine deploys, and baseline series and bystanders (coredns, a batch job).
  - Deploy timing: in 16 scenarios (one dev and one test per family) a harmless deploy 2 to 6 minutes before the alert is the newest deploy, its rollback forbidden and named as a red herring. Causal deploys land 4.5 to 35 minutes before the alert; bad-deploy-2 and bad-deploy-4 run as canaries first.
  - Config timing: in 16 scenarios (one dev and one test per family; 4 dev scenarios also have a harmless deploy) a harmless config or flag change 2 to 6 minutes before the alert is the newest change, named as a red herring; turning off a harmless flag is forbidden. Causal changes start 4.5 to 30 minutes before the alert: bad-config-3's flag builds up CPU for 15 minutes, and bad-config-5's covers 10% of charges first.
  - Labels: class, service, optional alternatives, onset, required signals, acceptable and forbidden actions, red herrings.
  - `make scenarios-check` runs the validator and fails when a released set's file changed against `SCENARIOS_BASE`.
  - `evals/README.md` has the families, scores and commands.
  - Regexes (`=~`, `!~`, `|~`, `!~`) run on RE2 (`google-re2`, linear time, the same syntax as Prometheus and Loki); a pattern RE2 rejects answers 400 `bad_data` with a message the agent can act on.
  - Every request stops at `SIM_REQUEST_TIME_LIMIT_SECONDS` (2 s) with a 400, and query_range at 250,000 points in total. Out-of-range times, steps and cursors answer 400, never 500. `/docs` and `/openapi.json` are off.
  - The agent's `get_dependencies` accepts only service names of letters, digits, `.`, `_` and `-`, and quotes the path segment, so it cannot reach other sim routes.
- Eval harness (`evals/harness/`):
  - `run.py`: one run through the real path (`make smoke`).
    - Activates the scenario through the sim's admin port and posts its alert to the webhook as a new tenant with an opaque name.
    - Waits for the agent, then for the report in the gateway's mirror.
    - Collects the gateway's report and steps, the agent's tool and model calls, and the approvals and executions.
    - Timing runs from the alert group's `created_at` to the report's `recorded_at`, both on the database clock.
  - `score.py`: deterministic per-run scores, metrics overall and per family, 95% bootstrap intervals that resample scenarios, and the floor rules. Action correctness carries its do-nothing baseline (what an agent that never proposes scores on the same runs), and action correctness on scenarios with an acceptable action is its own metric; the reports and the README table show both.
  - `eval.py`: `make eval SPLIT=dev|test SUBSET=ci|full RUNS=n [ESTIMATE=1] [SET_FLOORS=1] [ALLOW_LOOSER=1]`.
    - Runs every first run before any second, 60 s apart.
    - Caches each run under the prompt hash, the pinned models, the scenario sha256 and the run index, and resumes a parked run first.
    - A run with a fallback or unpinned model does not count; a run where the sim lost its scenario reruns once.
    - Exit codes: 1 for a broken floor or gate (any forbidden proposal), 2 for a quota stop (Gemini's day or Claude's credits), 3 for setup errors.
    - Writes `evals/reports/<date>.json` and `.md` and the README's eval table for the full test split on the default models; other runs write `<date>-<split>-<subset>-<runs>-<prompt sha, first 8>`, with `-<primary model>` appended on other models (`report_name`).
    - Costs: `cost_paid` prices every token at its model's rates (cache reads and writes at their own); `cost_billed` counts only models billed from credits, and its $0 gate holds only when every pinned model is free.
  - `evals/thresholds.json` holds the floor rules and per-scope floors (none set yet). `evals/pricing.json` holds each model's rates and billing: the Gemini models at paid-tier rates (official page, 2026-10-15) and `free`; `claude-haiku-5-5` at $0.10 in, $0.01 cache reads, $0.125 cache writes and $0.50 out per million tokens, `credits`. `evals/prompt-log.md` logs prompt changes; the prompt is frozen at `2b91d4523fb028a6` (`test_eval_score.py` pins it).
  - `make dev-agent AGENT_RELOAD=` runs the agent without its reloader, which watches the whole repo, so code edits never restart it during an eval.
- CI (`.github/`, step 7; every action pinned to a full commit SHA, every base image by digest):
  - `ci.yml` on pull requests and pushes to main. Jobs:
    - `checks`: lint, typecheck, contracts-check, build, actionlint, then scenarios-check and contracts-breaking against `HEAD^1`, or the pushed `before` commit when it exists.
    - `unit`, `integration` (`make test-int` with the fastembed cache restored), `secrets` (gitleaks over the full history) and `images` (build only).
    - `ci-result`, the one check branch protection requires: it fails unless every job succeeded.
    - No job gets a secret or a model key.
  - `publish.yml` pushes the four images to `ghcr.io/<owner>/triage-copilot-<service>` (tags `sha-<12>`, `main`). It runs only after a successful `ci.yml` run of a push to this repo's main, builds exactly that commit and downloads nothing from it.
  - `eval.yml`, off until the variable `EVAL_ENABLED` is true:
    - Triggers: a manual run (ci subset or full dev split) and Mondays 18:00 UTC (the ci subset, about 85 Gemini requests).
    - Only `GEMINI_API_KEY` is a secret. `NVIDIA_API_KEY` is the placeholder `ci-unused-not-a-key`, which the startup check accepts and evals never use. It fails if Claude could be called.
    - A quota stop fails with "rerun after the quota resets".
  - `.github/actions/setup` installs pnpm, Node 24, uv and the locked dependencies. It writes CI's `.env` with `scripts/ci_env.sh`: `.env.example` without the provider keys, `FASTEMBED_CACHE_PATH`, `AI_ENABLED` and `LLM_FALLBACK_ON_RATE_LIMIT`, which the Makefile's include would otherwise force on the job.
- Images (`infra/docker/`, built from the repo root, `.dockerignore` keeps `.env*` and `.git` out):
  - `python.Dockerfile` has targets agent, executor and sim (`uv sync --frozen --no-dev --no-editable`, non-root). Agent adds runbooks and migrations and mounts the model at `/models/fastembed`; sim adds the scenarios.
  - `gateway.Dockerfile` keeps the workspace layout for Node's type stripping and includes drizzle-kit for `db:migrate`.
  - Services still bind 127.0.0.1 by default; step 10's compose sets the hosts.

Still planned: the counted eval runs (ci subset, dev split, test split 3 times, then floors; commands in Current Development Status), the executor's queue consumer and effectors.

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
  - Then the id and body are validated (400), and the decision store (`decisions.ts`) gives 200, 404, 409, or 503 when it cannot record the decision.
  - Errors, including Fastify's own, are problem+json.
- Approver key: `APPROVER_API_KEY` (at least 32 characters) is required by the gateway and the web server and checked at startup. The Makefile unexports it and passes it only to `dev-ts` (gateway and web), so the agent, executor, sim, tests and tools never receive it. `apps/web/src/lib/decisions.ts` (`decideAction`) is the only code that sends it.
- web: home page shows gateway status through a server-side GraphQL call; `/healthz`, `/readyz` (gateway reachable).
- agent: `/healthz`, `/readyz` (Postgres and queue), the worker (consumer and resumable job, see Important Components), and the internal state API.
- Step 4 (workers), checked by tests:
  - Restart: an agent stopped mid-investigation finishes and records its running tool call, leaves the message, and the next agent resumes at the next step; every tool call ran once. After a hard kill only the interrupted call runs again, since it had no recorded result (`services/agent/tests/test_workers_int.py`).
  - DLQ: a message whose job fails 5 times lands in the DLQ, and its investigation is `failed`; replayed with the cause fixed, it resolves. A poison message goes to `dead_letters` without using receives. Both replay commands move messages back.
  - The heartbeat keeps a job that outlasts the visibility timeout from redelivering; at shutdown it stops.
  - Budget guard: a new investigation waits as `queued_budget` with no model call until the day's usage is under budget; resumed ones pass the budget but not `AI_ENABLED=false`; tokens add up per UTC day and per investigation. The tenant cap holds a third run of a tenant capped at 2 while another tenant proceeds.
  - Storm: 500 webhook posts from 10 concurrent clients (400 alerts in 8 groups plus 100 re-sends) are all acknowledged with p99 under 200 ms (about 86 ms measured), store every alert, open 8 groups and queue only 5 investigations, one message per FIFO group (`apps/gateway/src/intake.int.test.ts`). A new alert that arrives resolved opens a group without an investigation.
  - Rate limit: 429 with `Retry-After` and `RateLimit-*` headers once a credential uses its minute, a separate count per credential, and probes never limited. Limits: 201 alerts get 400, a body over 1 MB gets 413, and the request timeout is 10 s (`apps/gateway/src/app.test.ts`).
- Step 4 follow-up (parking and recovery sweeper), checked by tests:
  - Two agents (`services/agent/tests/test_parking_int.py`, with the real gateway process and its sweeper): a storm of 40 alerts over 8 groups starts 5 investigations against a tenant cap of 2. Three park and give up their messages; each runs after one re-queue; all 5 resolve with one model turn each; the DLQ and `dead_letters` stay empty; the groups take `resolved`.
  - A budget-parked investigation stays parked through three sweeps, then runs once the day's usage resets, after one re-queue.
  - Queue loss (`apps/gateway/src/sweeper.int.test.ts`): after a purge, one sweep re-queues exactly the lost, resumable and stuck investigations, each with a new deduplication id; parked, budget, busy, failed and uninvestigated groups stay; a second sweep re-queues nothing; one that parks again goes back once resumable.
  - The resumable order (`test_workers_int.py`): with both slots held, three parked investigations are not resumable; each freed slot goes to the earliest parked.
  - The internal API answers 401 problem details with `WWW-Authenticate` without the token, with a wrong one, or with another scheme (`test_agent_api.py`).
- executor: `/healthz`, `/readyz`, and the safety checks in `executor.run` (no consumer yet). sim: the scenario engine (step 6a).
- Step 6a (AI core), checked by tests: no investigation records more than 150,000 tokens over 30 seeded runs of a model that always fills its input bound and output allowance, and every request's input stays under its bound; a turn that cannot fit ends with the job's partial report; metrics down (503) puts `query_metrics` in missing evidence and caps confidence at 0.6; 5,000 log lines come back in pages of at most 200 (100 on page 1), later pages summarized, 15 pages under 40 KB of context; a proposal stays pending and the report reaches `agent-events.fifo` after its steps, in order; a report citing an unknown result goes back to the model; runbook search uses the nomic prefixes, and the cached model ranks the pool-exhaustion runbook first offline. Gateway: mirror rows, tool-call pairing, idempotent redelivery, steps before their report, the alerts route's 401 matrix. Providers: the Gemini key in `x-goog-api-key` whatever the environment, 429 per day versus per minute, OpenAI-compatible request and usage; router fallback and eval mode; the limiter.
- Step 6b (in progress, 2026-10-16), checked by tests (`make test`: Python 379 passed, TypeScript passing; `make test-int`: TypeScript 33, Python 39):
  - Scenario set v1: all 40 scenarios pass the validator (0 problems). Every required signal shows in the real tools' results against the in-process sim. A cold query for every log line stays under 1.5 s. The frozen manifest pins every file, the generator rewrites them byte for byte, and the set has 8 families of 5, 15 dev and 25 test, and 6 degraded scenarios covering all four backends.
  - Deploy timing: a harmless deploy is the newest deploy, minutes before the alert, in a dev and a test scenario of every family, forbidden to roll back and named as a red herring; causal deploys span under 5 to over 30 minutes before the alert.
  - Config timing: the same for config and flag changes, with turning off a harmless flag forbidden; causal changes start from under 5 to over 15 minutes before the alert. Both timing tests fail when a decoy loses its red herring or its forbidden action.
  - Validator: one test per inconsistency, each caught on a mutated copy of the pool scenario. The 6a pool timeline (warn lines claiming a full pool from -8m while the pool filled until -4m) fails it.
  - Isolation: the data app answers 404 or 405 on every admin path, encoded or traversing, and leaks no admin state. Hostile arguments in every string argument of every sim tool reach only 127.0.0.1:4230 and the data routes. The agent's settings hold no admin address.
  - Resume: a run that parked for model quota mid-investigation and resumed after more than `MAX_SECONDS` finishes with a full, not a partial, report (`test_workers_int.py`).
  - Scoring:
    - top-1 needs the class and the service;
    - grounding needs a cited result of the signal's own tool;
    - action correctness needs every proposal acceptable, and carries its do-nothing baseline; the share on scenarios with an acceptable action is its own metric, and the README table shows both;
    - forbidden proposals count with any parameters;
    - validity counts only `ToolInputError`;
    - degraded runs must name the down tool;
    - intervals repeat exactly from the seed;
    - floors follow the rules, including the known-miss exception, and only tighten;
    - the plan runs every scenario once before any second run, and the cache keys change with the prompt, the models and the scenario.
  - An independent review of v1 found label ambiguity, noisy-neighbor data that argued the wrong class, family tells and wrong log texts. Fixed before any counted run: the prompt's labelling convention, baseline series and bystanders, node contention, an OOM sawtooth, and corrected texts and action labels (docs/memory.md).
- Step 5 (tests), checked by tests:
  - Fingerprints: golden vectors from prometheus/common, a real Alertmanager 0.28.1 payload, UTF-8 byte order, and fast-check properties (same labels in any order, the same fingerprint; different sets, different ones). Grouping property: alerts sent within one window join one group per group key, whatever their label order or claimed fingerprint (`intake.int.test.ts`).
  - Decisions (`decisions.int.test.ts`): a second approval and an approval after a rejection get 409; of 20 concurrent decisions exactly one is recorded, with at most one ActionApproved; when queueing fails, no decision is recorded.
  - Keyset (`investigations.int.test.ts`): no duplicate and no gap while a writer inserts before and behind the cursor; pages ending inside one millisecond and one microsecond.
  - Credentials (`app.test.ts`): the webhook needs no X-API-Key; every other `/v1` route and `/graphql` answer 401 without it; the public listener answers 404 on the executor's routes.
  - Executor (`test_executor_run.py`; end to end against the gateway process in `test_executor_int.py`): no approval row, a rejection or an unknown action is refused; a kind off the allowlist is refused before any call; a replayed idempotency key runs once; a dry run never acts; an unreachable or failing gateway means no action. The database refuses an execution without an approved decision (`executions.int.test.ts`).
  - Agent loop (`test_workers_int.py`): with the scripted model, the loop stops at 15 tool calls or at 150,000 tokens with a partial report and no exception.
  - Story A (partition swap) and story B (upcaster) run in the integration layer: `test_partitioning_int.py`, `test_migrations_int.py`.
- Both schemas, migrated by Drizzle (`gateway.__drizzle_migrations`) and Alembic (`agent.alembic_version`). Tests apply both chains to an empty database, run the seed, and check the models against the result (`alembic check`). They also downgrade the agent chain to empty and back.
- Story A (online partitioning of `investigation_steps`): expand, dual-write flag, resumable backfill, verified swap, contract, and the partition job. A test runs the whole sequence on 2,000,000 seeded steps while a writer records steps: zero errors, writes in every phase, slowest write under 70 ms. Two guard tests confirm that the swap refuses before the backfill finishes, and the drop refuses while the old table holds a row the new one lacks.
- Story B (event versioning): replaying stored v1 steps returns v2 objects; a golden snapshot pins the result, and stored rows are never rewritten.

## Current Development Status

Steps 1 (scaffolding), 2 (contracts) and 3 (data) done on 2026-10-14, step 4 (workers) with its follow-up (parking, recovery sweeper), step 5 (tests) and step 6a (AI core) on 2026-10-15. A live run on bad-deploy found the right root cause and proposed the rollback in 6 model turns and 35,090 tokens.

Step 6b is in progress: the scenario set (frozen, manifest sha256 `b81c80f4ba442427...`), validator, admin isolation, harness and docs are built and tested. Prompt tuning on the dev split is finished (evals/README.md, Status; `evals/prompt-log.md`):

- Baseline `146b161991e14f7c`: dev top-1 0.53, action correctness 0.60 against a do-nothing baseline of 0.53, one forbidden proposal.
- Change 1, `2b91d4523fb028a6`: dev top-1 0.73, grounding 0.93, action correctness 0.73, 0.43 on scenarios with an acceptable action, no forbidden proposal.
- Change 2, `e0f86d4ad3b47b1c` (finished 2026-10-17): dev top-1 0.53, grounding 0.80, action correctness 0.60, 2 forbidden proposals.
- The prompt is frozen at change 1 (2026-10-17); a test pins its sha. The ci subset on it, from cached runs: top-1 0.62, action correctness 0.75 against a do-nothing baseline of 0.50, no forbidden proposal.
- Claude Haiku 5.5 is built in as the first fallback (off by default) and ran the dev split once on the frozen prompt (2026-10-17): top-1 0.80, action correctness 0.80, no forbidden proposal, validity 1.00, $0.0971 of credits for 15 investigations. No model-specific breakage; 11 of 15 runs reached the token reserve, since Haiku counts about 1.5 times Gemini's tokens per turn.
- Python unit tests: 400 passed; `make test-int`: Jest 33 and pytest 41 passed; ruff and pyright clean. The dev database is at agent 0009.
- The test split on Gemini started on 2026-10-17: 9 of 75 runs done, then exit 2 when the UTC day's token budget ran out (3,050,302 of 3,000,000; Gemini at 230 of 260 requests). Day 2, 2026-10-18: 32 of 75 done, then exit 2 at the agent's limit of 260 Gemini requests, all answered (1,803,303 tokens). db-pool-exhaustion-4 run 2 is parked with 16 steps.
- Gateway, web, agent and sim are stopped, with no process left on their ports, and the compose infra is down (volumes kept).
- `make lint` passes (2026-10-17): `.prettierignore` skips the frozen scenario YAMLs and the eval reports, which are generated, instead of reformatting them.
- Step 7 (CI/CD) is built locally and verified (2026-10-17): actionlint, the four image builds, lint, typecheck, unit tests, build, contracts and scenario checks, gitleaks. The GitHub steps are done (the user confirmed on 2026-10-18): PR #1 is `0779d77` on origin/main. `make test-int` runs locally again after the exam.

Next, in this order:

1. Resume the test split after midnight Pacific with the exact steps in docs/todo.md (step 6b): infra, sim, the parked run's world, gateway, agent, eval. The other 43 runs need about 470 requests, about 2 more quota days.
2. Set the floors: `make eval SPLIT=test SET_FLOORS=1 ...` and `make eval SUBSET=ci SET_FLOORS=1 ...`. Both rescore cached runs and make no model calls.
3. The Claude comparison on the test split (evals/README.md, Running), once the Gemini split is complete and before the credits expire on 2026-11-10: about 846 calls, 1.7 hours, and about $0.49 of credits at the dev pass's $0.0065 per investigation.

## Known Limitations

- All targets are design goals until the test split runs; only the dev split and the ci subset have run.
- Claude Haiku counts about 1.5 times Gemini's tokens for the same prompt, and the 150,000-token cap counts cached input too, so most Haiku investigations end at the token reserve with a forced report (dev split, 2026-10-17). Time to first hypothesis can only be measured by a proxy, the time to the report, until the agent records a hypothesis step (docs/todo.md).
- Scenario set v1 gaps, for v2:
  - No scenario's answer is traffic_surge, and none's is unknown.
  - Pool exhaustion is always `database`, and no certificate case has an acceptable action.
  - 21 of 40 scenarios have no acceptable action; reports show the do-nothing baseline beside action correctness, and action correctness on the other 19.
  - A run resumed after a quota stop sees tool windows up to 15 minutes after the alert, which can push single-line evidence off the first log page.
  - Some callers stay healthy when the service they call fails, and external providers get routine deploys.
- Sim backlog (docs/todo.md):
  - Log cursors are not bound to the query, window or world.
  - `GET /api/v1/deploys?service=<unknown>` answers an empty 200 instead of 404.
  - An activation anchor outside 1970 to 3000 answers 500 on Windows, because `Activation.anchor` has no bounds.
- The contracts are ahead of the code:
  - `GET /v1/investigations/{id}` is not served (step 9). Nothing but the seed writes `proposed_actions`: no consumer copies ActionProposed into the gateway yet.
  - The executor has no queue consumer and no effectors: ActionApproved messages wait in `actions.fifo`, and `executor.run` runs only in tests.
  - Executions stuck in claimed are only logged by the sweeper; alerting on them belongs to step 8.
  - The investigation list spans every tenant, since X-API-Key is not tied to one.
  - The agent serves `GET .../state` only; `POST .../cancel` is not built, and `cancel_requested` is always false.
  - The GraphQL `investigations` and `investigation` queries have no resolvers (step 9).
- `RootCauseClass` and `ActionKind` are initial enums; the scenario engine and eval steps may extend them. A new value in a response breaks older generated Python clients (pydantic enums reject unknown values), so ship the gateway and the clients together.
- Action `parameters` are free-form; per-kind parameter schemas are open (docs/todo.md).
- The gateway derives a tool call's `timeout` status from the agent's summary text (`failed: ...Timeout...:`), since StepRecorded v3 has no status field (a `ponytail:` note in `events.ts`).
- The sim serves one active scenario per process, so evals run scenarios one after another.
- The gateway stores alert labels but not annotations, so `get_alert_context` cannot show an alert's summary text.
- The rate limiter's minute window lives in the agent process, and its day count sees only recorded calls; several agents sharing one key can pass a limit by a few requests.
- Agent outbox rows parked after 10 failed sends are not replayed by `make dlq-replay` yet.
- Older `final_report` rows (seed, step 4 placeholders) keep the previous report shape.
- Agent tables record a tenant only when an investigation parks; the agent reads it from InvestigationRequested v2.
- Parked investigations resume up to `SWEEPER_INTERVAL_SECONDS` after their slot or budget frees, since the sweeper polls. One re-queued that loses the freed slot to a new arrival parks again at the back of the line.
- The sweeper asks the agent once per queued group per sweep (a `ponytail:` note): fine for hundreds; batch it past a few thousand.
- Only investigations the gateway requested (with an alert group) are re-queued; `--large` seed rows and hand-sent test messages are not.
- The sweeper copies only finished statuses onto groups; `running` and `failed` stay `queued` in the gateway until step 9 brings the status flow.
- Rate limits count in memory, so they hold for one gateway instance only. Requests without a credential are counted per client address, which is the proxy's behind Tailscale Funnel unless `trustProxy` is set (step 10).
- A duplicate delivery of a running investigation ("busy") still returns to SQS and uses up receives; one that outlasts about 5 backoffs reaches the DLQ.
- A group opened without an investigation (cap reached, or only resolved alerts) stays without one for its 5-minute window, even if firing alerts join it.
- The storm test sends its 500 posts within seconds rather than over 2 minutes, which is harsher on latency.
- `STEPS_DUAL_WRITE` and the dual-write code stay for the partitioning test; past revision 0004 the flag must stay false.
- `make seed SEED_ARGS=--large` adds agent rows only: its extra investigations have no gateway alert groups.
- Embeddings are `vector(768)`; another dimension needs a new column and a re-embed.
- Apps run natively in dev; Dockerfiles and the prod compose override come with steps 7 and 10.
- Gateway subscriptions use in-process pub/sub, so they work with one gateway instance only.
- Toxiproxy runs without proxies; fault-injection tests will create them.
- ElasticMQ messages survive container restarts (H2 on the `elasticmq-data` volume); if that volume is lost, the recovery sweeper re-queues the lost investigations after `SWEEPER_STUCK_AFTER_MINUTES`.

## Planned Features

See `docs/todo.md` for the 10-step build plan (8 build steps, review UI, deploy).
