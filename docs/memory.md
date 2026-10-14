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
- 2026-10-15: Each tenant has its own Alertmanager webhook token (user decision). The gateway stores only its SHA-256 on `tenants` and finds the tenant by it; `pnpm --filter @triage/gateway tenant:token <name>` issues one. Reason: alerts and the per-tenant cap need a tenant, and the webhook contract has no tenant field.
- 2026-10-15: The gateway emits InvestigationRequested v2, which adds `tenant_id`. Its MessageGroupId is sha256(tenant_id + "\n" + group_key) and its MessageDeduplicationId sha256 over tenant, group key and window start (user decision). Reason: the spec's "MessageGroupId = group key", made SQS-safe; every window of one Alertmanager group shares a FIFO group, so its investigations run one at a time. This replaces the alert group id for v2.
- 2026-10-15: The webhook queues investigations through a transactional outbox (`gateway.outbox`), not a SendMessage in the request. The alert group and its message commit together, and a relay in the gateway sends it: at once when nudged after the commit, otherwise every second. Reason: neither a crash nor an SQS outage loses an investigation, and webhook latency does not depend on SQS. Rows parked after 10 failed sends are the gateway's failure store.
- 2026-10-15: A grouping window opens with the first new alert of a group key and lasts 5 minutes; it is not aligned to the clock. Only opening a window takes a transaction advisory lock, per tenant, so two requests never open the same one. Reason: a storm across a clock boundary would otherwise start two investigations of one incident.
- 2026-10-15: A tenant starts at most `MAX_NEW_INVESTIGATIONS` (5) investigations within any 5 minutes. A new group past that, or one whose new alerts are all resolved, is kept as `queued_not_investigated`, with no outbox message, for its whole window. Reason: the 500-alert storm target allows 5 investigations however many group keys it spans; the schema's `queued_not_investigated` status was made for this. Found by the independent review, after a storm test over 3 keys could not fail.
- 2026-10-15: Rate limits use @fastify/rate-limit's in-memory store, keyed by the SHA-256 of the credential (bearer token, X-API-Key or X-Approver-Key), or by client address without one. Probes are exempt. Several gateway instances would each count alone and need a shared store (Redis). Invented credentials each get their own count, so floods of them need a per-address limit at the edge.
- 2026-10-15: Webhook auth runs in `preParsing`: after the rate limit and before the body is parsed. Reason: @fastify/rate-limit appends its hook after a route's own `onRequest` hooks, so auth there would skip the limit.
- 2026-10-15: The 200-alert limit lives in the contract's description and in code, not as `maxItems`. Reason: oasdiff rates a new `maxItems` on a request as breaking (`request-property-max-items-set`). Alertmanager's `max_alerts` should be 200, so it truncates bigger groups instead of getting 400.
- 2026-10-15: A tool call is two steps. The model's request is a `tool_call` step with tokens and null `result` and `latency_ms`; its `tool_calls` row, written in the same transaction, holds the arguments with a null latency. The result is a `tool_call` step without tokens, which completes that row in one transaction. Report steps have null `result` and `latency_ms` too. Reason: StepRecorded v3 defines `result` and `latency_ms` as the tool's, and a resume after the model turn must not ask the model again. An earlier version kept the ToolRequest JSON in `result`; the independent review caught it before release.
- 2026-10-15: `failed` is not a finished status for the job: it marks an investigation whose message went to the DLQ, and a replayed message resumes it. Reason: with `failed` finished, `make dlq-replay` deleted exactly the messages it should retry (found by the independent review).
- 2026-10-15: The consumer runs one message per FIFO group from each receive. A later message of the same group in the batch goes back with visibility 0, and SQS holds it until the earlier one is deleted. Reason: one receive can return several messages of a group, which would run investigations of one Alertmanager group at once.
- 2026-10-15: Each job holds one Postgres session for its whole run. It takes a session advisory lock per investigation (a duplicate delivery gets "busy") and one of `AGENT_TENANT_CAP` slot locks per tenant. Reason: session locks end with the process, so a crash never leaks a slot, as a counter row would.
- 2026-10-15: A "busy" message (tenant at its cap, or the investigation running elsewhere) returns to SQS after 60 to 120 s. Ceiling: these waits use up receives, so with several agents a tenant over its cap for about 10 minutes dead-letters work; park such messages in Postgres if that happens. One agent never hits it, since its 5 slots equal the cap. Superseded the same day by parking (below): only a duplicate delivery is still "busy".
- 2026-10-15: Budget waits keep the message in flight with heartbeats, status `queued_budget`. Reason: the spec deletes a message only after its final report, and releasing it would use up receives during an hours-long wait. Once every slot waits, the consumer stops receiving, so the rest stay queued untouched. Only new investigations (no steps) wait for the budget; `AI_ENABLED=false` holds every investigation at its next model turn. Ceiling: while every slot holds a waiting new investigation, redelivered resumed ones wait in the queue too, until the budget frees; park budget waiters in Postgres if that delay matters. Superseded the same day by parking (below).
- 2026-10-15: Until build step 6 the agent runs `ScriptedModel` with one placeholder report ("Not investigated: the AI pipeline arrives in build step 6."), which resolves each investigation. Reason: the queue, steps, budget and delete path run end to end now.
- 2026-10-15: Parking replaces holding (user decision). A message that must wait for a tenant slot or model budget marks its investigation waiting and is deleted: status `queued`, `waiting_reason` `tenant_cap` or `budget`, `waiting_since`, and the `tenant_id` from the message. Reason: waits used up SQS receives, so a tenant over its cap dead-lettered work, and budget waiters held every worker slot.
- 2026-10-15: Waiting is two columns, not a new status. Reason: the internal API's `InvestigationStatus` must equal the gateway's REST and GraphQL enums (`contracts.test.ts`), so the API reports `waiting_for` beside status `queued`.
- 2026-10-15: The gateway's recovery sweeper re-queues through the gateway's outbox. The agent decides when a parked investigation can run: `resumable` in its internal state API. Reason: the gateway owns alert groups and the outbox, only the agent sees tenant slots and the budget, and no service reads another's schema.
- 2026-10-15: Free slots are read from `pg_locks`, never probed: a two-key advisory lock shows as `classid = hashtext(key)::oid` and `objsubid` 2, negative hashes included (checked by hand). A tenant-cap waiter is resumable while fewer earlier-parked investigations of its tenant still wait than slots are free. A re-queued one stays parked until it takes its slot. Reason: the sweep then never sends more than the free slots, and the first to park runs first.
- 2026-10-15: Each re-queue has its own MessageDeduplicationId: the window's hash input plus `\nrequeue N`. Reason: the window's id is fixed, and SQS silently drops a repeat within 5 minutes, which would strand a parked investigation.
- 2026-10-15: Stuck means unknown to the agent (404), or `queued` or `running` and not waiting, with no checkpoint for `SWEEPER_STUCK_AFTER_MINUTES`, counted from the last re-queue too. `failed` is left alone: its message is in the DLQ, which `make dlq-replay` owns. A false positive only sends a duplicate, which the investigation lock and the checkpoints absorb.
- 2026-10-15: The sweeper copies `awaiting_approval`, `resolved` and `cancelled` onto the alert group, so each sweep asks only about unfinished work. Reason: nothing else moves a group off `queued` until step 9. `running` and `failed` are not copied: they can still change, and the gateway would show a stale status.
- 2026-10-15: `AGENT_SERVICE_TOKEN` (at least 32 characters) is required by the agent and the gateway. The agent compares SHA-256 digests in constant time, and every internal API error is problem details. Reason: the contract's service token, and a sweeper that must tell an outage (error) from a lost message (404).

## Preferences and Instructions

- 2026-10-14: The three portfolio projects run in parallel Claude Code sessions on one PC, sharing Docker. Each project has its own port range and compose project name; never prune or remove another project's Docker resources (user setup).
- 2026-10-13: General coding rules live in `AGENTS.md`. `CLAUDE.md` imports it so Claude Code loads the rules.
- 2026-10-13: Markdown filenames use kebab-case (for example `docs/migrations.md`).

## Solved Problems

- 2026-10-15: Waits for a tenant slot used up SQS receives and could dead-letter work, and budget waits held every worker slot. Parking fixed both: the two-agent storm test shows no DLQ entry and every parked investigation running.

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
- 2026-10-15: postgres.js serializes a parameter by the type the server reports. A JSON string passed to `${x}::jsonb` is stored as a JSON string scalar ("cannot call jsonb_to_recordset on a non-array"). Pass the value through `sql.json(value)`; spread an interface (`{ ...message }`) so it type-checks as JSON.
- 2026-10-15: TypeScript narrows a `let` read inside an immediately invoked async function, so typescript-eslint's `no-unnecessary-condition` called `while (!stopped)` constant. The relay uses an AbortController's `signal.aborted` instead.
- 2026-10-15: Jest shows no console output from the gateway integration suite here; the storm test writes its p99 to stderr.
- 2026-10-15: pyright widens a literal in `self.x = param` to `str`; annotate the attribute (`self.outcome: Outcome | Exception = outcome`).
- 2026-10-15: Turbo replays cached test output (the log timestamps repeat), so a second `make test` may not run the TypeScript suites again. For repeated-run checks use `pnpm turbo run test --force`.
- 2026-10-15: pyright strict reports a `@contextmanager` function annotated `-> Iterator[T]` as deprecated; annotate `-> Generator[T]`.

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
- 2026-10-15: Storm test (`apps/gateway/src/intake.int.test.ts`): 500 webhook posts from 10 concurrent clients against the local Postgres and ElasticMQ. They carried 400 alerts in 8 Alertmanager groups plus 100 re-sends. p99 was 86.4, 86.5 and 86.7 ms over 3 runs, with 8 groups, 5 investigations and queued messages, and no alert dropped. The posts arrive within seconds, which is harsher than the target's 2 minutes. With 3 groups and no cap, p99 was 64 to 73 ms; the per-tenant lock on opening a window adds the difference.

- 2026-10-15: Parking end to end (`services/agent/tests/test_parking_int.py`): the real gateway process with its sweeper and two agents. The storm parks exactly 3 of 5 investigations under a cap of 2, and each runs after one re-queue; both tests take about 20 s. `make test-int` passed 3 runs in a row with pytest at 98 to 108 s and storm p99 at 81 to 110 ms.

## Constraints

None yet.

## Unresolved Questions

- 2026-10-13: OpenTelemetry Demo failure-injection flag names vary by release.
- 2026-10-14: Free-tier rate limits will slow the 3-run eval (hundreds of calls). Spread runs out and check provider consoles first. Dollar cost targets apply only on a paid tier.
