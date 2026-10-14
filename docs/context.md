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

TypeScript (Fastify, GraphQL Yoga, Next.js, Drizzle, Jest) and Python 3.12+ (uv, FastAPI, SQLAlchemy 2, Alembic, pydantic, boto3, pytest). Postgres + pgvector, AWS SQS (LocalStack in dev), OpenTelemetry, Docker, GitHub Actions.

LLM providers (switchable by env vars): Gemini Flash (free tier) primary, NVIDIA NIM fallback, Ollama for local runs. Deployment: UI on Vercel, backend on the PC via Tailscale Funnel.

Local ports (bound to 127.0.0.1; chosen so all three projects can run at once; compose project name `triagecopilot`). Planned; Prompt 1 must use these.

| Service | Host port |
| --- | --- |
| Postgres (pgvector) | 5434 |
| LocalStack (SQS) | 4566 |
| gateway / agent / executor / sim | 4200 / 4210 / 4220 / 4230 |
| web (Next.js) | 3200 |
| Grafana | 3201 |
| Jaeger UI | 16688 |
| Prometheus | 9092 |
| OTel Collector (gRPC / HTTP) | 4337 / 4338 |
| Toxiproxy API | 8476 (proxies in 15500-15599) |

## Architecture and System Design

Planned design (not yet implemented):

1. `apps/gateway` (TS): webhook intake, dedupe and grouping, approvals API, GraphQL with subscriptions.
2. SQS FIFO `investigations.fifo` with MessageGroupId per alert group; redrive to DLQ after 5 receives.
3. `services/agent` (Python): SQS consumer, agent loop, checkpoint after every step, visibility heartbeat.
4. `services/executor` (Python): runs approved, allowlisted actions only, with idempotency keys.
5. `sim/` (Python): metrics, paginated logs, and deploy history for scripted incidents.
6. `apps/web` (Next.js): live timeline, hypotheses, approve and reject.

## Important Components

Not built yet. Planned: gateway intake and grouping, SQS consumer with checkpoints, agent tools, executor safety checks, scenario engine, eval harness.

## Implemented Functionality

None yet.

## Current Development Status

Not started. Design complete. Next step: scaffolding.

## Known Limitations

All targets are design goals and have not been measured.

## Planned Features

See `docs/todo.md` for the 10-step build plan (8 build steps, review UI, deploy).
