# Activity Log

Chronological record of significant development activity, newest first. Skip trivial changes. Do not auto-commit this file.

## Entry Format

```
### YYYY-MM-DD: <task performed>

- Changes: <important changes>
- Files: <relevant files>
- Validation: <tests or checks run and results>
- Outstanding: <open issues, or "None">
```

## Entries

### 2026-10-15: Independent review of the events consumer and the sim

- Changes:
  - Two fresh agents reviewed the gateway's agent-events consumer and `sim/`, with repros. Separate agents fixed the high and medium findings.
  - Gateway:
    - One message per receive.
    - Permanent failures quarantined to `agent-events-dlq.fifo` at once; transient ones retried in-process for up to 15 minutes without costing receives.
    - Tool-call pairing that does not depend on arrival order, and redeliveries that rewrite no row.
    - Report actions and status only from a newly inserted report; the status update skips the sweeper's locked row.
    - `events:replay` added to `make dlq-replay`.
  - Sim:
    - RE2 for every regex (`google-re2`, user decision; import checked on this PC), with rejected patterns as 400 `bad_data`.
    - A 2 s per-request time limit and a 250,000-point cap.
    - The down switch answers before parameter validation.
    - Out-of-range inputs answer 400; the docs routes are off; `per_minute` is at most 6000.
  - Agent: `get_dependencies` validates and quotes the service name (path traversal reached `/admin/state`).
  - todo item for storing Alertmanager annotations; the low findings listed in docs/todo.md.
- Files: apps/gateway/src (events, events.test, events.int.test, config, main, outbox, cli), apps/gateway/package.json, sim/src/sim (promql, logql, world, main, scenario), sim/tests (test_sim_limits, test_sim_api), sim/pyproject.toml, uv.lock, services/agent/src/agent/tools.py, services/agent/tests/test_llm.py, Makefile, .env.example, README.md, docs/
- Validation:
  - `make test` (Turbo forced) and `make test-int` pass, once each after the fixes: TypeScript 84 unit and 33 integration tests, Python 222 unit and 38 integration tests (sim 114).
  - `make lint`, `typecheck` and `contracts-check` pass.
  - Sim determinism digests are unchanged, and `(\w+\s*)+timeout` answers in 45 to 167 ms.
  - Live smoke on bad-deploy (run 2):
    - Result: `awaiting_approval`, 6 turns, 35,218 tokens, the same root cause and rollback.
    - Tool latencies: get_alert_context 8 ms, query_metrics 5 ms, query_logs 30 ms, list_deploys 4 ms, propose_action under 1 ms.
    - The gateway mirrored the report and 11 steps.
- Outstanding: the low findings in docs/todo.md.

### 2026-10-15: Step 6a, AI core

- Changes:
  - Provider layer: Gemini (google-genai 2.27.0) and OpenAI-compatible clients. Roles come from env vars: primary `gemini-3.5-flash-lite`, fallback `deepseek-ai/deepseek-v4.1-flash` on NIM, small `gemini-3.1-flash-lite`. A 429 or a spent day falls back outside evals. An RPM and RPD limiter keeps each role under its limits, and `model_calls` records every request. `AI_ENABLED` now defaults to false.
  - Tools with pydantic arguments, results cited by id:
    - `get_alert_context` reads the new gateway internal route with the agent's token.
    - `query_metrics`, `query_logs` (later pages summarized), `list_deploys` and `get_dependencies` read the sim.
    - `search_runbooks` uses fastembed nomic with task prefixes, over 4 new runbooks.
    - `propose_action` writes only a pending row.
  - Agent loop:
    - It stops at the report, 15 tool calls, 5 minutes or the token reserve, then forces a final report.
    - 150,000 tokens is a hard cap, enforced by an input bound on every call.
    - The report is structured, with evidence ids, missing evidence and confidence ceilings enforced in code.
  - Delivery: ReportRecorded v1 (spec-first, codegen in both languages, contract tests). The agent outbox relays to `agent-events.fifo`, and a gateway consumer fills the mirror tables, pairing tool calls by id. ActionProposed v1 is marked unused in a union file.
  - Sim: a deterministic scenario engine (Prometheus, Loki-style and deploy APIs, a down switch), plus scenarios bad-deploy and db-pool-exhaustion. The sim and the gateway side were built by forked agents and checked through their tests and the live smoke run, not line by line.
  - Harness runner `evals/harness/run.py` (`make smoke`) and Makefile targets `embed-model` and `runbooks`. Provider keys are unexported except for `dev-agent`.
- Files: contracts/ (report-recorded.v1, action-proposed union, gateway.yaml 0.4.0), packages/ts-clients (codegen, contract tests), packages/py-clients (AlertContextClient, tests), services/agent (0007, db, settings, model, providers, llm, prompt, tools, runbooks, outbox, job, main, tests), apps/gateway (0004, schema, events, internal, investigations, config, main, tests), sim/, evals/scenarios, evals/harness/run.py, runbooks/, infra/elasticmq.conf, Makefile, pyproject.toml, uv.lock, .env.example, README.md, docs/
- Validation:
  - `make test` (Turbo forced) and `make test-int` passed three runs in a row: Python 189 unit and 38 integration tests; TypeScript 83 unit and 27 integration tests.
  - `make lint`, `typecheck`, `contracts-check` and `contracts-breaking` pass.
  - The hard-cap, degraded-tool and pagination tests pass.
  - Live smoke with `AI_ENABLED=true` on bad-deploy:
    - Result: `awaiting_approval` after 6 Gemini turns and 35,090 tokens, every turn's input under its bound.
    - Report: `bad_deploy` on checkout at 0.95, with a rollback to v1.7.3 proposed.
    - The gateway mirrored the report and 11 steps.
  - Live probe: deepseek-v4.1-flash answered with a tool call. nemotron-3-super gave none, kimi-k3 is end of life, glm-5.3 timed out.
- Outstanding: docs/todo.md (6b, `FASTEMBED_CACHE_PATH` in `.env`, AI Studio limits, ActionProposed v1, outbox replay, alert annotations).

### 2026-10-15: Step 5 tests

- Changes:
  - Test harness (user decisions: scratch databases, not Testcontainers):
    - Scratch databases are named `triage_test_<unix seconds>_<run>_<id>` and dropped after each test.
    - A stale cleanup drops only names in that exact format, over an hour old.
    - Both harnesses repoint the database URL variables at a database that does not exist, and refuse a dev database name with the prefix.
    - `db.int.test.ts` and `test_infra_int.py` moved off the dev database.
  - Gateway fingerprints: `fingerprint()` ports Prometheus's `LabelSet.Fingerprint`, and the webhook deduplicates on it and logs mismatches. The contract example's made-up fingerprint was fixed, and a real Alertmanager 0.28.1 payload was captured as a fixture.
  - Gateway decisions: the real decision store (`decisions.ts`), with ActionApproved through the outbox (new `queue` column, `ACTIONS_QUEUE`, `actions.fifo` in ElasticMQ). The relay keeps each FIFO group in order after a failed send. A store failure answers 503.
  - Gateway API: X-API-Key (`GATEWAY_API_KEY`) on every route except the probes, the webhook and approve or reject. `GET /v1/investigations` with keyset pagination (limit 25 by default, 1 to 100). The web helper sends the key.
  - Executor (user decisions): `executions` table (Drizzle 0003), with a foreign key to approved decisions only.
    - Claim and result routes sit on a separate listener (`GATEWAY_INTERNAL_PORT` 4201, 127.0.0.1) behind `EXECUTOR_SERVICE_TOKEN`; the sweeper logs stuck claims.
    - `executor.run.run_action` checks the allowlist, claims (idempotent, fail closed) and refuses when the approved action differs from the message. A dry run records a plan.
    - `py_clients.ExecutionsClient`.
  - The gateway refuses to start when two of its four credentials are equal.
  - Agent: an investigation stops at 15 tool calls or 150,000 tokens with a partial report (`Report.partial`).
  - Migrations: agent 0001 sets `lock_timeout`; docs/migrations.md marks 0005's downgrade as a maintenance-window step. Gateway API 0.3.0; clients regenerated.
  - `.env.example` gains `ACTIONS_QUEUE`, `GATEWAY_API_KEY`, `GATEWAY_INTERNAL_HOST`, `GATEWAY_INTERNAL_PORT` and `EXECUTOR_SERVICE_TOKEN`. Random values for the two secrets were appended to `.env` without being shown.
  - New dev dependency: fast-check 4.10.2 (gateway). The executor now depends on the workspace `py-clients` and on httpx2, which was already locked.
- Files:
  - apps/gateway:
    - src: app.ts, config.ts, decisions.ts (new), executions.ts (new), intake.ts, internal.ts (new), investigations.ts (new), main.ts, outbox.ts, schema.ts, sweeper.ts, testing.ts.
    - Tests: app.test.ts, intake.test.ts, internal.test.ts and investigations.test.ts (new), db.int.test.ts, decisions.int.test.ts, executions.int.test.ts and investigations.int.test.ts (new), intake.int.test.ts, sweeper.int.test.ts.
    - src/testdata/alertmanager-0.28.1-webhook.json (new); drizzle/0003_decisions.sql and meta/; package.json.
  - apps/web: src/lib (config.ts, gateway.ts, gateway.test.ts), src/app (page.tsx, readyz/route.ts).
  - Contracts and clients: contracts/openapi (gateway.yaml, examples/alertmanager-webhook.json), packages/ts-clients (src/gateway.ts, generated), packages/py-clients (clients.py, generated, tests).
  - services/agent: src/agent (job.py, model.py), migrations/versions/0001_baseline.py, tests (conftest.py, test_infra_int.py, test_parking_int.py, test_workers_int.py, test_executor_int.py (new)).
  - services/executor: src/executor/run.py (new), tests/test_executor_run.py (new), pyproject.toml.
  - Infra, config and docs: infra/elasticmq.conf, .env.example, uv.lock, pnpm-lock.yaml, README.md, docs/.
- Validation:
  - `make lint`, `typecheck`, `contracts-check` and `contracts-breaking` pass; oasdiff reports no breaking change.
  - Unit, forced past Turbo's cache (`pnpm turbo run test --force`, 0 cached): Jest 72 tests (ts-config 2, ts-clients 29, web 3, gateway 38) in 4.1 s. `uv run pytest -m "not integration"`: 80 passed in 7.2 s (8.2 s wall).
  - `make test-int`, three runs in a row, all green. pnpm runs `test:int` directly, never through Turbo, so nothing was replayed.
    - Run 1: Jest 23/23 in 5.9 s, pytest 30 in 102.6 s, 1 m 51 s, storm p99 79.7 ms.
    - Run 2: Jest 23/23 in 5.6 s, pytest 30 in 101.1 s, 1 m 49 s, storm p99 78.8 ms.
    - Run 3: Jest 23/23 in 5.7 s, pytest 30 in 101.3 s, 1 m 49 s, storm p99 80.1 ms.
    - The partition-swap tests (`test_partitioning_int.py`, 3) and the upcaster and migration tests (`test_migrations_int.py`, 3) are among the 30.
  - Mutation check: the old `::timestamptz` cursor cast failed 2 of 3 keyset tests. That bug was real: postgres.js turns the parameter into a JS Date and drops the microseconds, so a page skipped the rest of a millisecond tie group.
  - A first full run failed the parking storm test: its 40 alerts had identical labels, which now correctly dedupe into one. They got distinct labels.
- Review fixes (independent review: 0 high, 1 medium, 5 low):
  - Fixed, low:
    - The executor runs nothing that differs from the approved message.
    - A cursor naming an impossible instant gets 400, not 500.
    - The four credentials must differ.
    - The relay keeps FIFO-group order after a failed send (new test).
    - The stale cleanup ignores names it did not make.
  - Not changed, medium: key guessing is not throttled, because each invented credential gets its own rate-limit count. This predates the step and is now part of the Funnel item in docs/todo.md.
- Outstanding:
  - The executor's queue consumer and effectors, and the gateway's ActionProposed consumer.
  - Keeping the internal listener off Funnel (step 10); alerts on stuck executions (step 8).
  - Degraded-tool and `query_logs` tests (step 6). All are in docs/todo.md.

### 2026-10-15: Step 4 follow-up, parking and recovery sweeper

- Changes:
  - Agent:
    - A message that must wait for a tenant slot or model budget parks its investigation and is deleted (Alembic 0006: `tenant_id`, `waiting_reason`, `waiting_since`). Only a duplicate delivery still returns to SQS as "busy".
    - `Job.resumable`: budget waiters once model calls are back; tenant-cap waiters first parked first, never more than the free slots read from `pg_locks`.
  - Agent internal API (`agent/api.py`): `GET /internal/investigations/{id}/state` behind `AGENT_SERVICE_TOKEN` (401 problem details with `WWW-Authenticate: Bearer`); every error is problem details.
  - Gateway recovery sweeper (`sweeper.ts`):
    - Every `SWEEPER_INTERVAL_SECONDS`, over queued groups without an outbox row (SKIP LOCKED), it re-queues through the outbox resumable parked investigations and stuck ones: unknown to the agent, or no step for `SWEEPER_STUCK_AFTER_MINUTES`.
    - Each re-queue has its own deduplication id. Groups take finished statuses.
    - Drizzle 0002 adds `requeued_at` and `requeue_count`. `investigationRequest` in `intake.ts` builds the message for intake and sweeper.
  - Contract: agent API 0.3.0 (`waiting_for`, `waiting_since`, `resumable`); codegen.
  - Config: `.env.example` gains `AGENT_SERVICE_TOKEN`, `AGENT_URL` and `SWEEPER_*`. A random `AGENT_SERVICE_TOKEN` was appended to `.env`, value not shown.
  - The gateway's integration helpers moved to `src/testing.ts`.
- Files:
  - services/agent: src/agent/api.py (new), job.py, consumer.py, db.py, settings.py, main.py; migrations/versions/0006_waiting.py (new); tests: test_parking_int.py (new), test_workers_int.py, test_consumer.py, test_agent_api.py.
  - apps/gateway: src/sweeper.ts, sweeper.test.ts, sweeper.int.test.ts and testing.ts (new); intake.ts, config.ts, main.ts, schema.ts, intake.int.test.ts; drizzle/0002_sweeper.sql and meta/ (new).
  - Contracts and clients: contracts/openapi/agent.yaml; packages/ts-clients and packages/py-clients generated files; packages/py-clients/tests/test_clients.py.
  - Config and docs: .env.example, docs/.
- Validation:
  - `make lint`, `typecheck`, `contracts-check` and `contracts-breaking` pass. oasdiff: no breaking change in agent.yaml.
  - `make test` passed 3 runs in a row: pytest 62, Jest 18 in the gateway; the TypeScript suites were forced past Turbo's cache.
  - `make test-int` passed 3 runs in a row: Jest 7, pytest 26, 104 to 115 s, storm p99 109.7, 95.0 and 81.3 ms.
  - Done-when checks:
    - (1) Two agents with the real gateway process: 5 investigations under a cap of 2. Three parked, each ran after one re-queue; no DLQ entry or dead letter.
    - (2) A budget-parked investigation stayed parked through three sweeps and ran once the budget reset.
    - (3) Queue loss: each stuck group was re-enqueued exactly once, and a second sweep re-enqueued nothing.
    - (4) The internal API answers 401 without the token, with a wrong one, or with another scheme.
  - Dev database migrated to agent 0006 and gateway 0002 (`make migrate`); `alembic check` clean. drizzle-kit generated only the two new columns, so `schema.ts` had not drifted.
- Not changed: migration 0001 still sets no `lock_timeout`, and `queued_budget` stays in the enum until its contract step. Both are in docs/todo.md.

### 2026-10-15: Step 4 workers

- Changes:
  - Contracts: InvestigationRequested v2 adds `tenant_id` for the per-tenant cap, and its description fixes MessageGroupId and MessageDeduplicationId as SHA-256 hashes of tenant and group key (and window start) (user decision). The union accepts v1 (deprecated) and v2. `gateway.yaml` describes per-tenant webhook tokens, rate limiting, the 200-alert limit and a 413. Codegen regenerated.
  - Gateway:
    - Per-tenant webhook tokens (user decision): `tenants.webhook_token_sha256` and `pnpm tenant:token`.
    - `POST /v1/alerts`: auth in `preParsing`, fingerprint dedupe, 5-minute windows opened by the first new alert, 202 `{group_id, deduped}`.
    - A transactional outbox with a relay to `investigations.fifo` and `outbox:replay` for parked rows.
    - @fastify/rate-limit keyed by credential hash, 1 MB, 200 alerts and a 10 s request timeout. Problem titles now come from the status code.
    - `buildApp` is async. Drizzle migration 0001. New dependencies: @fastify/rate-limit 11.2.1 and @aws-sdk/client-sqs 3.1150.0.
  - Agent:
    - An asyncio consumer: 20 s long poll, 5 in flight, visibility heartbeat every 30 s, graceful shutdown that leaves the message.
    - A resumable job: two steps per tool call, one transaction per step with its tool_calls row, tool retries with jitter, model retries honoring Retry-After.
    - A per-tenant cap with advisory locks, a budget guard (`AI_ENABLED`, `DAILY_AI_TOKEN_BUDGET`, `ai_usage`), poison messages to `dead_letters`, and `python -m agent.dlq replay`.
    - A placeholder `ScriptedModel` until step 6. Alembic 0005.
  - `make dlq-replay`; `.env.example` and README updated; the ElasticMQ probe's queue fixtures moved to `conftest.py`.
- Files: contracts/ (messages/investigation-requested*.json, openapi/gateway.yaml), packages/ts-clients (src/generated/, src/contracts.test.ts), packages/py-clients (src/py_clients/generated/, tests/test_contracts.py), apps/gateway (src/app.ts, intake.ts, outbox.ts, cli.ts, main.ts, config.ts, schema.ts, app.test.ts, db.int.test.ts, intake.int.test.ts, drizzle/0001_workers.sql, drizzle/meta/, package.json), services/agent (src/agent/consumer.py, job.py, model.py, dlq.py, main.py, settings.py, db.py, migrations/versions/0005_workers.py, tests/conftest.py, test_consumer.py, test_elasticmq_int.py, test_workers_int.py), pnpm-lock.yaml, Makefile, .env.example, README.md, docs/
- Validation:
  - After the review fixes: `make lint`, `typecheck`, `test` (Jest 50, pytest 54), `test-int` (Jest 6, pytest 23, about 85 s), `contracts-check` and `contracts-breaking` pass.
  - Done-when checks:
    - (1) `test_shutdown_mid_investigation_resumes_at_the_next_step`: every tool ran once across the restart. `test_killed_mid_tool_call_repeats_only_that_call`: only the interrupted call ran twice.
    - (2) The storm test over 8 group keys: p99 86.4, 86.5 and 86.7 ms over 3 runs; 8 groups, 5 investigations, 5 queued messages; 400 of 400 alerts stored.
    - (3) `test_a_message_that_fails_five_times_lands_in_the_dlq`: 5 model calls, then the DLQ, and the investigation is `failed`; after `replay_dlq` it resolves.
  - The gateway integration and consumer unit tests passed 3 runs in a row.
  - A first `maxItems: 200` failed `contracts-breaking` (`request-property-max-items-set`), so the limit moved to the description.
  - Live run on the dev stack after `make migrate`:
    - `tenant:token` issued a token. The sample webhook got 202 with `RateLimit-*` headers, its repeat got `deduped: true`, and a wrong token got 401.
    - The relay queued the message, and the agent resolved the investigation with the placeholder report in 1 step.
    - Both queues ended empty, with no error in either log.
  - The live run came before the review fixes.
- Review fixes (independent review: 1 high, 2 medium and 4 low findings):
  - High: `failed` counted as finished, so a message replayed from the DLQ was deleted without running. `failed` now resumes, and the DLQ test replays its message to resolution (the separate replay test, which used a fresh message, is gone).
  - Medium: nothing capped investigations per storm, and the storm test's 3 keys could not exceed 5. The gateway now starts at most `MAX_NEW_INVESTIGATIONS` (5) per tenant in any 5 minutes and keeps further groups `queued_not_investigated`, under a per-tenant lock. The storm runs over 8 keys.
  - Low:
    - A new alert that arrives resolved no longer opens an investigation.
    - One receive's later messages of a FIFO group go back instead of running in parallel (new unit test).
    - `AGENT_MAX_IN_FLIGHT` is capped at 10, the SQS receive maximum.
    - Request and report steps no longer put the ToolRequest or report JSON and the model latency into `result` and `latency_ms`, which StepRecorded v3 defines as the tool's. Arguments now live in the `tool_calls` row written with the request (0005 makes its latency nullable).
  - Not changed (medium): budget waiters keep their slots, so resumed investigations can wait behind them until the budget frees. Documented with a `ponytail:` note and in docs/memory.md.
- Outstanding: the recovery sweeper; trust-proxy addressing behind Tailscale Funnel; StepRecorded publishing and the report message (step 6). All are in docs/todo.md.

### 2026-10-14: StepRecorded v3 non-empty summary

- Changes: `step-recorded.v3.schema.json` adds `minLength: 1` to `summary`, and the stored `StepRecordedV2` model adds `min_length=1` (user decision). Both test tables gained the boundary cases: an empty summary is rejected, one character is allowed. Codegen regenerated.
- Files: contracts/messages/step-recorded.v3.schema.json, packages/py-clients/src/py_clients/generated/messages/step_recorded_v3_schema.py, packages/ts-clients (src/generated/messages.ts, src/contracts.test.ts), services/agent (src/agent/steps.py, tests/test_steps.py), docs/
- Validation:
  - Before the change, no stored event in the dev database had an empty or missing summary: 0 of 14,735 v1 and 0 of 7,132 v2 rows, the shortest being 16 characters. `triage` is the only database.
  - `make lint`, `typecheck`, `test` (Jest 42, pytest 45), `test-int` (Jest 2, pytest 15), `contracts-check` and `contracts-breaking` pass. The v1 and v2 files still match their sha256 and have no diff against HEAD.
  - Mutations failed the tests: `min_length` removed from the stored model (Python), and `minLength` removed from v3 (Python and TypeScript).
- Outstanding: None.

### 2026-10-14: StepRecorded v3 limits

- Changes:
  - `step-recorded.v3.schema.json` (still unreleased) caps `summary` at 2,000 characters (`maxLength`, counted in code points) and requires `latency_ms` and both token counts to be at least 0 (user decision). The v1 and v2 files are unchanged.
  - The stored `StepRecordedV2` model and its `TokenUsage` enforce the same limits, so an event that breaks one cannot be built or written.
  - `record_step` now builds the v3 message before inserting, so an event that skipped validation raises before anything is stored.
  - Codegen regenerated.
  - The user added `**/drizzle/meta/` to `.prettierignore`. The memory note about running Prettier after `db:generate` is closed, and so is its todo item.
- Files: contracts/messages/step-recorded.v3.schema.json, packages/py-clients/src/py_clients/generated/messages/step_recorded_v3_schema.py, packages/ts-clients (src/generated/messages.ts, src/contracts.test.ts), services/agent (src/agent/steps.py, tests/test_steps.py, tests/test_migrations_int.py), docs/
- Validation:
  - `make lint`, `typecheck`, `test` (Jest 40, pytest 43), `test-int` (Jest 2, pytest 15), `contracts-check` and `contracts-breaking` pass. The v1 and v2 files still match their sha256 and have no diff against HEAD.
  - No stored event on the dev database breaks a limit: the longest summary is 145 characters, with no negative values.
  - Each limit has accept and reject cases at its boundary, including 2,000 four-byte emoji, so Python and ajv count characters the same way.
  - Mutations failed the tests: `ge=0` removed from the stored latency, and `maxLength` removed from v3 (Python and TypeScript).
- Outstanding: v3 still allows an empty summary, which v1 and v2 messages rejected.

### 2026-10-14: StepRecorded v3 message

- Changes:
  - `contracts/messages/step-recorded.v3.schema.json` (user decision):
    - The payload is the stored StepRecorded v2 event field for field, with the same nullability and no extra limits.
    - Around it are `schema_version` 3, `investigation_id`, `seq` and `recorded_at`.
    - Two examples: with latency and tokens, and with both unknown.
  - The union `step-recorded.schema.json` accepts v1, v2 and v3, and marks v2 `"deprecated": true`, with a description, beside its `$ref`. The released v1 and v2 files are unchanged.
  - Codegen added `StepRecordedV3` in both languages; both StepRecorded unions are now v1, v2 and v3.
  - `agent.steps.step_message` builds the v3 message from a stored event, and `record_step` returns it, so the first producer (the agent loop, step 6) publishes v3.
  - Contract tests:
    - TypeScript accepts each version through the union, pins v2 as the only deprecated branch, adds v3 to the enum parity check, and probes version 4 as unknown.
    - Python validates each version through the union.
    - Agent tests check that the v3 payload fields equal the stored v2 fields, and that stored events (upcast v1 included) become v3 messages unchanged.
- Files: contracts/messages (step-recorded.v3.schema.json, step-recorded.schema.json), packages/ts-clients (src/generated/messages.ts, src/contracts.test.ts), packages/py-clients (src/py_clients/generated/messages/, tests/test_contracts.py), services/agent (src/agent/steps.py, tests/test_steps.py), docs/
- Validation:
  - `make lint`, `typecheck`, `test` (Jest 32, pytest 35), `test-int` (Jest 2, pytest 14), `contracts-check` and `contracts-breaking` pass.
  - The v1 and v2 files match their sha256 from before the change and have no diff against HEAD.
  - Mutations failed the tests: an extra field on the stored v2 model (field parity), and `step_message` dropping `tokens` (round trip).
- Outstanding: v3 has no tool call status for the API's `Step.tool_call`, and adding `**/drizzle/meta/` to `.prettierignore` is a manual step; both are in docs/todo.md.

### 2026-10-14: Step 3 data

- Changes:
  - Gateway schema in Drizzle (`apps/gateway/src/schema.ts`, `drizzle/0000_init.sql`):
    - `tenants`, `services` and `service_dependencies`.
    - `alert_groups`: a status enum that includes `queued_not_investigated`, and a CHECK tying that status to a null `investigation_id`.
    - `alerts`: one row per firing.
    - The `proposed_actions` copy, and `approvals` keyed by `action_id`.
    - Keyset indexes `(tenant_id, created_at DESC NULLS FIRST, id DESC NULLS FIRST)`.
  - Agent schema: SQLAlchemy 2 models (`agent/db.py`) and plain-SQL Alembic revisions 0001 to 0004. `env.py` gained the autogenerate filters, `search_path=public` and one transaction per revision.
  - Story A:
    - 0002 expand.
    - The `STEPS_DUAL_WRITE` flag, with the dual-write in `record_step`.
    - The resumable backfill CLI.
    - 0003, which checks the counts and then swaps the names, leaving a compat view.
    - 0004 contract.
    - The partition job in the agent: at startup, then daily.
  - Story B: stored StepRecorded v1 and v2 models, the upcaster chain and `replay`, pinned by a golden snapshot.
  - Seed: `scripts/seed.py` (`make seed`), with Faker and a fixed seed, writing through both roles, with `--reset` and `--large`. Faker joined the root dev group.
  - Tests:
    - A scratch-database fixture.
    - Both chains from empty, plus the seed and `alembic check`.
    - A downgrade round trip that carries a row.
    - The partition story under a writer at 2,000,000 steps, plus two guard tests.
    - The golden replay.
- Files: apps/gateway (src/schema.ts, drizzle/, drizzle.config.ts, package.json), services/agent (src/agent/db.py, steps.py, partitions.py, settings.py, main.py; migrations/env.py, migrations/versions/; tests/conftest.py, test_steps.py, test_migrations_int.py, test_partitioning_int.py, golden/), scripts/seed.py, Makefile, pyproject.toml, uv.lock, .env.example, README.md, docs/
- Validation:
  - `make lint`, `typecheck`, `test` (Jest 27, pytest 29), `test-int` (Jest 2, pytest 14, about 60 s), `build` and `contracts-check` pass.
  - Dev database:
    - `make migrate` applied both chains to the empty schemas; running it again is a no-op.
    - The gateway schema was reset once: the first apply had a truncated 66-character foreign key name, and the schema held only that apply's empty tables.
    - `make seed` took 2.6 s. A second run refused without `--reset`, and `--reset` reproduced identical rows (checksums).
    - EXPLAIN of the keyset queries shows index-only scans with no sort.
  - The agent started against the dev database with the partition thread: `/readyz` ok, no errors.
  - Mutations each failed the tests: an upcaster writing 0 for an unknown latency, 0003 without the compat view, and dual-write left on past 0004.
- Review fixes (independent review: 0 high, 3 medium, 7 low findings):
  - `python -m agent.partitions ensure` crashed: `extra={"created": ...}` collides with a LogRecord attribute, which also rolled back the partitions. The key is renamed, and the partition test runs the CLI.
  - 0002 and its downgrade now set `lock_timeout` (the foreign key locks `investigations`).
  - 0004's downgrade copies the rows back, so the chain downgrades with data present.
  - 0004 refuses to drop while the old table holds a row the new one lacks; a test covers it.
  - 0003 takes the partition job's advisory lock. Without it, the swap, an attach and a writer could deadlock.
  - Revisions now commit one at a time; the docs say to upgrade a live database one revision at a time.
  - The partition job retries hourly after a failure, and the ATTACH lock wording is corrected.
  - The test's backfill gets a CLI-like engine, and the numbering assertion can no longer pass on an empty result.
  - Seeded groups now start at their window start, so no alert predates its group.
  - Not changed: per-month savepoints in `ensure_partitions`, and reading the backfill's date range from `investigations`. The hourly retry and the CLI's 60 s timeout cover both; a `ponytail:` comment marks the range scan.
- Outstanding: the stored step events and the StepRecorded queue messages have different v2 shapes; map them before step 6. The decision store still needs the outbox (step 4); both are in docs/todo.md. `.prettierignore` is unchanged because the config-protection hook blocks it, so run Prettier after `db:generate`.

### 2026-10-14: Approver credential

- Changes:
  - New required env var `APPROVER_API_KEY`, for the gateway (verifies) and the web server (sends). It is in `.env.example`, and the Makefile passes it only to `dev-ts`.
  - `gateway.yaml` has an `approverKey` scheme (`X-Approver-Key`) on approve and reject, and a 403 response for other credentials.
  - The gateway serves both routes with the approver check, request validation and problem+json errors, through an injected decision store that is a not-found placeholder until step 3.
  - `createGatewayClient` no longer types the decision routes; `createDecisionClient` and the web's `decideAction` handle them. Next transpiles `@triage/ts-clients`. The gateway's DB integration test no longer loads the full config.
  - The reviewer's probe queue was already gone: ElasticMQ holds only `investigations.fifo` and its DLQ.
- Files: contracts/openapi/gateway.yaml, apps/gateway/src (app.ts, app.test.ts, config.ts, main.ts, db.int.test.ts), apps/web (next.config.ts, src/lib/config.ts, decisions.ts, decisions.test.ts), packages/ts-clients/src (gateway.ts, contracts.test.ts), services/agent/tests/test_agent_api.py, services/executor/tests/test_executor_api.py, sim/tests/test_sim_api.py, packages/py-clients/tests/test_clients.py, Makefile, .env.example, docs/
- Validation:
  - Tests:
    - Approve with only `X-API-Key` gets 403, and so does the Alertmanager token.
    - A missing or wrong approver key gets 401 with the challenge.
    - The approver key gets 200 for approve and reject.
    - A bad body or id gets 400; unknown and decided actions get 404 and 409.
    - Agent, executor and sim settings hold no approver key, even when it is set in their environment.
    - The Python clients contain no `/v1/actions` path.
  - `make lint`, `typecheck`, `test` (Jest 27, pytest 25), `build`, `test-int`, `contracts-check` and `contracts-breaking` pass.
  - Live gateway on 4200: `X-API-Key` and bearer got 403, a wrong key 401, the right key 404 (placeholder store), all problem+json, and the key never reached the log. Without the key the gateway exits with `APPROVER_API_KEY is required`.
  - Make probes under sh and cmd.exe: a plain target does not see the key, even from the calling shell; the dev-ts export line does.
  - gitleaks: no leaks in the files with placeholder keys.
- Outstanding: Add `APPROVER_API_KEY` to the local `.env`, since the gateway and web now refuse to start without it. The other credentials and the decision store are in docs/todo.md.

### 2026-10-14: Step 2 contracts

- Changes: Wrote the contracts spec-first. Gateway OpenAPI 3.1 adds `/v1` alerts, investigations, approve and reject, with RFC 9457 errors, rate-limit headers and three security schemes. The agent internal API adds cancel and state. Added versioned message schemas with examples (StepRecorded v1, v2 and a union), and the GraphQL schema with a Relay connection and an `Investigation` subscription. Codegen now produces openapi-fetch clients with timeouts, ajv message validation and web typed documents (TypeScript), plus pydantic models in `py_clients.generated` and httpx2 clients (Python). Codegen wipes its output first. Added `make contracts-check` and `make contracts-breaking` (oasdiff in Docker), and contract tests on both sides. The agent now imports `InvestigationRequestedV1`.
- Files: contracts/, packages/ts-clients (scripts/codegen.ts, src/), packages/py-clients (scripts/codegen.py, src/py_clients/, tests/), apps/web/src/graphql, apps/web/src/generated, apps/web/src/lib/gateway.ts, apps/gateway/src/app.ts and app.test.ts, services/agent (imports), scripts/contracts_check.py, Makefile, pyproject.toml, README.md, docs/
- Validation: `make lint`, `typecheck` (pyright 0 errors), `test` and `build` pass. `make contracts-check` and `make contracts-breaking` pass from Git Bash and from PowerShell (cmd.exe). Negative checks failed as intended: a hand-edited generated file and an unregenerated schema edit failed `contracts-check`, and removing `/readyz` failed `contracts-breaking` (`api-path-removed-without-deprecation`). Real-socket check: `fetchWithTimeout` passes POST bodies and aborts with `TimeoutError`. `make test-int` passes (Jest 2, pytest 9).
- Review fixes (independent review, 0 high and 5 medium findings):
  - The MessageGroupId is the group id, since SQS rejects Alertmanager group keys.
  - The Python `GatewayClient` lost approve and reject.
  - Bad action kinds are now rejected in both languages, and a type-level enum parity check was added.
  - `contracts-breaking` now also fails on edited or deleted message versions.
  - oasdiff runs with `--network none` and only `.git` and `contracts/` mounted.
  - `/graphql` is documented as needing X-API-Key.
  - 401 responses carry `WWW-Authenticate`.
  - The response `Health` schemas allow unknown members.
  - The TS gateway client type excludes `/v1/alerts`.
- Review fix validation: everything above re-passed (Jest 21 including ts-clients 11, pytest 21) from both shells. Removing a GraphQL enum value failed `tsc` at the parity tuple. A scratch git repo confirmed the message-version rule: an edited or deleted version fails, and a new or staged file passes. The oasdiff container sees only `.git` and `contracts` and cannot resolve hosts.
- Outstanding: No service serves the new routes or enforces their credentials yet. Per-kind action parameters and the report message are open (docs/todo.md). GraphQL query resolvers are step 9. The question of a separate credential for approve and reject was settled by the approver credential entry above.

### 2026-10-14: ElasticMQ message persistence

- Changes: Enabled ElasticMQ `messages-storage` (H2) on a named volume `elasticmq-data` in both compose files; planned a recovery sweeper under step 4.
- Files: infra/elasticmq.conf, infra/docker-compose.yml, infra/docker-compose.prod.yml, docs/todo.md, docs/context.md, docs/memory.md
- Validation: A queued message survived a container restart and a recreate, with its receive count and the redrive policy intact. `make test-int` passes (Jest 2, pytest 9). The merged prod config mounts the volume and publishes no queue port.
- Outstanding: Recovery sweeper (docs/todo.md, step 4).

### 2026-10-14: Prompt 1 completion items

- Changes: Config validation moved to zod and pydantic-settings with one-line startup errors; gitleaks pre-commit hook; production compose override; ElasticMQ probe tests; turbo agent guidance turned off and its AGENTS.md block removed.
- Files: packages/ts-config, packages/py-config, apps/gateway (config, main, drizzle.config), apps/web/src/lib/config.ts, services/agent, services/executor, sim, .pre-commit-config.yaml, Makefile, pyproject.toml, infra/docker-compose.prod.yml, turbo.json, AGENTS.md, README.md, docs/
- Validation: `make lint`, `typecheck`, `test` (Jest 10, pytest 9) and `test-int` (Jest 2, pytest 9) pass. Unset `GATEWAY_DATABASE_URL` or `AGENT_DATABASE_URL` stops startup with exit code 1 and a one-line error. The hook blocked a staged fake token and passed all 116 project files. The merged prod config publishes no Postgres or ElasticMQ ports. `docker compose ps` host ports match the context.md table.
- Outstanding: None.

### 2026-10-14: Step 1 scaffolding

- Changes: Polyglot monorepo (pnpm + Turborepo TS apps and packages, uv Python services and packages), contracts v0 with codegen, pinned infra compose (Postgres + pgvector, ElasticMQ, Toxiproxy, observability profile), per-service Postgres roles, root Makefile. ElasticMQ, TypeScript 6.0.3 and ESLint + Prettier chosen by the user. Fixes from an independent review: gateway CORS and GraphiQL off, agent consumer survives handler errors, gateway DB ping timeout, SWC parses `.ts` without TSX, shared configs hashed by turbo.
- Files: Makefile, package.json, pnpm-workspace.yaml, turbo.json, pyproject.toml, .env.example, infra/, contracts/, apps/, services/, sim/, packages/, evals/, runbooks/, README.md, docs/
- Validation: `make lint`, `typecheck`, `test` (Jest 10, pytest 9), `test-int` (Jest 2, pytest 3), `build`, `migrate` and `codegen` (deterministic) pass from PowerShell and Git Bash. Compose healthy, observability profile included. Every `/healthz` and `/readyz` returns 200 under `make dev`. Traces from gateway and agent reach Jaeger.
- Outstanding: See docs/todo.md.

### 2026-10-14: Plan updated for free-tier models, production readiness, and deployment

- Changes: Switched to free-tier LLMs behind a provider switch, added production-readiness requirements, added steps 9 (review UI) and 10 (deploy).
- Files: AGENTS.md, docs/context.md, docs/memory.md, docs/todo.md
- Validation: Documentation only. No code changed.
- Outstanding: See docs/todo.md.

### 2026-10-13: Project created from template

- Changes: Copied `_project-template`, filled project-specific rules in `AGENTS.md`, added `CLAUDE.md` importing `AGENTS.md`, recorded design in `docs/context.md`, decisions in `docs/memory.md`, build plan in `docs/todo.md`.
- Files: AGENTS.md, CLAUDE.md, README.md, docs/*
- Validation: Documentation only. No code to test.
- Outstanding: None.
