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
- 2026-10-14: datamodel-codegen writes CRLF files on Windows; `.gitattributes` stores them as LF, so `git add` only warns.

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
