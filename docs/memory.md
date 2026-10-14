# Memory

Persistent project knowledge for future sessions. Keep entries concise and accurate. Never invent history or decisions. Do not duplicate what is already in `context.md`.

Entry format: `- YYYY-MM-DD: <fact or decision>. Reason: <why>.`

## Decisions and Reasoning

From the approved design doc:

- 2026-10-13: Mutating actions are enforced in code via a separate executor. Reason: prompt-only safety is not verifiable.
- 2026-10-13: Structured final report with an enum root-cause class. Reason: makes evals deterministic with no LLM judge.
- 2026-10-13: SQS FIFO with MessageGroupId per alert group. Reason: one investigation per incident at a time, native redrive to DLQ.
- 2026-10-13: Service-owned schemas (gateway via Drizzle, agent via Alembic). Reason: avoids two migration tools fighting over one schema.
- 2026-10-13: No agent framework; the loop calls the provider layer's tool calling directly. Reason: keeps the loop explicit and testable.
- 2026-10-14: Default to free-tier LLMs behind a provider switch: Gemini Flash primary, NVIDIA NIM fallback (user decision). Groq is excluded from the agent loop because of its 8K tokens/minute free cap. Incidents are simulated, so free-tier training use is acceptable.
- 2026-10-14: Production foundations are built from the start, and deployment is a hybrid (UI on Vercel, backend on the PC via Tailscale Funnel, browser reaches the gateway only through Next.js). Executor and sim run on an internal Docker network. Reason: same images locally and deployed; keys stay server-side.
- 2026-10-14: SQS runs on ElasticMQ (`softwaremill/elasticmq-native`) in dev, not LocalStack (user decision). Reason: LocalStack images since 2026.03.0 need an account and auth token.
- 2026-10-14: TypeScript 6.0.3, not 7 (user decision). Reason: TS 7 ships no JS compiler API, which openapi-typescript needs. Upgrade once the codegen tools support it.
- 2026-10-14: ESLint 10 + Prettier for TypeScript (user decision). eslint-config-next's plugins declare stale ESLint peer ranges; `pnpm-workspace.yaml` allows ESLint 10.
- 2026-10-14: The gateway runs its TypeScript directly on Node 24 type stripping, with no build step. Reason: no compile config to maintain. Code must stay erasable (`erasableSyntaxOnly`); GraphQL enums are generated as string unions.
- 2026-10-14: Postgres roles `gateway` and `agent` each own only their schema; `gateway` also has CREATE on the database. Reason: least privilege; drizzle-kit migrate always runs `CREATE SCHEMA IF NOT EXISTS`.
- 2026-10-14: pnpm denies all dependency build scripts (`allowBuilds`) and keeps its minimum release age gate. Reason: supply-chain safety; native binaries arrive as optional packages anyway.
- 2026-10-14: Config validation uses zod (TypeScript) and pydantic-settings (Python) (user decision). Empty values count as unset; a missing or bad variable stops startup with one line and exit code 1.
- 2026-10-14: Secret scanning is the gitleaks pre-commit hook (`.pre-commit-config.yaml`, gitleaks v8.30.1). Reason: the hook's `golang` language makes pre-commit build gitleaks itself, so nothing is installed system-wide.
- 2026-10-14: `infra/docker-compose.prod.yml` publishes no Postgres or ElasticMQ ports, sets `restart: unless-stopped` and memory limits, and puts Toxiproxy behind `--profile chaos`. Reason: only containers on the compose network reach data services; fault injection stays out of production.
- 2026-10-14: ElasticMQ persists queues and messages (`messages-storage`, H2 at `/data/elasticmq`) on the named volume `elasticmq-data`, declared in both compose files. Reason: queued investigations must survive restarts; the prod copy keeps it even if dev drops the volume.
- 2026-10-14: The gateway turns off GraphQL Yoga's CORS and GraphiQL. Reason: Yoga's defaults reflect any origin with credentials, which would let any web page call the API from a browser.
- 2026-10-14: Queue messages are versioned per file (`<name>.v<N>.schema.json`, title `<Name>V<N>`) and strict (`additionalProperties: false`). Any change ships as a new version; consumers validate against an unversioned union file (`step-recorded.schema.json`) until the old version is drained. Reason: expand, migrate, contract for messages; strict schemas catch producer bugs.
- 2026-10-14: Nullable message fields are required and typed `[T, "null"]`, never optional. Reason: pydantic dumps `None` as `null`, so an optional non-nullable field would make Python producers emit messages that TypeScript consumers reject. The Python contract test checks that every example dumps back unchanged.
- 2026-10-14: REST response schemas allow unknown members; request bodies and messages forbid them. Reason: datamodel-codegen turns `additionalProperties: false` into `extra="forbid"`, so a new response field would break deployed Python clients. Response enums have no such slack: a new value fails pydantic validation, so ship it with regenerated clients.
- 2026-10-14: Approve and reject take their own credential (user decision): `APPROVER_API_KEY`, sent as `X-Approver-Key` and held only by the web server. Any other gateway credential on those routes gets 403; a missing or wrong approver key gets 401. Reason: a credential that can read investigations must not be able to approve actions, and the agent, executor and sim never receive the approver key.
- 2026-10-14: The Makefile `unexport`s `APPROVER_API_KEY` and exports it only for `dev-ts` (gateway and web). Reason: `export` after `-include .env` hands every variable to every recipe, including the Python services; checked under sh and cmd.exe, even with the key set in the calling shell.
- 2026-10-14: The Python `GatewayClient` is read-only. It has no approve or reject, because only the web backend-for-frontend records a human decision and the agent imports `py_clients`. Reason: the agent must have no code path that approves its own proposals.
- 2026-10-14: Released message versions are immutable. `make contracts-breaking` fails when a `*.v*.schema.json` present at `CONTRACTS_BASE` is edited or deleted (`git diff --diff-filter=MD`); new files pass. Reason: oasdiff covers only OpenAPI.
- 2026-10-14: Commands (alert intake, approve, reject) use REST; GraphQL has no mutations and serves reads and live updates. Reason: one write path per action, each with an OpenAPI contract and a breaking-change check.
- 2026-10-14: Python HTTP clients use httpx2, the maintained httpx successor, instead of httpx. Reason: it is already locked (Starlette's TestClient needs it), so the venv keeps one HTTP library.
- 2026-10-14: oasdiff runs from the pinned image `tufin/oasdiff:v1.33.0` and compares against a git ref (`CONTRACTS_BASE`). It runs with `--network none` and only `.git` and `contracts/` mounted read-only. Reason: no Go toolchain needed (the image ships git), and a third-party image never sees `.env` or the network.
- 2026-10-14: `make contracts-check` compares hashes of the generated dirs before and after codegen instead of running `git diff`. Reason: it works on an uncommitted tree and catches added and deleted files.
- 2026-10-14: Stored step events (`agent.investigation_steps.event`) follow the step 3 spec: StepRecorded v1 has `observation`; v2 has `result`, `latency_ms` and `tokens`. They are versioned apart from the StepRecorded queue messages of step 2, whose v2 nests the tool call and token usage instead. Reason: released message versions are immutable, and the spec's v1 has a field the message v1 never had. Message v3 (next entry) now carries the stored v2 shape.
- 2026-10-14: StepRecorded message v3 is the stored v2 step event, field for field, plus `schema_version`, `investigation_id`, `seq` and `recorded_at` (user decision).
  - Producers emit only v3; `record_step` returns it.
  - Consumers accept v1, v2 and v3. The union marks v2 `"deprecated": true` beside its `$ref`, because released v1 and v2 files stay byte-identical.
  - v3 and the stored v2 model share the same limits (user decision): summary of 1 to 2,000 code points, `latency_ms` and token counts not negative.
  - The model enforces them when an event is built. `record_step` also builds the v3 message before writing, so a stored event always converts to a valid v3.
  - Reason: no mapping layer between the stored event and the message.
- 2026-10-14: Step `seq` comes from `investigations.step_count`, incremented under the row lock in `record_step`. Reason: a partitioned table's unique keys must include `created_at`, so the database can no longer enforce unique `(investigation_id, seq)`.
- 2026-10-14: The 0003 swap renames the old table to `investigation_steps_old` and leaves a view named `investigation_steps_p` over it. Reason: agents still dual-writing keep working without errors, and the old table stays complete for a rollback until the flag goes off.
- 2026-10-14: New monthly partitions are built as plain tables and then attached. Reason: `ATTACH PARTITION` locks the parent only in SHARE UPDATE EXCLUSIVE mode, where `CREATE TABLE ... PARTITION OF` takes ACCESS EXCLUSIVE. The attach still briefly locks `investigations`, to clone the foreign key, and the default partition. So the job runs under `lock_timeout` and the 0003 swap takes the job's advisory lock, and the two never overlap.
- 2026-10-14: Every agent revision runs in its own transaction (`transaction_per_migration=True`). 0004 compares the old table row by row with the new one before its irreversible drop. Reason: a long `upgrade head` must not hold one revision's locks into the next. Counts alone miss a row that a writer without dual-write slipped in between 0003's count and its lock.
- 2026-10-14: Alembic revisions are plain SQL, and `alembic check` (run by the migration tests) compares them with the SQLAlchemy models. Reason: exact DDL for enums, partitions and pgvector, with no app imports in revisions.
- 2026-10-14: `migrations/env.py` connects with `search_path=public`. Reason: the agent role's default schema is `agent`, which autogenerate otherwise treats as unnamed, reporting every table as missing.
- 2026-10-14: Migration tests create throwaway databases (`triage_test_*`) as the Postgres superuser, with `POSTGRES_PASSWORD` from `.env`, and drop them afterwards. Reason: the from-empty and partition tests must not touch the dev database.
- 2026-10-14: `approvals` uses `action_id` as its primary key, and the gateway's `proposed_actions` has no status column; status comes from `approvals`. Reason: one decision per action, and no second copy of the status to drift.
- 2026-10-14: Keyset indexes are `DESC NULLS FIRST`. Reason: drizzle-kit writes `.desc()` as `DESC NULLS LAST`, which a plain `ORDER BY ... DESC` cannot read without a sort. With `NULLS FIRST`, EXPLAIN shows an index-only scan and no sort.
- 2026-10-14: The seed lives in `scripts/` (Faker in the dev group, never in an image). It writes each schema through its own role, anchors history to the year before 2026-10-01, and refuses to touch a database with data unless given `--reset`. Reason: the same rows on every run, and no way to seed production by accident.
- 2026-10-14: Embeddings are `vector(768)`: Gemini embeddings at `output_dimensionality` 768, or nomic-embed-text in Ollama. Reason: one dimension that both the primary and the local provider can produce.

## Preferences and Instructions

- 2026-10-14: The three portfolio projects run in parallel Claude Code sessions on one PC, sharing Docker. Each project has its own port range and compose project name; never prune or remove another project's Docker resources (user setup).
- 2026-10-13: General coding rules live in `AGENTS.md`. `CLAUDE.md` imports it so Claude Code loads the rules.
- 2026-10-13: Markdown filenames use kebab-case (for example `docs/migrations.md`).

## Solved Problems

None yet.

## Known Issues and Pitfalls

- 2026-10-14: make runs recipes with cmd.exe when started from PowerShell and with sh from Git Bash. Keep recipes shell-neutral: no single quotes, no `VAR=x cmd`, no `$(...)`.
- 2026-10-14: Jest can only load ESM-only packages (Fastify `inject` pulls `cookie` 2) with `NODE_OPTIONS=--experimental-vm-modules`; gateway test scripts set it through pnpm's `shellEmulator`.
- 2026-10-14: Turbo filters env vars (strict mode), so dev servers need `--env-mode=loose` (the Makefile passes it). Pages that read runtime env must call `await connection()`, or `next build` tries to prerender them and fails.
- 2026-10-14: pytest importlib mode turns the repo's `sim/` folder into a fake `sim` package that shadows the real one. Use the default prepend mode with unique test file names.
- 2026-10-14: Starlette 1.8 `TestClient` is typed against `httpx2`, not `httpx`. Full `types-boto3` makes `boto3.client` "partially unknown" under pyright strict, so the repo uses `types-boto3-lite` and annotates clients.
- 2026-10-14: Jaeger 2.22 serves only the v3 query API (`/api/v3/services`). The collector's `otlp` exporter alias is deprecated; use `otlp_grpc`.
- 2026-10-14: Compose does not recreate a container when a bind-mounted config file changes; restart the service.
- 2026-10-14: datamodel-codegen writes CRLF files on Windows; `.gitattributes` stores them as LF, so `git add` only warns. `scripts/contracts_check.py` ignores line endings for the same reason.
- 2026-10-14: datamodel-codegen runs `ruff check` on its output before formatting, so E501 failed on long schema descriptions. `**/generated/**` ignores E501.
- 2026-10-14: json-schema-to-typescript's `declareExternallyReferenced: false` also drops a schema's own `$defs`. The codegen sets it only on schemas that `$ref` other files, which would otherwise redeclare those types.
- 2026-10-14: A YAML plain scalar cannot contain `: ` (for example `{"to_version": "v1.7.3"}` in a description); quote it. oasdiff reports it as a parse error.
- 2026-10-14: Under `nodenext`, ajv-formats' plugin is `addFormats.default(ajv)`: the package is CommonJS and Node's ESM interop puts the function on `default`.
- 2026-10-14: drizzle-kit's first migration started with `CREATE SCHEMA "gateway"`, which fails because `infra/postgres-init.sql` creates the schema; it was edited to `IF NOT EXISTS`. Postgres truncates names over 63 characters, so drizzle-kit's snapshot and the database disagree; name long constraints by hand (`proposed_actions_investigation_id_fk`).
- 2026-10-14: The config-protection hook blocks agents from editing lint and format config such as `.prettierignore`; the user makes those edits. The user added `**/drizzle/meta/` to `.prettierignore`, because drizzle-kit rewrites its snapshots in a style Prettier rejects.
- 2026-10-14: json-schema-to-typescript and datamodel-codegen ignore keywords beside a `$ref` inside `oneOf` (`deprecated`, `description`). The generated StepRecorded unions do not show v2 as deprecated; only the schema does, which ajv validates and the contract tests pin.
- 2026-10-14: Postgres 18 names NOT NULL constraints `<table>_<column>_not_null`, so after the swap the partitioned table keeps `investigation_steps_p_*_not_null` names. Harmless.
- 2026-10-14: Log `extra` keys must not be LogRecord attributes (`created`, `name`, `msg`, `args`, `message` and so on): `Logger.makeRecord` raises `KeyError` when the level is enabled. `extra={"created": ...}` crashed `python -m agent.partitions ensure` and rolled back its partitions; an independent review caught it, and the partition test now runs that CLI.
- 2026-10-14: SQLAlchemy 2.1 deprecated `Result.tuples()`, and `dict()` over it fails; iterate the rows. Enum types take the MetaData schema by default.
- 2026-10-14: Alembic's `op.execute` wraps SQL in `text()`, which reads `:name` as a bind parameter. Keep `:` in migration SQL only after a digit (`'2026-10-01T00:00Z'`) or in `::` casts. Multi-statement strings work with psycopg 3, because a query without parameters goes through the simple protocol.
- 2026-10-14: SQS rejects an Alertmanager group key as MessageGroupId: it allows only alphanumerics and punctuation (no spaces) and at most 128 characters. Group keys look like `{}:{alertname="X", service="Y"}`. Found by an independent review against this stack's ElasticMQ (`InvalidParameterValue`). Use the gateway's group id (UUID) instead.

## Rejected Approaches

None yet.

## Lessons Learned

- 2026-10-14: ElasticMQ 1.7.1 probe (`services/agent/tests/test_elasticmq_int.py`, run by `make test-int`), all six checks passed:
  - FIFO: messages in one MessageGroupId arrive in order, and the group is held while a message is in flight; other groups keep flowing.
  - MessageDeduplicationId: a repeat inside the dedup window is dropped.
  - Long polling: an empty receive waits the full WaitTimeSeconds; a message arriving mid-wait returns at once.
  - ChangeMessageVisibility: an extension keeps a message hidden past its original timeout; 0 releases it immediately.
  - Message attributes: String and Number custom attributes round-trip; MessageGroupId and ApproximateReceiveCount come back through MessageSystemAttributeNames.
  - Redrive: ApproximateReceiveCount runs 1 to 5, and the sixth receive moves the message to the DLQ.
- 2026-10-14: Partition story on 2,000,000 seeded steps: about 50 s for the whole test. In the final run the writer committed in every phase, with 2,256 writes during the backfill, 132 during the verify-and-swap migration and 250 during 0004's row check. It had zero errors, and the slowest write took 63 ms. Each test was also run against a broken version and failed as it should: without the compat view, with dual-write left on past 0004, and with an upcaster that writes 0 for an unknown latency.
- 2026-10-14: With `messages-storage` on, a message queued on `investigations.fifo` survived `docker compose restart elasticmq` and a forced container recreate; its receive count (1, then 2) and the redrive policy carried over. Checked by hand, not in `make test-int`, because the check restarts the shared container.

## Constraints

None yet.

## Unresolved Questions

- 2026-10-13: OpenTelemetry Demo failure-injection flag names vary by release.
- 2026-10-14: Free-tier rate limits will slow the 3-run eval (hundreds of calls). Spread runs out and check provider consoles first. Dollar cost targets apply only on a paid tier.
