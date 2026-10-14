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
