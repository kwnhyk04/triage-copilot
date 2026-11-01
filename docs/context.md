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
- Real incidents from the user's two apps on this PC, FreightAudit and FilingLens (planned 2026-10-24; Real Incidents below). They are never scored against eval floors.
- Must demonstrate: backpressure, retries, pagination, partial failures, schema migrations.

Targets (design goals) and the counted test split on Gemini: 2026-10-20, 25 scenarios x 3 runs, frozen prompt `2b91d4523fb028a6`, report `evals/reports/2026-10-20.md`, 95% intervals in brackets. The exam failed: the forbidden-proposal gate broke, so `make eval` exited 1, and 13 of the 18 measured targets missed.

| Metric | Target | Test split, 2026-10-20 | Result |
| --- | --- | --- | --- |
| Root cause top-1 | 0.75 | 0.60 (0.44 to 0.75) | fail |
| Root cause top-3 | 0.90 | 0.60 (0.44 to 0.75) | fail |
| Consistency (correct in all 3 runs) | 0.65 | 0.44 (0.24 to 0.64) | fail |
| Evidence grounding | 0.95 | 0.76 (0.60 to 0.89) | fail |
| Action correctness (reported beside its do-nothing baseline) | 0.80 | 0.71, baseline 0.52 (0.55 to 0.85) | fail |
| Action correctness, scenarios with an acceptable action | 0.80 | 0.39 (0.19 to 0.60) | fail |
| Forbidden action proposals | 0 (hard gate) | 1: noisy-neighbor-4 run 3 proposed rolling back payments | fail, eval exit 1 |
| Unapproved executions | 0 (hard gate, proven by tests) | 0; no approval or execution during the eval | pass (tests) |
| Tool-call validity | 0.99 | 0.98 (0.97 to 0.99) | fail |
| Degraded-tool scenarios | 0.60 correct, 100% name missing evidence | 0.42 correct (0.00 to 0.83); 0.75 name the down tool (0.00 to 1.00) | fail, fail |
| Tool calls per investigation | median 8, hard cap 15 | median 10 (7 to 11), max 15 | fail; cap held |
| Time to first hypothesis | p95 45 s | 77 s (69 s to 98 s), by its proxy, the time to the report | fail |
| Investigation time | p95 3 min | 77 s (69 s to 98 s) | pass |
| Webhook ack during 500-alert storm | p99 200 ms, 0 dropped, 5 or fewer investigations | p99 86 ms, 0 dropped, 5 investigations (`make test-int`, 2026-10-15) | pass (tests) |
| Tokens per investigation | median 60k, hard cap 150k | median 64,998 (47,629 to 81,584), max 116,986 | fail; cap held |
| Cost per investigation | $0.25 | $0.0148 paid-equivalent; $0 billed (free tier) | pass |

The same split on Claude Haiku 5.5 (2026-10-20 UTC, report `evals/reports/2026-10-21-test-full-3-2b91d452-claude-haiku-5-5.md`), every target side by side in the README (Model comparison): 9 of the 18 measured targets met. Haiku did better on top-1 (0.75), validity (1.00), the degraded scenarios, time (p95 31 s), and forbidden proposals (none). It did worse on action correctness (0.59, and 0.14 on scenarios with an acceptable action) and median tokens (112,178), and it billed $0.4984 of credits for the 75 runs.

A second configuration on Haiku had a token cap of 350,000 (2026-10-21 UTC, report `evals/reports/2026-10-21-test-full-3-2b91d452-claude-haiku-5-5-cap350k.md`; README, Model comparison). It met 8 of the 18 measured targets, with top-1 0.85 and 0.39 on scenarios with an acceptable action. It also made 3 forbidden restarts, which the proposal check then did not judge (version 2 does, 2026-10-25), and billed $0.7800 of credits. It is not an exam: the cap was sized from run 1's results on this split.

## Technical Stack

TypeScript 6.0 strict on Node 24 LTS (pnpm 12, Turborepo 2, Fastify 5, GraphQL Yoga 5, Next.js 16, Drizzle, zod 4, Jest 30, ESLint 10, Prettier) and Python 3.12+ (uv workspace, FastAPI, SQLAlchemy 2.1, Alembic, pydantic 2, pydantic-settings, boto3, pytest, ruff, pyright strict). Postgres 18 + pgvector, SQS (ElasticMQ in dev), OpenTelemetry, Docker, gitleaks (pre-commit hook), GitHub Actions.

LLM providers (switchable by env vars): Claude Haiku 5.5 first on Anthropic API credits until they expire on 2026-11-10 (always for live triage while `ANTHROPIC_API_KEY` is set; for the sim and evals with `LLM_CLAUDE_FIRST=true`, off by default), then Gemini Flash (free tier), then the NVIDIA NIM fallback (never in evals); Ollama for local runs. The Anthropic account's $50 monthly spend limit is shared with two other projects (docs/memory.md). Deployment: UI on Vercel, backend on the PC via Tailscale Funnel.

Local ports (bound to 127.0.0.1; chosen so all three projects can run at once; compose project name `triagecopilot`, and `triagecopilot-prod` for production, which publishes only 4202). Set in `.env`; template in `.env.example`. Checked against `docker compose ps` under `make dev` on 2026-10-14; 4202 was checked free on 2026-11-01.

| Service | Host port |
| --- | --- |
| Postgres (pgvector) | 5434 |
| SQS (ElasticMQ; container port 9324, stats UI 9325 not published) | 4566 |
| gateway / agent / executor / sim | 4200 / 4210 / 4220 / 4230 |
| gateway internal listener (executor only) | 4201 |
| production's watch port (`make prod-up`, project `triagecopilot-prod`; the egress proxy forwards it to the gateway) | 4202 |
| sim admin listener (eval harness only) | 4231 |
| watch (`make dev-watch`; only its probes listen) | 4240 |
| web (Next.js) | 3200 |
| browser tests (`make test-e2e`): web / isolated web / fake gateway | 3202 / 3203 / 4250 |
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
6. `apps/web` (Next.js): sign-in, the reports as triage tags with live progress by polling, plan decisions, and a public demo of saved reports.

## Important Components

- `infra/docker-compose.yml`: Postgres 18 + pgvector, ElasticMQ, Toxiproxy; OTel Collector, Jaeger, Prometheus and Grafana behind the `observability` profile. Images pinned.
- `infra/postgres-init.sql`: roles `gateway` and `agent`, each owning only its schema.
- `infra/elasticmq.conf`: `investigations.fifo`, redriven to `investigations-dlq.fifo` after 5 receives. Messages persist in H2 on the `elasticmq-data` volume.
- `contracts/` (spec-first; code is generated from it, never the reverse):
  - `openapi/gateway.yaml` (OpenAPI 3.1): `POST /v1/alerts` (Alertmanager webhook v4, 202 `{group_id, deduped}`), `GET /v1/investigations` (keyset: `limit`, opaque `cursor`, `next_cursor`), `GET /v1/investigations/{id}`, `POST /v1/actions/{id}/approve|reject` (body `{approver, reason?}`, 409 once decided). Errors are RFC 9457 problem details; 401 carries `WWW-Authenticate`; 429 carries `RateLimit-Limit`, `RateLimit-Remaining`, `Retry-After`; 503 carries `Retry-After`. Security: the webhook takes its own bearer token, approve and reject take `X-Approver-Key` (`APPROVER_API_KEY`, held only by the web server), other `/v1` routes and `/graphql` take `X-API-Key`, health probes none. `openapi/examples/alertmanager-webhook.json` is a sample payload.
  - `openapi/agent.yaml` (internal, 0.3.0): `POST /internal/investigations/{id}/cancel`, `GET /internal/investigations/{id}/state`, behind a separate service bearer token (`AGENT_SERVICE_TOKEN`). The state adds `waiting_for` (`tenant_cap` or `budget`), `waiting_since` and `resumable`. Only state is served; cancel is not built.
  - `messages/`: one strict JSON Schema per message version (`<name>.v<N>.schema.json`, `schema_version` const, embedded `examples`): InvestigationRequested v1 and v2, StepRecorded v1, v2 and v3, ActionProposed v1, ActionApproved v1. `step-recorded.schema.json` is the union of v1, v2 and v3 that consumers validate against; it marks v2 deprecated. Producers emit v3, whose payload is the agent's stored StepRecorded v2 event unchanged. Both require a summary of 1 to 2,000 characters, and latency and token counts that are not negative.
  - PlanRecorded v1 (P2.2, 2026-10-26): a real incident's plan, sent once after its ReportRecorded: `plan_id`, `plan` (`kind` none, restart_service or code_fix; `target`; up to 10 `files`; `rationale`, the only free text) or null with `problem`, and `models` (part `plan`). Gateway API 0.7.0 serves it as `Investigation.plan` (`proposal`, `problem`, `status`, the decision) and adds `POST /v1/plans/{id}/approve|reject` (approver key; 409 once decided or without a proposal); the plan's models follow the report's in `Investigation.models`.
  - ReportRecorded v2 (P1.7, 2026-10-25) carries v1's report by reference, plus a nullable `verdict` (`real`, `not_real`; null for the sim and for a report the agent wrote after a stop) and `models`: every model asked, per part (`triage`, `summary`), with the calls it answered, the ones it failed or skipped, and the last reason. The union `report-recorded.schema.json` accepts v1 (deprecated) and v2; the agent emits v2.
  - InvestigationRequested v2 adds `tenant_id`; the union `investigation-requested.schema.json` accepts v1 (deprecated) and v2, and the gateway emits v2. Its MessageGroupId is the hex SHA-256 of tenant and Alertmanager `group_key`, which SQS cannot take raw (spaces, over 128 characters); its MessageDeduplicationId hashes tenant, group key and window start.
  - `graphql/schema.graphql`: reads and live updates only. Investigation, Hypothesis, Evidence, ProposedAction, Step, ToolCall; Relay connection `investigations(first, after)`; `investigation(id)`; subscription `investigationUpdated(id): Investigation!`.
- Codegen (`make codegen`): `packages/ts-clients/scripts/codegen.ts` writes `packages/ts-clients/src/generated/` (openapi-typescript, json-schema-to-typescript plus the embedded schemas, graphql-codegen resolver types) and `apps/web/src/generated/graphql.ts` (typed document strings for `apps/web/src/graphql/*.graphql`). `packages/py-clients/scripts/codegen.py` writes `packages/py-clients/src/py_clients/generated/` (datamodel-codegen). Both wipe their output first.
- Clients: `@triage/ts-clients/gateway` has `createGatewayClient` (openapi-fetch over the X-API-Key routes only; the webhook and decision routes are excluded from its type) and `createDecisionClient` (approve and reject with `X-Approver-Key`; only `apps/web/src/lib/decisions.ts` calls it). `/agent` uses the service token. All have a 10 s default timeout; `/messages` (`parseMessage(name, data)`, ajv 2020 validation). `py_clients.clients` (httpx2, 10 s default timeout): `AlertsClient`, a read-only `GatewayClient` (no approve or reject: only the web backend-for-frontend records human decisions), `AgentClient`, and `ApiError` (status, problem, Retry-After).
- `make contracts-check` (`scripts/contracts_check.py`): hashes the generated dirs, runs codegen, fails if anything changed. `make contracts-breaking` compares against the git ref `CONTRACTS_BASE` (default `HEAD`). It runs oasdiff (`tufin/oasdiff:v1.33.0` in Docker, no network, only `.git` and `contracts/` mounted) on both OpenAPI specs and fails on ERR-level changes. It also fails when a `contracts/messages/*.v*.schema.json` that exists at the base was edited or deleted.
- `infra/docker-compose.prod.yml` (step 10, 2026-11-01): production, on top of the base file, as its own compose project `triagecopilot-prod`, with its own containers, networks and volumes. Run it only through `make prod-up`, `prod-down`, `prod-token`, `prod-switch` and `prod-digest`.
  - Ports: only the egress proxy publishes, `127.0.0.1:${PROD_GATEWAY_PORT}` (4202) to its 4202, which it forwards to the gateway's 4200 (egress lockdown, 2026-11-01). Docker publishes no port from an internal network, so the gateway has none of its own. Postgres, ElasticMQ, Toxiproxy (profile `chaos`) and the observability services publish nothing.
  - Networks (egress lockdown, 2026-11-01): every network is internal except `egress`, which only `egress-proxy` joins.
    - `data`: Postgres, ElasticMQ, the gateway, the agent, the three one-shots, Toxiproxy and the collector.
    - `edge`: the gateway and the Funnel sidecar alone, the sidecar at 10.231.233.18.
    - `ingress`: the gateway at 10.231.233.34 and the proxy, for 4202.
    - `outbound`: the proxy's clients at fixed addresses, the agent (10.231.233.2) and the sidecar (10.231.233.3).
    - `monitoring`: the observability profile, with the collector also on `data`.
    - The fixed ranges sit in 10.231.233.0/24, beside FreightAudit's 10.231.231 and FilingLens's 10.231.232.
  - The egress proxy (squid, `infra/squid/squid.conf` mounted read-only; image `triage-copilot-squid` from `infra/docker/squid.Dockerfile`; read-only, no capability): CONNECT on 443 only, from each client's own port (agent 3128, sidecar 3129) and address, to that client's hosts by name (agent: `api.anthropic.com`, `generativelanguage.googleapis.com`, `integrate.api.nvidia.com`; sidecar: `controlplane.tailscale.com`, `^derp[0-9]+[a-z]*[.]tailscale[.]com$`, `acme-v02.api.letsencrypt.org`). Every non-public destination is refused first, by each address a name resolves to. Port 4202 is a reverse proxy to the gateway's fixed address only: a CONNECT there gets squid's own 405, the cache manager 403, and the gateway sees no X-Forwarded-For from it. It passes the client's `Authorization` on (`login=PASSTHRU`, 2026-11-01), which squid otherwise drops on the way to an origin server; the probe's peer passes none. `max_filedescriptors 4096`: at Docker's 1,048,576 squid idled at 153 MiB of its 256, at 4,096 at 8.6 MiB. It starts after the gateway, so a gateway from before the lockdown frees 4202 first. The agent and the sidecar get `HTTPS_PROXY`; the sidecar runs with `--no-logs-no-support`, no capabilities and no new privileges, so it cannot forge the agent's address on `outbound` (the agent runs as a non-root user).
  - Services: the gateway and the agent run from the GHCR images (`TC_IMAGES`, `TC_TAG=sha-<12>`), and so does the egress proxy. Three one-shots run before them: `gateway-migrate`, `agent-migrate` and `agent-runbooks`. A Tailscale sidecar (`tailscale/tailscale:v1.104.4`, pinned by digest) serves `infra/tailscale/serve.json`: Funnel's :443 to `http://gateway:4200` only. The sim and the executor are not deployed.
  - Settings: compose reads `.env.prod` (the three database passwords, the image names, `TS_AUTHKEY`). The gateway reads `.env.prod.gateway` and the agent services `.env.prod.agent`. Every container-side host, port and URL is fixed in `environment:`.
  - Lifecycle: `make prod-up` waits at most 300 s (`--wait-timeout`) for the one-shots to finish, the egress proxy's port check and the gateway's and the agent's `/readyz` healthchecks to pass. A container that crash-loops on a refused setting ends the wait there, and its logs name the setting. Restart is unless-stopped, and each service has a memory limit.
  - Alerting (branch `prod-alerting`, 2026-11-01; README, Deploy, Alerting; docs/alerts.md), all in the `observability` profile:
    - Prometheus runs `infra/prometheus.prod.yml` and `infra/prometheus-alerts.yml` (8 rules: GatewayNotReady, AgentNotReady, WatchNotReady, InvestigationsFailing, ClaudeOff, ClaudeDailyCapReached, EgressDenied, MonitoringBlind; each links its section of `docs/alerts.md`), with the volume `prometheus-data`. It sits on `data`, `monitoring` and `alerting` (10.231.233.52).
    - The blackbox exporter (`prom/blackbox-exporter:v0.29.0`, `infra/blackbox.yml`) shares Prometheus's network namespace and listens on 127.0.0.1:9115. It probes the gateway's, the agent's and watch's `/readyz`; watch's goes through the proxy's port 4240 to `host.docker.internal:4240`.
    - Alertmanager (`prom/alertmanager:v0.34.1`, `infra/alertmanager/`) on `alerting` alone (10.231.233.50), with the volume `alertmanager-data`. It sends one message per rule to Discord (proxy port 3130, `discord.com:443`) and to Gmail through `smtp-relay` (socat from the proxy's image, 10.231.233.51, proxy port 3131, `smtp.gmail.com:587`, STARTTLS end to end). Its entrypoint takes the four `ALERT_*` values from `.env.prod`, writes the secrets to tmpfs files, and stops naming any missing or malformed one.
    - egress-log (the collector image, `infra/otel-egress-log.yaml`, 10.231.233.53) receives the proxy's access log over UDP and serves `tc_egress_denied_total`.
    - The agent's `GET /metrics` (`agent/metrics.py`, agent API 0.4.0) serves `tc_investigations_failed_recent{kind}` and `tc_claude_stopped{reason}`, the code of `llm.stop()`, which `spent()` now wraps.
    - Grafana reads `PROD_GRAFANA_ADMIN_PASSWORD`, never dev's.
  - `make prod-env` (`scripts/prod_env.py`) writes the three env files with fresh secrets, prints none, leaves empty only what the user pastes, and refuses when any of them exists. `COMPOSE_PROFILES` is exported to dev's compose alone (`infra-up`, `infra-down`), so production reads it from `.env.prod`.
  - Tests: `services/agent/tests/test_prod_compose.py` checks the ports, the network map, the fixed addresses, the env files, the secrets each service may read, the Funnel config and that CI builds every image production runs. Alerting adds `test_alert_rules.py`, `test_prod_env.py`, and in `make test-int` `test_monitoring_int.py` (promtool's rule tests and each config's own check), `test_metrics_int.py` and `test_alerting_int.py` (live delivery to a fake Discord and a fake Gmail on throwaway networks). `test_egress_int.py` (in `make test-int`) runs the real squid image and conf, with compose's hardening, on throwaway networks at 10.231.239.x, under its memory limit: each client's allowed tunnels open, every other request is refused, the proxy idles under a quarter of its limit, and watch's port reaches only the gateway and passes watch's token on. Its gateway stand-in, bash and socat from the proxy's image, answers with the request line and headers it got. `test_providers.py` checks that each model provider's client tunnels through `HTTPS_PROXY`.
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
  - Tables: `investigations` (`tenant_id` from the message, recorded when it starts since P1.11 and before that only when it parked; while parked: `waiting_reason` and `waiting_since`; `queued_budget` is no longer written), `investigation_steps` (partitioned by month on `created_at`), `tool_calls`, `runbook_chunks` (`vector(768)`, no ANN index yet), `proposed_actions` (unique `idempotency_key`), `dead_letters` (poison messages), `ai_usage` (model tokens per UTC day).
  - Retention (`agent/retention.py`, P1.11): a daemon thread beside the partitions one deletes, at start and then daily, the steps, tool calls and unsent step messages over 30 days old of finished investigations of `LIVE_TENANTS`. The investigation row, its report and report message, and its model calls stay. An unfinished one keeps its steps, which its next turn replays.
- Gateway intake (`apps/gateway/src/intake.ts`, `outbox.ts`, `cli.ts`):
  - `POST /v1/alerts` checks the tenant's bearer token after the rate limit and before reading the body, then validates the payload (1 MB, 200 alerts, 10 s to send the request).
  - It stores each firing once per (tenant, fingerprint, startsAt) and records status changes. New alerts join the group of their group key whose 5-minute window opened last, or open a group. A new group with a firing alert gets an investigation, whose InvestigationRequested v2 goes to `outbox` in the same transaction, unless the tenant already started `MAX_NEW_INVESTIGATIONS` (5) in the last 5 minutes; otherwise it stays `queued_not_investigated`. It answers 202 `{group_id, deduped}`; the request path never calls a model.
  - The relay sends due outbox rows to `investigations.fifo` (nudged after each commit, else every second), retries a failure after 2^attempts seconds up to 5 minutes, and parks a row after 10 failures.
  - `cli.ts`: `tenant:token <name>` issues a webhook token (stored as SHA-256); `outbox:replay` re-queues parked rows.
  - Flood guard (P1.9): a tenant also starts at most `MAX_DAILY_INVESTIGATIONS` (30) investigations a UTC day, and every `queued_not_investigated` group records why (gateway 0007: `resolved`, `burst`, `daily_cap`). `cli.ts digest [day]` (`make digest`) lists a day's uninvestigated groups per tenant, with their reason, alert count and alert names.
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
- Plan decisions (P2.4, `createDecidePlan`): one row per plan in `gateway.plan_decisions` (gateway 0008, beside `gateway.plans`); a second decision gets 409, as does a plan without a proposal, and nothing is queued: in phase 2 an approval runs nothing.
- Gateway decisions (`apps/gateway/src/decisions.ts`): approve and reject insert into `approvals` (`ON CONFLICT DO NOTHING`; a second decision gets 409) and, for an approval, queue ActionApproved v1 through the outbox in the same transaction. Its FIFO group is the hash of tenant and target, so one service's actions run in order; its deduplication id is the action id. A store failure answers 503 with `Retry-After`.
- Gateway list (`apps/gateway/src/investigations.ts`): `GET /v1/investigations`, investigated alert groups of every tenant, newest first by (created_at, investigation id), with an opaque base64url cursor holding created_at to the microsecond. `limit` defaults to 25; outside 1 to 100, or with a cursor the gateway never issued, it answers 400. The title is the first alert's alertname and service, and each item carries the report's `verdict` (gateway API 0.6.0, P1.10).
- Gateway API 0.8.0 (step 9): the list takes `source` (`live`, `sim`, from `LIVE_TENANTS`), and every investigation carries `source`, `cause` (the top hypothesis) and `plan_status`. A `queued` group with a step under `SWEEPER_STUCK_AFTER_MINUTES` old reads `running`, derived on read. `make demo-save IDS=...` (`cli.ts demo-save`) writes eval runs to `apps/web/src/demo/generated/eval-runs.ts` on a read-only connection, refusing any live one. It refuses to run unless `LIVE_TENANTS` is set (`{}` while live is off), since unset would read every investigation as simulated.
- Web (`apps/web`, step 9):
  - Sign-in: one password, its argon2id hash in `LOGIN_PASSWORD_HASH` (`node:crypto`, 64 MiB, 3 passes, 4 lanes). An 8-hour HMAC session cookie `__Host-triage_session` (HttpOnly, Secure, Lax), signed with `SESSION_SECRET`. 5 attempts per address and 30 in all per 15 minutes, counted before the hash check. Both settings are required under `next start` and on Vercel; under `next dev` it is both or neither.
  - Keys (step 10): under production (`next start`, Vercel) the `.env.example` values of `GATEWAY_API_KEY` and `APPROVER_API_KEY` are refused, and the two keys must differ everywhere. The Eval runs tab's empty text links to the demo's recorded runs (`/demo?source=sim`), since the production database keeps none.
  - `src/proxy.ts` sends every request without a session to `/login?next=...` (303), except `/`, `/login`, `/logout`, the probes and `/demo`. It sets a nonce CSP with `frame-ancestors 'none'`.
  - `src/lib/data.ts` is the only way pages and actions reach the gateway, and each function checks the session first; `app/data-access.test.ts` scans for any other import. A gateway that does not answer (a throw, the 5 s timeout, 502, 503 or 504) sends the reader to `/demo?offline=1`. Decisions record the approver `operator`.
  - Pages: the landing; the list with Live incidents and Eval runs tabs; the report (verdict tag, decision, cause and evidence, timeline, models, fallback notes); `(app)/error.tsx`; `not-found.tsx`. Design: `globals.css` (triage tags; light and dark from the system), fonts committed in `src/app/fonts/` with `OFL.txt`.
  - Live progress: `LiveRefresh` calls `router.refresh()` every 3 s (report) or 10 s (list) while the tab is visible and not paused. It runs only while something can change: running, queued under 15 minutes, or up to 6 minutes after a real verdict for its plan.
  - Demo (`/demo`): five recorded eval runs and three hand-written live samples (`src/demo/`), labeled illustrative, with no gateway or session; the plan buttons are disabled.
  - Browser tests (`make test-e2e`, `apps/web/e2e`): Playwright 1.64.1 on Chromium with `@axe-core/playwright` 4.14.0, against `e2e/fake-gateway.ts` (`node:http`, 4250) and two `next start` servers (3202; 3203 with its own limiter and a closed gateway). Browsers live in `PLAYWRIGHT_BROWSERS_PATH` (`H:\.cache\ms-playwright`). CI's `e2e` job installs Chromium with `--with-deps`, and `ci-result` needs it.
- Gateway detail (`createGetInvestigation`, P1.10): `GET /v1/investigations/{id}` behind X-API-Key; 400 for an id that is no uuid, 404 for an unknown one. It serves the group's status, the report (summary, hypotheses ranked in the report's order with their `service`, `missing_evidence`, `partial`), `verdict` and `models` from `investigation_reports`, the proposals with their decisions, and the mirrored steps; each piece of evidence quotes the first 2,000 characters of its cited result, or is empty when no result is kept. `tokens_used` sums the kept steps' tokens.
- Retention (`apps/gateway/src/retention.ts`, P1.11): at start and then daily, for the tenants in `LIVE_TENANTS` (the agent's variable; the gateway reads its keys), the `snapshot` annotation of alerts stored over 30 days ago is removed (summary and description stay) and mirrored steps over 30 days old are deleted. Reports stay.
- Gateway credentials (`apps/gateway/src/app.ts`): a `preParsing` hook asks for `GATEWAY_API_KEY` as X-API-Key (constant-time) on every route, unknown ones included, except the probes, the webhook and approve or reject; 401 problem details with `WWW-Authenticate: ApiKey header="X-API-Key"`. The web helpers send it.
  - Failed checks (step 10): the 401s of the X-API-Key check, the webhook token and the approver key count per client address, in fixed one-minute windows (`credentialLimit`). Past `AUTH_FAILURES_PER_MINUTE` (20), an unverified credential from that address gets 429 problem details with Retry-After; the webhook's comes before its database lookup. A key that verifies is never held back, nor is a webhook token that verified in the last 10 minutes.
  - `GATEWAY_TRUST_PROXY`: addresses or CIDRs feed Fastify's `trustProxy` (off by default). `true` and a hop count are refused; Fastify 5.12.7 trusts nothing on a hop count. So is any range that holds every client, in any spelling: all of IPv4 (`0.0.0.0/0`), all of IPv6 (`::/0`), or all of IPv4 as the mapped range Fastify matches IPv4 clients against (`::ffff:0:0/96`) (2026-11-01). Production's value is the Funnel sidecar's fixed address, 10.231.233.18 (README, Deploy, check 5).
  - Under `NODE_ENV=production`, which the prod compose sets, startup refuses any of the four keys or tokens still at its `.env.example` value (`PLACEHOLDERS` in `@triage/ts-config`, kept equal to `.env.example` by a test).
- Gateway internal listener (`apps/gateway/src/internal.ts`, `executions.ts`), on `GATEWAY_INTERNAL_HOST`:`GATEWAY_INTERNAL_PORT` (127.0.0.1:4201; 0.0.0.0 inside the production container, published nowhere), behind `EXECUTOR_SERVICE_TOKEN`: `POST /internal/actions/{id}/execution` claims an approved action (201 new, 200 an earlier claim, 404 no approved decision); `.../execution/result` records succeeded or failed once (409 after). The public listener has no such route. The sweeper logs executions stuck in claimed past `SWEEPER_STUCK_AFTER_MINUTES`.
- Webhook fingerprints (`apps/gateway/src/intake.ts`): `fingerprint()` ports Prometheus's `LabelSet.Fingerprint`; the webhook replaces each alert's fingerprint with it before deduplication and logs how many differed.
- Watch (`services/watch/src/watch/`, P1.4 to P1.6, 2026-10-25), on the host, never in a container, through `make dev-watch`:
  - `catalog.py`: per app (FA, FL) the compose project of each stack, the services with their kind (app, worker, database, queue, cache, storage, proxy), dependencies and stacks, the JSON log fields kept, the log lines that are alerts (`alert` equal to `extract_paused` or `claude_switched_off` in FA; FL's `event` of the Claude switch-offs, a provider out, a failed readiness check or a task failed for good), the dev stack's Prometheus rules with service and severity, and its snapshot queries.
  - `scrub.py`: allowlisted JSON fields, then masks (PEM, key shapes, JWTs, bearer tokens, URL credentials, query strings, emails, `*.ts.net`, `password=`-style assignments), a traceback cut to its exception line, fields at 500 and lines at 1,000 characters. Every pattern starts only where a run of its characters starts, so a long run costs linear time.
  - `readers.py`: `docker ps` (label filter on the compose project, names matched exactly, the label checked again) and `docker logs --timestamps --since --until --tail 200 -- <listed name>`; `git log --name-only` of `refs/remotes/origin/main` with hooks, fsmonitor, textconv, external diff, signatures, lazy fetch, system and global config off; HTTP GET to configured URLs only, with no redirect, proxy or cookie and at most 64 KB read. Every argv token passes a charset with nothing a shell reads, each child sees only an allowlist of environment variables (no TC secret), and each call has a timeout (docker and git 10 s, HTTP 5 s).
  - `poll.py` (`Watcher`): each poll probes the health URLs, reads `docker ps` and each container's new log lines and, with a dev stack's Prometheus, its alerts. An alert fires after 3 failed health checks, 2 down container states or 1 log line or Prometheus alert in a row, and resolves after 2 good polls; a log signal stays bad 10 minutes after its line, and 10 error lines in 5 minutes make an ErrorBurst. Only transitions are posted, one alert per webhook, to the tenant's webhook (`groupKey` `{}:{app="<app>"}`). Its own rate limit (P1.9): an alert that fired 3 times within an hour stays firing until the first of those firings is an hour old, so a flapping alert posts at most 3 firings an hour and shows as one long firing. Labels are always alertname, app, service, severity and source; annotations hold the summary, a description and, on firing, the snapshot (at most 48 KB: health answers, containers, the newest scrubbed log lines of the service and its dependencies, recreations and origin/main's commits of the last 24 hours, Prometheus alerts and queries, reader problems). A refused post is sent again at the next poll.
  - `main.py`: `WATCH_*` settings (a token, stack, health URLs, repo and Prometheus URL per app; an app is watched only with its token), `/healthz`, and `/readyz`, ready while the last poll, at most three intervals ago, read and posted everything. It posts to `WATCH_GATEWAY_URL`, by default the production gateway on `127.0.0.1:4202` (step 10), which the egress proxy forwards to it; the web's `GATEWAY_URL` is not read.
- Executor (`services/executor/src/executor/run.py`): `run_action(message, client, dry_run, effectors)` validates ActionApproved v1, checks `ALLOWED_KINDS` (and the effector) before any call, claims through `py_clients.ExecutionsClient`, checks the approved kind again, then runs the effector or, in a dry run, records the plan, and reports the result. Gateway unreachable or any error: refused. A claim found claimed is never retried. No consumer runs it yet.
- AI pipeline (step 6a), `services/agent/src/agent/`:
  - `providers.py`: `GeminiProvider` (google-genai 2.27.0 on an httpx2 client; explicit key sent as `x-goog-api-key`, `vertexai=False`; function calling mode ANY, thinking LOW, thought signatures kept in `tool_calls.provider_state`), `AnthropicProvider` and `OpenAICompatibleProvider` (NVIDIA NIM, OpenRouter, Ollama). All return the tool call, usage (input with cached tokens, output with thinking, cache reads and writes) and raise `RateLimited`, `QuotaExhausted` (a per-day 429), `CreditsExhausted` or `ModelError`; none retries.
  - `AnthropicProvider` (anthropic 1.15.0, native tool use, `claude-haiku-5-5`): a forced `tool_choice` (`any`, or the report tool) with `disable_parallel_tool_use`, which skips thinking, so no thinking block is ever replayed; summaries send `thinking: disabled`. One cache breakpoint on the system prompt caches the tool definitions and the system prompt (5,036 tokens). The explicit key and base URL win over `ANTHROPIC_*` variables, the SDK never retries, and its spans stay `metadata_only`. A 402 `billing_error`, or a 400 naming a spend or usage limit or the credit balance, is `CreditsExhausted`; every 429, with or without retry-after, is `RateLimited`; a 401 or 403 is `KeyRejected`; a refusal is not retried. The tests feed Anthropic's error shapes (`services/agent/tests/anthropic_errors.py`) through the real SDK on a mock transport.
  - `llm.py`: `Router` (Claude first while it is in the chain, then the primary, then the free fallback; with `LLM_FALLBACK_ON_RATE_LIMIT=false`, for evals, Claude and the primary but never the free fallback. Any failure of Claude, a 429 or 529 included, moves that call alone down the chain; nothing waits for Claude. Log pages are summarized by Claude first, then the small role) and `Limiter` (RPM in a sliding minute per process; before each request it refuses a role that `spent()` reports, or whose provider's key was rejected in this process, so no request reaches a spent model; a rejected Anthropic key stops every Claude model until the agent restarts, and `available()` sees it too). `spent()`: Claude from `LLM_CLAUDE_UNTIL` (UTC) on; RPD from `agent.model_calls` since midnight Pacific; a provider's "daily quota spent" since midnight Pacific; a `credits_exhausted` call since the month began (UTC), with a warning log line when Claude's credits run out. Parked investigations stay parked while every model of the chain is spent. `LLM_PRIMARY_RPD` defaults to 260, under the 275 requests measured on 2026-10-16 (not a documented limit). Every model call records `prompt_sha`, the sha256 of the prompt its process runs (`job.prompt_sha`); the eval refuses a run whose calls name another prompt.
  - Roles (`settings.py`): `LLM_{PRIMARY,FALLBACK,SMALL}_{PROVIDER,MODEL,BASE_URL,RPM,RPD}`, each provider's key from its own variable; keys and `FASTEMBED_CACHE_PATH` are required only when `AI_ENABLED=true`, which now defaults to false. Pinned 2026-10-15: `gemini-3.5-flash-lite`, fallback `deepseek-ai/deepseek-v4.1-flash`, small `gemini-3.1-flash-lite`. Claude: `LLM_CLAUDE_FIRST` (false: the sim's chain asks `LLM_TRIAGE_MODEL`, `claude-haiku-5-5`, first, as live triage's always does), `LLM_CLAUDE_RPM` (50), `LLM_CLAUDE_UNTIL` (2026-11-10), `ANTHROPIC_API_KEY`; any role may also run on `anthropic`, as the pinned-Haiku evals did. Every Claude call but a plan is held to `LLM_TRIAGE_DAILY_USD` a day and every Claude call to `LLM_CLAUDE_MONTHLY_USD` a month, each counted in its own database.
  - `tools.py`: pydantic argument models and the definitions the model sees (plain JSON schema, a required `reason`): `get_alert_context` (the gateway's internal route, agent token), `query_metrics` (Prometheus selectors against the sim), `query_logs` (100 raw lines on page 1; later pages of 200 summarized by the small model), `list_deploys`, `get_dependencies`, `search_runbooks`, `propose_action` and `submit_report` (both handled by the job). Results are capped at 24,000 bytes and cited by their `tc_` id; 4xx is the model's to fix, 5xx and timeouts are missing evidence.
  - Live triage (P1.8, 2026-10-25): live tenants run on their own `Prompt` (`job.SIM`, `job.LIVE`). `live.py` holds the live system prompt, brief and sha (`LIVE_PROMPT_SHA`, apart from the sim's frozen one), `LiveReportDraft` (the report plus a required `verdict`) and the live tools: `get_alert_context` with annotations, `read_snapshot` (watch's snapshot of one alert as text, the newest log lines first, or one service's lines), `get_dependencies` from watch's catalog, and `search_runbooks`; no `propose_action`. The agent depends on the `watch` package for its catalog and scrubber only. The live router asks Claude first (`Settings.triage()`, `LLM_TRIAGE_MODEL`, while `ANTHROPIC_API_KEY` is set), then the free chain; a Claude call runs only if today's Claude spend (UTC, every Anthropic call priced by `pricing.py`) plus its worst case fits `LLM_TRIAGE_DAILY_USD` ($0.40). Claude's 429 with a retry-after of at most 60 s is waited out; a longer one moves on. Live investigations get `LIVE_INVESTIGATION_TOKEN_CAP` (350,000) and `LIVE_DAILY_AI_TOKEN_BUDGET` (25,000,000); the sim's keep the eval's. Calls the agent never sent are recorded `skipped` (agent 0012), and provider text is masked before it is kept or logged. Every report goes out as ReportRecorded v2, its `models` grouped from `model_calls`.
  - The planner (P2.1 to P2.3, 2026-10-26; `agent/planner.py`): a live triage's real verdict, with the `planner` switch on (default off), adds a due row to `agent.plans` (agent 0013) in its report's transaction; the job then makes the plan once and sends PlanRecorded v1 after the report, and a redelivery finishes a plan left due. The planner is told the report, the results it cites, the app's catalog and source allowlist, and the newest watch snapshot without its log lines, within 40,000 bytes. Its chain (`Router.plan`): the planner's Claude (`Settings.planner()`: `LLM_PLANNER_MODEL` `claude-opus-5-5`, `LLM_PLANNER_EFFORT` medium, one streamed structured-output call with no tools, 16,000 output tokens, 300 s, no server-side refusal fallback), then the free chain through one forced call of `submit_plan`; a skipped, refused, cut-off (`Truncated`), failed or invalid answer moves on, and every call is recorded as role `planner`. A Claude plan runs only if today's plans plus its worst case ($0.48, input at $4 since nothing is cached) fit `LLM_PLANNER_DAILY_USD` ($1.00), inside TC's $15 a month; the triage cap no longer counts plans. `plan_refusal` (proposal check version 2 on real targets) then refuses a restart or change of a database, queue, cache, storage or proxy, a restart unless the target's own health check or container fails while everything it depends on is shown healthy, and a code fix outside the app's source paths; a refused plan is recorded as no plan, with the reason.
  - TC's monthly Claude cap (P1.10 branch, 2026-10-25): every Claude role (live triage, the Claude fallback, any role an eval puts on `anthropic`) carries `LLM_CLAUDE_MONTHLY_USD` ($15); a call runs only if this UTC month's Claude spend plus its worst case fits. `Router.available()` judges each model on the largest call a turn can make (`LARGEST_CALL`: a prompt at the limit and the forced report's output), so an eval pinned to Claude parks until the month ends rather than waking at every sweep. A Claude model without prices fails at startup.
  - `prompt.py`: the system prompt lists the sim's 21-metric catalog (a test keeps the two equal) and the root-cause classes with a labelling convention: name the first cause in the chain, and the service where it lives (`evals/prompt-log.md` records every change).
  - `job.py`: the loop. Stops at the report, 15 tool calls, 300 s since `started_at` (moved forward by any time parked, so a run resumed after a quota stop keeps the time it had left), or when another turn would leave too little for a forced report or could take its prompt past 100,000 tokens; then a forced `submit_report` turn, or the job's own partial report. A hard token cap per investigation, `INVESTIGATION_TOKEN_CAP` (150,000 by default; the second Haiku run uses 350,000), and a hard 100,000-token limit on every prompt (`PROMPT_LIMIT`, log summaries included), both through `TurnBudget` (see docs/memory.md). Every model request is recorded in `model_calls` with its tokens before the step it produced. `build_report` enforces evidence ids, missing evidence, the 0.95 and 0.6 confidence ceilings, and proposals from `proposed_actions`; status `awaiting_approval` when there are proposals.
  - `outbox.py`: StepRecorded v3 and ReportRecorded v1 committed with their step, relayed to `agent-events.fifo` (group = investigation, `message_type` attribute); 10 failed sends park a row.
  - `runbooks.py`: fastembed `nomic-ai/nomic-embed-text-v1.5` from `FASTEMBED_CACHE_PATH` (H:\.cache\fastembed, about 0.5 GB), `search_document: ` and `search_query: ` prefixes, pgvector cosine search. CLI `python -m agent.runbooks download|ingest|info`.
- Gateway events (`apps/gateway/src/events.ts`): consumes `agent-events.fifo` one message at a time into `investigation_steps` and `investigation_reports` (ReportRecorded v1 or v2; v2's `verdict` and `models` in their own columns, gateway 0006), copies the report's proposals into `proposed_actions` and moves the alert group to the report's status; idempotent and in order per investigation.
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
    - Caches each run under the prompt hash, the pinned models, the scenario sha256, the run index, the proposal check's version and, off its default, the token cap, and resumes a parked run first.
    - A run with a fallback or unpinned model does not count; a run where the sim lost its scenario reruns once.
    - Exit codes: 1 for a broken floor or gate (any forbidden proposal), 2 for a quota stop (Gemini's day or Claude's credits), 3 for setup errors.
    - Writes `evals/reports/<date>.json` and `.md` and the README's eval table (only between the eval markers; the README's Exam history below them is kept by hand) for the full test split on the default models; other runs write `<date>-<split>-<subset>-<runs>-<prompt sha, first 8>`, with `-<primary model>` appended on other models and `-cap<N>k` off the default token cap (`report_name`). Every report names its token cap and counts the reports each cap forced; off the default cap its notes start "Second configuration, not an exam". `SET_FLOORS=1` off the default models or cap, and a run whose stop note names another cap or that passes the cap, stop the eval with exit 3.
    - Costs: `cost_paid` prices every token at its model's rates (cache reads and writes at their own); `cost_billed` counts only models billed from credits, and its $0 gate holds only when every pinned model is free.
  - `evals/thresholds.json` holds the floor rules and per-scope floors, set on 2026-10-20 for `test-full-3` and `dev-ci-1`. `SET_FLOORS=1` writes them before the gate check, so a run that exits 1 still sets them. It writes the file as Prettier prints it, with LF line ends (`prettier_json`), since lint runs Prettier over it. Three `dev-ci-1` floors are set by hand (validity 0.92, median tool calls 12, median tokens 80,425) and its p95 times have none, so CI only reports them; `SET_FLOORS=1` is never rerun on the ci subset (docs/memory.md, 2026-10-20). `evals/pricing.json` holds each model's rates and billing: the Gemini models at paid-tier rates (official page, 2026-10-15) and `free`; `claude-haiku-5-5` at $0.10 in, $0.01 cache reads, $0.125 cache writes and $0.50 out per million tokens, `credits`. `evals/prompt-log.md` logs prompt changes; the prompt is frozen at `2b91d4523fb028a6` (`test_eval_score.py` pins it).
  - `make dev-agent AGENT_RELOAD=` runs the agent without its reloader, which watches the whole repo, so code edits never restart it during an eval.
- CI (`.github/`, step 7; every action pinned to a full commit SHA, every base image by digest):
  - `ci.yml` on pull requests and pushes to main. Jobs:
    - `checks`: lint, typecheck, contracts-check, build, actionlint, then scenarios-check and contracts-breaking against `HEAD^1`, or the pushed `before` commit when it exists.
    - `unit`, `integration` (`make test-int` with the fastembed cache restored), `secrets` (gitleaks over the full history) and `images` (build only).
    - `ci-result`, the one check branch protection requires: it fails unless every job succeeded.
    - No job gets a secret or a model key.
  - `publish.yml` pushes the five images (gateway, agent, executor, sim and, from 2026-11-01, squid, the egress proxy) to `ghcr.io/<owner>/triage-copilot-<service>` (tags `sha-<12>`, `main`). Both workflows list each image's Dockerfile and target in one `matrix.include`. It runs only after a successful `ci.yml` run of a push to this repo's main, builds exactly that commit and downloads nothing from it.
  - `eval.yml`, off until the variable `EVAL_ENABLED` is true:
    - Triggers: a manual run (ci subset or full dev split) and Mondays 18:00 UTC (the ci subset, about 85 Gemini requests).
    - Only `GEMINI_API_KEY` is a secret. `NVIDIA_API_KEY` is the placeholder `ci-unused-not-a-key`, which the startup check accepts and evals never use. It fails if Claude could be called.
    - A quota stop fails with "rerun after the quota resets".
  - `.github/actions/setup` installs pnpm, Node 24, uv and the locked dependencies. It writes CI's `.env` with `scripts/ci_env.sh`: `.env.example` without the provider keys, `FASTEMBED_CACHE_PATH`, `AI_ENABLED` and `LLM_FALLBACK_ON_RATE_LIMIT`, which the Makefile's include would otherwise force on the job.
- Images (`infra/docker/`, built from the repo root, `.dockerignore` keeps `.env*` and `.git` out):
  - `python.Dockerfile` has targets agent, executor and sim (`uv sync --frozen --no-dev --no-editable`, non-root). Agent adds runbooks and migrations and mounts the model at `/models/fastembed`; sim adds the scenarios.
  - `gateway.Dockerfile` keeps the workspace layout for Node's type stripping and includes drizzle-kit for `db:migrate`.
  - `squid.Dockerfile` (2026-11-01): `ubuntu:24.04` by digest and Ubuntu's newest squid from noble-security, never older than `SQUID_MIN` (6.14-0ubuntu0.24.04.4; the first build got .5), run as the `proxy` user. Ubuntu's own squid.conf is removed, so the image never runs without TC's.
  - Services bind 127.0.0.1 by default; the production compose sets 0.0.0.0 inside its containers, on internal networks, and publishes only the egress proxy's 4202, on 127.0.0.1:4202, which it forwards to the gateway's 4200 (step 10; egress lockdown, 2026-11-01).

Still planned: the executor's queue consumer and effectors.

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
- web report view (P1.10, `apps/web/src/app/investigations/`): `/investigations` lists every investigation, newest first, 25 a page, with its status and verdict; `/investigations/<id>` shows one report (`components/report.tsx`): the verdict, the cause with its evidence excerpts, missing evidence, proposals, the models per part and the fallback notes (`lib/report.ts`: each model that missed requests, what answered its part instead, and why). The server renders both per request through `lib/investigations.ts`, which sends the gateway's API key and passes only a uuid on; the browser never calls the gateway. The pages have no login yet (step 9).
- web plan section (P2.2 and P2.4): the report shows its plan (the proposal, or why there is none, and its decision). A pending plan has a form (a name, an optional reason, Approve or Reject) that posts to a server action (`app/investigations/[id]/actions.ts`), which records the decision through `decidePlan` with the approver key; Next.js refuses an action posted from another origin.
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
  - Proposal check (2026-10-21, branch `proposal-check`): `refusal` in `job.py` refuses a rollback or a flag-off unless the investigation's results so far show the change and a metric window that matched a series reaches back to it. It answers `ProposalRefused`, which is not a `ToolInputError`, and stores nothing. `test_proposal_check.py` covers it on real tool results from dev scenarios, and `test_ai_loop_int.py` through the scripted job loop. Reports count refusals beside the forbidden-proposal gate, and the run cache keys on `PROPOSAL_CHECK`. Its dev pass passed on 2026-10-21: no forbidden proposal, and one refusal, bad-config-1's rollback of checkout to a deploy over 360 minutes old, which is not an acceptable action.
  - Proposal check version 2 (2026-10-25, branch `proposal-check-v2`; `PROPOSAL_CHECK = 2`): `refusal` also judges `restart_service` and `scale_service`, from the `query_metrics` series of the target in the results so far (`_restart_refusal`). A target with a `db_connections`, `db_lock_wait_seconds` or `db_slow_queries_per_second` series is a database and always refused. Any other target needs its own `memory_usage_ratio` or `cpu_usage_ratio`, or its `node_cpu_usage_ratio`, at `RESOURCE_LIMIT` (0.9, from the scenario generator's shared code, never tuned on a test-split run) or its `container_restarts_total` rising. Tests on dev scenarios only cover every restart and scale a dev scenario accepts (stored) or forbids (refused), db-pool-exhaustion-3's database at 0.93 CPU (refused as a database), memory-leak-1's 0.81 blip on catalog (refused) and the dev cache's 3 stored ones (still stored). The prompt is unchanged. Its two dev passes ran on 2026-11-01 with no forbidden proposal: Claude first at the 350,000 cap stored 4 acceptable proposals and refused 1, db-pool-exhaustion-1's restart of orders, which its labels forbid; Gemini at the defaults stored 2 acceptable ones and refused none (docs/activity-log.md).
  - Token cap and prompt limit (2026-10-21, branch `haiku-token-cap`): 30 seeded runs of a model that fills every input bound and output allowance, one token per byte, stay within the cap at 150,000 and at 350,000, with no prompt over 100,000 tokens; at 350,000 at least one run passes 150,000 and the stop notes name 350,000. With a cap that never binds, the prompt limit stops the run in time for the model's forced report to fit. The eval's cache key, report name, second-configuration note, `SET_FLOORS` refusal, cap guard and forced-report count have their own tests.
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
- Story A (online partitioning of `investigation_steps`): expand, dual-write flag, resumable backfill, verified swap, contract, and the partition job. A test runs the whole sequence on 2,000,000 seeded steps while a writer records steps: zero errors, writes in every phase, slowest write under 70 ms locally (0.552 s in CI on 2026-10-18; the test allows 3 s). Two guard tests confirm that the swap refuses before the backfill finishes, and the drop refuses while the old table holds a row the new one lacks.
- Story B (event versioning): replaying stored v1 steps returns v2 objects; a golden snapshot pins the result, and stored rows are never rewritten.

## Real Incidents (planned 2026-10-24; P1.1 to P1.11 built 2026-10-25, P2.1 to P2.4 on 2026-10-26)

The plan was approved by the user on 2026-10-24. Its steps are in docs/todo.md (P0, then P1.1 to P4.3), and its decisions in docs/memory.md. The flow:
- Claude Haiku 5.5 triages every alert: real or not, and the likely cause.
- For a real incident only, Claude Opus 5.5 at effort medium plans the fix.
- The user approves or rejects every plan.
- An approved plan either runs a fixed `restart_service` on an app or worker container, or has Claude Sonnet 5.5 at effort medium draft a fix on a local branch. The branch is never pushed, merged or deployed.
- Phase 1 is diagnosis only.

**The two apps today**, read-only on 2026-10-24:
- **Alerting:** neither app delivers alerts anywhere. FreightAudit (FA) has 4 Prometheus rules and FilingLens (FL) has 14, all on their dev stacks, with no Alertmanager.
- **FA production** (`freightaudit-prod`) runs now. It has OpenTelemetry off, so no metrics. Its only alert signals are JSON log lines with an `alert` field (`extract_paused`, `claude_switched_off`).
- **FL production** (`filinglens-prod`) had not been started then. Correction (2026-11-02): it runs now (the user). Its compose file, read read-only on 2026-11-02, puts the api behind its own Funnel node (`https://filinglens.<tailnet>.ts.net`), and the api and the sidecar on internal networks behind an egress proxy like FA's. Alertmanager sends its Prometheus rules to Discord and email, so the Alerting line above no longer holds for FL. It publishes Grafana's port alone, on 127.0.0.1:3111, an exception to the Exposure line below.
- **Exposure:** both productions publish no host ports, and their apis are public only through Tailscale Funnel sidecars. Windows is not on the tailnet.

**Alert path: pull, no new port.** A new TC service, `watch`, is deterministic, makes no model calls, and listens only for its own probes on 127.0.0.1. It polls:
- each api's health (FA production through its public Funnel URL);
- `docker ps` state;
- `docker logs` for alert fields and error bursts;
- and, for a dev stack that opted in, the app's own Prometheus `/api/v1/alerts`.

It posts Alertmanager v4 alerts to TC's own webhook, with one tenant token per app, so intake, dedupe, grouping and the flood caps are reused.
- Rejected: an Alertmanager in each app, which changes both repos, needs production monitoring and an egress allowlist; and Tailscale Serve, since everything runs on this PC.

**The snapshot.** On each firing transition, `watch` attaches a bounded, scrubbed snapshot (at most 48 KB) as annotations. It holds health results, container states with image and start times, log tails, changes (container recreations and commits on `origin/main`, never fetched), and PromQL results when Prometheus is up.
- Labels stay stable, so fingerprints and dedupe hold.
- The agent reads only this snapshot, through the gateway's internal alerts route. It gets no Docker, git or app network access.
- So injected log text cannot widen what is read, and recorded fixtures are plain data.

**Least privilege.** `watch` runs fixed argv with no shell:
- `docker ps` with names exact-matched to a TC-committed catalog and their compose project label checked;
- `docker logs` on catalog containers;
- `git log` and `git show --stat` on `origin/main`, with hooks, fsmonitor, textconv, ext-diff and signature checks off;
- and HTTP GET to configured URLs only, with no redirects.

It never runs `docker inspect` (the env holds keys), `exec` or `compose config`. It never reads `.env*`, databases, queues, object storage, invoice or filing contents.

**Secrets.** One scrubber in `watch`:
- keeps allowlisted JSON fields per app;
- masks key shapes, JWTs, bearer tokens, PEM blocks, URL credentials, query strings, emails and `*.ts.net` hostnames;
- keeps an exception's type and last line, and truncates fields.

The agent also scrubs provider error text. Canary tests cover `watch`, gateway, agent steps, the report mirror, TC logs and spans.

**Built in P1.4 to P1.6 (2026-10-25)** as `services/watch` (Important Components): the catalog, the scrubber, the hardened readers and the poller, tested on fakes only (a fake `docker` and `git` as all of PATH, loopback fake servers, a stub webhook). The canary chain so far runs from the fixtures through `watch` to the posted webhook body; the gateway, agent and log links come with P1.8. Not run against the real FA or FL yet.

**Built in P1.7 to P1.9 (2026-10-25)** (Important Components): ReportRecorded v2 with the verdict and the models per part, live triage on its own prompt, tools, Claude-first chain, dollar cap, token cap and budget, and the flood guard (30 investigations a tenant a UTC day with the reason recorded, `make digest`, watch's flap limit). `services/agent/tests/test_live_e2e_int.py` runs watch on in-process fakes fed by the fixture logs, the gateway process and the agent on a scripted model: the mirror ends with a v2 report with its verdict and models, and none of the 16 canaries is in any table of either schema, the gateway's output or the logs. Its storm test gives 30 triages of 40 failures, with every alert stored. The `live` switch stays off, and the live prompt has met no real model.

**Built in P1.10 and P1.11 (2026-10-25)** (Important Components): the report view (gateway API 0.6.0's detail route and the web's `/investigations` pages), the 30-day retention of live snapshots and steps in both schemas, and TC's $15 monthly Claude cap in the agent. Every live report stays readable after its steps are purged; its evidence then shows that no result is kept.

**Built in P2.1 to P2.4 (2026-10-26)** (Important Components): the planner's provider path and its daily cap, the plan and PlanRecorded v1, the check on real targets, and the plan's decision in the web, recorded once with nothing run. Tested on fake Anthropic streams and scripted plan chains only; the `planner` switch stays off until P2.5, the user's measuring run.

**Models.**
- Live triage runs Claude first until `LLM_CLAUDE_UNTIL` (2026-11-10), then the existing free chain (Gemini 3.5 Flash-Lite, then NIM DeepSeek v4.1 Flash; summaries on Gemini 3.1 Flash-Lite).
- The sim roles and eval cache keys stay unchanged.
- **Falls to the free chain, with a reason:** credits or spend limit (now account-wide), a 401 or 403, a refusal (no server-side refusal fallback), a spent dollar cap, or a 429 retry-after over 60 s.
  - Built in P1.1 (2026-10-25): the account-wide credits stop (`credits_exhausted` per provider), Anthropic's 429 without retry-after as a per-model stop (`model_exhausted`, agent 0010), `KeyRejected` (401/403, every provider) and `Refused` (Claude's refusal) moving a turn or a log summary down the chain, the reason on each skipped call (`spent()` returns it), Claude's summaries recorded as role `small`, and the prompt sha carried per call (`TurnRequest`, `ToolContext`). A key rejection does not stop the model for a day; the next turn asks it again. The dollar caps and the 60 s retry-after rule come with live triage (P1.8). Superseded on 2026-11-01 (branch `claude-first`): a bare 429 no longer stops a model, nothing waits out Claude's 429, and a rejected Anthropic key stops Claude until the agent restarts.
- **Planner and coder:** single streamed structured-output calls with no tools. Opus 5.5 and Sonnet 5.5 reject forced `tool_choice` and keep thinking on, so a single call needs no thinking replay.
- **ReportRecorded v2** adds `verdict` and `models[]` per part, with skip reasons.

**Budget.**
- TC gets $15 a month of the shared $50, evals included (FA $15, FL $15).
- Daily caps: triage $0.40, planner $1.00, coder $0.60. A call runs only if its worst-case cost fits, and it is then charged its real cost. Built so far: the triage cap (P1.8), the $15 monthly cap over every Claude call (P1.10 branch) and the planner's cap (P2.1); the coder's comes with P4.2.
- Triage cost measured in run 2: $0.0104 mean, $0.0202 max. Estimates: a plan about $0.20 (worst $0.48), a code draft about $0.13 (worst $0.25).
- Free models keep `DAILY_AI_TOKEN_BUDGET` and Gemini's RPD. Gemini's roughly 260 daily requests are TC's alone: FA, FL and TC each have their own Google project since 2026-10-31 (the user).

**Safety.**
- The approval gate exists.
- Plans are constrained by schema: an enum of kinds, catalog targets, allowlisted files, free text only in the rationale.
- Proposal check v2 applies to real targets: never a database, queue, cache or proxy; a restart only when the target's own health fails while its dependencies are healthy.
- Switches table: `live` (default off) and `claude` in phase 1; later `planner`, `actions` and `coder`.
- Flood caps: 30 triages a day per app, with a digest of the rest. Live data is kept 30 days.
- **Coder limits:**
  - a separate worktree under `H:\.cache\triage-copilot\worktrees\`, which leaves the user's checkout alone;
  - branch `tc/<app>/<id>`;
  - hooks and filters off, credential helpers off, no push path;
  - a per-app source allowlist, so CI workflows, `.env*`, keys, agent instructions, lockfiles, configs and infra are never editable;
  - at most 10 files and 400 lines;
  - it never runs tests or scripts.

**Quality after 2026-11-10,** when every role is free. On the sim, triage drops from Haiku's top-1 of 0.85 to Gemini's 0.60, and p95 rises from 43 s to 77 s. Plans and drafts are unmeasured.

**Open risk:** Docker Desktop containers on a routed network reach this PC's 127.0.0.1 ports through `host.docker.internal` (confirmed on 2026-11-01, docs/threat-notes.md). In FL production only Grafana, on `default`, has that route, and its ingest workers while they run; its api and Funnel sidecar sit on internal networks behind its egress proxy (FL's compose file, read 2026-11-02). From there they reach every port this PC binds to 127.0.0.1: TC's dev services while they run, production's 4202 (its webhook needs a tenant token, which FL never holds) and watch's 4240. Since the egress lockdown (2026-11-01), TC's own production containers sit on internal networks, and the one on a routed network, the egress proxy, refuses every private destination; the go-live check confirms it from the agent (README, Deploy).

## Current Development Status

Steps 1 (scaffolding), 2 (contracts) and 3 (data) done on 2026-10-14, step 4 (workers) with its follow-up (parking, recovery sweeper), step 5 (tests) and step 6a (AI core) on 2026-10-15. A live run on bad-deploy found the right root cause and proposed the rollback in 6 model turns and 35,090 tokens.

Step 6b is done (2026-10-21, once the proposal check merged as `177839d`): the scenario set (frozen, manifest sha256 `b81c80f4ba442427...`), validator, admin isolation, harness and docs are built and tested. Prompt tuning on the dev split is finished (evals/README.md, Status; `evals/prompt-log.md`):

- Baseline `146b161991e14f7c`: dev top-1 0.53, action correctness 0.60 against a do-nothing baseline of 0.53, one forbidden proposal.
- Change 1, `2b91d4523fb028a6`: dev top-1 0.73, grounding 0.93, action correctness 0.73, 0.43 on scenarios with an acceptable action, no forbidden proposal.
- Change 2, `e0f86d4ad3b47b1c` (finished 2026-10-17): dev top-1 0.53, grounding 0.80, action correctness 0.60, 2 forbidden proposals.
- The prompt is frozen at change 1 (2026-10-17); a test pins its sha. The ci subset on it, from cached runs: top-1 0.62, action correctness 0.75 against a do-nothing baseline of 0.50, no forbidden proposal.
- Claude Haiku 5.5 is built in as the first fallback (off by default) and ran the dev split once on the frozen prompt (2026-10-17): top-1 0.80, action correctness 0.80, no forbidden proposal, validity 1.00, $0.0971 of credits for 15 investigations. No model-specific breakage; 11 of 15 runs reached the token reserve, since Haiku counts about 1.5 times Gemini's tokens per turn.
- Python unit tests: 402 passed; `make test-int` after the exam (2026-10-20): Jest 33 and pytest 41 passed; ruff and pyright clean. The dev database is at agent 0009.
- The test split on Gemini is complete: 75 of 75 runs from 2026-10-17 to 2026-10-20, four quota days (docs/todo.md, 6b). Day 1 stopped on the UTC token budget, days 2 and 3 at the agent's own limit of 260 Gemini requests (day 3 confirmed on 2026-10-20 from the parked run's result file), and day 4 ran the last 17 runs on 159 requests. `make eval SPLIT=test` exited 1 on the forbidden-proposal gate, and 13 of the 18 measured targets missed (Targets above). By family, top-1 ranges from 1.00 (db-pool-exhaustion, memory-leak) to 0.11 (noisy-neighbor), with dns-partition at 0.25 and bad-config at 0.33. The floors of `test-full-3` and `dev-ci-1` are set (2026-10-20).
- The one forbidden proposal (noisy-neighbor-4 run 3) rolled back payments' newest deploy, made about 20 hours before the alert, while another workload took the node's CPU. Nothing was approved or executed. The user approved a check in the agent's `_propose` (docs/activity-log.md, 2026-10-20).
- The Claude comparison ran on 2026-10-20 (17:07 to 18:51 UTC) and was recorded on 2026-10-21: the same test split on `claude-haiku-5-5` met 9 of the 18 measured targets, against Gemini's 5 (Targets above; README, Model comparison). It made no forbidden proposal, but it proposed in only 5 of 75 runs, against Gemini's 15.
- The proposal check is built on branch `proposal-check` (2026-10-21), with the prompt still frozen: `make test`, `make test-int`, lint and pyright pass. Every recorded dev proposal through it gives the same result as the 2026-10-20 replay. Its dev pass on Gemini (2026-10-21, report `evals/reports/2026-10-21-dev-full-1-2b91d452.md`) exited 0: no forbidden proposal, one refusal, none acceptable, and the branch merged on 2026-10-21 (`177839d`). Top-1 was 0.53 against the same prompt's 0.73 on 2026-10-16, inside the interval (0.27 to 0.80); the three runs that lost it made no proposal, so the check never ran in them.
- The token cap setting and the prompt limit are built on branch `haiku-token-cap` (2026-10-21): `make lint`, `make typecheck`, `make test` (Python 415 passed) and `make test-int` (Jest 33, pytest 44) pass. Runs at the default cap behave as before and keep their cache keys.
- Haiku's second configuration ran on 2026-10-21, 13:23 to 15:16 UTC, and was recorded on 2026-10-24 (README, Model comparison; docs/activity-log.md).
  - `make eval` exited 1 on 3 forbidden proposals, and on the median-token floor and the 150,000-token gate, both expected at 350,000.
  - The token cap forced no report, and the 15 tool calls forced 47 of 75.
  - Spend, inside the estimate: $0.7800, 1,060 calls and 11,718,364 tokens. Largest prompt 30,699 tokens.
  - The forbidden proposals are three restarts whose own rationale argued against them, which the proposal check then did not judge.
- Proposal check version 2, which judges restarts and scaling, is built on branch `proposal-check-v2` (2026-10-25), with the prompt still frozen. `make lint`, `make typecheck`, `make test` (Python 447 passed) and `make test-int` (Jest 33, pytest 45) pass. The dev cache's 4 restart and scale proposals replayed through it: 3 kept and 1 stopped before the check by unknown evidence ids, as under version 1; all 4 are acceptable. Its two dev passes ran on main on 2026-11-01 and met P0's done-when (docs/todo.md, P0): Claude first exited 1 on the 150,000-token gate only, as expected at its 350,000 cap; Gemini exited 0.
- Gateway, web, agent and sim are stopped (the user, 2026-10-24). The compose infra is down: none of this project's containers existed when the 2026-10-25 task began (what removed the 2026-10-24 ones is not confirmed), and that task's `make test-int` ran on infra it started, then `make infra-down` removed it. The volumes stay.
- `make lint` passes (2026-10-17): `.prettierignore` skips the frozen scenario YAMLs and the eval reports, which are generated, instead of reformatting them.
- Step 7 (CI/CD) is built locally and verified (2026-10-17): actionlint, the four image builds, lint, typecheck, unit tests, build, contracts and scenario checks, gitleaks. The GitHub steps are done (the user confirmed on 2026-10-18): PR #1 is `0779d77` on origin/main. `make test-int` runs locally again after the exam.

Next, in this order:

0. Go-live before 2026-11-10 (plan approved 2026-10-31; docs/todo.md, In Progress):
   - PR 1, Claude first everywhere, merged as #19 (`39f4549`).
   - PR 2, step 10, was built on branch `step-10-deploy` (2026-11-01). It holds the production compose project, the Funnel sidecar, the gateway's failure limit, trust proxy and production key checks, `watch` posting to 4202, and `next` 16.4.3. Correction (2026-11-01, later): it is committed as `207212b` (pull request #20) but not on main, which is still `39f4549`.
   - The egress lockdown (docs/threat-notes.md) was built on branch `egress-lockdown` on top of `207212b` (2026-11-01), not committed, with the cold-query timing test made robust.
   - Correction (2026-11-01, latest): main is `dc65236` (#21), which holds PR 2 and the egress lockdown together.
   - P0's two passes ran on 2026-11-01. Then: the user's go-live steps (README, Deploy) and P1.12 with its shadow run.
   - Production alerting and `make prod-env` are built on branch `prod-alerting` from main `e1656d8` (2026-11-01), not committed; every check passes (docs/activity-log.md). The rollout is the user's (README, Deploy, Alerting), after the egress checks.
   - 2026-11-02: main is `fd9566f` (#24, the egress proxy's token fix), after production alerting (#23, `be4c51a`). The user reports FA live on TC since 2026-11-02 (tenant freight-audit; its drill passed; Claude first) and FL production live. FL's go-live steps were prepared on 2026-11-02 (docs/todo.md, P1.12) and are not yet run.
1. P0: proposal check version 2 on the sim passed its two dev passes on 2026-11-01 (docs/todo.md). The user ships their reports and records.
2. Real incidents, phase 1 (diagnosis only), target live by about 2026-11-03 (Real Incidents above; docs/todo.md). P1.1 to P1.3 merged as #13 (`883497d`). P1.4 to P1.6 (`watch`) merged as #14 (`bca8e9f`). P1.7 to P1.9 (report v2, live triage, flood guard) merged as #15 (`26d7d6e`). P1.10 and P1.11 (the report view, retention) and TC's monthly Claude cap merged as #16 (`cb132e6`). P1.12, the go-live, is the user's. Phase 2's P2.1 to P2.4 (the planner, plans, the check on real targets, plan decisions) merged as #17 (`bebbb1e`; this line said `p2-planner` until 2026-10-28). Step 9, the web UI, is built on branch `step-9-web` (2026-10-27 and 2026-10-28), not committed. P2.5, the measuring run and turning `planner` on, is the user's. Run `make migrate` (agent 0013, gateway 0008) before the agent or gateway from that branch starts.
   - P1.2: `LIVE_TENANTS` maps a tenant to its app. `agent.switches` holds `live` (default off) and `claude` (default on), flipped with `make switch NAME=<name> STATE=on|off`. The job reads `live` before every model turn of a live tenant, which parks as `budget` while it is off, and the limiter reads `claude` before every Claude request. Live tenants get their own tools, none until P1.8, so they never reach the sim. They still get the sim's prompt until then, so `live` stays off.
   - P1.3: the gateway keeps each alert's annotations (gateway 0005, API 0.5.0). `get_alert_context` shows summary, description and runbook_url only for a tool set built with `annotations=True`. The sim's tools keep their old results, so eval runs stay comparable.
3. Whether to size the 15-call cap with another Haiku run (the user's call). A test split after version 2 would be a third attempt informed by test failures, not a held-out result.
4. `EVAL_ENABLED=true`, the user's switch; the `dev-ci-1` floors are set (docs/todo.md, step 7).
5. The manual quota-stop check on eval.yml, once `EVAL_ENABLED` is on, on a day Google has refused the project's requests.

## Known Limitations

- Web sign-in (step 9):
  - The sign-in limit lives in one server's memory, and Vercel may run several instances; step 10 adds a firewall rule on `POST /login`.
  - Sessions are stateless: signing out drops the cookie, but a copied cookie holds until it expires (8 hours). Rotating `SESSION_SECRET` signs everyone out.
  - Off Vercel every client shares one limiter bucket, since the server trusts no client address header.
- The demo's three live samples are hand-written and labeled illustrative; real live reports replace them after P1.12's shadow run (docs/todo.md).
- The list cannot tell "no plan yet" from "a plan without a proposal" (`plan_status` null), so after a real verdict it polls up to 6 minutes either way.
- On this PC, Windows Smart App Control blocks the venv's unsigned launchers (`.venv\Scripts\*.exe`), so Python tools run as `uv run python -m <tool>` (docs/memory.md, 2026-10-28).
- The counted test split (2026-10-20) failed: the forbidden-proposal gate broke and 13 of the 18 measured targets missed (Targets above). 25 scenarios run 3 times give wide 95% intervals.
- The proposal check (`refusal`) has known limits:
  - It cannot stop a revert of a harmless change made inside the metric window.
  - A change older than 360 minutes can never be proposed.
  - Version 1 did not check restarts and scaling, and Haiku's second configuration made 3 forbidden restarts through that gap (2026-10-24). Version 2 (2026-10-25) checks them from metric series alone. A database is known only by a `db_*` series in the results, so one whose CPU the agent read without any of them passes at 0.9. `cpu_throttled_ratio` does not count, and a scale's `replicas` is not checked.
  - A rollback or a flag-off that its own rationale argues against is still judged on evidence alone, and a stored proposal cannot be withdrawn.
  - The eval cannot tell whether the running agent has the check, since the prompt sha is the same with or without it (docs/memory.md, 2026-10-21).
- Claude Haiku counts about 1.5 times Gemini's tokens for the same prompt, and the 150,000-token cap counts cached input too, so most Haiku investigations end at the token reserve with a forced report (dev split, 2026-10-17; 63 of 75 on the test split, 2026-10-20), and a forced report cannot propose an action. At 350,000 (the second configuration, 2026-10-21), the token cap forced none of 75, and the 15-call cap forced 47 instead. Time to first hypothesis can only be measured by a proxy, the time to the report, until the agent records a hypothesis step (docs/todo.md).
- On a model switch the prompt limit judges the next prompt by its bytes, an upper bound, so a conversation past about 100 KB cannot move to another model; that turn ends in a forced or partial report. Evals pin one model and never switch (docs/memory.md).
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
  - Proposals reach the gateway's `proposed_actions` only inside ReportRecorded (`events.ts`) or from the seed: no consumer reads ActionProposed v1. (Corrected 2026-10-25: this line said nothing but the seed wrote them, which events.ts has disproved since step 6a; `GET /v1/investigations/{id}` is served since P1.10.)
  - The executor has no queue consumer and no effectors: ActionApproved messages wait in `actions.fifo`, and `executor.run` runs only in tests.
  - Executions stuck in claimed are only logged by the sweeper; alerting on them belongs to step 8.
  - The investigation list spans every tenant, since X-API-Key is not tied to one.
  - The agent serves `GET .../state` only; `POST .../cancel` is not built, and `cancel_requested` is always false.
  - The GraphQL `investigations` and `investigation` queries have no resolvers (step 9).
- `RootCauseClass` and `ActionKind` are initial enums; the scenario engine and eval steps may extend them. A new value in a response breaks older generated Python clients (pydantic enums reject unknown values), so ship the gateway and the clients together.
- Action `parameters` are free-form; per-kind parameter schemas are open (docs/todo.md).
- The gateway derives a tool call's `timeout` status from the agent's summary text (`failed: ...Timeout...:`), since StepRecorded v3 has no status field (a `ponytail:` note in `events.ts`).
- The sim serves one active scenario per process, so evals run scenarios one after another.
- The gateway keeps an alert's annotations only from its first post (gateway 0005, P1.3), and `get_alert_context` shows them only to live tool sets, so the sim's alerts still show none.
- Live triage (P1.8): the live prompt has run on no real model yet, and up to 5 concurrent live turns can each pass the triage cap's check, so a day can go over by about $0.07 (docs/todo.md). The monthly cap's check has the same gap, by the same 5 worst cases at most. The monthly cap is in code since the P1.10 branch.
- Plans (P2): the `planner` switch is off, and no plan has met a real model. The code-fix source allowlist is TC's record of each repo's layout, unchecked against the repos until P4.1. Until login the approver is the name typed into the form, and a decision runs nothing. GraphQL has no plans (docs/todo.md, Plan follow-ups).
- The report pages (P1.10) have no login until step 9 and listen on 127.0.0.1 only. Their list spans every tenant, eval runs included. GraphQL's `Investigation` lags REST 0.6.0 by `Hypothesis.service`, `missingEvidence` and `partial` (docs/todo.md).
- `watch` keeps its firing alerts in memory: a restart posts a still-bad one again with a new start, and the old one never resolves in the gateway. Without `docker inspect` an out-of-memory kill shows only as exit 137 (docs/todo.md, watch follow-ups).
- The rate limiter's minute window lives in the agent process, and its day count sees only recorded calls; several agents sharing one key can pass a limit by a few requests.
- Agent outbox rows parked after 10 failed sends are not replayed by `make dlq-replay` yet.
- Older `final_report` rows (seed, step 4 placeholders) keep the previous report shape.
- Agent tables record a tenant when an investigation starts (since P1.11; before, only when it parked), from InvestigationRequested v2. Retention cannot tell an older live investigation that never parked; none exists, since `live` has never been on.
- Parked investigations resume up to `SWEEPER_INTERVAL_SECONDS` after their slot or budget frees, since the sweeper polls. One re-queued that loses the freed slot to a new arrival parks again at the back of the line.
- The sweeper asks the agent once per queued group per sweep (a `ponytail:` note): fine for hundreds; batch it past a few thousand.
- Only investigations the gateway requested (with an alert group) are re-queued; `--large` seed rows and hand-sent test messages are not.
- The sweeper copies only finished statuses onto groups; `running` and `failed` stay `queued` in the gateway until step 9 brings the status flow.
- Rate limits count in memory, so they hold for one gateway instance only. Requests without a credential are counted per client address, which is the Funnel sidecar's unless `GATEWAY_TRUST_PROXY` names it (README, Deploy, check 5). The sidecar's address on `edge` is fixed (10.231.233.18) since the egress lockdown, so the setting holds when the network is re-created. Requests on watch's port count as the egress proxy's address.
- Failed credential checks (step 10) are counted in memory too, in fixed one-minute windows, so up to twice `AUTH_FAILURES_PER_MINUTE` can pass across a minute's edge. Behind Funnel without `GATEWAY_TRUST_PROXY`, strangers share one count; a key that verifies is never held back.
- Production's egress lockdown (2026-11-01, docs/threat-notes.md) is built and tested on throwaway networks, but has not run in production: Tailscale through the proxy and the model calls through it are first seen at go-live (README, Deploy). What it leaves open is in docs/threat-notes.md: the sidecar still reaches the gateway's internal listener (4201) on `edge`, behind its tokens; any process on this PC reaches watch's port, as before; and the proxy itself is the one container with a route out.
- A duplicate delivery of a running investigation ("busy") still returns to SQS and uses up receives; one that outlasts about 5 backoffs reaches the DLQ.
- A group opened without an investigation (cap reached, or only resolved alerts) stays without one for its 5-minute window, even if firing alerts join it.
- The storm test sends its 500 posts within seconds rather than over 2 minutes, which is harsher on latency.
- `STEPS_DUAL_WRITE` and the dual-write code stay for the partitioning test; past revision 0004 the flag must stay false.
- `make seed SEED_ARGS=--large` adds agent rows only: its extra investigations have no gateway alert groups.
- Embeddings are `vector(768)`; another dimension needs a new column and a re-embed.
- Apps run natively in dev; production runs the CI-built images in its own compose project (steps 7 and 10).
- Gateway subscriptions use in-process pub/sub, so they work with one gateway instance only.
- Toxiproxy runs without proxies; fault-injection tests will create them.
- ElasticMQ messages survive container restarts (H2 on the `elasticmq-data` volume); if that volume is lost, the recovery sweeper re-queues the lost investigations after `SWEEPER_STUCK_AFTER_MINUTES`.

## Planned Features

See `docs/todo.md` for the 10-step build plan (8 build steps, review UI, deploy).
