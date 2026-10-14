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
- 2026-10-14: With `messages-storage` on, a message queued on `investigations.fifo` survived `docker compose restart elasticmq` and a forced container recreate; its receive count (1, then 2) and the redrive policy carried over. Checked by hand, not in `make test-int`, because the check restarts the shared container.

## Constraints

None yet.

## Unresolved Questions

- 2026-10-13: OpenTelemetry Demo failure-injection flag names vary by release.
- 2026-10-14: Free-tier rate limits will slow the 3-run eval (hundreds of calls). Spread runs out and check provider consoles first. Dollar cost targets apply only on a paid tier.
