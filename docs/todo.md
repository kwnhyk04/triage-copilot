# To-Do

Concise, verifiable tasks. Move items between sections as status changes. Trim old completed items when the list grows large; the activity log keeps the history.

Each step maps to a Claude Code prompt in the design doc. Done when the check after the colon passes.

## Pending

- [ ] (Waits for the user's decision) Proposal check version 2, on restarts and scaling (proposed 2026-10-24, docs/activity-log.md): Haiku's second configuration made 3 forbidden restarts, which version 1 does not judge. `refusal` would also judge `restart_service` and `scale_service` from the investigation's `query_metrics` results. A database target (one with a `db_connections`, `db_lock_wait_seconds` or `db_slow_queries_per_second` series) is refused. Any other is stored only when its own `memory_usage_ratio`, `cpu_usage_ratio` or `node_cpu_usage_ratio` reaches 0.9, or its `container_restarts_total` rises. `PROPOSAL_CHECK` becomes 2, and the prompt stays frozen. Steps:
  1. Tests first, on dev scenarios: `test_proposal_check.py` (db-pool-exhaustion-1 and -3, dns-partition-1, memory-leak-1, noisy-neighbor-1 and -3) and `test_ai_loop_int.py`.
  2. The code, and a replay of the dev cache's restart and scale proposals.
  3. One dev pass on Haiku at `INVESTIGATION_TOKEN_CAP=350000` (about $0.16) and one on Gemini at the defaults (free).
  - Done when both dev passes show no forbidden proposal and no refused proposal that the labels accept, with every refusal listed. Never the test split.
- [ ] (The user's call) Size the 15-call cap for Haiku from data: it forced 47 of the second configuration's 75 reports, so how many calls Haiku would use is not measured. Measuring it takes a run with a higher call cap, which breaks the design's 15-call gate by construction.
- [ ] Two advisories have no fix inside current constraints (docs/activity-log.md, 2026-10-24): pillow 12.3.0 (41, fix 13.0.0, blocked by fastembed `pillow<13`) and esbuild <=0.24.2 (GHSA-67mh-4wv8-2f99, via `drizzle-kit` 0.31.11 `@esbuild-kit/esm-loader`; only drizzle-kit 1.0 beta/rc drops it). Done when `pnpm audit` and `uv audit` list nothing, with no ignore entries.
- [ ] Move `next` and `eslint-config-next` from 16.4.2 to 16.4.3 once it is past pnpm's release-age gate (published 2026-10-23).
- [ ] Add `pnpm audit` and `uv audit` to CI (`.github/workflows/ci.yml` has only gitleaks and actionlint), so advisories fail in the pull request, not later.
- [ ] (Later, the user's call) Haiku with thinking. Every Claude turn forces its tool call, so Haiku 5.5 never thinks (0 thought tokens on all 864 calls of run 1), while Gemini thinks at LOW. Turning it on needs: `tool_choice` `auto` on investigation turns; a path for a reply without a tool call (a nudge text would change the prompt sha); thinking blocks stored in `tool_calls.provider_state` and replayed unchanged, with a test that each rebuilt request matches the previous one byte for byte (Haiku 5.5 answers 400 when a replayed block follows an edited turn); a larger turn output cap, so a larger token cap; explicit `thinking` and `effort`; a cache key and report label for it; then one Haiku dev pass (about $0.15) before any test split.
- [ ] (manual) Get free API keys: Google AI Studio (Gemini) and NVIDIA build. Put them in `.env`, never in the repo.
- [ ] (manual) pre-commit's cache, with its gitleaks build, is on C: (`PRE_COMMIT_HOME` unset), against the AGENTS.md disk rule. Set `PRE_COMMIT_HOME` to a folder under `H:\.cache` and run `make install` again.
- [ ] (manual, before step 10) Vercel and Tailscale accounts created.
- [ ] Contract the unused `queued_budget` status: once no investigation holds it, rebuild `agent.investigation_status` without it in a maintenance window (docs/migrations.md, 2026-10-15 parking entry).
- [ ] Parked investigations resume oldest first (`PARKED_AHEAD` in `services/agent/src/agent/job.py` orders by `waiting_since`, then id), but the design says overflow waits by severity. The agent never learns a severity: InvestigationRequested v2 has none, and only the gateway's `alert_groups.severity` holds it. Ordering by severity needs: InvestigationRequested v3 with `severity` (expand, migrate, contract), an `agent.investigations.severity` column recorded when an investigation parks (as `tenant_id` is), `PARKED_AHEAD` ordered by severity rank (critical, warning, info) and then `waiting_since`, and the gateway's sweeper asking about groups in that order. Done when a critical investigation parked after a warning one takes the next free slot.
- [ ] Executor consumer: read `actions.fifo` and call `executor.run.run_action` per message, deleting it once that returns. Settings `EXECUTOR_SERVICE_TOKEN`, `GATEWAY_INTERNAL_URL` and `EXECUTOR_DRY_RUN` (default true); one effector per kind, each with a timeout; `/readyz` checks the gateway's internal listener. Done when an approval made in the web app runs once in dry-run mode end to end.
- [ ] ActionProposed v1 is unused (proposals reach the gateway inside ReportRecorded v1): remove it in a contract step, or use it again if actions must become approvable before the report.
- [ ] (manual) Check the project's real per-model limits in AI Studio and set `LLM_*_RPM` and `LLM_*_RPD` (docs/memory.md holds the unverified figures).
- [ ] (manual) uv's managed Python, which the venv runs on, lives on C: (`%APPDATA%\uv\python\cpython-3.12-windows-x86_64-none`), against the AGENTS.md disk rule. Set `UV_PYTHON_INSTALL_DIR` to a folder under `H:\.cache`, reinstall Python 3.12 there, recreate `.venv`, and record the path in docs/context.md.
- [ ] `make dlq-replay` does not re-arm agent outbox rows parked after 10 failed sends (`agent.outbox.failed_at`). Later rows of the same investigation are sent past a parked row, so the gateway can get a report before one of its steps; tool-call pairing copes, but the mirror lacks that step until the row is re-armed.
- [ ] Low findings from the 2026-10-15 review of the gateway's events consumer:
  - The recovery sweeper holds `FOR NO KEY UPDATE` on queued groups across agent HTTP calls. The consumer now skips such a row; the sweeper should lock each group only briefly.
  - The 30 s SQS client timeout also covers DeleteMessage and GetQueueUrl.
  - `events.stop()` runs after the sweeper and relay and has no deadline (writes are idempotent).
  - `/readyz` does not check that `agent-events.fifo` exists.
  - The mirror tables have no `tenant_id`; readers join `alert_groups`.
  - Replay drops the quarantine `error` attribute.
  - Add an alert on `agent-events-dlq.fifo` depth (step 8).
- [ ] Low findings from the 2026-10-15 review of the sim:
  - Log cursors are not bound to the query, window or world: reused with other parameters they return a silent partial page. Put a digest of scenario, seed, anchor, query and window in the cursor.
  - `GET /api/v1/deploys?service=<typo>` answers an empty page; answer 404 with the known services, as dependencies does.
  - Fidelity gaps: `direction` is case-sensitive, there is no POST form for query_range, and `/api/v1/labels`, `/api/v1/series` and the Loki label routes answer 404 outside the Prometheus envelope.
  - Activation with an anchor outside 1970 to 3000 answers 500 on Windows (timestamp formatting); bound `Activation.anchor`.
- [ ] Store Alertmanager annotations (`summary`, `description`, `runbook_url`) in the gateway with an expand-only migration (a nullable `alerts.annotations jsonb`, written at intake), and return them from `GET /internal/investigations/{id}/alerts` (a new optional field in `StoredAlert`, gateway API minor version) and from `get_alert_context`. Real alerts carry key evidence there; today only labels are stored. Done when an alert posted with annotations shows its summary, description and runbook URL in the tool's result.
- [ ] Before step 10: the gateway's internal listener (4201) binds only the internal Docker network in `docker-compose.prod.yml` and is never published; check that Tailscale Funnel exposes only the public port.
- [ ] Before step 10: check which client address the gateway sees behind Tailscale Funnel. If it is the proxy's, set Fastify `trustProxy`, or every request without a credential shares one rate-limit count. Then limit failed credential checks per address: today each invented key gets its own rate-limit count, unknown routes included, so key guessing is never throttled (independent review, 2026-10-15). Also refuse the `.env.example` placeholder keys at startup.
- [ ] Per-kind parameter schemas for ActionProposed and ActionApproved (for example `rollback_deploy` requires `to_version`): the executor rejects unknown or missing parameters.
- [ ] Emit an early hypothesis step, so time to first hypothesis is measured directly instead of by its proxy (webhook intake to the report, an upper bound) and step 9's timeline can show it. Today hypotheses exist only in the final report. A new step kind needs StepRecorded v4 (expand, migrate, contract) or a `hypothesis` step with the existing kind; the job records it the first time the model names a likely cause, and the eval harness reads its `recorded_at`. Done when the eval report measures time to first hypothesis from that step and the timeline shows it.
- [ ] Before step 10: the sim's admin port (`SIM_ADMIN_PORT`, 4231) is never published in `docker-compose.prod.yml`, and only the eval harness gets `SIM_ADMIN_URL`.
- [ ] 7. CI/CD: GitHub Actions for test, build, docker push; actionlint passes; test PR green
  - [x] Built locally (2026-10-17): `ci.yml`, `publish.yml`, `eval.yml`, the setup action, `scripts/ci_env.sh`, `infra/docker/` images and `.dockerignore`. actionlint passes; all four images build; lint, typecheck, unit tests, build, contracts and scenario checks pass; gitleaks finds nothing.
  - [x] The GitHub steps (now under Completed), then the test PR green on `ci-result` and the images in GHCR: done, the user confirmed on 2026-10-18 (PR #1 is `0779d77` on origin/main).
  - [x] `make test-int` locally after the exam (2026-10-20): Jest 7 suites and 33 tests, pytest 41 passed in 140.6 s. This project's three containers were started by `make infra-up` and removed by `make infra-down`; the volumes stay.
  - [ ] Set `EVAL_ENABLED=true` (the user's switch): both the Gemini split and the Haiku comparison are recorded (2026-10-21). The `dev-ci-1` floors are set by hand (2026-10-20, docs/memory.md): validity 0.92, median tool calls 12, median tokens 80,425, and no floor for the two p95 times, which CI only reports. Never rerun `make eval SUBSET=ci SET_FLOORS=1`: it would put the targets back. Optionally ask for a role's key only when the router can call it, which drops the NIM placeholder.
  - [ ] The manual quota-stop check, once `EVAL_ENABLED=true` (while it is off a manual run fails at once): on a day Google has refused the project's Gemini requests ("daily quota spent"), run eval.yml by hand; it must end red with "rerun after the quota resets". CI's database starts empty, so in CI only Google's refusal stops a run, never the agent's count of 260.
- [ ] 8. Instrumentation: OpenTelemetry traces span services end to end; dashboard and alerts committed; screenshots saved. Include an alert on executions stuck in claimed (the sweeper only logs them now) and a count of payload fingerprints that differ from the computed ones.
- [ ] 9. Review UI and demo mode: resolvers, login, backend-for-frontend, demo snapshot shown when the backend is offline; Playwright tests pass
- [ ] 10. Deploy: UI on Vercel, backend on the PC via Tailscale Funnel; phone test passes; 401 without API key; demo mode when the PC is off

## In Progress

None.

## Completed

- [x] Project created from template (2026-10-13).
- [x] 1. Scaffolding: monorepo layout, docker-compose infra healthy, every service answers /healthz (2026-10-14).
- [x] 2. Contracts: OpenAPI and GraphQL contracts, typed clients generated, contracts-check and breaking-change check pass (2026-10-14).
- [x] 3. Data: both migration chains apply from empty, the partition swap test passes on 2,000,000 steps while a writer loop runs with zero errors, v1 replay matches the golden snapshot, seed runs (2026-10-14).
- [x] Stored StepRecorded v2 events reach the queue as message v3, which carries them unchanged, so no mapping is needed: v3 contract, union, codegen and contract tests pass (2026-10-14).
- [x] (manual) `.prettierignore` lists `**/drizzle/meta/`; `pnpm prettier --check .` passes (2026-10-14).
- [x] (manual) `.prettierignore` skips the frozen scenario YAMLs (`evals/scenarios/**/*.yaml`) and the eval reports (`evals/reports/`) instead of reformatting them; `make lint` passes (2026-10-17).
- [x] v3 and the stored v2 model share the limits: a summary of 1 to 2,000 characters, and latency and token counts that are not negative. Each limit is tested in both languages (2026-10-14).
- [x] 4. Workers: queue job with retries and dead-letter handling, backpressure and crash-recovery tests pass (2026-10-15). InvestigationRequested v2 with `tenant_id`; per-tenant webhook tokens, fingerprint dedupe, 5-minute grouping, outbox relay, rate limit and input limits in the gateway; consumer, resumable job, tenant cap, budget guard, dead letters and replay in the agent. Done-when checks pass: (1) a stopped or killed agent resumes at the next step with no recorded tool call repeated; (2) a 500-alert storm over 8 groups queues 5 investigations with webhook p99 about 86 ms; (3) a message that fails 5 times lands in the DLQ, and a replay resumes it.

- [x] Step 4 follow-up, parking instead of holding (2026-10-15). A message that must wait for a tenant slot or the model budget marks its investigation waiting (`tenant_cap` or `budget`) and is deleted. The gateway's recovery sweeper re-queues, through the outbox, waiting investigations the agent reports resumable, and stuck ones: unknown to the agent, or no step for `SWEEPER_STUCK_AFTER_MINUTES`. The agent reports state at `GET /internal/investigations/{id}/state`, behind the service token. Done-when checks pass, with `make test` and `make test-int` green 3 runs in a row:
  - Two agents: a storm over the tenant cap leaves no DLQ entry, and every parked investigation runs.
  - A budget-parked investigation resumes after the budget resets.
  - Queue loss (purge after enqueueing): each stuck group is re-enqueued exactly once, and a second sweep re-enqueues nothing.
  - The internal API answers 401 without the service token.

- [x] 6a. AI core (2026-10-15): provider layer (Gemini primary, NIM fallback, small model, RPM and RPD limiter), tools, scenario engine with bad-deploy and db-pool-exhaustion, agent loop with a hard 150,000-token cap and a forced final report, ReportRecorded v1 delivery through the agent outbox into the gateway's mirror tables. `make test` (Turbo forced) and `make test-int` passed three runs in a row; the hard-cap, degraded-tool and pagination tests pass; a live run on bad-deploy found bad_deploy on checkout, proposed the rollback, and the gateway received the report.

- [x] 5. Tests (2026-10-15): unit tests in both languages run in about 12 s, and three consecutive `make test-int` runs are green. Scratch databases with guards; fingerprint port and fast-check properties; the real decision store with its outbox queue; X-API-Key and keyset `GET /v1/investigations`; agent caps with a partial report; executor safety checks with the gateway's claim route on a separate listener; agent 0001 `lock_timeout`. Degraded-tool and `query_logs` tests moved to step 6.

- [x] (manual) GitHub, in this order (step 7). Done by the user, confirmed on 2026-10-18: PR #1 is `0779d77` on origin/main.
  1. Create an empty public repo (no README, license or .gitignore).
  2. `git remote add origin https://github.com/<you>/<repo>.git`, then `git push -u origin main`. Run gitleaks over the full history right before; it was clean on 2026-10-17.
  3. Settings → Secrets and variables → Actions → Secrets: `GEMINI_API_KEY` only. Never `ANTHROPIC_API_KEY`, never a real NIM key.
  4. Variables: leave `EVAL_ENABLED` unset until the exam is done.
  5. Settings → Actions → General: workflow permissions read-only; Actions may not create or approve pull requests. Actions pinned to full-length SHAs and approval for outside contributors' pull requests are already set.
  6. Commit the step 7 work on a branch, push it, and open the test PR to main; it must be green.
  7. After its first green run: branch protection on main requiring the `ci-result` check. Then merge; `publish.yml` pushes the four images.
  8. Packages: set each `triage-copilot-*` package's visibility.

- [x] Partition test flake (2026-10-18): `phase_done` in `services/agent/tests/test_partitioning_int.py` polls up to 10 s for a write past the previous phase's count instead of sleeping 0.3 s. Merged on main as #3 (`26f70cf`); ship merges only after every check passes. The user confirmed on 2026-10-19.

- [x] 6b. Scenario set v1, eval harness and the full eval (2026-10-16 to 2026-10-21): scenario set v1 frozen (40 scenarios, 15 dev and 25 test) with its validator, the sim's isolated admin port, the harness (intervals, run cache, floors, reports), the prompt frozen at change 1 (`2b91d4523fb028a6`), the test split on Gemini (the exam, 2026-10-17 to 2026-10-20: exit 1 on one forbidden proposal; floors set) and on Claude Haiku (2026-10-20: exit 1 on a floor), and the proposal check, which passed its dev pass and merged on 2026-10-21 (`177839d`). Details in docs/activity-log.md and docs/context.md.
- [x] Claude Haiku, second configuration (2026-10-21 to 2026-10-24). The plan was approved on 2026-10-21, and the token cap setting merged as #8 (`22acfae`). The user ran the test split on `claude-haiku-5-5` with `INVESTIGATION_TOKEN_CAP=350000` on 2026-10-21 (13:23 to 15:16 UTC; report `evals/reports/2026-10-21-test-full-3-2b91d452-claude-haiku-5-5-cap350k.md`), and Claude Code recorded it on 2026-10-24.
  - Exit 1, on 3 forbidden restarts, the median-token floor and the 150,000-token gate.
  - Inside the estimate: $0.7800, 1,060 calls, 11,718,364 tokens, 1 h 52 min.
  - Largest prompt 30,699 tokens. The 15 calls forced 47 of 75 reports, the token cap none.
  - Results: README (Model comparison) and docs/activity-log.md. The run steps are in `git show fa2872a:docs/todo.md` (In Progress).
- [x] A parked investigation that hit the provider's "daily quota spent" stays parked until midnight Pacific, and `make eval` exits 2 on it (2026-10-16): `Limiter.spent()` reads the provider's `quota_exhausted` answers; `test_workers_int.py` and `test_eval_score.py` cover both.
- [x] `LLM_PRIMARY_RPD` defaults to 260, under the 275 requests measured on 2026-10-16 (2026-10-16). Still open: check the project's real limit in AI Studio.
- [x] Every model call records the agent's prompt sha (agent 0008), and the eval refuses a run whose calls name another prompt (2026-10-16).

## Blocked

None.
