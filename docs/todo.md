# To-Do

Concise, verifiable tasks. Move items between sections as status changes. Trim old completed items when the list grows large; the activity log keeps the history.

Each step maps to a Claude Code prompt in the design doc. Done when the check after the colon passes.

## Pending

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
  - [ ] After the exam: `make test-int` locally (skipped during the exam: its scratch databases and queues share the exam's Postgres, ElasticMQ and volumes; the test PR runs it); set `EVAL_ENABLED=true` once the Gemini split and the local Haiku comparison are done; optionally ask for a role's key only when the router can call it, which drops the NIM placeholder.
  - [ ] After the exam: a manual eval run on a spent quota must end red with the quota message.
- [ ] 8. Instrumentation: OpenTelemetry traces span services end to end; dashboard and alerts committed; screenshots saved. Include an alert on executions stuck in claimed (the sweeper only logs them now) and a count of payload fingerprints that differ from the computed ones.
- [ ] 9. Review UI and demo mode: resolvers, login, backend-for-frontend, demo snapshot shown when the backend is offline; Playwright tests pass
- [ ] 10. Deploy: UI on Vercel, backend on the PC via Tailscale Funnel; phone test passes; 401 without API key; demo mode when the PC is off

## In Progress

- [ ] 6b. Scenario set v1, eval harness and the full eval (2026-10-16). Done when the full test split runs 3 times end to end (resuming across quota days if needed) and the report lists every target with pass or fail; the ci subset runs; unit and integration tests stay green.
  - [x] Agent: a run resumed after a quota park keeps the wall-clock time it had left.
  - [x] Sim: admin routes on their own port (4231); tests prove no agent tool reaches them.
  - [x] Validator (`python -m sim.validate`, `make scenarios-check`, a test over every set) and the db-pool-exhaustion timeline fix.
  - [x] Scenario set v1: 40 YAML scenarios from `evals/scenarios/v1/generate.py`, sha256 manifest, 15 dev and 25 test, 6 with a backend down; every required signal shows in the tools' results.
  - [x] Harness: `make eval`, deterministic scores with 95% bootstrap intervals per family and overall, run cache and resume, floors, reports, README table.
  - [x] Deploy timing in v1: a harmless deploy 2 to 6 minutes before the alert in 16 scenarios (2 per family, 8 dev and 8 test), the newest deploy in each, its rollback forbidden and named as a red herring; causal deploys 4.5 to 35 minutes before the alert, two of them canaries.
  - [x] Action scoring: the do-nothing baseline beside action correctness, and action correctness on the scenarios with an acceptable action, in the reports and the README table.
  - [x] Regenerate and validate v1, rerun the tests, freeze v1 with its manifest (a test pins its sha256).
  - [x] Config timing in v1: a harmless config or flag change 2 to 6 minutes before the alert in 16 scenarios (2 per family, 8 dev and 8 test, 4 in the ci subset), the newest change in each, named as a red herring, turning off a harmless flag forbidden; causal changes 4.5 to 30 minutes before the alert. v1 unfrozen, regenerated, validated, tested and frozen again.
  - [x] ci subset (`make eval SUBSET=ci`), 2026-10-16: all 8 runs counted; exit 1 on one forbidden proposal (noisy-neighbor-1 turned off a harmless flag). Report `evals/reports/2026-10-16-dev-ci-1-146b1619.md`.
  - [x] Dev baseline and prompt change 1 (2026-10-16): dev top-1 0.53 to 0.73, forbidden proposals 1 to 0 (evals/prompt-log.md, evals/README.md).
  - [x] Change 2's dev pass (2026-10-17) and the choice: change 2 lost (top-1 0.53 against 0.73, 2 forbidden proposals against none), so the prompt is frozen at change 1, `2b91d4523fb028a6`; `test_the_prompt_is_frozen` pins it. No further prompt changes.
  - [x] Claude Haiku 5.5 as the first fallback (2026-10-17): `LLM_CLAUDE_FALLBACK` (off by default), credits-gone stop until the month ends, no call from 2026-11-10, cost in credits, agent 0009; unit and integration tests for each case.
  - [x] The dev split once on Haiku, frozen prompt, no tuning (2026-10-17): no model-specific breakage; top-1 0.80, 0 forbidden, $0.0971 of credits; 11 of 15 runs reached the token reserve. The ci subset scored on the frozen prompt from cached runs.
  - [ ] The test split 3 times on Gemini; set the floors. Started 2026-10-17: 9 of 75 runs done, then exit 2 when the day's token budget ran out (3,050,302 of 3,000,000 tokens, UTC day; Gemini at 230 of its 260 requests). Day 2 (2026-10-18): 32 of 75 done (every first run and 7 second runs), then exit 2 at the agent's own limit of 260 Gemini requests, all answered (1,803,303 tokens, UTC day).
    Next resume, day 3: after 15:00 Taipei on 2026-10-19 (00:00 PDT, 07:00 UTC), when both the Gemini quota and the UTC token budget have reset. Parked: db-pool-exhaustion-4 run 2 (16 steps), state file `evals/reports/runs/db-pool-exhaustion-4-run2-b8fdb20f568b7e4d.state.json`. Left: 43 runs, about 470 requests (11 per run on day 2), about 2 quota days. Run these from the repo root, in this order:
    1. `make infra-up`
    2. Terminal 1: `make dev-sim`. Wait until `curl.exe -s http://127.0.0.1:4231/admin/state` answers.
    3. Load the parked run's world before the agent starts, since the gateway's sweeper re-queues it within 15 s: `uv run python -c "import glob, json, sys; sys.path.insert(0, 'evals/harness'); import run; [run.resume(json.load(open(p))) for p in glob.glob('evals/reports/runs/*.state.json')]"`. Then `curl.exe -s http://127.0.0.1:4231/admin/state` must name the parked scenario (db-pool-exhaustion-4 on day 3).
    4. Terminal 2: `make dev-ts`. Wait until `curl.exe -s http://127.0.0.1:4200/readyz` answers.
    5. Terminal 3: `make dev-agent AGENT_RELOAD= AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false DAILY_AI_TOKEN_BUDGET=3000000`
    6. Terminal 4: `make eval SPLIT=test AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false`. It resumes the parked run first, then runs about 22 more before Gemini's 260 requests run out (day 2: 23 runs in 40 minutes and 1.8 million tokens, under the budget).
    7. On exit 2, stop the servers by process tree, agent first:
       - List the make PIDs: `Get-CimInstance Win32_Process -Filter "Name='make.exe'" | Select-Object ProcessId, CommandLine`.
       - Put each PID in `$make` as a quoted string and run the block below in PowerShell. It stops a tree only when the PID is a `make.exe` whose command line names that target, and prints "not stopped" for anything else; then list the PIDs again and correct that one. Quote the keys: an int index on an `[ordered]` table picks by position, so the lookup misses and the block stops nothing (docs/memory.md, 2026-10-18).

         ```powershell
         $make = [ordered]@{ '<agent PID>' = 'dev-agent'; '<dev-ts PID>' = 'dev-ts'; '<sim PID>' = 'dev-sim' }
         foreach ($id in $make.Keys) {
           $want = $make[$id]
           $p = Get-CimInstance Win32_Process -Filter "ProcessId=$id"
           if ($want -and $p -and "$($p.ProcessId)" -eq $id -and $p.Name -eq 'make.exe' -and ($p.CommandLine -split '\s+') -contains $want) {
             taskkill /PID $id /T /F
           } else { "PID $id is not make $want; not stopped" }
         }
         ```

       - Check that ports 4200, 4210 and 4230 are free, then `make infra-down`. Repeat from step 1 after the next midnight Pacific.
    8. On exit 0 or 1 (all 75 runs done; report `evals/reports/<date>.md` and the README table): `make eval SPLIT=test SET_FLOORS=1 AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false`, then `make eval SUBSET=ci SET_FLOORS=1 AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false`. Both rescore cached runs and make no model calls.
  - [ ] The Claude comparison: the test split pinned to `claude-haiku-5-5` (evals/README.md, Running), before the credits expire on 2026-11-10. Run it only once the Gemini test split is complete: a Haiku agent would resume a parked Gemini run, and that run would not count. Give its agent `DAILY_AI_TOKEN_BUDGET=10000000` (about 8 million tokens).
- [x] A parked investigation that hit the provider's "daily quota spent" stays parked until midnight Pacific, and `make eval` exits 2 on it (2026-10-16): `Limiter.spent()` reads the provider's `quota_exhausted` answers; `test_workers_int.py` and `test_eval_score.py` cover both.
- [x] `LLM_PRIMARY_RPD` defaults to 260, under the 275 requests measured on 2026-10-16 (2026-10-16). Still open: check the project's real limit in AI Studio.
- [x] Every model call records the agent's prompt sha (agent 0008), and the eval refuses a run whose calls name another prompt (2026-10-16).

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

## Blocked

None.
