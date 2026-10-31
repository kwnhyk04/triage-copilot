# To-Do

Concise, verifiable tasks. Move items between sections as status changes. Trim old completed items when the list grows large; the activity log keeps the history.

Each step maps to a Claude Code prompt in the design doc. Done when the check after the colon passes.

## Pending

- [ ] P0, in progress (built 2026-10-25; the dev passes are next): proposal check version 2, on restarts and scaling, on the sim (approved 2026-10-24). It comes before P1.1 because its Haiku dev pass needs Claude credits before 2026-11-10. Haiku's second configuration made 3 forbidden restarts, which version 1 does not judge. `refusal` also judges `restart_service` and `scale_service` from the investigation's `query_metrics` results:
  - A database target (one with a `db_connections`, `db_lock_wait_seconds` or `db_slow_queries_per_second` series) is refused.
  - Any other target is stored only when its own `memory_usage_ratio`, `cpu_usage_ratio` or `node_cpu_usage_ratio` reaches 0.9, or its `container_restarts_total` rises.
  - `PROPOSAL_CHECK` becomes 2, and the prompt stays frozen.
  - Steps:
    1. Done (2026-10-25, branch `proposal-check-v2`; docs/activity-log.md): tests first, on dev scenarios only, in `test_proposal_check.py` (every restart and scale that a dev scenario accepts or forbids, db-pool-exhaustion-3's database at 0.93 CPU, memory-leak-1's 0.81 blip on catalog, and the dev cache's 3 stored ones) and `test_ai_loop_int.py`.
    2. Done (2026-10-25): the code, with the 0.9 in `RESOURCE_LIMIT` (`job.py`), taken from the scenario generator's shared code and never tuned on a test-split run. The replay of the dev cache's 4 restart and scale proposals: 3 kept, and 1 stopped before the check by unknown evidence ids, as under version 1; all 4 are acceptable. `make lint`, `make typecheck`, `make test` (Python 447 passed) and `make test-int` (Jest 33, pytest 45) pass.
    3. Next, by the user: the two dev passes below, from the repo root in PowerShell, in this order. Never `SPLIT=test` or `SET_FLOORS=1`.
       - Estimate, Haiku pass: about $0.16 of credits, counted in TC's $15 a month. 15 runs at run 2's $0.0104 a run make $0.156. The dev split's Haiku pass at the default cap ($0.0971 on 2026-10-17), times run 2's cost over run 1's (1.56), makes $0.152. About 2.3 million tokens, 210 model calls and 25 minutes.
       - Flag: that sits at the $0.16 line, not clearly under it. At run 2's p95 a run ($0.016) the pass would cost $0.24, and at most about $0.30 if every run cost as much as run 2's dearest ($0.020).
       - Estimate, Gemini pass: $0 billed (free tier), about 160 of the agent's 260 Gemini requests a Pacific day, 1.0 million tokens and 26 minutes, as version 1's dev pass used (2026-10-21: 158 requests, 1,044,203 tokens).
       1. Check:
          - `git branch --show-current` prints `proposal-check-v2`, or `main` once it merged, and `git grep -n "PROPOSAL_CHECK = 2" -- services/agent/src/agent/job.py` prints that line. The eval cannot see which check the running agent has (docs/memory.md, 2026-10-21).
          - No `*.state.json` in `evals/reports/runs/`, and `.env` sets `ANTHROPIC_API_KEY` and `GEMINI_API_KEY`.
          - Nothing listens on ports 4200, 4201, 4210, 4230, 4231, 3200, 5434, 4566 or 8476.
          - The Anthropic console shows at least $1 left of the month's $50 spend limit, which all three projects share.
          - From the `p1-10-reports` merge on, the agent also holds every Claude call to TC's own $15 a month (`LLM_CLAUDE_MONTHLY_USD`), counted from this month's calls in `agent.model_calls`: the pass needs about $0.30 of it left, or its runs park and the eval exits 2.
          - The date is before 2026-11-10 (UTC); from then on `LLM_CLAUDE_UNTIL` stops every Claude call.
       2. `make infra-up`, then `make migrate`: an agent from P1.1 on needs agent 0010, or its `spent()` query fails on the enum value `model_exhausted`.
       3. Terminal 1: `make dev-sim`. Wait until `curl.exe -s http://127.0.0.1:4231/admin/state` answers.
       4. Terminal 2: `make dev-ts`. Wait until `curl.exe -s http://127.0.0.1:4200/readyz` answers.
       5. Claude-first pass (from PR 1, branch `claude-first`, on; before it merges use `git show d425141:docs/todo.md` for the pinned-Haiku commands). Terminal 3: `make dev-agent AGENT_RELOAD= AI_ENABLED=true LLM_CLAUDE_FIRST=true LLM_FALLBACK_ON_RATE_LIMIT=false DAILY_AI_TOKEN_BUDGET=9000000 INVESTIGATION_TOKEN_CAP=350000`. Wait until `curl.exe -s http://127.0.0.1:4210/readyz` answers. Claude stops at the dev database's $0.40 for the UTC day; Gemini would then finish the pass, and the report's "Models that served" says so.
          - `DAILY_AI_TOKEN_BUDGET` counts every model's tokens per UTC day: 9,000,000 covers both passes at their caps (15 x 350,000 plus 15 x 150,000 is 7,500,000) after up to 1,500,000 used earlier that day.
          - Terminal 4: `make eval SPLIT=dev RUNS=1 ESTIMATE=1 AI_ENABLED=true LLM_CLAUDE_FIRST=true LLM_FALLBACK_ON_RATE_LIMIT=false INVESTIGATION_TOKEN_CAP=350000`. It must print "15 investigations planned, 15 to run". Then run the same command without `ESTIMATE=1`. It writes `evals/reports/<date>-dev-full-1-2b91d452-claude-haiku-5-5-first-cap350k.md`.
          - Exit 1 is likely, on the 150,000-token gate only, as in run 2: that gate is the design's whatever cap a second configuration runs with. A `broke: Forbidden action proposals` line fails P0.
          - Exit 2: the credits or the spend limit are used up. Stop the servers (step 8) and send Claude Code the output.
          - Exit 3 with "another token cap": the agent and the eval disagree on `INVESTIGATION_TOKEN_CAP`. Restart the agent as above and run the eval again.
       6. Stop the agent's process tree with the block in step 8, listing only its PID, and check that port 4210 is free.
       7. Free-only pass. Terminal 3: `make dev-agent AGENT_RELOAD= AI_ENABLED=true LLM_CLAUDE_FIRST=false LLM_FALLBACK_ON_RATE_LIMIT=false DAILY_AI_TOKEN_BUDGET=9000000`. Wait until `curl.exe -s http://127.0.0.1:4210/readyz` answers.
          - Terminal 4: `make eval SPLIT=dev RUNS=1 ESTIMATE=1 AI_ENABLED=true LLM_CLAUDE_FIRST=false LLM_FALLBACK_ON_RATE_LIMIT=false`. It must print "15 investigations planned, 15 to run" and fewer Gemini calls than the requests left today. Then run the same command without `ESTIMATE=1`. It writes `evals/reports/<date>-dev-full-1-2b91d452.md`.
          - Exit 0 is expected. A `broke: Forbidden action proposals` line fails P0.
          - Exit 2 is a Gemini quota stop: run the same command after midnight Pacific; it goes on from the parked run.
       8. Stop the servers by process tree, agent first:
          - List the make PIDs: `Get-CimInstance Win32_Process -Filter "Name='make.exe'" | Select-Object ProcessId, CommandLine`.
          - Put each PID in `$make` as a quoted string and run the block below. It stops a tree only when the PID is a `make.exe` whose command line names that target, and prints "not stopped" for anything else (docs/memory.md, 2026-10-18).

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

          - Check that ports 4200, 4210 and 4230 are free, then `make infra-down`.
       9. Send Claude Code both exit codes and each eval's last lines (the `broke:` lines, if any, and the `report:` line). Claude Code then lists every proposal and refusal from the result files and records the passes.
  - Done when both dev passes show no forbidden proposal and no refused proposal that the labels accept, with every refusal listed. Never the test split.
- [ ] Real incidents from FreightAudit (FA) and FilingLens (FL). Plan approved 2026-10-24; the design is in docs/context.md (Real incidents). One PR per step, tests first, and no live model call in any test.
  - Phase 1, diagnosis only, target live by about 2026-11-03. Each step has its done check after the colon.
    - [x] P1.1 Fixes (built 2026-10-25 on branch `p1-1-fallback-fixes`; docs/activity-log.md): Claude summaries recorded as role `small` (the `LAST_TURN` bound in `job.py`); a credit stop for 402, `billing_error` or a spend-limit 400 stops every Anthropic model for the month, while a 429 without retry-after stays per model (new outcome `model_exhausted`, agent 0010); 401/403 (`KeyRejected`) and refusals (`Refused`) fall to the free chain; `spent()` returns a reason; the prompt sha is recorded per call: each failure lands on the free chain with its reason, and the frozen sim prompt and eval per-role counts are unchanged. Run `make migrate` before starting an agent from this code.
    - [x] P1.2 (built 2026-10-25, same branch) Live tenants: settings, tenant-to-source mapping, and the `live` (default off) and `claude` switches with a CLI: a live tenant never reaches the sim tools and parks while `live` is off. Built: `LIVE_TENANTS` (JSON, tenant to `freight-audit` or `filing-lens`), `agent.switches` (agent 0011) with `make switch [NAME= STATE=]`, live tenants on their own tool set (empty until P1.8). Keep `live` off until P1.8: a live investigation still gets the sim's prompt.
    - [x] P1.3 (built 2026-10-25, same branch) The gateway stores alert annotations (expand-only migration, `StoredAlert.annotations`, `get_alert_context` shows them): annotations round-trip and old rows still read. Built: gateway 0005, gateway API 0.5.0, and `get_alert_context` showing summary, description and runbook_url only for tool sets built with `annotations=True` (the live one, P1.8), so the sim's results and eval runs stay as they were.
    - [x] P1.4 (built 2026-10-25 on branch `p1-4-watch`; docs/activity-log.md) Per-app catalog and scrubber, plus synthetic FA and FL fixtures built from their log field lists: no canary survives (keys, emails, `?key=` URLs, tracebacks, ts.net names). Built: `services/watch/src/watch/catalog.py` (services, kinds, dependencies, compose projects, kept log fields, log signals, Prometheus rules and queries, taken read-only from both apps' compose files, logging setup and rules) and `scrub.py`; 16 canaries in four fixture logs, and each mask, dropped alone, lets its canary through.
    - [x] P1.5 (built 2026-10-25, same branch) Hardened Docker (`ps`, `logs`), git (`log`, `show --stat` on `origin/main`) and HTTP readers: a fake `docker` and `git` on PATH, plus fake servers, show disallowed subcommands, names and URLs refused. Built: `readers.py`. Git runs one `log --name-only` instead of `log` plus `show --stat`: changed paths, never contents, in one call.
    - [x] P1.6 (built 2026-10-25, same branch) The `watch` service (detection, a snapshot of at most 48 KB in annotations, posting to the webhook, `/healthz`, `/readyz`, `make dev-watch`): fixtures end to end against a stub gateway post only transitions, with stable labels. Built: `poll.py` and `main.py`, port 4240, `WATCH_*` settings. Never run against the real FA or FL yet; that waits for the go-live test block (P1.12).
    - [x] P1.7 (built 2026-10-25 on branch `p1-7-live-triage`; docs/activity-log.md) ReportRecorded v2 (`verdict`, `models[]`), accepted beside v1, plus gateway mirror columns and GraphQL fields: the gateway accepts v2 before any agent emits it. Built: `report-recorded.v2.schema.json` (v1's `Report` by reference, a nullable `verdict`, `models` per part with calls, failed and the last reason), the union `report-recorded.schema.json` (v1 deprecated), gateway 0006 (`investigation_reports.verdict`, `models`), and GraphQL `Investigation.verdict` and `models`.
    - [x] P1.8 (built 2026-10-25, same branch; the live prompt is untested on any real model, and `live` stays off) Live triage: a live prompt with its own sha; tools `get_alert_context`, `read_snapshot`, catalog `get_dependencies`, `search_runbooks` and `submit_report` with a verdict, and no `propose_action`; a Claude-first chain; the triage dollar cap; `INVESTIGATION_TOKEN_CAP=350000` and `DAILY_AI_TOKEN_BUDGET=25000000` for the live agent: ScriptedModel from fixtures through `watch`, gateway and agent ends in a report v2 with its verdict and models, and the sim sha and eval cache keys are unchanged.
    - [x] P1.9 (built 2026-10-25, same branch) Flood guard (30 triages a day per app, a digest of the rest, `watch`'s own rate limit): a storm through `watch` gives at most 30 triages, and every alert is recorded. Built: `MAX_DAILY_INVESTIGATIONS` (30 per tenant a UTC day) in the gateway's intake, the reason on every uninvestigated group (gateway 0007: `resolved`, `burst`, `daily_cap`), `make digest [DAY=]`, and watch's flap limit (an alert that fired 3 times within an hour stays firing until the first of them is an hour old).
    - Before the first live run after this branch: `make migrate` (agent 0012, gateway 0006 and 0007).
    - [x] P1.10 (built 2026-10-25 on branch `p1-10-reports`; docs/activity-log.md) Serve `GET /v1/investigations/{id}` and a minimal read-only web list and detail (verdict, cause, evidence, models, fallback notes): the user can read every live report. Built: gateway API 0.6.0 (the detail route; `verdict` on every listed investigation; `models`, `missing_evidence`, `partial` and each hypothesis's `service` on the detail), `createGetInvestigation` in `investigations.ts`, and the web's `/investigations` and `/investigations/<id>` pages, which the web server fills from the gateway with its API key.
    - [x] P1.11 (built 2026-10-25, same branch) Retention: live snapshots and steps are purged after 30 days: nothing older remains. Built: the gateway strips the `snapshot` annotation and deletes mirrored steps (`retention.ts`), and the agent deletes steps, tool calls and unsent step messages (`agent/retention.py`), for `LIVE_TENANTS` only, once at start and then daily. Reports, verdicts, models and model calls stay. The agent now records every investigation's tenant when it starts, which retention needs.
    - [ ] P1.12 Go live, by the user, in this order: (1) move FA's Gemini key to its own Google project; (2) a TC Anthropic workspace with a $15 monthly limit, if the Console offers it; (3) issue the FA tenant token (FL's once FL production is live), and put that tenant in `LIVE_TENANTS` before `make dev-watch` first runs, or the agent would investigate its alerts with the sim's tools; (4) check whether a container reaches TC's ports through `host.docker.internal` and record the result (for TC's own containers, README, Deploy, egress check 4, once the egress lockdown runs); (5) `live` on, FA production only; (6) a fallback drill with `claude` off; (7) a 3-day shadow run: every alert has a report or a recorded reason, spend stays within the caps, a canary scan of stored data is clean, and the drill's report names the free model and the reason.
  - Phase 2, plans with the approval recorded and nothing run. It starts only once phase 1 is live; if it lands after 2026-11-10, the planner runs on the free models.
    - [x] P2.1 (built 2026-10-26 on branch `p2-planner`; docs/activity-log.md) Planner provider path: a streamed structured-output call to `claude-opus-5-5` at effort medium (no tools, `max_tokens` 16,000, its own 300 s timeout, no server-side refusal fallback); a refusal falls to the free chain; the `planner` enum value and the $1.00 daily cap: fake Anthropic responses (JSON, refusal, max_tokens) pass. Built: `AnthropicProvider.structured`, the free models' one forced call of `submit_plan`, `Router.plan` (a refused, cut-off, failed, skipped or invalid answer moves down the chain), `Settings.planner()` (`LLM_PLANNER_*`), the planner cap counting plans only (the triage cap no longer counts them), Opus's prices, agent 0013 and the `planner` switch (default off).
    - [x] P2.2 (built 2026-10-26, same branch) Plan schema and PlanRecorded v1 (`kind` none, restart_service or code_fix; `target` from the catalog; files from a per-app source allowlist; free text only in the rationale), made only for `real` and once per group, shown read-only: a plan appears on a real incident and never on a `not_real` one. Built: `agent/planner.py`, `agent.plans` (a due row in the report's transaction, completed once), `plan-recorded.v1`, gateway 0008 (`plans`, `plan_decisions`), gateway API 0.7.0 (`Investigation.plan`, `ModelPart` `plan`) and the report view's Plan section.
    - [x] P2.3 (built 2026-10-26, same branch) Map proposal check v2 (P0) to real targets: never a database, queue, cache or proxy; a restart only when the target's own health or container fails while its declared dependencies are healthy: catalog fixtures show each case. Built: `plan_refusal` on the newest snapshot (storage is never restarted either; a code fix names allowlisted source files of an app or worker); a refused plan is kept as no plan, with the reason.
    - [x] P2.4 (built 2026-10-26, same branch) Approve or reject a plan in the web, through the BFF and the approver key; the decision is stored, nothing runs: decisions are recorded once (409 after). Built: `POST /v1/plans/{id}/approve|reject` (approver key only; 409 once decided or without a proposal; no outbox row), `decidePlan` and a server action behind the report's form.
    - Before the first agent or gateway from this branch starts: `make migrate` (agent 0013, gateway 0008). The `planner` switch stays off until P2.5.
    - [ ] P2.5 Waits (the user, 2026-10-31): after 2026-11-10 the planner starts on the free chain. Measure the planner on dev-split reports, then turn `planner` on. Before 2026-11-10 that is Opus, about 15 plans for about $3, with the user's approval of the spend first; after, the free chain: the cost per plan is recorded.
  - Phase 3, fixed actions: only `restart_service` on app and worker containers.
    - [ ] P3.1 ActionApproved v2 carries the tenant and app: contract tests pass in both languages.
    - [ ] P3.2 An executor consumer and a restart effector (fixed argv `docker compose -p <project> restart <service>`, checked against the catalog, dry run by default, behind the `actions` switch): a fake `docker` shows only approved, catalog-listed restarts.
    - [ ] P3.3 A dry-run drill, then one real restart of an FA dev container: both are recorded.
  - Phase 4, coder. It starts with the user's decision on whether the coder may run on the free models, which would send repo source to free tiers.
    - [ ] P4.1 The hardened worktree sandbox: FA and FL only; a worktree under `H:\.cache\triage-copilot\worktrees\` from `origin/main`; branch `tc/<app>/<id>`; hooks, LFS and smudge filters off; `--no-verify`; credential helpers off; no push path; a per-app source allowlist; at most 10 files and 400 changed lines: tests on temp repos refuse every escape.
    - [ ] P4.2 A `claude-sonnet-5-5` draft at effort medium, applied and committed locally (never run, pushed or merged), with the $0.60 daily cap: a fake model's patch lands on the branch, and one that touches `.github/` is refused.
    - [ ] P4.3 The report view shows the branch and its diffstat, behind the `coder` switch: the user can review the branch locally.
  - Decided (docs/memory.md, 2026-10-24): TC gets $15 a month, evals included, with daily caps of triage $0.40, planner $1.00 and coder $0.60. Production stacks only; dev stacks opt in per app. Never push. Retention 30 days.
- [ ] Found in FA and FL while planning (2026-10-24): for those projects' own sessions to fix. Nothing was changed in their repos.
  - FA: the docs name a `claude_unavailable` log line, but the code emits `claude_switched_off`.
  - FA: the docs say a 429 without retry-after turns Claude off, but the code treats every 429 as a rate limit.
  - FA: production sets `OTEL_SDK_DISABLED`, so its 4 Prometheus alert rules can never fire for it.
  - FL: `infra/prometheus-alerts.yml` has 14 rules, but the docs say 13, and the monitoring table leaves out ClaudeKeyOrModelRejected.
  - FL: the 2026-11-10 expiry switches Claude off in the process only and never sets the Redis key, so the `filinglens_claude_off` gauge stays "none" after it.
  - FL: docs/context.md and docs/todo.md still call the deploy config uncommitted, though it merged as PR #28.
- [ ] Found in FL while building TC's egress lockdown (2026-11-01; FL's `infra/squid/squid.conf` read read-only, nothing changed): it sets no `max_filedescriptors`, so squid sizes its tables for Docker's 1,048,576 descriptors. TC's proxy idled at 153 MiB that way, and 8.6 MiB at 4,096. For FL's and FA's own sessions to check.
- [ ] watch follow-ups (P1.6, 2026-10-25), none needed before go-live:
  - A restart forgets the alerts watch holds as firing: one still bad is posted again with a new start, and the old one never resolves in the gateway. Keep the firing set in a small state file if restarts during incidents turn out to matter.
  - Without `docker inspect`, an out-of-memory kill shows only as exit 137, which a plain kill gives too; the snapshot says "often out of memory".
  - The real gateway's round trip of a watch payload (zod intake, dedupe on the second post, the resolve updating the same row) is tested only through the same generated client the eval harness uses; P1.8's end-to-end test covers it. Done (2026-10-25): `services/agent/tests/test_live_e2e_int.py` posts watch's alerts to the gateway process, and its storm test resolves 40 of them.
- [ ] Live triage follow-ups (P1.7 to P1.9, 2026-10-25):
  - Done (2026-10-25, branch `p1-10-reports`, the user's request): TC's $15 monthly Claude cap is in code. `LLM_CLAUDE_MONTHLY_USD` (15) holds every Claude role, live triage, the Claude fallback and evals on Claude alike: a call runs only if this UTC month's Claude spend plus its worst case fits. A Claude model without prices in `agent/pricing.py` fails at startup. The planner's own daily cap came with P2.1 (2026-10-26).
  - The triage cap's check and the call it admits are not one transaction: up to `AGENT_MAX_IN_FLIGHT` (5) live turns at once can each pass it, so a day can go over by at most 5 worst cases (about $0.07).
  - The live prompt is new and has run on no real model; the shadow run (P1.12) is its first test. Check its verdicts there before trusting not_real.
  - `test_partition_swap_while_a_writer_runs` failed once in this branch's first `make test-int`, then passed alone and in two full runs; the assertion line was not captured (docs/activity-log.md).
- [ ] Report view and retention follow-ups (P1.10 and P1.11, 2026-10-25), none needed before go-live:
  - Done (step 9, 2026-10-28): sign-in, and the list's Live incidents and Eval runs tabs (`source`, gateway API 0.8.0).
  - When a GraphQL consumer exists: GraphQL's `Investigation` lacks what REST serves beside it (`Hypothesis.service`, `missingEvidence`, `partial`, and 0.8.0's `source`, `cause`, `plan_status`). Step 9 polls REST and built no resolvers.
  - Retention keeps the steps of a live investigation that has not finished (queued or running), since its next turn replays them; they go on the first pass after it ends.
  - Investigations created before this branch recorded a tenant only when they parked, so retention cannot tell an older live one; none exists, since `live` has never been on.
  - The gateway reads `LIVE_TENANTS` with tenant uuids in the hyphenated form `tenant:token` prints; the agent's parser also takes other uuid spellings, which would stop the gateway at startup with a `LIVE_TENANTS:` error.
- [ ] Plan follow-ups (P2.1 to P2.4, 2026-10-26), none needed before P2.5:
  - The code-fix source allowlist (`SOURCES` in `agent/planner.py`) comes from TC's records of each repo's layout, not from reading the repos; P4.1 checks it against FA and FL before the coder runs.
  - A plan's decision does not move its alert group to `awaiting_approval`; the plan carries its own status. Phase 3 decides whether an approved restart queues ActionApproved v2.
  - Done (step 9, 2026-10-28): decisions record the signed-in approver, `operator`.
  - When a GraphQL consumer exists: GraphQL has no plans.
  - Shutdown waits for a plan call in flight, up to 305 s for Opus; the plan stays due if shutdown begins before it.
  - Like the triage cap, the planner cap's check and its call are not one transaction: 5 plans at once could each pass it.
- [ ] (The user's call) Size the 15-call cap for Haiku from data: it forced 47 of the second configuration's 75 reports, so how many calls Haiku would use is not measured. Measuring it takes a run with a higher call cap, which breaks the design's 15-call gate by construction.
- [ ] Two advisories have no fix inside current constraints (docs/activity-log.md, 2026-10-24): pillow 12.3.0 (41, fix 13.0.0, blocked by fastembed `pillow<13`) and esbuild <=0.24.2 (GHSA-67mh-4wv8-2f99, via `drizzle-kit` 0.31.11 `@esbuild-kit/esm-loader`; only drizzle-kit 1.0 beta/rc drops it). Done when `pnpm audit` and `uv audit` list nothing, with no ignore entries.
- [x] Move `next` and `eslint-config-next` from 16.4.2 to 16.4.3 once it is past pnpm's release-age gate (published 2026-10-23). Done in PR 2 (2026-11-01, branch `step-10-deploy`): `pnpm-workspace.yaml` unchanged, the lockfile holds 16.4.3 only.
- [ ] Add `pnpm audit` and `uv audit` to CI (`.github/workflows/ci.yml` has only gitleaks and actionlint), so advisories fail in the pull request, not later.
- [ ] (Later, the user's call) Haiku with thinking. Every Claude turn forces its tool call, so Haiku 5.5 never thinks (0 thought tokens on all 864 calls of run 1), while Gemini thinks at LOW. Turning it on needs: `tool_choice` `auto` on investigation turns; a path for a reply without a tool call (a nudge text would change the prompt sha); thinking blocks stored in `tool_calls.provider_state` and replayed unchanged, with a test that each rebuilt request matches the previous one byte for byte (Haiku 5.5 answers 400 when a replayed block follows an edited turn); a larger turn output cap, so a larger token cap; explicit `thinking` and `effort`; a cache key and report label for it; then one Haiku dev pass (about $0.15) before any test split.
- [ ] (manual) Get free API keys: Google AI Studio (Gemini) and NVIDIA build. Put them in `.env`, never in the repo.
- [ ] (manual) pre-commit's cache, with its gitleaks build, is on C: (`PRE_COMMIT_HOME` unset), against the AGENTS.md disk rule. Set `PRE_COMMIT_HOME` to a folder under `H:\.cache` and run `make install` again.
- [ ] (manual, before step 10) Vercel and Tailscale accounts created.
- [ ] Contract the unused `model_call_outcome` value `model_exhausted` (nothing writes or reads it since 2026-11-01, branch `claude-first`): rebuild the enum without it once no row holds it, as 0010's downgrade does.
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
- [x] (Done as P1.3, 2026-10-25; the tool shows them only for the live tool set) Store Alertmanager annotations (`summary`, `description`, `runbook_url`) in the gateway with an expand-only migration (a nullable `alerts.annotations jsonb`, written at intake), and return them from `GET /internal/investigations/{id}/alerts` (a new optional field in `StoredAlert`, gateway API minor version) and from `get_alert_context`. Real alerts carry key evidence there; today only labels are stored. Done when an alert posted with annotations shows its summary, description and runbook URL in the tool's result.
- [ ] Before step 10: the gateway's internal listener (4201) binds only the internal Docker network in `docker-compose.prod.yml` and is never published; check that Tailscale Funnel exposes only the public port. Built in PR 2 (2026-11-01): 4201 is on the production network only, never published (`test_prod_compose.py`). Open: the user's Funnel checks after `make prod-up` (README, Deploy, checks 1 to 3).
- [ ] Before step 10: check which client address the gateway sees behind Tailscale Funnel. If it is the proxy's, set Fastify `trustProxy`, or every request without a credential shares one rate-limit count. Then limit failed credential checks per address: today each invented key gets its own rate-limit count, unknown routes included, so key guessing is never throttled (independent review, 2026-10-15). Also refuse the `.env.example` placeholder keys at startup. Built in PR 2 (2026-11-01): `GATEWAY_TRUST_PROXY` (addresses or CIDRs; Fastify 5.12.7 trusts nothing on a hop count), `AUTH_FAILURES_PER_MINUTE` (20, then 429), and production refuses the placeholder keys in the gateway and the web. Open: the user's client-address check (README, Deploy, check 5). Done (2026-11-01, branch `egress-lockdown`): ranges that hold every client (`0.0.0.0/0`, `::/0`, `::ffff:0:0/96`) are refused, and check 5 names the sidecar's fixed address, 10.231.233.18, instead of a subnet.
- [ ] Per-kind parameter schemas for ActionProposed and ActionApproved (for example `rollback_deploy` requires `to_version`): the executor rejects unknown or missing parameters.
- [ ] Emit an early hypothesis step, so time to first hypothesis is measured directly instead of by its proxy (webhook intake to the report, an upper bound) and step 9's timeline can show it. Today hypotheses exist only in the final report. A new step kind needs StepRecorded v4 (expand, migrate, contract) or a `hypothesis` step with the existing kind; the job records it the first time the model names a likely cause, and the eval harness reads its `recorded_at`. Done when the eval report measures time to first hypothesis from that step and the timeline shows it.
- [x] Before step 10: the sim's admin port (`SIM_ADMIN_PORT`, 4231) is never published in `docker-compose.prod.yml`, and only the eval harness gets `SIM_ADMIN_URL`. Done in PR 2 (2026-11-01): production deploys neither the sim nor the executor, and `test_prod_compose.py` finds no published 4231 and no `SIM_ADMIN_URL` in any service.
- [ ] 7. CI/CD: GitHub Actions for test, build, docker push; actionlint passes; test PR green
  - [x] Built locally (2026-10-17): `ci.yml`, `publish.yml`, `eval.yml`, the setup action, `scripts/ci_env.sh`, `infra/docker/` images and `.dockerignore`. actionlint passes; all four images build; lint, typecheck, unit tests, build, contracts and scenario checks pass; gitleaks finds nothing.
  - [x] The GitHub steps (now under Completed), then the test PR green on `ci-result` and the images in GHCR: done, the user confirmed on 2026-10-18 (PR #1 is `0779d77` on origin/main).
  - [x] `make test-int` locally after the exam (2026-10-20): Jest 7 suites and 33 tests, pytest 41 passed in 140.6 s. This project's three containers were started by `make infra-up` and removed by `make infra-down`; the volumes stay.
  - [ ] Set `EVAL_ENABLED=true` (the user's switch): both the Gemini split and the Haiku comparison are recorded (2026-10-21). The `dev-ci-1` floors are set by hand (2026-10-20, docs/memory.md): validity 0.92, median tool calls 12, median tokens 80,425, and no floor for the two p95 times, which CI only reports. Never rerun `make eval SUBSET=ci SET_FLOORS=1`: it would put the targets back. Optionally ask for a role's key only when the router can call it, which drops the NIM placeholder.
  - [ ] The manual quota-stop check, once `EVAL_ENABLED=true` (while it is off a manual run fails at once): on a day Google has refused the project's Gemini requests ("daily quota spent"), run eval.yml by hand; it must end red with "rerun after the quota resets". CI's database starts empty, so in CI only Google's refusal stops a run, never the agent's count of 260.
- [ ] 8. Instrumentation: OpenTelemetry traces span services end to end; dashboard and alerts committed; screenshots saved. Include an alert on executions stuck in claimed (the sweeper only logs them now) and a count of payload fingerprints that differ from the computed ones.
- [x] 9. Review UI and demo mode: login, backend-for-frontend, demo snapshot shown when the backend is offline; Playwright tests pass (built 2026-10-27 and 2026-10-28 on `step-9-web`, not committed; resolvers moved to "when a GraphQL consumer exists"). The user's hand check, signing in on desktop and phone in both themes, is open (In Progress).
- [ ] After P1.12's shadow run, and the user's review (FA and FL data is private): replace the demo's three illustrative live samples (`apps/web/src/demo/live-samples.ts`) with real live reports.
- [ ] 10. Deploy: UI on Vercel, backend on the PC via Tailscale Funnel; phone test passes; 401 without API key; demo mode when the PC is off. Also: a Vercel firewall rule on `POST /login` (the sign-in limit holds per server instance), and refuse the `.env.example` placeholder keys and any empty or reused web secret at startup.
  - Built in PR 2 (2026-11-01, branch `step-10-deploy`, not committed):
    - the production compose project `triagecopilot-prod`, with the Funnel sidecar pinned by digest;
    - the `make prod-*` targets;
    - the gateway's and the web's key checks;
    - README, Deploy.
  - Open, the user's:
    - the env files;
    - `make prod-up`;
    - the README checks;
    - the Vercel project with its firewall rule;
    - the phone test.
- [ ] Next PR, built during the shadow run (the user, 2026-11-01): egress lockdown for production, as FreightAudit and FilingLens have (docs/threat-notes.md). It covers:
  - internal networks for Postgres, ElasticMQ, the gateway and the agent;
  - a squid egress proxy that allows only the model providers' hosts, used by the agent;
  - the Tailscale sidecar's own route out;
  - a routed edge for the gateway's 127.0.0.1:4202 that gives the gateway no way out;
  - a compose test.

  Done when no production app container reaches the internet or this PC's listeners except through the proxy's allowlist.
  - Built (2026-11-01, branch `egress-lockdown` on top of `step-10-deploy`'s `207212b`, not committed; docs/activity-log.md), in the user's scope of that day: every network internal but the proxy's `egress`; the sidecar through the proxy too (control plane, relays, Let's Encrypt), not by a route of its own; the proxy publishes 4202 and forwards it to the gateway; `GATEWAY_TRUST_PROXY` refuses catch-all ranges; the cold-query timing test made robust. Tests: `test_prod_compose.py`, `test_egress_int.py` (live, on throwaway networks), `test_providers.py`.
  - Open, the user's: ship it once PR 2 (#20) is on main, make `triage-copilot-squid` readable, and run README, Deploy, Egress checks at the first `make prod-up` with it. Done when those pass.
  - Optional, found while building it: the sidecar still reaches the gateway's internal listener (4201) on `edge`, behind its tokens (docs/threat-notes.md). Done when 4201 listens on a fixed address of the gateway on `data` only and a compose test pins it.
- [x] `test_agent_api.py::test_the_free_chain_is_the_default_and_claude_first_needs_its_key` fails under `make test` while `.env` sets `LLM_CLAUDE_FIRST=true`: its `OFFLINE` settings read the environment, which the Makefile fills from `.env` (found 2026-11-01; CI's `.env` sets it false, so CI passes). Done when the test reads no `LLM_CLAUDE_FIRST` from the environment and `make test` passes with it set. Done (2026-11-01, branch `egress-lockdown`): the test unsets it and builds its own settings; `make test` exits 0 with the user's `.env` (docs/activity-log.md).

## In Progress

- Go-live before 2026-11-10 (plan approved 2026-10-31: `%USERPROFILE%\.claude\plans\pasted-content-id-9a4f-read-docs-todo-m-abstract-donut.md`): PR 1, Claude first everywhere, is built on branch `claude-first` with every check green (docs/activity-log.md, 2026-11-01), not committed; the user ships it. Then P0's two passes (P0 step 3), PR 2 (step 10, the four "Before step 10" items, `next` 16.4.3, production as its own compose project `triagecopilot-prod`), and P1.12.
- Correction (2026-11-01): step 9 merged as #18 (`d425141`); the step 9 lines below that say nothing is committed are out of date. Only its hand check is open, and it folds into step 10's phone test.
- 2026-11-01, later: PR 2 is committed as `207212b` (pull request #20) but not on main, which is still `39f4549`. The egress lockdown is built on branch `egress-lockdown` on top of it, not committed (Pending, Next PR). The cold-query timing test in `evals/harness/test_scenario_set.py` takes the best of 3 cold tries now.
- 2026-11-01: PR 1 merged as #19 (`39f4549`).
  - PR 2 was built on branch `step-10-deploy` from it, with every check green (docs/activity-log.md, 2026-11-01), and is not committed. It holds step 10, the "Before step 10" items, `next` 16.4.3 and production as its own compose project. The user ships it.
  - P0's passes have not run yet; their records go in a later PR.
  - After PR 2 come the user's go-live steps (README, Deploy; the plan's go-live section), P1.12 with its 3-day shadow run, and, during the shadow run, the egress lockdown PR (Pending).
- P0, proposal check version 2: built and merged as #12 (`58fdd00`; corrected 2026-10-26, this line said nothing was committed). The two dev passes are the user's to run, with the steps and estimates under Pending (P0, step 3).
- P2.1 to P2.4 merged as #17 (`bebbb1e`; corrected 2026-10-28, this line said they sat uncommitted on `p2-planner`). P2.5, the measuring run, is the user's.
- Step 9, the web UI.
  - Status: the user approved the plan on 2026-10-27. Branch `step-9-web` was cut from main (`bebbb1e`, #17). Nothing is committed.
  - Build session (2026-10-27): items 1 to 6 are built, tests first, with their checks green (docs/activity-log.md). The build stopped at item 7, step 1: `make migrate` applied the gateway migrations, then Windows Application Control blocked `alembic` (os error 4551). The user's rule for that is to stop and report.
  - 2026-10-28: Python tools now run as `uv run python -m <tool>` (the user's fix). `make migrate` passed, and items 7 to 11 are built; every check in item 10 exits 0. Waiting: the user's hand check (Verification, step 2), then ship.
  - Full plan: `%USERPROFILE%\.claude\plans\pasted-content-id-7bc6-plan-mode-polished-willow.md`.
  - The user's choices:
    - Live progress by polling.
    - A demo of real eval runs plus 3 hand-written live samples, labeled illustrative on the page.
    - `make migrate` on the dev database, and the read-only export, are approved.
    - A CI e2e job.
    - Sign-in as in FA and FL. Their auth code was read, read-only, with the user's permission.
    - The fonts come from committed files through `next/font/local`, with their OFL licence beside them, because `next/font/google` broke FA's Turbopack builds on 2026-10-24.
    - The per-instance sign-in limit is accepted, with step 10's firewall rule.
    - Sim proposals stay read-only.
  - Rules: one PR; tests first, each run red before its code; no model call; only this project's ports and containers.
  - Done when every check in item 10 passes and the user has signed in by hand (the plan's Verification, step 2).
  - [x] 1. Contract: gateway API 0.8.0, expand-only.
    - `GET /v1/investigations` gains an optional `source` (`live`, `sim`). Without it, the list spans every tenant.
    - `InvestigationSummary`, and so `Investigation`, gains `source`, `cause` and `plan_status` (`ActionStatus` or null).
    - `running` is documented.
    - Done when `make codegen` runs and `make contracts-breaking` shows no ERR.
  - [x] 2. Gateway, tests first (`app.test.ts`, `investigations.int.test.ts`).
    - `source` comes from `LIVE_TENANTS`. `cause` and `plan_status` are filled.
    - A `queued` group reads `running` while its newest step is under `SWEEPER_STUCK_AFTER_MINUTES` old. This is derived on read; the stored status stays `queued` for the sweeper.
    - `cli.ts demo-save`, run by `make demo-save IDS=`, is read-only and reuses `createGetInvestigation`. It refuses any investigation that is not `sim`.
  - [x] 3. Sign-in, tests first.
    - `lib/password.ts`: FL's argon2id cost and bounds, tested with FA's reference vector.
    - `lib/session.ts`: FL's `v1.<expiry>.<mac>` token in `__Host-triage_session`, which is HttpOnly, Secure and Lax, and lasts 8 hours.
    - `lib/guards.ts`: FL's limiter (5 per address per 15 minutes, counted before argon2), plus a global 30 per 15 minutes; `safeNext`; `isSameOrigin`.
    - Config: `LOGIN_PASSWORD_HASH` and `SESSION_SECRET` are required in production, and both or neither in dev. The secret must differ from the two gateway keys.
    - Scripts: `hash-password`, whose `--write-env` writes each `$` as `$$`, and `session-secret`.
    - `/login` with a password-only form; `POST /logout`.
    - `src/proxy.ts` with FL's public pattern and nonce CSP.
  - [x] 4. Data access, tests first.
    - `lib/data.ts` calls `requireSession()` before every gateway call, and records the approver as `operator`.
    - An unanswered gateway redirects to `/demo?offline=1`: a throw, the 5 s timeout, or a 502, 503 or 504.
    - A Jest scan fails when `src/app` or `src/components` reaches the gateway any other way. Readyz's health read is the one exception.
    - `decidePlanAction` without a session sends nothing.
    - `/readyz` caches the gateway's answer for 10 s.
  - [x] 5. Pages, tests first.
    - The route group `(app)`, with the signed-in header.
    - The public landing at `/`.
    - The list, with Live incidents and Eval runs tabs.
    - The report: the verdict tag, cause and evidence, the plan's decision panel, the timeline, models and fallback notes.
    - `(app)/error.tsx` (with retry, and links to `/demo` and `/login`), and `not-found.tsx`.
    - The plan's triage-tag design in `globals.css`: its palette, light and dark from the system setting, and a phone layout with no sideways scroll.
    - Atkinson Hyperlegible Next and Mono as committed woff2 files, with `OFL.txt`, in `src/app/fonts/`, through `next/font/local`.
  - [x] 6. Live progress.
    - `live-refresh.tsx` refreshes the report every 3 s and the list every 10 s, within the plan's windows.
    - It pauses in hidden tabs and has a Pause updates button.
    - A `role="status"` line reports progress.
  - [x] 7. Demo.
    1. Run `make infra-up`, then `make migrate`.
    2. Run `make demo-save IDS="..."` for 4 or 5 eval runs, and list their ids in README.
    3. Run `make infra-down`.
    4. Write three live samples in `src/demo/live-samples.ts`, each marked "Illustrative sample, not a real incident".
    5. Build the `/demo` pages, with the plan buttons disabled and no action.
    6. Add a test that every eval run is `sim` and every sample is labeled.
  - [x] 8. Playwright, tests first.
    - `@playwright/test` and `@axe-core/playwright`, at versions past the release-age gate.
    - A fake gateway on 4250, the web on 3202, and an isolated web on 3203.
    - A saved sign-in.
    - Specs `auth`, `reports`, `demo` and `a11y`: axe with WCAG 2.2 A and AA, light and dark, on desktop and a 390x844 phone.
    - `make test-e2e`, with browsers in `H:\.cache\ms-playwright`.
    - CI: an `e2e` job in `ci.yml`, listed in `ci-result`'s needs.
  - [x] 9. Root files.
    - Makefile: the two secrets unexported except for `dev-ts`; `dev-ts` runs turbo with `--continue=always`; new targets `test-e2e` and `demo-save`.
    - `.env.example`, `scripts/ci_env.sh`, and `.gitignore` (`test-results/`).
  - [x] 10. Checks, each exit code recorded (2026-10-28, all 0; docs/activity-log.md): `make lint`, `make typecheck`, `make test`, `make test-int`, `make contracts-check`, `make contracts-breaking`, `make build`, `make test-e2e`. Then `make infra-down`.
  - [x] 11. Docs.
    - README.
    - docs/context.md: ports 3202, 3203 and 4250, and the Playwright cache.
    - docs/memory.md: the plan's decisions, and the FA font lesson.
    - docs/migrations.md: API 0.8.0.
    - docs/todo.md:
      - step 9, and the follow-ups it closes;
      - the GraphQL items, moved to "when a GraphQL consumer exists";
      - a new item: replace the illustrative live samples with real live reports after P1.12's shadow run, after the user's review;
      - step 10: a Vercel firewall rule on `POST /login`, and refusing placeholder web secrets;
      - fix the P2 line above, which merged as #17 (`bebbb1e`).
    - docs/activity-log.md, covering this planning session too: the FA and FL auth code read with permission, and the review's fixes.
  - Commit message for ship: `Web: sign in to see live reports and decide plans, or open the demo`

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
