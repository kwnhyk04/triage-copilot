# Triage Copilot

<!-- eval:start -->
## Eval results

**Result: failed.** The hard gate on forbidden action proposals broke: noisy-neighbor-4 run 3 proposed rolling back payments, which its scenario forbids, so `make eval SPLIT=test` exited 1. 13 of the 18 measured targets missed; 5 passed (both hard caps, investigation time and both costs).

Test split of scenario set v1, 25 scenarios x 3 runs, from `make eval SPLIT=test RUNS=3`; full report in `evals/reports/2026-10-20.md`, with every metric per failure family.

| Metric | Target | Measured | 95% CI | Result | Date | Models |
| --- | --- | --- | --- | --- | --- | --- |
| Root cause top-1 | 0.75 | 0.60 | 0.44 to 0.75 | fail | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Root cause top-3 | 0.90 | 0.60 | 0.44 to 0.75 | fail | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Consistency (correct in all 3 runs) | 0.65 | 0.44 | 0.24 to 0.64 | fail | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Evidence grounding | 0.95 | 0.76 | 0.60 to 0.89 | fail | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Action correctness | 0.80 | 0.71 (do-nothing baseline 0.52) | 0.55 to 0.85 | fail | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Action correctness, scenarios with an acceptable action | 0.80 | 0.39 | 0.19 to 0.60 | fail | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Forbidden action proposals | 0 (hard gate) | **1** (noisy-neighbor-4 run 3) | 0 to 3 | **fail: hard gate, eval exit 1** | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Tool-call validity | 0.99 | 0.98 | 0.97 to 0.99 | fail | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Degraded-tool scenarios: top-1 correct | 0.60 | 0.42 | 0.00 to 0.83 | fail | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Degraded-tool scenarios: down tool in missing evidence | 1.00 | 0.75 | 0.00 to 1.00 | fail | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Tool calls per investigation (median) | 8 | 10 | 7 to 11 | fail | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Tool calls per investigation (max, hard cap) | 15 (hard cap) | 15 | 15 to 15 | pass | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Time to first hypothesis (p95; proxy: time to the report) | 45 s | 77 s | 69 s to 98 s | fail | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Investigation time (p95) | 180 s | 77 s | 69 s to 98 s | pass | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Tokens per investigation (median) | 60,000 | 64,998 | 47,629 to 81,584 | fail | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Tokens per investigation (max, hard cap) | 150,000 (hard cap) | 116,986 | 115,669 to 116,986 | pass | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Cost per investigation, paid-equivalent (mean) | $0.2500 | $0.0148 | $0.0131 to $0.0164 | pass | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Cost per investigation, as billed (mean) | $0.0000 (hard gate) | $0.0000 | $0.0000 to $0.0000 | pass | 2026-10-20 | gemini-3.5-flash-lite (agent), gemini-3.1-flash-lite (log summaries), no fallback |
| Unapproved executions | 0 (hard gate) | 0 in every eval run; the gate is proven by tests | n/a | pass (tests) | 2026-10-20 | n/a |
| Webhook ack during a 500-alert storm | p99 200 ms, 0 dropped, 5 or fewer investigations | p99 86 ms, 0 dropped, 5 investigations | n/a | pass (tests) | 2026-10-15 | n/a |

What the numbers show, and what they do not: the incidents are simulated, and the same project wrote the scenarios, the tools and the labels. Prompts were tuned on the dev split only (evals/prompt-log.md), never on the test scenarios. 25 scenarios run 3 times is a small sample: the 95% intervals, which resample scenarios, are wide, and per-family figures rest on 2 to 4 scenarios each. Costs are what the paid tier would charge for the same tokens; the free tier billed nothing.

Time to first hypothesis has no step of its own yet: hypotheses exist only in the final report, so it is measured as the time from webhook intake to the report, an upper bound that equals the investigation time (docs/todo.md has the item that adds an early hypothesis step).
<!-- eval:end -->

Investigates production alerts with read-only tools (metrics, logs, deploys, runbooks), returns ranked root-cause hypotheses with cited evidence, and proposes fixes that only run after human approval.

## Exam history

Kept by hand: `make eval` rewrites only the results between the eval markers above, never this section.

### 2026-10-20: failed

Test split of scenario set v1, 25 scenarios x 3 runs, on gemini-3.5-flash-lite (agent) and gemini-3.1-flash-lite (log summaries) with the frozen prompt `2b91d4523fb028a6`; report `evals/reports/2026-10-20.md`. The forbidden-proposal gate broke, so `make eval` exited 1: noisy-neighbor-4 run 3 proposed rolling back payments, which its scenario forbids. Nothing was approved or executed. 13 of the 18 measured targets missed.

| Metric | Target | Measured |
| --- | --- | --- |
| Forbidden action proposals | 0 (hard gate) | **1: fail** |
| Unapproved executions | 0 (hard gate) | 0 |
| Root cause top-1 | 0.75 | 0.60 |
| Evidence grounding | 0.95 | 0.76 |
| Action correctness (do-nothing baseline) | 0.80 | 0.71 (0.52) |
| Tool-call validity | 0.99 | 0.98 |
| Tool calls per investigation, median / max | 8 / 15 | 10 / 15 |
| Tokens per investigation, median / max | 60,000 / 150,000 | 64,998 / 116,986 |
| Investigation time, p95 | 180 s | 77 s |
| Cost per investigation, paid-equivalent / billed | $0.25 / $0 | $0.0148 / $0 |

## Model comparison: Gemini 3.5 Flash-Lite and Claude Haiku 5.5

Kept by hand. The same test split (25 scenarios x 3 runs), agent, tools and frozen prompt `2b91d4523fb028a6`, with one model for the agent and its log summaries: gemini-3.5-flash-lite and gemini-3.1-flash-lite on Google's free tier (the exam above, 2026-10-17 to 2026-10-20, report `evals/reports/2026-10-20.md`), and claude-haiku-5-5 for both on Anthropic API credits (2026-10-20, 17:07 to 18:51 UTC, report `evals/reports/2026-10-21-test-full-3-2b91d452-claude-haiku-5-5.md`). Neither was tuned on the test split. 95% intervals in brackets.

| Metric | Target | Gemini 3.5 Flash-Lite | Claude Haiku 5.5 | Better |
| --- | --- | --- | --- | --- |
| Root cause top-1 | 0.75 | 0.60 (0.44 to 0.75): fail | 0.75 (0.63 to 0.85): fail, 0.747 | Haiku |
| Root cause top-3 | 0.90 | 0.60 (0.44 to 0.75): fail | 0.77 (0.65 to 0.88): fail | Haiku |
| Consistency (correct in all 3 runs) | 0.65 | 0.44 (0.24 to 0.64): fail | 0.48 (0.28 to 0.68): fail | Haiku, within noise |
| Evidence grounding | 0.95 | 0.76 (0.60 to 0.89): fail | 0.75 (0.60 to 0.88): fail | even |
| Action correctness (do-nothing baseline 0.52) | 0.80 | 0.71 (0.55 to 0.85): fail | 0.59 (0.40 to 0.76): fail | Gemini |
| Action correctness, scenarios with an acceptable action | 0.80 | 0.39 (0.19 to 0.60): fail | 0.14 (0.00 to 0.33): fail | Gemini |
| Forbidden action proposals | 0 (hard gate) | **1: fail** | 0: pass | Haiku |
| Tool-call validity | 0.99 | 0.98 (0.97 to 0.99): fail, 13 invalid of 703 | 1.00 (0.99 to 1.00): pass, 2 invalid of 788 | Haiku |
| Degraded-tool scenarios: top-1 correct | 0.60 | 0.42 (0.00 to 0.83): fail | 0.75 (0.33 to 1.00): pass | Haiku |
| Degraded-tool scenarios: down tool in missing evidence | 1.00 | 0.75 (0.00 to 1.00): fail | 1.00: pass | Haiku |
| Tool calls per investigation, median | 8 | 10 (7 to 11): fail | 10 (9 to 12): fail | even |
| Tool calls per investigation, max | 15 (hard cap) | 15: pass | 14: pass | both pass |
| Time to first hypothesis, p95 (proxy: time to the report) | 45 s | 77 s (69 s to 98 s): fail | 31 s (27 s to 33 s): pass | Haiku |
| Investigation time, p95 | 180 s | 77 s: pass | 31 s: pass | Haiku |
| Tokens per investigation, median | 60,000 | 64,998 (47,629 to 81,584): fail | 112,178 (108,563 to 114,178): fail | Gemini |
| Tokens per investigation, max | 150,000 (hard cap) | 116,986: pass | 125,936: pass | both pass |
| Cost per investigation, paid-equivalent (mean) | $0.25 | $0.0148: pass | $0.0066: pass | Haiku |
| Cost per investigation, as billed (mean) | $0 (hard gate, free setup only) | $0: pass | $0.0066 in credits: not gated | Gemini |
| Unapproved executions | 0 (hard gate) | 0: pass (tests) | 0: pass (tests) | same |
| Webhook ack during a 500-alert storm | p99 200 ms | p99 86 ms: pass (tests, no model) | same test | same |

| Totals | Gemini 3.5 Flash-Lite | Claude Haiku 5.5 |
| --- | --- | --- |
| Targets met, of 18 measured | 5 | 9, and the billed gate does not apply on credits |
| `make eval` exit | 1: the forbidden-proposal gate | 1, from the code and the report: action correctness on scenarios with an acceptable action, 0.14, under the `test-full-3` floor, 0.19, which Gemini's run set |
| Runs | 75 over four quota days | 75 in 1 h 44 min, no stop or retry |
| Model calls | 780: 778 answered, 2 Gemini 504s | 864: 863 agent turns, 1 log summary, all answered |
| Tokens | 5,103,763: 5,032,942 input (2,144,785 of it cached), 70,821 output | 8,165,311: 7,984,284 input (4,341,032 cache reads, 5,036 cache writes), 181,027 output |
| Billed | $0 on the free tier ($1.11 at paid rates) | $0.4984 of Anthropic API credits |
| Reports a cap forced | 20 of 75 | 63 of 75, all on the token budget |
| Runs with a proposal | 15: 14 acceptable, 1 forbidden | 5, all acceptable (4 rollbacks, 1 flag-off) |

Haiku was better at finding the cause (top-1 0.75 against 0.60, better in 6 of 8 families, worse in db-pool-exhaustion and memory-leak), with degraded tools, at valid tool calls, and in speed: the p95 time is 31 s against 77 s. Haiku was worse at acting. It proposed in only 5 runs, against 15 for Gemini. On scenarios with an acceptable action, it was right in 5 of 36 runs, against 14. In 28 of its 31 such runs without a proposal, the token budget forced the report, and a forced report cannot propose. It also used 1.6 times Gemini's tokens and spent credits, where Gemini billed nothing. It made no forbidden proposal and Gemini made one. With so few proposals, that says less about safety than about how rarely Haiku acted, so the check on proposals that revert a change (docs/todo.md) is built as approved, for any model.

## Layout

| Path | What | Port |
| --- | --- | --- |
| `apps/gateway` | Fastify + GraphQL Yoga (subscriptions over SSE) | 4200 |
| `apps/web` | Next.js UI and backend-for-frontend | 3200 |
| `services/agent` | SQS consumer + internal FastAPI | 4210 |
| `services/executor` | Runs approved, allowlisted actions only | 4220 |
| `sim/` | Scenario engine for scripted incidents; admin port for the eval harness only | 4230, 4231 |
| `contracts/` | OpenAPI, GraphQL and SQS message schemas | |
| `packages/ts-*`, `packages/py-*` | Shared config, telemetry and generated clients | |
| `infra/` | Docker Compose and service config | |
| `runbooks/`, `evals/` | Runbooks the agent searches; eval scenarios and reports | |

## Setup

Needs Node 24 (pnpm 12 via corepack), uv, Docker and GNU make.

```sh
cp .env.example .env
make install
make dev
```

Model calls (`AI_ENABLED=true`) run on Gemini's free tier, falling back to NVIDIA NIM. With `LLM_CLAUDE_FALLBACK=true` and `ANTHROPIC_API_KEY` in `.env`, Claude Haiku 5.5 comes between them, paid from Anthropic API credits until `LLM_CLAUDE_UNTIL`.

`make install` also installs the gitleaks pre-commit hook, which blocks commits that stage secrets.

CI (GitHub Actions): `ci.yml` runs lint, types, unit and integration tests, contract and scenario checks, a gitleaks scan of the full history and the image builds on every pull request and push to main; `ci-result` is the required check. `publish.yml` pushes the gateway, agent, executor and sim images to GHCR from main. `eval.yml` stays off until the repository variable `EVAL_ENABLED` is `true`; its only secret is `GEMINI_API_KEY`, and CI never calls Claude. `make dev` starts Postgres, ElasticMQ (SQS) and Toxiproxy, then all five apps. Set `COMPOSE_PROFILES=observability` in `.env` to add the OpenTelemetry Collector, Jaeger (16688), Prometheus (9092) and Grafana (3201), and uncomment `OTEL_EXPORTER_OTLP_ENDPOINT` to export traces.

## Usage

| Target | Does |
| --- | --- |
| `make dev` | Infra plus every app with reload |
| `make build` | Next.js build and Python wheels |
| `make test` | Jest and pytest unit tests; for repeated runs use `pnpm turbo run test --force`, since Turbo replays cached output |
| `make test-int` | Integration tests against the infra, about 3 minutes (the partitioning test seeds 2,000,000 steps). Each test gets a scratch database `triage_test_*`, dropped afterwards; tests never touch the dev database |
| `make lint` | ESLint, Prettier, ruff |
| `make typecheck` | tsc and pyright |
| `make codegen` | Regenerates clients from `contracts/` |
| `make contracts-check` | Regenerates clients; fails if any generated file changed |
| `make contracts-breaking` | oasdiff breaking-change check of both OpenAPI specs against `CONTRACTS_BASE` (default `HEAD`; needs Docker) |
| `make migrate` | Drizzle (gateway) and Alembic (agent) migrations; see `docs/migrations.md` |
| `make seed` | The same fake history every run in both schemas; `SEED_ARGS=--reset` replaces existing rows, `SEED_ARGS=--large` tops the agent up to 2,000,000 steps |
| `make eval` | The eval against the running stack (`make dev` with `AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false`, the agent started with `AGENT_RELOAD=` so no edit restarts it): `SPLIT=dev\|test`, `SUBSET=ci\|full`, `RUNS`; `ESTIMATE=1` prints the quota days, `SET_FLOORS=1` writes the floors. See `evals/README.md` |
| `make scenarios-check` | Validates every scenario set and fails when a released set's file changed against `SCENARIOS_BASE` (default `HEAD`) |
| `make embed-model` | Downloads the runbook embedding model (nomic-embed-text-v1.5, about 0.5 GB) into `FASTEMBED_CACHE_PATH`; the only command that downloads it |
| `make runbooks` | Embeds `runbooks/*.md` into `agent.runbook_chunks` for `search_runbooks` |
| `make smoke` | One scenario (`SCENARIO=bad-deploy-1`) against the running stack; needs `make dev` with `AI_ENABLED=true` and the provider keys in `.env`. Prints the report, each tool call, tokens and latency per model turn, and the gateway's copy; results go to `evals/reports/runs/` |
| `make dlq-replay` | Re-queues failed messages: parked gateway outbox rows, `agent-events-dlq.fifo`, `investigations-dlq.fifo`, and the agent's `dead_letters` |
| `pnpm --filter @triage/gateway tenant:token <name>` | Issues a tenant's Alertmanager webhook token (creates the tenant if new); prints it once, stores only its SHA-256 |
| `make infra-down` | Removes this project's containers; data volume stays |

Production runs the same compose file plus an override that unpublishes the database and queue ports and adds restart policies and memory limits:

```sh
docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml up -d
```

## Known gaps

Scenario set v1 (`evals/scenarios/v1/`, frozen on 2026-10-16) leaves these for v2:

- No scenario's answer is traffic_surge, and none's is unknown (evidence that supports no class).
- The rest of the list is in `evals/README.md`.

See `docs/context.md` for the design and `docs/todo.md` for the build plan.
