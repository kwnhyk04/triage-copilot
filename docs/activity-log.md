# Activity Log

Chronological record of significant development activity and small events (AGENTS.md, Documentation Rules), newest first. Skip only trivial modifications such as typo or formatting fixes. Never rewrite a past entry; a later entry corrects it. Do not auto-commit this file.

## Entry Format

```
### YYYY-MM-DD: <task or event>

- What happened: <the work, event or mistake>
- Why: <the cause or reason; "not confirmed" when it is a guess>
- What we did: <the exact steps in order (commands, buttons, where each setting lives), and why that was the best option>
- Files: <relevant files, if any>
- Validation: <checks run and their results, failed and skipped ones included>
- Outstanding: <open issues, or "None">
```

Entries before 2026-10-18 use the older Changes, Files, Validation and Outstanding fields and stay as written.

## Entries

### 2026-10-19: Partition flake item completed, and the day-3 docs moved to a branch

- What happened: The user confirmed that #3 is merged on main as `26f70cf`, which answers the day-3 entry's open item about the partition flake. They also asked to move the uncommitted day-3 docs changes off main to a new branch, `docs/exam-day-3`, and for a one-line commit message to ship it with.
- Why: the shipping rule (AGENTS.md, Git): the user commits and ships, and the day-3 changes sat uncommitted on main.
- What we did:
  1. `git switch -c docs/exam-day-3` on main at `26f70cf`. The four modified docs came along uncommitted.
  2. docs/todo.md: moved the partition flake item from In Progress to Completed, ticked, naming the merge and the user's confirmation.
  3. Gave the commit message in chat: "Docs: exam day 3 (58 of 75 runs), day-4 resume steps with a quota-stop check, partition flake done".
  - Why this was the best option: `git switch -c` keeps the working tree as it is, so nothing was stashed or copied, and the user's words decide the item.
- Files: docs/todo.md, docs/activity-log.md
- Validation: `git status` on `docs/exam-day-3` shows the same four modified docs and nothing else.
- Outstanding: the user commits and ships the branch. Nothing is committed.

### 2026-10-19: Test split on Gemini, day 3

- What happened: The user ran day 3 of the test split with the resume steps in docs/todo.md (6b). They reported exit 2 with "quota stop: the model's quota or credits are spent", then stopped the servers and ran `make infra-down`. The result files show 26 runs finished between 08:43 and 09:25 UTC (16:43 to 17:25 Taipei), all on `gemini-3.5-flash-lite`:
  - db-pool-exhaustion-4 run 2, parked on day 2;
  - the other 17 second runs;
  - 8 third runs (bad-config-3 to db-pool-exhaustion-4).
  22 ended resolved and 4 awaiting approval. 58 of 75 runs are done and 17 are left. Parked: db-pool-exhaustion-5 run 3, state file `evals/reports/runs/db-pool-exhaustion-5-run3-a254b47335b02e97.state.json` (investigation `a0c316e6-4db8-4a34-86ad-369d16e7466e`, seed 40190, anchor 2026-10-19T09:25:46Z). Before it started, Gemini had answered 248 requests since midnight Pacific, all `ok`, with no 429, 503 or error; the small model got no request. Those requests used 1,653,530 tokens. The 26 runs total 1,690,143 tokens, but 36,613 of them are the resumed run's day-2 calls; the eval prints that run as 56,060 tokens.
- Why: not confirmed. The eval prints the same quota-stop lines for two causes; day 2's saved eval output shows them for the first:
  - The agent's own limit of 260 requests a Pacific day: it parks before the next turn and records no call.
  - Google's 429 "daily quota spent": the agent records a `quota_exhausted` call whose error is `QuotaExhausted: gemini 429: daily quota spent` (`agent/providers.py`, `agent/llm.py`, `agent/job.py`).
  The 248 requests left the parked run 12 under the limit, and db-pool-exhaustion-5 made exactly 12 in runs 1 and 2, so either cause fits. Only `agent.model_calls` in Postgres, down since `make infra-down`, and the agent's terminal output, which no file keeps, can tell. The token budget did not stop it: the agent checks it only for an investigation with no steps, and the UTC day stood at 1.65 million of 3 million when the parked run started.
- What we did, all read-only (no eval, model call, container or `make test-int`, as the user asked):
  1. Listed `evals/reports/runs/` by write time with `find -printf`, since RTK's condensed `ls -la` showed no times: 26 result files from 2026-10-19 and one state file; day 2's state file was gone.
  2. Counted each day-3 result file's model calls by role, model and outcome, and its tokens, with a script in this session's scratchpad run by `.venv/Scripts/python.exe -I`. The resumed run's steps split by `recorded_at`: 8 calls on 2026-10-18 (steps 1 to 16, 36,613 tokens, the figures day 2's psql query gave) and 3 on 2026-10-19 (steps 17, 19 and 21, 19,447 tokens). 256 calls in the 26 files minus those 8 gives 248.
  3. Checked that no run was started twice, since a second attempt's calls would be missing from the result files: each run's first step comes 60 to 67 s after the previous result file, the eval's 60 s gap.
  4. Read the stop path: `evals/harness/eval.py` (`execute`), `run.py` (`wait`), and the agent's `job.py`, `llm.py` and `providers.py` for how each limit parks.
  5. Looked for saved day-3 output. The Makefile writes no log file. The only Claude Code session of this project started on day 3 (08:39 UTC) ran `/effort` and nothing else. Day 2's saved agent output (`H:\.tmp\claude\H--Projects-projects-triage-copilot\503914ee-4139-4213-bfc1-c351d9541335\tasks\b52che61c.output`) shows what day 3's terminal held: one `httpx2` line per Gemini POST with its HTTP status (260 lines, all `200 OK`, on day 2), then `investigation parked`.
  6. Looked for other users of the quota. In this project, `ci.yml` has no Gemini key; `eval.yml` runs only with `EVAL_ENABLED` true (unset by docs/todo.md; not checked, since gh is off limits) and on Mondays at 18:00 UTC, after the stop; tests use the scripted model; and the Makefile gives `GEMINI_API_KEY` only to `make dev-agent`. File times under `H:\.tmp\claude` show Claude Code sessions of freight-audit and filing-lens writing test and e2e logs during the eval. Their files were not opened (AGENTS.md, Context Isolation), so whether they called Gemini on a key of the same Google project is not known.
  7. Estimated day 4 from each remaining scenario's earlier runs: about 181 requests, 199 if each run repeats its scenario's highest count, and about 1.2 million tokens.
  8. docs/todo.md: day 3 on the progress line, and the day-4 resume steps. Step 1 now ends with a read-only query of day 3's counters, and step 7 runs the same query before `make infra-down`. docs/context.md (status, next steps) and docs/memory.md (Known Issues) updated.
  - Why this was the best option: the files answer everything except the parked run's calls, and the user ruled out containers today. One query on day 4, before anything starts, settles it from the agent's own counters.
- Small events:
  - GateGuard gated the first Bash command, the first Write of each of three scratchpad scripts, and the first edit of docs/todo.md, docs/context.md, docs/memory.md and this file. In a parallel batch it refused only the first call, and the batch's other edits applied. What we did: stated the facts and sent the refused call again.
  - `python` in Git Bash exited 49: "Python was not found; run without arguments to install from the Microsoft Store". Why: Windows' app execution alias, and no Python on PATH. What we did: ran the venv's interpreter with `-I` instead.
  - A grep of day 2's agent output for `429` matched timestamps such as `.942984`; a search for `HTTP/1.1 429` found no line.
- Files: docs/ (todo.md, context.md, memory.md, activity-log.md)
- Validation: each result file's `tokens` equals the sum of its calls' input and output tokens; 58 test-split result files, none for the same scenario and run twice. The stop message and both park paths were read from the code. No tests ran, since only Markdown changed. Step 1's query was not run (no containers today).
- Outstanding:
  - Which limit stopped day 3, the parked run's steps and the day's full tokens: day-4 step 1.
  - 17 runs on day 4, then step 8's commands and the Claude comparison.
  - docs/todo.md still lists the partition test flake as In Progress, while main has #3 (`26f70cf`), and ship merges only after every check passes. The user confirms before the item moves.
  - Nothing is committed.

### 2026-10-18: Partition test flake in CI after #2

- What happened: After #2 (docs only) merged, CI's integration job failed `test_partition_swap_while_a_writer_runs` (1 failed, 40 passed), as the user reported. The log: writes after each phase {'dual-write on': 0, 'backfill': 5505, 'swap': 5826, 'dual-write off': 5906, 'contract': 6599, 'partition job': 6679}, slowest 0.552 s. The same code had passed on the pull request.
- Why: confirmed from `services/agent/tests/test_partitioning_int.py`. `phase_done` slept a fixed 0.3 s and then read `writer.written`. Its first call comes right after `writer.start()`, so a first write slower than 0.3 s leaves the first phase at 0, and `all(after > before ...)` over `[0, 0, 5505, ...]` fails. The first write took over 0.3 s and at most 0.552 s; the log does not say which write was the slowest. Why it was slow is not confirmed; no migration or job runs in that phase, so it was not a lock wait. This reading assumes the counts assertion failed: had the writer logged errors, `writer.errors == []` would have failed first.
- What we did:
  1. `git switch -c fix/partition-test-flake main`, with main at `57e712d` (#2), the same as origin/main.
  2. `phase_done` now waits until `writer.written` passes the previous phase's count, polling every 10 ms for up to 10 s. Past that it fails through `pytest.fail`, naming the phase, the count before it and the writer's last three errors. Every assertion stays: no errors, `slowest < 3`, and a count that rises in every phase.
  3. Checked every fixed sleep in the integration tests, Python and TypeScript. None waits for progress and then asserts it, so none changed:
     - `test_workers_int.py:278`: the tool sleeps 0.5 s so it is still running when the stop arrives, and the stop is event-driven.
     - `test_workers_int.py:412`: the tool sleeps 5 s to outlast the 2 s visibility timeout; the test then polls with `eventually`.
     - `test_parking_int.py:60` (`Slow`), as `Slow(2)` in the storm test: it holds each slot for 2 s, a margin over message delivery, not a wait for progress.
     - `test_parking_int.py:275`: 3 s for three sweeps, then it asserts that nothing changed.
     - `test_elasticmq_int.py:96`: 1.5 s past a 1 s visibility timeout, then it asserts the message is still hidden.
     - `investigations.int.test.ts:122`: 1 ms of pacing in the writer; `written > 0` always holds, since the first insert starts before the walk and `await writer` lets it finish.
     - Already polls with a deadline: `eventually` in `test_workers_int.py`, the start-up waits in `test_parking_int.py`, and `drained` in `events.int.test.ts`.
  - Why this was the best option: it is the fix the user asked for; the wait ends on the write itself, and lock waits still show in `slowest` and in the writer's 5 s `statement_timeout`.
- Small events: the edit tool noted that docs/todo.md had changed on disk since it was read: #2's commit by ship, with the content written earlier today. `make test-int` and containers were not run, as the user asked; CI verifies.
- Files: services/agent/tests/test_partitioning_int.py, docs/ (todo.md, context.md, memory.md, activity-log.md)
- Validation: `uv run ruff check` and `uv run ruff format --check` on the test pass, and `uv run pyright` on it reports 0 errors. Not run: the test itself, which needs Postgres; CI runs it.
- Outstanding: the user ships the branch, and CI's integration job must pass. Nothing is committed.

### 2026-10-18: Stop step's PID check fixed, and the day-3 resume facts

- What happened: The user asked for a stop step whose PID check can fail, comparing the PIDs as the same type, shown failing on a made-up PID with taskkill left out, and for the resume steps in docs/todo.md to name the parked run, the earliest start and what is left.
- Why: today's check looked PIDs up in an `[ordered]` table with int keys, which PowerShell indexes by position, so the target came back empty and the command-line test (`-like "**"`) passed any make.exe. Correction to the day-2 entry below and to the chat report, which said the check could not fail: only that test could not; by its code, a missing PID or a process not named make.exe still failed it.
- What we did:
  1. Wrote the fixed block. PIDs are quoted string keys and the target must be found. The process must exist, its PID as a string must equal the key, its name must be make.exe, and its command line, split on whitespace, must hold the target as a whole token.
  2. Dry run in one PowerShell call, with the taskkill line replaced by a message. Cases B to D used a stub `Get-CimInstance`, defined in that shell only, that returned made-up make.exe processes:
     - 0: `[ordered]@{ 10 = 'ten'; 20 = 'twenty' }` gives `$t[1]` = 'twenty' and `$t[10]` = '', so the index is positional (confirms the memory entry's guess).
     - A: made-up PID 999996, no such process, the real `Get-CimInstance`: "not stopped".
     - B: made-up PID 999992, a stub make.exe running dev-ts, listed as dev-agent: the fixed block said "not stopped"; the old check would have stopped it, with an empty label.
     - C, the control: made-up PID 999988, a stub make.exe running dev-agent: the fixed block would stop it.
     - D: C with an int key by mistake: "not stopped", so a wrong key stops nothing.
  3. docs/todo.md, 6b step 7: how to list the make PIDs, the block, and what to do on "not stopped". The block parsed from the file with PowerShell's parser: 1 block, 0 errors.
  4. docs/todo.md, the resume line: next resume after 15:00 Taipei on 2026-10-19; parked, db-pool-exhaustion-4 run 2 with its state file; left, 43 runs, about 2 quota days. The progress line above it keeps the history only.
  5. docs/memory.md: a new entry confirms the positional index; the earlier one stays as written.
  - Why this was the best option: the stub tested the broken part against a make.exe without starting or stopping any process. String keys are the fix the user asked for, and the guard on the target makes a wrong key stop nothing.
- Files: docs/todo.md, docs/memory.md
- Validation: the dry run and the parser check above. Nothing was started or stopped.
- Outstanding: None.

### 2026-10-18: Shipping rule replaced, and step 7's GitHub steps completed

- What happened: The user replaced this morning's ship bullet in AGENTS.md (Git) with a "Shipping:" rule. Claude Code never commits, pushes, opens or merges pull requests, never runs gh for anything (read-only commands included), and asks the user for CI or pull request results; the user ships with `H:\Projects\projects\_tools\ship.ps1`. The same rule goes into docs/memory.md, Preferences. The user also confirmed that step 7's GitHub steps are done: PR #1 is `0779d77` on origin/main (the user checked read-only).
- Why: the rule is the user's, with no reason given. The GitHub steps answer the Outstanding item of this morning's Git rule entry.
- What we did:
  1. AGENTS.md: replaced the last bullet under "## Git" with the user's text, word for word, and changed nothing else. GateGuard asked for facts first.
  2. docs/memory.md, Preferences and Instructions: the same text as a dated entry.
  3. docs/todo.md: moved "(manual) GitHub, in this order (step 7)" with its 8 steps from Pending to Completed, with the user's confirmation, and ticked the matching sub-item of 7. CI/CD; its two after-exam sub-items stay open.
  4. docs/context.md, Current Development Status: the GitHub steps are done.
  - Why this was the best option: the user's words in both places, and moving the todo item keeps its steps on record.
- Files: AGENTS.md, docs/memory.md, docs/todo.md, docs/context.md
- Validation: `git diff AGENTS.md` shows the Shipping bullet as the last one under Git and, besides it, only the earlier uncommitted Documentation Rules change.
- Outstanding: None.

### 2026-10-18: freightaudit-prod was the user's pnpm demo:down

- What happened: The user explained the freightaudit-prod removal recorded in the entry below: they ran `pnpm demo:down` at 08:32 UTC for FreightAudit's offline check.
- Why: FreightAudit's offline check. The entry below guessed another session ("perhaps from the FreightAudit session"); it was the user.
- What we did: nothing, as the user asked; recorded it here and left the entry below as written. Why: the logging rule corrects a past entry with a new one.
- Outstanding: None; this answers the entry below.

### 2026-10-18: freightaudit-prod containers removed, not by this session

- What happened: After step 7, `docker ps` no longer listed the seven freightaudit-prod containers (worker, api, egress-proxy, tailscale, postgres, redis, minio) that ran when the session started. `docker events` shows each one killed, stopped and destroyed between 08:32:55 and 08:32:59 UTC. Their volumes remain (`freightaudit-prod_postgres-data`, `freightaudit-prod_minio-data`, `freightaudit-prod-tailscale-state`), and the freightaudit dev containers (postgres, redis, minio) still run.
- Why: not confirmed. This session's `make infra-down` (compose project `triagecopilot`) returned at 08:32:17 UTC by the session transcript and listed only this project's three containers and network; the next command ran after 08:33. Kill, stop and destroy in dependency order fit a `docker compose down` of that project, perhaps from the FreightAudit session.
- What we did, all read-only:
  1. `docker ps -a --filter name=freightaudit-prod`: no container left.
  2. `docker events --since 60m --until 0s --filter type=container --filter event=kill --filter event=stop --filter event=destroy`: the times above. It did not list this project's own down events from 08:32:13; the daemon keeps only its latest events, which health-check execs fill (not confirmed).
  3. `docker volume ls --filter name=freightaudit`, and this session's transcript timestamps.
  - Nothing of theirs was started, stopped or removed. Why: AGENTS.md and the user forbid touching another project's containers, and whether the demo runs is the user's call.
- Outstanding: the user decides whether freightaudit-prod should run again.

### 2026-10-18: Test split on Gemini, day 2

- What happened: Resumed the test split with the steps in docs/todo.md (step 6b). 23 runs finished between 07:49 and 08:29 UTC: dns-partition-2 run 1 (parked on day 1), the other 15 first runs left, and 7 second runs (bad-config-3 to db-pool-exhaustion-2); 17 ended resolved and 6 awaiting approval. Then exit 2, a quota stop, at db-pool-exhaustion-4 run 2: 32 of 75 runs are done. Gemini (`gemini-3.5-flash-lite`, primary) answered all 260 requests, with no 429, 503 or error; the small model got no request. The UTC day used 1,803,303 of the 3,000,000-token budget.
- Why: the agent's own `LLM_PRIMARY_RPD` (260) stopped it: the parked run waits for budget and stays unresumable until midnight Pacific. Google never answered 429.
- What we did:
  1. Checked that this project's ports were free with `Get-NetTCPConnection -State Listen`: 4200, 4201, 4210, 4230, 4231, 3200, 5434, 4566, 8476 and 15500-15509. `docker ps` showed only freightaudit containers, none on those ports.
  2. Steps 1 to 6 from PowerShell, the servers as background tasks:
     - `make infra-up` (all three containers healthy).
     - `make dev-sim`, until `curl.exe -s http://127.0.0.1:4231/admin/state` answered.
     - The `run.resume` one-liner; the sim then named dns-partition-2 (seed 60433, anchor 2026-10-17T08:47:52Z).
     - `make dev-ts`, until `/readyz` on 4200 answered `{"status":"ok"}`.
     - `make dev-agent AGENT_RELOAD= AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false DAILY_AI_TOKEN_BUDGET=3000000`.
     - `make eval SPLIT=test AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false` at 07:49 UTC. It printed `[1/66]`, so 9 runs were done before.
  3. Not in the steps, read-only: before the eval, waited for the agent's `/readyz` on 4210, since the eval polls the agent's state API as soon as it starts. The gateway's sweeper had already re-queued the parked run (first Gemini call at 07:48:52 UTC), and the eval's `run.resume` activated the same world again.
  4. The eval printed `quota stop:` and make `Error 2`, so the harness's own exit code was 2 (make exits 2 for any failed recipe; docs/memory.md, 2026-10-17).
  5. Step 7: `taskkill /PID <make PID> /T /F` for the agent (29116), `dev-ts` (37028) and the sim (40840). Ports 4200, 4201, 4210, 4230, 4231 and 3200 were then free.
  6. Not in the steps, read-only, before `make infra-down` stops Postgres: the day's usage, for the report the user asked for. `docker compose -f infra/docker-compose.yml --env-file .env exec -T postgres psql -U postgres -d triage -c ...` over `agent.ai_usage`, `agent.model_calls` since midnight Pacific by model, role and outcome, and the parked row of `agent.investigations`.
  7. `make infra-down`; the `pgdata` and `elasticmq-data` volumes remain.
  - Why this was the best option: the steps as written; the two read-only additions guarded the eval's start and produced the requested figures without changing any state.
- Small events:
  - GateGuard gated the first Bash command, the first edit of each file, and again the first Bash command after the eval's completion notice, which it also called the first of the session. Why: not confirmed. What we did: stated the facts and sent the same calls again; they ran.
  - `grep -nE '^(infra-up|...)\b' -A 14 Makefile` in Bash printed nothing and exited 1, though the targets exist; the Grep tool found them. Why: not confirmed. In the same command `TZ=Asia/Taipei date` printed GMT (Git Bash's time zone data, not confirmed).
  - `docker ps --format` and `docker events --format` with `{{.Label "com.docker.compose.project"}}` failed in PowerShell: `failed to parse template: template: :1: function "com" not defined`. Why: Windows PowerShell 5.1 drops inner double quotes from arguments to native programs (not confirmed). What we did: dropped the label from the template.
  - Mistake: the stop script's PID check could not fail (an `[ordered]` dictionary with an int key; docs/memory.md, 2026-10-18). The parent PIDs taskkill printed (22280, 29624, 32400) equal those of the make processes listed at the start, so the right trees were stopped.
  - taskkill printed `ERROR: The process with PID 43800 (child process of PID 39364) could not be terminated. Reason: There is no running instance of the task.` in the `dev-ts` tree. Why: not confirmed; it had likely exited when a parent or child was killed. The ports were free afterwards.
  - The three server tasks ended with exit code 1, from `taskkill /F`.
  - The resumed dns-partition-2 run shows 82,924 s, timed from its alert group's intake on 2026-10-17; reports leave resumed runs out of the timing figures.
- Files: evals/reports/runs/ (23 results and one state file, ignored by git), docs/ (todo.md, context.md, memory.md, activity-log.md)
- Validation: 23 result files written this session; one state file left, `db-pool-exhaustion-4-run2-b8fdb20f568b7e4d.state.json`, whose investigation `70ef1946-24bb-4c94-b663-3a4789ec25a6` is `queued`, waiting for budget since 08:29:26 UTC, with 16 steps and 36,613 tokens. `make test-int` not run (skipped during the exam). No scores yet: the report comes after all 75 runs.
- Outstanding: 43 runs, about 470 requests and about 2 more quota days, from the steps in docs/todo.md; then step 8's floor commands and the Claude comparison. Nothing is committed.

### 2026-10-18: Git rule: the user ships with ship.ps1

- What happened: The user added a rule to AGENTS.md, "## Git": they commit, push and merge with ship (`H:\Projects\projects\_tools\ship.ps1`), which merges only when every check passes; agents never push, open or merge a pull request, with `gh` or otherwise.
- Why: the user asked for it and gave no reason.
- What we did: added the user's line verbatim as the last bullet under "## Git" and changed nothing else. GateGuard asked for facts first; only CLAUDE.md imports AGENTS.md (`@AGENTS.md`). Why: the user's exact words and place.
- Files: AGENTS.md
- Validation: `git diff AGENTS.md` shows the one new line beside the earlier uncommitted Documentation Rules change.
- Outstanding: docs/todo.md still lists the GitHub steps (step 7) as pending, while the last commit, `0779d77`, is "Step 7: CI, image publishing, eval workflow (off), Dockerfiles (#1)" and `origin` points at GitHub; which of those steps are done is the user's to confirm. Nothing is committed.

### 2026-10-18: Mistake: the GateGuard entry named a hook location before checking it

- What happened: The GateGuard entry below says both hooks come from `%USERPROFILE%\.claude\plugins\cache\ecc\ecc\2.2.3\hooks\hooks.json`. Only `pre:edit-write:gateguard-fact-force` is named there (line 86). For Bash, that file runs `scripts/hooks/pre-bash-dispatcher.js`, and `pre:bash:gateguard-fact-force` is defined in `scripts/hooks/bash-hook-dispatcher.js` (line 48); the gate's code, `scripts/hooks/gateguard-fact-force.js`, names that id too (line 45). The entry also leaves out a fourth switch the Bash gate offered, `GATEGUARD_BASH_ROUTINE_DISABLED=1`.
- Why: the entry was written after a case-insensitive search for "gateguard" in `%USERPROFILE%\.claude` matched that hooks.json, before each hook id was searched for, and it stated the location as confirmed instead of "not confirmed".
- What we did: searched that hooks.json for each full id, then the plugin's `scripts` folder for the Bash one, and added this entry. The entry below stays as written. Why: the logging rule keeps mistakes in the log and corrects them with a later entry.
- Outstanding: None.

### 2026-10-18: Logging rule for small events

- What happened: The user added a rule to AGENTS.md (Documentation Rules). Activity log entries, and memory entries that record a problem or a decision, include small events: a failed command, a skipped check, a tool or prompt that behaved unexpectedly, a setting changed. Each entry states what happened, why it happened ("not confirmed" for a guess), and what we did, step by step, with why that was the best option. Mistakes stay in, and past entries are not rewritten.
- Why: the user asked for it and gave no reason.
- What we did:
  1. Read AGENTS.md, docs/context.md, docs/memory.md, docs/todo.md and docs/activity-log.md. The activity log was read to line 487 of 607: the file is over the Read tool's 25,000-token cap, and the rest holds entries from 2026-10-14 and older, which this change does not touch.
  2. Checked what reads the edited files. CLAUDE.md imports AGENTS.md (`@AGENTS.md`). `evals/harness/eval.py` (`set_floors`) reads docs/memory.md and takes a line that contains "floor" and a metric key as the reason for a looser floor. The new memory text has no such line, so floors read the same.
  3. AGENTS.md, Documentation Rules: added the rule in the user's words as the last two bullets, its three parts as a numbered list.
  4. Narrowed the bullet above it, "Do not log trivial modifications.", to typo and formatting fixes, and stated that the small events are never trivial. Reason: unchanged, it contradicted the new rule, since a changed setting could pass for trivial.
  5. docs/activity-log.md: the header said "Skip trivial changes" (the same contradiction); it now names small events and the same exception. The entry format replaces Changes with What happened, Why and What we did, and keeps Files, Validation and Outstanding. Earlier entries keep the old fields and stay as written.
  6. docs/memory.md: a second entry format, for problems and decisions, with the three parts; a new entry corrects or supersedes an old one, which stays as written.
  - Why this was the best option: AGENTS.md is read first in every session, and new entries get copied from the two formats, so the rule, the headers and the formats now agree, and no past entry was touched.
- Files: AGENTS.md, docs/activity-log.md, docs/memory.md
- Validation: re-read the edited sections. No Prettier check applies: `.prettierignore` skips `*.md`. No tests ran: only Markdown changed, and none of the lines `set_floors` reads.
- Outstanding: None. Nothing is committed.

### 2026-10-18: GateGuard hook stopped the first Bash command and the first edit of each file

- What happened: The session's first Bash command (print `.prettierignore`, grep the Prettier wiring, `git status --short`) did not run. Later the first edits of AGENTS.md, docs/memory.md and docs/activity-log.md, sent together, were all refused, and none was applied. Each time a PreToolUse hook answered "[Fact-Forcing Gate]" and asked for facts first: for Bash, the current request and what the command does; for an edit, the files that import the file, the functions affected, the structure of any data files it reads or writes, and the user's instruction verbatim.
- Why: the ecc plugin's GateGuard hooks, `pre:bash:gateguard-fact-force` and `pre:edit-write:gateguard-fact-force`, from `%USERPROFILE%\.claude\plugins\cache\ecc\ecc\2.2.3\hooks\hooks.json`. They gate the first Bash command of a session and the first edit of each file. They belong to the user's global Claude Code setup; this repo has no `.claude` folder.
- What we did: stated the facts in the reply, then sent the same calls again, and they ran. Answering the edit gate turned up a real reader of docs/memory.md, `evals/harness/eval.py` (the entry above, step 2). Nothing was disabled, though the hooks offer `ECC_GATEGUARD=off`, `ECC_DISABLED_HOOKS` and `GATEGUARD_EXEMPT_GLOBS`. Why: the gate costs one reply per file, it caught a dependency worth checking, and the setting belongs to the user's global setup, not this project.
- Outstanding: None.

### 2026-10-17: Step 7, CI/CD built locally

- Changes:
  - `.github/workflows/ci.yml`: checks, unit, integration, secrets and images jobs, plus `ci-result`, the one required check.
  - `publish.yml`: GHCR pushes, guarded against `workflow_run` from pull requests and forks.
  - `eval.yml`: off until `EVAL_ENABLED` is true; a weekly ci subset; a quota stop fails; no Claude.
  - `.github/actions/setup`, `scripts/ci_env.sh`, `infra/docker/python.Dockerfile` (agent, executor, sim), `infra/docker/gateway.Dockerfile`, `.dockerignore`.
  - Makefile: oasdiff gets `safe.directory`.
  - Actions are pinned by SHA to releases at least 14 days old; base images by digest.
  - The agent, the eval harness, the prompt and the scenarios are untouched; the exam is parked.
- Files: .github/ (actions/setup/action.yml, workflows/ci.yml, publish.yml, eval.yml), scripts/ci_env.sh, infra/docker/, .dockerignore, Makefile, docs/
- Validation (no exam server or container up):
  - actionlint passes on all workflows, shellcheck included.
  - All four images build: agent and gateway stop on missing config with their one-line error, and executor and sim start.
  - Passing make targets: `lint`, `typecheck`, `test`, `build`, `contracts-check`, `scenarios-check`, `contracts-breaking`.
  - gitleaks finds nothing in the full history (11 commits) or the new files.
  - `make test-int` was not run: it shares the exam's Postgres, ElasticMQ and volumes. The test PR runs it.
- Outstanding: the GitHub steps in docs/todo.md, the test PR, branch protection on `ci-result`; after the exam, local `make test-int` and `EVAL_ENABLED`. Nothing is committed.

### 2026-10-17: Test split on Gemini, day 1, and the Prettier ignore lines

- Changes:
  - `.prettierignore` skips the frozen scenario YAMLs (`evals/scenarios/**/*.yaml`) and the eval reports (`evals/reports/`) rather than reformat them, since reformatting would change the scenarios' sha256. The config-protection hook blocked the agent's edit; the user added the lines.
  - The test split started on the frozen prompt: infra, sim, gateway, then the agent with `AGENT_RELOAD= AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false DAILY_AI_TOKEN_BUDGET=3000000`, then `make eval SPLIT=test AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false`.
  - docs/todo.md holds the exact resume steps.
- Files: .prettierignore (the user's edit), docs/ (todo.md, context.md, memory.md, activity-log.md), evals/README.md
- Validation:
  - With the lines in `.prettierignore`, `make lint` passes (exit 0): ESLint, `prettier --check .`, `ruff check .` and `ruff format --check .`.
  - Test split: 9 of 75 runs done, all on gemini-3.5-flash-lite, then exit 2 at dns-partition-2 run 1, which parked with no steps when the UTC day's token budget ran out (3,050,302 of 3,000,000 tokens; the Claude dev pass had used 1.59 million of them). Gemini had answered 230 of its 260 requests since midnight Pacific; every call in the run was answered.
  - Gateway, web, agent and sim stopped by process tree, no process left on their ports; `make infra-down`, with the `pgdata` and `elasticmq-data` volumes kept.
- Outstanding: 66 runs, about 3 quota days, from the resume steps in docs/todo.md; then the floors and the Claude comparison. Nothing is committed.

### 2026-10-17: Claude Haiku 5.5 as the first fallback, and its dev split run

- Changes:
  - `agent/providers.py`: `AnthropicProvider` on the official anthropic SDK (1.15.0, added to the agent), native tool use with a forced `tool_choice` and parallel calls off, one cache breakpoint on the system prompt (caching it and the tool definitions), `max_retries=0`, explicit key and base URL, spans pinned to `metadata_only`. Errors: a 402 `billing_error`, a 400 naming a spend or usage limit, or a 429 without retry-after raise `CreditsExhausted`; a 429 with retry-after `RateLimited`; a refusal is not retried. Usage counts cache reads and writes into the input.
  - `agent/llm.py`: the chain Gemini, then Claude (`LLM_CLAUDE_FALLBACK`, default false), then the free fallback, for turns and for summaries; Claude's 429 with retry-after goes back to the job to retry. `spent()` replaces `Limiter.spent`: Claude from `LLM_CLAUDE_UNTIL` (2026-11-10) on, RPD, Google's daily quota, and a `credits_exhausted` call this month (UTC), with a warning log line. The limiter refuses a spent role before any request.
  - `agent/settings.py`, `.env.example`, `Makefile`: provider `anthropic`, `LLM_CLAUDE_FALLBACK|MODEL|RPM|UNTIL`, `ANTHROPIC_API_KEY` (passed to the agent alone), and `AGENT_RELOAD` for `make dev-agent`.
  - Agent migration 0009 (expand only): outcome `credits_exhausted`, `model_calls.cache_write_tokens` (docs/migrations.md).
  - Harness: per-model billing in `evals/pricing.json` (Haiku $0.10 in, $0.01 cache reads, $0.125 cache writes, $0.50 out, `credits`); `cost_billed` gated at $0 on the free setup only; reports on other models named `...-<model>` and never written to the README; `estimate()` counts only runs on the pinned primary model.
  - The dev split once with `claude-haiku-5-5` pinned for the agent and summaries, on the frozen prompt, without tuning.
- Files: services/agent (pyproject.toml, src/agent/providers.py, llm.py, model.py, settings.py, main.py, db.py, job.py, migrations/versions/0009_model_calls_credits.py, tests/test_providers.py, test_llm.py, test_agent_api.py, test_workers_int.py), uv.lock, evals/harness (eval.py, score.py, run.py, test_eval_score.py), evals/pricing.json, evals/reports/2026-10-17-dev-full-1-2b91d452-claude-haiku-5-5.{json,md}, evals/README.md, Makefile, .env.example, docs/
- Validation:
  - Tests for each case: 402, a 400 naming the spend limit and a 429 without retry-after raise `CreditsExhausted`; a 429 with retry-after raises `RateLimited` with its delay, and the router asks Claude again; credits gone move the turn to the free fallback with a warning, and a spent Claude is never asked; the chain order; evals never reach Claude; summaries go to Claude while the small model is rate limited; Claude is spent from its end date; a `credits_exhausted` call holds parked work until the month ends (integration); the key and URL win over the environment; the free chain is the default and Claude needs its key.
  - Python unit tests 400 passed; `make test-int`: Jest 33 and pytest 41 passed; ruff and pyright clean. `make migrate` took the dev database to 0009.
  - Haiku dev pass (`reports/2026-10-17-dev-full-1-2b91d452-claude-haiku-5-5.md`), exit 0: top-1 0.80, grounding 0.80, action correctness 0.80 (do-nothing 0.53), 0.57 with an acceptable action, 0 forbidden, validity 1.00, tool calls median 10, tokens median 109,442, p95 24 s. All 169 calls answered; one 5,036-token cache write, 846,048 tokens read from the cache; no input above its bound. 11 of 15 runs reached the token reserve. Credits: $0.0971 ($0.0065 per investigation).
  - Test-split estimates (no calls): Gemini about 795 requests (10.6 per investigation), 3 full quota days plus a few requests; Haiku about 846 calls, 1.7 hours, about $0.49.
- Outstanding: the test split on Gemini, floors, then the Haiku comparison on the test split before 2026-11-10 (docs/todo.md). Gateway, web, agent and sim are stopped and the compose infra is down. Nothing is committed.

### 2026-10-17: Step 6b, change 2's dev pass and the prompt freeze

- Changes:
  - Stack restarted in the order the parked run needs: sim, bad-deploy-1's saved world (its anchor and seed, through `run.resume`), gateway, then the agent with `DAILY_AI_TOKEN_BUDGET=3000000` on its command line only and no reloader. The resumed run recorded prompt sha `e0f86d4ad3b47b1c` on every new call.
  - Change 2's dev pass finished: 13 runs and the resumed one, 129 requests, all answered.
  - Change 1 won on every accuracy metric, so `prompt.py` and `model.py` went back to it by undoing change 2's six edits (taken from the previous session's transcript; no commit held change 1), which gives its sha `2b91d4523fb028a6` byte for byte. `test_the_prompt_is_frozen` pins the full sha; `evals/prompt-log.md` logs the choice.
  - The ci subset scored on the frozen prompt from cached runs, with no model calls.
- Files: services/agent/src/agent (prompt.py, model.py), evals/prompt-log.md, evals/harness/test_eval_score.py, evals/reports (2026-10-17-dev-full-1-e0f86d4a, 2026-10-17-dev-ci-1-2b91d452), evals/README.md, docs/
- Validation:
  - Change 2 (`reports/2026-10-17-dev-full-1-e0f86d4a.md`), eval exit 1 on the forbidden-proposal gate: top-1 0.53, grounding 0.80, action correctness 0.60, 0.29 with an acceptable action, 2 forbidden proposals, validity 0.97, degraded top-1 0.00; change 1 had 0.73, 0.93, 0.73, 0.43, 0, 0.98 and 0.50.
  - ci subset on the frozen prompt (`reports/2026-10-17-dev-ci-1-2b91d452.md`): top-1 0.62, grounding 0.88, action correctness 0.75 (do-nothing 0.50), 0 forbidden.
  - The source's prompt sha is `2b91d4523fb028a67e6a0ec69a7542f7de6b4102eefc592ee5a9a99f143bdad1`, equal to the cached change-1 runs' key.
- Outstanding: none for tuning; the prompt is frozen.

### 2026-10-16: Quota stop, measured RPD and the per-run prompt check (no quota used)

- Changes:
  - `agent/llm.py`: `Limiter.spent()` also returns true once the provider has answered "daily quota spent" for the model since midnight Pacific (`EXHAUSTED_TODAY`). Parked investigations, resumed ones included, stay unresumable until the quota day resets; the sweeper stops re-sending them, and `make eval` stops with exit 2.
  - `LLM_PRIMARY_RPD` defaults to 260 (settings and `.env.example`), under the 275 requests measured on 2026-10-16, not a documented limit.
  - The agent computes its own prompt sha (`agent.job.prompt_sha`, the function `eval.py` used before) and stores it on every model call: `model_calls.prompt_sha`, agent migration 0008, expand only. `eval.problems()` refuses to file a run whose calls name another prompt than the source's ("the agent ran X, not the source's prompt Y: restart it"). Calls from before 0008 carry no sha and count only beside calls that name the prompt, so the parked bad-deploy-1 run can resume.
  - `eval.estimate()` takes the RPD default from the agent's settings.
- Files: services/agent/src/agent (llm.py, job.py, db.py, settings.py), services/agent/migrations/versions/0008_model_calls_prompt_sha.py, services/agent/tests/test_workers_int.py, evals/harness (eval.py, run.py, test_eval_score.py), .env.example, docs/ (migrations.md, context.md, memory.md, todo.md), evals/README.md
- Validation:
  - `make test`: Python 381 passed, with new tests for the prompt check and for the exit-2 quota stop; TypeScript 4 of 4 tasks passed.
  - `make test-int`: Jest 33 and pytest 40 passed, the new quota test included. A provider "daily quota spent" row makes a parked investigation unresumable whether resumed or new, and it is resumable again once the row is a day old.
  - `make migrate`: the dev database is at agent 0008.
  - ruff and pyright are clean. The prompt sha is unchanged by the move (`e0f86d4ad3b47b1c`).
- Outstanding: after midnight Pacific, restart the agent, finish change 2's dev pass, choose and freeze a prompt (docs/todo.md).

### 2026-10-16: Step 6b, dev split baseline and prompt tuning (stopped on the provider's daily quota)

- Changes:
  - Dev baseline on prompt `146b161991e14f7c`: 7 new runs; the 8 ci runs on the same prompt were reused from the cache.
  - Prompt change 1 (`2b91d4523fb028a6`, `services/agent/src/agent/prompt.py`), from the baseline's dev failures. It adds:
    - config and flag changes to look for;
    - a timeline rule;
    - follow the chain and check the named component;
    - reading resource metrics;
    - when to propose and when not to;
    - the service named is the failing component.
  - Prompt change 2 (`e0f86d4ad3b47b1c`), from pass 1's dev failures:
    - a check before `submit_report` (timeline, the component named, proposals);
    - a description on `submit_report`'s `service` field (`agent/model.py`);
    - evidence ids without brackets;
    - one LogQL selector, with no "or";
    - rollout lines in the logs when `list_deploys` is down.
  - Both changes are logged in `evals/prompt-log.md` with their dev evidence. Every ci miss is a dev scenario, since the ci subset is dev only.
  - `eval.py`: reports other than the full test split carry the prompt sha's first 8 characters, since the second dev run of the day had overwritten the first. The day's reports were renamed to match.
  - The agent ran with `DAILY_AI_TOKEN_BUDGET=3000000`, passed on the command line, because each dev pass uses about 0.8 million tokens. `.env` and the default are unchanged.
- Files: services/agent/src/agent (prompt.py, model.py), evals/harness/eval.py, evals/prompt-log.md, evals/reports (2026-10-16-dev-full-1-146b1619, -2b91d452, 2026-10-16-dev-ci-1-146b1619), evals/README.md, docs/
- Validation:
  - Dev baseline: top-1 0.53, top-3 0.53, grounding 0.73, action correctness 0.60 (do-nothing 0.53), 0.14 with an acceptable action, 1 forbidden proposal, validity 1.00. Tool calls median 10, tokens median 50,411, p95 144 s.
  - Change 1: top-1 0.73, top-3 0.73, grounding 0.93, action correctness 0.73, 0.43 with an acceptable action, 0 forbidden, validity 0.98. Tool calls median 8, tokens median 53,465, p95 112 s. Exit 0.
  - Change 2: 2 of 15 runs (bad-config-1 and -2, both right on cause and action) before gemini-3.5-flash-lite answered "daily quota spent" at 10:12 UTC.
  - Tokens: 409,595 for the 7 baseline runs, 834,452 for pass 1, 114,648 for pass 2 so far. That is 1,358,695 since midnight Pacific, and 1,717,116 for the UTC day with the ci run, which matches `agent.ai_usage`. Billed $0.
  - `pytest services/agent evals/harness/test_eval_score.py`: 72 passed; ruff and pyright clean.
- Outstanding:
  - Finish change 2's dev pass after midnight Pacific: bad-deploy-1 is parked and resumes first.
  - Three new todo items: a parked quota-spent investigation loops every 15 s and makes the eval exit 3; `LLM_PRIMARY_RPD` is above the observed cap of 275; the eval cannot tell when the agent runs an older prompt.
  - Gateway, web, agent and sim were stopped by process tree, with no listener or worker left. Nothing is committed.

### 2026-10-16: Step 6b, v1 config timing, refreeze and the first ci run

- Changes:
  - Scenario set v1, unfrozen before any eval run had used it:
    - A harmless config or flag change 2 to 6 minutes before the alert in 16 scenarios, one dev and one test per family, 4 of them in the ci subset. It is the newest change in each. A red herring names it, and turning off a harmless flag is forbidden (11 flags); config pushes (5) are herrings only, since no allowlisted action reverts one. `Draft.harmless_change` writes them.
    - Causal changes start 4.5 to 30 minutes before the alert. bad-config-1's flag lands at 4.5 minutes. bad-config-3's flag builds pricing's CPU up for 15 minutes before its latency follows. bad-config-5's flag covers 10% of charges for 24 minutes, failing too few to alert, then all of them.
    - In bad-config-2 and bad-config-3 the harmless change comes after the causal one.
    - Frozen again: the test now pins manifest sha256 `b81c80f4ba442427...`.
  - Test: `test_config_timing_does_not_give_the_answer_away`.
  - The ci eval ran, on prompt `146b161991e14f7c`.
- Files: evals/scenarios/v1 (generate.py, 40 YAML files, manifest.json), evals/harness/test_scenario_set.py, evals/reports/2026-10-16-dev-ci-1.json and .md (now `-146b1619`), evals/README.md, docs/
- Validation:
  - Validator: 40 scenarios checked, 0 problems.
  - `make test`: Python 379 passed; TypeScript passed. `make test-int`: TypeScript 33 and Python 39 passed. `ruff` and `pyright` are clean.
  - The new test fails when a harmless change loses its red herring or its forbidden flag action (checked on modified copies).
  - ci eval, 8 runs, all counted (exit 1, one forbidden proposal):
    - Top-1 0.62, top-3 0.62, grounding 0.75, validity 1.00.
    - Action correctness 0.62 against a do-nothing baseline of 0.50; 0.25 on the 4 scenarios with an acceptable action.
    - Tool calls median 5.5, max 14; tokens median 33,860, max 117,780; p95 time 126 s; paid-equivalent $0.0122 per investigation, billed $0.
    - noisy-neighbor-1 blamed the harmless search-results-badges flag and proposed turning it off. db-pool-exhaustion-1 named orders instead of orders-db. dns-partition-1 named payments instead of coredns. bad-config-1 and memory-leak-1 found the cause but proposed nothing.
- Outstanding:
  - The dev split, prompt tuning on it, the test split and the floors (docs/context.md, Current Development Status).
  - Gateway, web, agent and sim were left running.
  - Nothing is committed.

### 2026-10-16: Step 6b, v1 deploy timing, action baseline and freeze (ci eval blocked)

- Changes:
  - Scenario set v1, before any counted run:
    - A harmless deploy 2 to 6 minutes before the alert in 16 scenarios, one dev and one test per family, 4 of them in the ci subset. It is the newest deploy in each; its rollback is forbidden and a red herring names it. `Draft.harmless_deploy` also writes the unrelated 2-hour deploy, and `build` drops a forbidden action listed twice.
    - Causal deploys land 4.5 to 35 minutes before the alert. bad-deploy-2 and bad-deploy-4 run as canaries (`Draft.canary`, one pod of eight, below the alert threshold) for 15 and 29 minutes; bad-deploy-5 lands at 4.5 minutes.
    - bad-config-4's red herring said 502s where its log says 503.
    - v1 is frozen: a test pins the manifest's sha256, `b4eed100167fb712...`.
  - Scoring:
    - `Scored.actionable`.
    - Action correctness carries its do-nothing baseline (`Metric.do_nothing`), shown beside it in the report and the README table.
    - New metric `action_correctness_actionable` (target 0.80), in the report, the family tables and the README table. A report note explains the baseline.
  - Known gaps (traffic_surge and unknown answers, config-change timing) in evals/README.md and README.md.
- Files: evals/scenarios/v1 (generate.py, 40 YAML files, manifest.json), evals/harness (score.py, eval.py, test_eval_score.py, test_scenario_set.py), evals/README.md, README.md, docs/
- Validation:
  - Validator: 40 scenarios checked, 0 problems.
  - `make test`: Python 378 passed, with 2 new tests (deploy timing; the baseline, the actionable share and the README row text). TypeScript passed, unchanged.
  - `make test-int`: TypeScript 33 and Python 39 passed.
  - `ruff check`, `ruff format --check` and `pyright` are clean.
  - Generated data: the harmless deploy is the newest deploy in all 16 scenarios; causal deploys at 4.5, 6, 8, 20 and 35 minutes; 21 scenarios without an acceptable action (8 of 15 dev, 13 of 25 test, 4 of 8 ci).
- Outstanding:
  - The ci eval did not run. Port 4210 is held by an agent worker left from an earlier session: PID 20260, whose `--reload` parent, PID 28332, had exited. It answers the agent's internal route and polls the queue. A fresh agent cannot bind, and the leftover would take eval messages with old code. Stopping it was denied in this session.
  - Once it is stopped: the four stack commands, then `make eval SUBSET=ci AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false`. Then the dev and test runs as before.
  - Nothing is committed.

### 2026-10-16: Step 6b, scenario set v1 and the eval harness (eval runs pending)

- Changes:
  - Fixes before any eval run:
    - db-pool-exhaustion's timeline. The pool now fills from -9m to -6m, its warn and error lines start at -6m and assert a full pool, and the error ratio ramps from -5m.
    - The sim's admin routes now listen on their own port (`SIM_ADMIN_PORT` 4231). One process serves the data app and the admin app; only `make eval` and `make smoke` get `SIM_ADMIN_URL`.
    - A run resumed after a quota park keeps the wall-clock time it had left: `_start` moves `started_at` forward by the time parked.
  - Validator: `sim.validate` checks timelines and whether labels can be met (CLI, `make scenarios-check`, and a test over every set).
  - Scenario schema: YAML, with alert rule, log asserts, `down`, and labels (alternatives, onset, signals, acceptable and forbidden actions). Ten metrics were added to the sim catalog and the prompt.
  - Scenario set v1:
    - 40 scenarios in 8 families, from `generate.py`, pinned by a sha256 manifest.
    - 15 dev and 25 test, with a ci subset of one per family and 6 degraded scenarios.
    - Each carries an unrelated deploy about 2 hours before, a loud error log, baseline series and bystanders.
    - The 6a JSON scenarios were removed; v1 holds their successors.
  - Harness: `run.py` (the real path), `score.py` and `eval.py`, with `make eval`, `thresholds.json`, `pricing.json`, `prompt-log.md`, the reports and the README block.
    - Deterministic scores, overall and per family, with 95% cluster bootstrap intervals.
    - The run cache is keyed on the prompt, the models, the scenario and the run index, and resumes across quota days.
    - Floors follow the shared rules, with the known-miss exception for efficiency (docs/memory.md).
  - An independent review of v1, read-only, before any counted run found:
    - label ambiguity in about 25 scenarios;
    - noisy-neighbor data that argued resource_exhaustion of the victim;
    - families told apart by which series and services exist;
    - an OOM model where restarts freed nothing;
    - wrong Postgres, DNS and NAT log texts;
    - action labels at odds with the runbooks.
  - Fixed from the review:
    - a labelling convention in the system prompt;
    - baseline series and bystanders (coredns, a batch job) in every scenario;
    - node contention instead of victim throttling;
    - an OOM sawtooth, with the restart counter following the log;
    - corrected log texts and action labels;
    - one alternative answer, on dns-partition-2.
  - docs/todo.md:
    - added the early-hypothesis-step item, the step 10 admin-port check and the step 7 CI commands;
    - closed two sim review lows (the pool timeline, the admin credential).
- Files:
  - sim/src/sim: main.py, scenario.py, validate.py, catalog.py
  - sim/pyproject.toml (PyYAML), typings/yaml/__init__.pyi
  - sim/tests: conftest.py, test_sim_admin.py, test_sim_api.py, test_sim_scenarios.py, test_sim_limits.py, test_sim_promql.py, test_sim_logs.py, test_sim_validate.py
  - services/agent: src/agent/job.py, src/agent/prompt.py; tests/test_workers_int.py, test_ai_loop_int.py, test_sim_isolation.py
  - evals: scenarios/v1/ (generate.py, 40 YAML files, manifest.json), harness/ (run.py, score.py, eval.py, test_eval_score.py, test_scenario_set.py), thresholds.json, pricing.json, prompt-log.md, README.md
  - removed: evals/scenarios/bad-deploy.json, evals/scenarios/db-pool-exhaustion.json
  - Makefile, pyproject.toml, .gitignore, .env.example, docs/
- Validation:
  - Validator: 40 scenarios checked, 0 problems. The 6a pool timeline fails it, and every check catches its mutation.
  - Every required signal shows in the real tools' results. A cold query for every log line takes under 1.5 s. The generator rewrites the set byte for byte.
  - `make test`: Python 376 passed; TypeScript 84 passed with Turbo forced (`pnpm turbo run test --force`).
  - `make test-int`: TypeScript 33 passed; Python 39 passed in 134 s, including the new resume test.
  - `ruff check`, `ruff format --check` and `pyright` are clean.
  - Quota estimate on 2026-10-16, with 12 primary calls already used that Pacific day:
    - ci subset: about 72 calls.
    - test split, 75 runs: about 675 calls, 2 quota days at the unverified 500 requests a day.
  - Live check before the review fixes:
    - bad-config-1 resolved with 95,058 tokens in 66 s, over the 60,000-token median target.
    - bad-deploy-1 reached awaiting approval with 35,263 tokens in 15 s.
    - db-pool-exhaustion-1 was posted when the eval was stopped.
    - These ran on the old prompt (`572e32b350ec93f1`) and pre-review v1, so they do not count. Their cache entries no longer match any key.
- Outstanding:
  - The counted runs, in this order (commands in docs/context.md, Current Development Status, and evals/README.md):
    - `make eval SUBSET=ci`
    - `make eval SPLIT=dev RUNS=1`, tuning on dev only and logging each change in prompt-log.md
    - `make eval SPLIT=test ESTIMATE=1`
    - `make eval SPLIT=test`, rerun after midnight Pacific on exit 2
    - `SET_FLOORS=1` for the test and ci scopes
    - each with `AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false`
  - After the full test run: the README eval table (the harness writes it), then 6b's done check.
  - Scenario gaps for v2:
    - no traffic_surge or unknown label;
    - causal deploys always 5 to 8 minutes before the alert;
    - 21 of 40 scenarios with no acceptable action;
    - resumed runs can lose single-line evidence off the first log page.
  - Sim and gateway backlog (docs/todo.md):
    - log cursors not bound to the query;
    - an empty 200 for deploys of an unknown service;
    - no bounds on `Activation.anchor` (500 on Windows outside 1970 to 3000);
    - alert annotations not stored.
  - Nothing is committed.

### 2026-10-15: Independent review of the events consumer and the sim

- Changes:
  - Two fresh agents reviewed the gateway's agent-events consumer and `sim/`, with repros. Separate agents fixed the high and medium findings.
  - Gateway:
    - One message per receive.
    - Permanent failures quarantined to `agent-events-dlq.fifo` at once; transient ones retried in-process for up to 15 minutes without costing receives.
    - Tool-call pairing that does not depend on arrival order, and redeliveries that rewrite no row.
    - Report actions and status only from a newly inserted report; the status update skips the sweeper's locked row.
    - `events:replay` added to `make dlq-replay`.
  - Sim:
    - RE2 for every regex (`google-re2`, user decision; import checked on this PC), with rejected patterns as 400 `bad_data`.
    - A 2 s per-request time limit and a 250,000-point cap.
    - The down switch answers before parameter validation.
    - Out-of-range inputs answer 400; the docs routes are off; `per_minute` is at most 6000.
  - Agent: `get_dependencies` validates and quotes the service name (path traversal reached `/admin/state`).
  - todo item for storing Alertmanager annotations; the low findings listed in docs/todo.md.
- Files: apps/gateway/src (events, events.test, events.int.test, config, main, outbox, cli), apps/gateway/package.json, sim/src/sim (promql, logql, world, main, scenario), sim/tests (test_sim_limits, test_sim_api), sim/pyproject.toml, uv.lock, services/agent/src/agent/tools.py, services/agent/tests/test_llm.py, Makefile, .env.example, README.md, docs/
- Validation:
  - `make test` (Turbo forced) and `make test-int` pass, once each after the fixes: TypeScript 84 unit and 33 integration tests, Python 222 unit and 38 integration tests (sim 114).
  - `make lint`, `typecheck` and `contracts-check` pass.
  - Sim determinism digests are unchanged, and `(\w+\s*)+timeout` answers in 45 to 167 ms.
  - Live smoke on bad-deploy (run 2):
    - Result: `awaiting_approval`, 6 turns, 35,218 tokens, the same root cause and rollback.
    - Tool latencies: get_alert_context 8 ms, query_metrics 5 ms, query_logs 30 ms, list_deploys 4 ms, propose_action under 1 ms.
    - The gateway mirrored the report and 11 steps.
- Outstanding: the low findings in docs/todo.md.

### 2026-10-15: Step 6a, AI core

- Changes:
  - Provider layer: Gemini (google-genai 2.27.0) and OpenAI-compatible clients. Roles come from env vars: primary `gemini-3.5-flash-lite`, fallback `deepseek-ai/deepseek-v4.1-flash` on NIM, small `gemini-3.1-flash-lite`. A 429 or a spent day falls back outside evals. An RPM and RPD limiter keeps each role under its limits, and `model_calls` records every request. `AI_ENABLED` now defaults to false.
  - Tools with pydantic arguments, results cited by id:
    - `get_alert_context` reads the new gateway internal route with the agent's token.
    - `query_metrics`, `query_logs` (later pages summarized), `list_deploys` and `get_dependencies` read the sim.
    - `search_runbooks` uses fastembed nomic with task prefixes, over 4 new runbooks.
    - `propose_action` writes only a pending row.
  - Agent loop:
    - It stops at the report, 15 tool calls, 5 minutes or the token reserve, then forces a final report.
    - 150,000 tokens is a hard cap, enforced by an input bound on every call.
    - The report is structured, with evidence ids, missing evidence and confidence ceilings enforced in code.
  - Delivery: ReportRecorded v1 (spec-first, codegen in both languages, contract tests). The agent outbox relays to `agent-events.fifo`, and a gateway consumer fills the mirror tables, pairing tool calls by id. ActionProposed v1 is marked unused in a union file.
  - Sim: a deterministic scenario engine (Prometheus, Loki-style and deploy APIs, a down switch), plus scenarios bad-deploy and db-pool-exhaustion. The sim and the gateway side were built by forked agents and checked through their tests and the live smoke run, not line by line.
  - Harness runner `evals/harness/run.py` (`make smoke`) and Makefile targets `embed-model` and `runbooks`. Provider keys are unexported except for `dev-agent`.
- Files: contracts/ (report-recorded.v1, action-proposed union, gateway.yaml 0.4.0), packages/ts-clients (codegen, contract tests), packages/py-clients (AlertContextClient, tests), services/agent (0007, db, settings, model, providers, llm, prompt, tools, runbooks, outbox, job, main, tests), apps/gateway (0004, schema, events, internal, investigations, config, main, tests), sim/, evals/scenarios, evals/harness/run.py, runbooks/, infra/elasticmq.conf, Makefile, pyproject.toml, uv.lock, .env.example, README.md, docs/
- Validation:
  - `make test` (Turbo forced) and `make test-int` passed three runs in a row: Python 189 unit and 38 integration tests; TypeScript 83 unit and 27 integration tests.
  - `make lint`, `typecheck`, `contracts-check` and `contracts-breaking` pass.
  - The hard-cap, degraded-tool and pagination tests pass.
  - Live smoke with `AI_ENABLED=true` on bad-deploy:
    - Result: `awaiting_approval` after 6 Gemini turns and 35,090 tokens, every turn's input under its bound.
    - Report: `bad_deploy` on checkout at 0.95, with a rollback to v1.7.3 proposed.
    - The gateway mirrored the report and 11 steps.
  - Live probe: deepseek-v4.1-flash answered with a tool call. nemotron-3-super gave none, kimi-k3 is end of life, glm-5.3 timed out.
- Outstanding: docs/todo.md (6b, `FASTEMBED_CACHE_PATH` in `.env`, AI Studio limits, ActionProposed v1, outbox replay, alert annotations).

### 2026-10-15: Step 5 tests

- Changes:
  - Test harness (user decisions: scratch databases, not Testcontainers):
    - Scratch databases are named `triage_test_<unix seconds>_<run>_<id>` and dropped after each test.
    - A stale cleanup drops only names in that exact format, over an hour old.
    - Both harnesses repoint the database URL variables at a database that does not exist, and refuse a dev database name with the prefix.
    - `db.int.test.ts` and `test_infra_int.py` moved off the dev database.
  - Gateway fingerprints: `fingerprint()` ports Prometheus's `LabelSet.Fingerprint`, and the webhook deduplicates on it and logs mismatches. The contract example's made-up fingerprint was fixed, and a real Alertmanager 0.28.1 payload was captured as a fixture.
  - Gateway decisions: the real decision store (`decisions.ts`), with ActionApproved through the outbox (new `queue` column, `ACTIONS_QUEUE`, `actions.fifo` in ElasticMQ). The relay keeps each FIFO group in order after a failed send. A store failure answers 503.
  - Gateway API: X-API-Key (`GATEWAY_API_KEY`) on every route except the probes, the webhook and approve or reject. `GET /v1/investigations` with keyset pagination (limit 25 by default, 1 to 100). The web helper sends the key.
  - Executor (user decisions): `executions` table (Drizzle 0003), with a foreign key to approved decisions only.
    - Claim and result routes sit on a separate listener (`GATEWAY_INTERNAL_PORT` 4201, 127.0.0.1) behind `EXECUTOR_SERVICE_TOKEN`; the sweeper logs stuck claims.
    - `executor.run.run_action` checks the allowlist, claims (idempotent, fail closed) and refuses when the approved action differs from the message. A dry run records a plan.
    - `py_clients.ExecutionsClient`.
  - The gateway refuses to start when two of its four credentials are equal.
  - Agent: an investigation stops at 15 tool calls or 150,000 tokens with a partial report (`Report.partial`).
  - Migrations: agent 0001 sets `lock_timeout`; docs/migrations.md marks 0005's downgrade as a maintenance-window step. Gateway API 0.3.0; clients regenerated.
  - `.env.example` gains `ACTIONS_QUEUE`, `GATEWAY_API_KEY`, `GATEWAY_INTERNAL_HOST`, `GATEWAY_INTERNAL_PORT` and `EXECUTOR_SERVICE_TOKEN`. Random values for the two secrets were appended to `.env` without being shown.
  - New dev dependency: fast-check 4.10.2 (gateway). The executor now depends on the workspace `py-clients` and on httpx2, which was already locked.
- Files:
  - apps/gateway:
    - src: app.ts, config.ts, decisions.ts (new), executions.ts (new), intake.ts, internal.ts (new), investigations.ts (new), main.ts, outbox.ts, schema.ts, sweeper.ts, testing.ts.
    - Tests: app.test.ts, intake.test.ts, internal.test.ts and investigations.test.ts (new), db.int.test.ts, decisions.int.test.ts, executions.int.test.ts and investigations.int.test.ts (new), intake.int.test.ts, sweeper.int.test.ts.
    - src/testdata/alertmanager-0.28.1-webhook.json (new); drizzle/0003_decisions.sql and meta/; package.json.
  - apps/web: src/lib (config.ts, gateway.ts, gateway.test.ts), src/app (page.tsx, readyz/route.ts).
  - Contracts and clients: contracts/openapi (gateway.yaml, examples/alertmanager-webhook.json), packages/ts-clients (src/gateway.ts, generated), packages/py-clients (clients.py, generated, tests).
  - services/agent: src/agent (job.py, model.py), migrations/versions/0001_baseline.py, tests (conftest.py, test_infra_int.py, test_parking_int.py, test_workers_int.py, test_executor_int.py (new)).
  - services/executor: src/executor/run.py (new), tests/test_executor_run.py (new), pyproject.toml.
  - Infra, config and docs: infra/elasticmq.conf, .env.example, uv.lock, pnpm-lock.yaml, README.md, docs/.
- Validation:
  - `make lint`, `typecheck`, `contracts-check` and `contracts-breaking` pass; oasdiff reports no breaking change.
  - Unit, forced past Turbo's cache (`pnpm turbo run test --force`, 0 cached): Jest 72 tests (ts-config 2, ts-clients 29, web 3, gateway 38) in 4.1 s. `uv run pytest -m "not integration"`: 80 passed in 7.2 s (8.2 s wall).
  - `make test-int`, three runs in a row, all green. pnpm runs `test:int` directly, never through Turbo, so nothing was replayed.
    - Run 1: Jest 23/23 in 5.9 s, pytest 30 in 102.6 s, 1 m 51 s, storm p99 79.7 ms.
    - Run 2: Jest 23/23 in 5.6 s, pytest 30 in 101.1 s, 1 m 49 s, storm p99 78.8 ms.
    - Run 3: Jest 23/23 in 5.7 s, pytest 30 in 101.3 s, 1 m 49 s, storm p99 80.1 ms.
    - The partition-swap tests (`test_partitioning_int.py`, 3) and the upcaster and migration tests (`test_migrations_int.py`, 3) are among the 30.
  - Mutation check: the old `::timestamptz` cursor cast failed 2 of 3 keyset tests. That bug was real: postgres.js turns the parameter into a JS Date and drops the microseconds, so a page skipped the rest of a millisecond tie group.
  - A first full run failed the parking storm test: its 40 alerts had identical labels, which now correctly dedupe into one. They got distinct labels.
- Review fixes (independent review: 0 high, 1 medium, 5 low):
  - Fixed, low:
    - The executor runs nothing that differs from the approved message.
    - A cursor naming an impossible instant gets 400, not 500.
    - The four credentials must differ.
    - The relay keeps FIFO-group order after a failed send (new test).
    - The stale cleanup ignores names it did not make.
  - Not changed, medium: key guessing is not throttled, because each invented credential gets its own rate-limit count. This predates the step and is now part of the Funnel item in docs/todo.md.
- Outstanding:
  - The executor's queue consumer and effectors, and the gateway's ActionProposed consumer.
  - Keeping the internal listener off Funnel (step 10); alerts on stuck executions (step 8).
  - Degraded-tool and `query_logs` tests (step 6). All are in docs/todo.md.

### 2026-10-15: Step 4 follow-up, parking and recovery sweeper

- Changes:
  - Agent:
    - A message that must wait for a tenant slot or model budget parks its investigation and is deleted (Alembic 0006: `tenant_id`, `waiting_reason`, `waiting_since`). Only a duplicate delivery still returns to SQS as "busy".
    - `Job.resumable`: budget waiters once model calls are back; tenant-cap waiters first parked first, never more than the free slots read from `pg_locks`.
  - Agent internal API (`agent/api.py`): `GET /internal/investigations/{id}/state` behind `AGENT_SERVICE_TOKEN` (401 problem details with `WWW-Authenticate: Bearer`); every error is problem details.
  - Gateway recovery sweeper (`sweeper.ts`):
    - Every `SWEEPER_INTERVAL_SECONDS`, over queued groups without an outbox row (SKIP LOCKED), it re-queues through the outbox resumable parked investigations and stuck ones: unknown to the agent, or no step for `SWEEPER_STUCK_AFTER_MINUTES`.
    - Each re-queue has its own deduplication id. Groups take finished statuses.
    - Drizzle 0002 adds `requeued_at` and `requeue_count`. `investigationRequest` in `intake.ts` builds the message for intake and sweeper.
  - Contract: agent API 0.3.0 (`waiting_for`, `waiting_since`, `resumable`); codegen.
  - Config: `.env.example` gains `AGENT_SERVICE_TOKEN`, `AGENT_URL` and `SWEEPER_*`. A random `AGENT_SERVICE_TOKEN` was appended to `.env`, value not shown.
  - The gateway's integration helpers moved to `src/testing.ts`.
- Files:
  - services/agent: src/agent/api.py (new), job.py, consumer.py, db.py, settings.py, main.py; migrations/versions/0006_waiting.py (new); tests: test_parking_int.py (new), test_workers_int.py, test_consumer.py, test_agent_api.py.
  - apps/gateway: src/sweeper.ts, sweeper.test.ts, sweeper.int.test.ts and testing.ts (new); intake.ts, config.ts, main.ts, schema.ts, intake.int.test.ts; drizzle/0002_sweeper.sql and meta/ (new).
  - Contracts and clients: contracts/openapi/agent.yaml; packages/ts-clients and packages/py-clients generated files; packages/py-clients/tests/test_clients.py.
  - Config and docs: .env.example, docs/.
- Validation:
  - `make lint`, `typecheck`, `contracts-check` and `contracts-breaking` pass. oasdiff: no breaking change in agent.yaml.
  - `make test` passed 3 runs in a row: pytest 62, Jest 18 in the gateway; the TypeScript suites were forced past Turbo's cache.
  - `make test-int` passed 3 runs in a row: Jest 7, pytest 26, 104 to 115 s, storm p99 109.7, 95.0 and 81.3 ms.
  - Done-when checks:
    - (1) Two agents with the real gateway process: 5 investigations under a cap of 2. Three parked, each ran after one re-queue; no DLQ entry or dead letter.
    - (2) A budget-parked investigation stayed parked through three sweeps and ran once the budget reset.
    - (3) Queue loss: each stuck group was re-enqueued exactly once, and a second sweep re-enqueued nothing.
    - (4) The internal API answers 401 without the token, with a wrong one, or with another scheme.
  - Dev database migrated to agent 0006 and gateway 0002 (`make migrate`); `alembic check` clean. drizzle-kit generated only the two new columns, so `schema.ts` had not drifted.
- Not changed: migration 0001 still sets no `lock_timeout`, and `queued_budget` stays in the enum until its contract step. Both are in docs/todo.md.

### 2026-10-15: Step 4 workers

- Changes:
  - Contracts: InvestigationRequested v2 adds `tenant_id` for the per-tenant cap, and its description fixes MessageGroupId and MessageDeduplicationId as SHA-256 hashes of tenant and group key (and window start) (user decision). The union accepts v1 (deprecated) and v2. `gateway.yaml` describes per-tenant webhook tokens, rate limiting, the 200-alert limit and a 413. Codegen regenerated.
  - Gateway:
    - Per-tenant webhook tokens (user decision): `tenants.webhook_token_sha256` and `pnpm tenant:token`.
    - `POST /v1/alerts`: auth in `preParsing`, fingerprint dedupe, 5-minute windows opened by the first new alert, 202 `{group_id, deduped}`.
    - A transactional outbox with a relay to `investigations.fifo` and `outbox:replay` for parked rows.
    - @fastify/rate-limit keyed by credential hash, 1 MB, 200 alerts and a 10 s request timeout. Problem titles now come from the status code.
    - `buildApp` is async. Drizzle migration 0001. New dependencies: @fastify/rate-limit 11.2.1 and @aws-sdk/client-sqs 3.1150.0.
  - Agent:
    - An asyncio consumer: 20 s long poll, 5 in flight, visibility heartbeat every 30 s, graceful shutdown that leaves the message.
    - A resumable job: two steps per tool call, one transaction per step with its tool_calls row, tool retries with jitter, model retries honoring Retry-After.
    - A per-tenant cap with advisory locks, a budget guard (`AI_ENABLED`, `DAILY_AI_TOKEN_BUDGET`, `ai_usage`), poison messages to `dead_letters`, and `python -m agent.dlq replay`.
    - A placeholder `ScriptedModel` until step 6. Alembic 0005.
  - `make dlq-replay`; `.env.example` and README updated; the ElasticMQ probe's queue fixtures moved to `conftest.py`.
- Files: contracts/ (messages/investigation-requested*.json, openapi/gateway.yaml), packages/ts-clients (src/generated/, src/contracts.test.ts), packages/py-clients (src/py_clients/generated/, tests/test_contracts.py), apps/gateway (src/app.ts, intake.ts, outbox.ts, cli.ts, main.ts, config.ts, schema.ts, app.test.ts, db.int.test.ts, intake.int.test.ts, drizzle/0001_workers.sql, drizzle/meta/, package.json), services/agent (src/agent/consumer.py, job.py, model.py, dlq.py, main.py, settings.py, db.py, migrations/versions/0005_workers.py, tests/conftest.py, test_consumer.py, test_elasticmq_int.py, test_workers_int.py), pnpm-lock.yaml, Makefile, .env.example, README.md, docs/
- Validation:
  - After the review fixes: `make lint`, `typecheck`, `test` (Jest 50, pytest 54), `test-int` (Jest 6, pytest 23, about 85 s), `contracts-check` and `contracts-breaking` pass.
  - Done-when checks:
    - (1) `test_shutdown_mid_investigation_resumes_at_the_next_step`: every tool ran once across the restart. `test_killed_mid_tool_call_repeats_only_that_call`: only the interrupted call ran twice.
    - (2) The storm test over 8 group keys: p99 86.4, 86.5 and 86.7 ms over 3 runs; 8 groups, 5 investigations, 5 queued messages; 400 of 400 alerts stored.
    - (3) `test_a_message_that_fails_five_times_lands_in_the_dlq`: 5 model calls, then the DLQ, and the investigation is `failed`; after `replay_dlq` it resolves.
  - The gateway integration and consumer unit tests passed 3 runs in a row.
  - A first `maxItems: 200` failed `contracts-breaking` (`request-property-max-items-set`), so the limit moved to the description.
  - Live run on the dev stack after `make migrate`:
    - `tenant:token` issued a token. The sample webhook got 202 with `RateLimit-*` headers, its repeat got `deduped: true`, and a wrong token got 401.
    - The relay queued the message, and the agent resolved the investigation with the placeholder report in 1 step.
    - Both queues ended empty, with no error in either log.
  - The live run came before the review fixes.
- Review fixes (independent review: 1 high, 2 medium and 4 low findings):
  - High: `failed` counted as finished, so a message replayed from the DLQ was deleted without running. `failed` now resumes, and the DLQ test replays its message to resolution (the separate replay test, which used a fresh message, is gone).
  - Medium: nothing capped investigations per storm, and the storm test's 3 keys could not exceed 5. The gateway now starts at most `MAX_NEW_INVESTIGATIONS` (5) per tenant in any 5 minutes and keeps further groups `queued_not_investigated`, under a per-tenant lock. The storm runs over 8 keys.
  - Low:
    - A new alert that arrives resolved no longer opens an investigation.
    - One receive's later messages of a FIFO group go back instead of running in parallel (new unit test).
    - `AGENT_MAX_IN_FLIGHT` is capped at 10, the SQS receive maximum.
    - Request and report steps no longer put the ToolRequest or report JSON and the model latency into `result` and `latency_ms`, which StepRecorded v3 defines as the tool's. Arguments now live in the `tool_calls` row written with the request (0005 makes its latency nullable).
  - Not changed (medium): budget waiters keep their slots, so resumed investigations can wait behind them until the budget frees. Documented with a `ponytail:` note and in docs/memory.md.
- Outstanding: the recovery sweeper; trust-proxy addressing behind Tailscale Funnel; StepRecorded publishing and the report message (step 6). All are in docs/todo.md.

### 2026-10-14: StepRecorded v3 non-empty summary

- Changes: `step-recorded.v3.schema.json` adds `minLength: 1` to `summary`, and the stored `StepRecordedV2` model adds `min_length=1` (user decision). Both test tables gained the boundary cases: an empty summary is rejected, one character is allowed. Codegen regenerated.
- Files: contracts/messages/step-recorded.v3.schema.json, packages/py-clients/src/py_clients/generated/messages/step_recorded_v3_schema.py, packages/ts-clients (src/generated/messages.ts, src/contracts.test.ts), services/agent (src/agent/steps.py, tests/test_steps.py), docs/
- Validation:
  - Before the change, no stored event in the dev database had an empty or missing summary: 0 of 14,735 v1 and 0 of 7,132 v2 rows, the shortest being 16 characters. `triage` is the only database.
  - `make lint`, `typecheck`, `test` (Jest 42, pytest 45), `test-int` (Jest 2, pytest 15), `contracts-check` and `contracts-breaking` pass. The v1 and v2 files still match their sha256 and have no diff against HEAD.
  - Mutations failed the tests: `min_length` removed from the stored model (Python), and `minLength` removed from v3 (Python and TypeScript).
- Outstanding: None.

### 2026-10-14: StepRecorded v3 limits

- Changes:
  - `step-recorded.v3.schema.json` (still unreleased) caps `summary` at 2,000 characters (`maxLength`, counted in code points) and requires `latency_ms` and both token counts to be at least 0 (user decision). The v1 and v2 files are unchanged.
  - The stored `StepRecordedV2` model and its `TokenUsage` enforce the same limits, so an event that breaks one cannot be built or written.
  - `record_step` now builds the v3 message before inserting, so an event that skipped validation raises before anything is stored.
  - Codegen regenerated.
  - The user added `**/drizzle/meta/` to `.prettierignore`. The memory note about running Prettier after `db:generate` is closed, and so is its todo item.
- Files: contracts/messages/step-recorded.v3.schema.json, packages/py-clients/src/py_clients/generated/messages/step_recorded_v3_schema.py, packages/ts-clients (src/generated/messages.ts, src/contracts.test.ts), services/agent (src/agent/steps.py, tests/test_steps.py, tests/test_migrations_int.py), docs/
- Validation:
  - `make lint`, `typecheck`, `test` (Jest 40, pytest 43), `test-int` (Jest 2, pytest 15), `contracts-check` and `contracts-breaking` pass. The v1 and v2 files still match their sha256 and have no diff against HEAD.
  - No stored event on the dev database breaks a limit: the longest summary is 145 characters, with no negative values.
  - Each limit has accept and reject cases at its boundary, including 2,000 four-byte emoji, so Python and ajv count characters the same way.
  - Mutations failed the tests: `ge=0` removed from the stored latency, and `maxLength` removed from v3 (Python and TypeScript).
- Outstanding: v3 still allows an empty summary, which v1 and v2 messages rejected.

### 2026-10-14: StepRecorded v3 message

- Changes:
  - `contracts/messages/step-recorded.v3.schema.json` (user decision):
    - The payload is the stored StepRecorded v2 event field for field, with the same nullability and no extra limits.
    - Around it are `schema_version` 3, `investigation_id`, `seq` and `recorded_at`.
    - Two examples: with latency and tokens, and with both unknown.
  - The union `step-recorded.schema.json` accepts v1, v2 and v3, and marks v2 `"deprecated": true`, with a description, beside its `$ref`. The released v1 and v2 files are unchanged.
  - Codegen added `StepRecordedV3` in both languages; both StepRecorded unions are now v1, v2 and v3.
  - `agent.steps.step_message` builds the v3 message from a stored event, and `record_step` returns it, so the first producer (the agent loop, step 6) publishes v3.
  - Contract tests:
    - TypeScript accepts each version through the union, pins v2 as the only deprecated branch, adds v3 to the enum parity check, and probes version 4 as unknown.
    - Python validates each version through the union.
    - Agent tests check that the v3 payload fields equal the stored v2 fields, and that stored events (upcast v1 included) become v3 messages unchanged.
- Files: contracts/messages (step-recorded.v3.schema.json, step-recorded.schema.json), packages/ts-clients (src/generated/messages.ts, src/contracts.test.ts), packages/py-clients (src/py_clients/generated/messages/, tests/test_contracts.py), services/agent (src/agent/steps.py, tests/test_steps.py), docs/
- Validation:
  - `make lint`, `typecheck`, `test` (Jest 32, pytest 35), `test-int` (Jest 2, pytest 14), `contracts-check` and `contracts-breaking` pass.
  - The v1 and v2 files match their sha256 from before the change and have no diff against HEAD.
  - Mutations failed the tests: an extra field on the stored v2 model (field parity), and `step_message` dropping `tokens` (round trip).
- Outstanding: v3 has no tool call status for the API's `Step.tool_call`, and adding `**/drizzle/meta/` to `.prettierignore` is a manual step; both are in docs/todo.md.

### 2026-10-14: Step 3 data

- Changes:
  - Gateway schema in Drizzle (`apps/gateway/src/schema.ts`, `drizzle/0000_init.sql`):
    - `tenants`, `services` and `service_dependencies`.
    - `alert_groups`: a status enum that includes `queued_not_investigated`, and a CHECK tying that status to a null `investigation_id`.
    - `alerts`: one row per firing.
    - The `proposed_actions` copy, and `approvals` keyed by `action_id`.
    - Keyset indexes `(tenant_id, created_at DESC NULLS FIRST, id DESC NULLS FIRST)`.
  - Agent schema: SQLAlchemy 2 models (`agent/db.py`) and plain-SQL Alembic revisions 0001 to 0004. `env.py` gained the autogenerate filters, `search_path=public` and one transaction per revision.
  - Story A:
    - 0002 expand.
    - The `STEPS_DUAL_WRITE` flag, with the dual-write in `record_step`.
    - The resumable backfill CLI.
    - 0003, which checks the counts and then swaps the names, leaving a compat view.
    - 0004 contract.
    - The partition job in the agent: at startup, then daily.
  - Story B: stored StepRecorded v1 and v2 models, the upcaster chain and `replay`, pinned by a golden snapshot.
  - Seed: `scripts/seed.py` (`make seed`), with Faker and a fixed seed, writing through both roles, with `--reset` and `--large`. Faker joined the root dev group.
  - Tests:
    - A scratch-database fixture.
    - Both chains from empty, plus the seed and `alembic check`.
    - A downgrade round trip that carries a row.
    - The partition story under a writer at 2,000,000 steps, plus two guard tests.
    - The golden replay.
- Files: apps/gateway (src/schema.ts, drizzle/, drizzle.config.ts, package.json), services/agent (src/agent/db.py, steps.py, partitions.py, settings.py, main.py; migrations/env.py, migrations/versions/; tests/conftest.py, test_steps.py, test_migrations_int.py, test_partitioning_int.py, golden/), scripts/seed.py, Makefile, pyproject.toml, uv.lock, .env.example, README.md, docs/
- Validation:
  - `make lint`, `typecheck`, `test` (Jest 27, pytest 29), `test-int` (Jest 2, pytest 14, about 60 s), `build` and `contracts-check` pass.
  - Dev database:
    - `make migrate` applied both chains to the empty schemas; running it again is a no-op.
    - The gateway schema was reset once: the first apply had a truncated 66-character foreign key name, and the schema held only that apply's empty tables.
    - `make seed` took 2.6 s. A second run refused without `--reset`, and `--reset` reproduced identical rows (checksums).
    - EXPLAIN of the keyset queries shows index-only scans with no sort.
  - The agent started against the dev database with the partition thread: `/readyz` ok, no errors.
  - Mutations each failed the tests: an upcaster writing 0 for an unknown latency, 0003 without the compat view, and dual-write left on past 0004.
- Review fixes (independent review: 0 high, 3 medium, 7 low findings):
  - `python -m agent.partitions ensure` crashed: `extra={"created": ...}` collides with a LogRecord attribute, which also rolled back the partitions. The key is renamed, and the partition test runs the CLI.
  - 0002 and its downgrade now set `lock_timeout` (the foreign key locks `investigations`).
  - 0004's downgrade copies the rows back, so the chain downgrades with data present.
  - 0004 refuses to drop while the old table holds a row the new one lacks; a test covers it.
  - 0003 takes the partition job's advisory lock. Without it, the swap, an attach and a writer could deadlock.
  - Revisions now commit one at a time; the docs say to upgrade a live database one revision at a time.
  - The partition job retries hourly after a failure, and the ATTACH lock wording is corrected.
  - The test's backfill gets a CLI-like engine, and the numbering assertion can no longer pass on an empty result.
  - Seeded groups now start at their window start, so no alert predates its group.
  - Not changed: per-month savepoints in `ensure_partitions`, and reading the backfill's date range from `investigations`. The hourly retry and the CLI's 60 s timeout cover both; a `ponytail:` comment marks the range scan.
- Outstanding: the stored step events and the StepRecorded queue messages have different v2 shapes; map them before step 6. The decision store still needs the outbox (step 4); both are in docs/todo.md. `.prettierignore` is unchanged because the config-protection hook blocks it, so run Prettier after `db:generate`.

### 2026-10-14: Approver credential

- Changes:
  - New required env var `APPROVER_API_KEY`, for the gateway (verifies) and the web server (sends). It is in `.env.example`, and the Makefile passes it only to `dev-ts`.
  - `gateway.yaml` has an `approverKey` scheme (`X-Approver-Key`) on approve and reject, and a 403 response for other credentials.
  - The gateway serves both routes with the approver check, request validation and problem+json errors, through an injected decision store that is a not-found placeholder until step 3.
  - `createGatewayClient` no longer types the decision routes; `createDecisionClient` and the web's `decideAction` handle them. Next transpiles `@triage/ts-clients`. The gateway's DB integration test no longer loads the full config.
  - The reviewer's probe queue was already gone: ElasticMQ holds only `investigations.fifo` and its DLQ.
- Files: contracts/openapi/gateway.yaml, apps/gateway/src (app.ts, app.test.ts, config.ts, main.ts, db.int.test.ts), apps/web (next.config.ts, src/lib/config.ts, decisions.ts, decisions.test.ts), packages/ts-clients/src (gateway.ts, contracts.test.ts), services/agent/tests/test_agent_api.py, services/executor/tests/test_executor_api.py, sim/tests/test_sim_api.py, packages/py-clients/tests/test_clients.py, Makefile, .env.example, docs/
- Validation:
  - Tests:
    - Approve with only `X-API-Key` gets 403, and so does the Alertmanager token.
    - A missing or wrong approver key gets 401 with the challenge.
    - The approver key gets 200 for approve and reject.
    - A bad body or id gets 400; unknown and decided actions get 404 and 409.
    - Agent, executor and sim settings hold no approver key, even when it is set in their environment.
    - The Python clients contain no `/v1/actions` path.
  - `make lint`, `typecheck`, `test` (Jest 27, pytest 25), `build`, `test-int`, `contracts-check` and `contracts-breaking` pass.
  - Live gateway on 4200: `X-API-Key` and bearer got 403, a wrong key 401, the right key 404 (placeholder store), all problem+json, and the key never reached the log. Without the key the gateway exits with `APPROVER_API_KEY is required`.
  - Make probes under sh and cmd.exe: a plain target does not see the key, even from the calling shell; the dev-ts export line does.
  - gitleaks: no leaks in the files with placeholder keys.
- Outstanding: Add `APPROVER_API_KEY` to the local `.env`, since the gateway and web now refuse to start without it. The other credentials and the decision store are in docs/todo.md.

### 2026-10-14: Step 2 contracts

- Changes: Wrote the contracts spec-first. Gateway OpenAPI 3.1 adds `/v1` alerts, investigations, approve and reject, with RFC 9457 errors, rate-limit headers and three security schemes. The agent internal API adds cancel and state. Added versioned message schemas with examples (StepRecorded v1, v2 and a union), and the GraphQL schema with a Relay connection and an `Investigation` subscription. Codegen now produces openapi-fetch clients with timeouts, ajv message validation and web typed documents (TypeScript), plus pydantic models in `py_clients.generated` and httpx2 clients (Python). Codegen wipes its output first. Added `make contracts-check` and `make contracts-breaking` (oasdiff in Docker), and contract tests on both sides. The agent now imports `InvestigationRequestedV1`.
- Files: contracts/, packages/ts-clients (scripts/codegen.ts, src/), packages/py-clients (scripts/codegen.py, src/py_clients/, tests/), apps/web/src/graphql, apps/web/src/generated, apps/web/src/lib/gateway.ts, apps/gateway/src/app.ts and app.test.ts, services/agent (imports), scripts/contracts_check.py, Makefile, pyproject.toml, README.md, docs/
- Validation: `make lint`, `typecheck` (pyright 0 errors), `test` and `build` pass. `make contracts-check` and `make contracts-breaking` pass from Git Bash and from PowerShell (cmd.exe). Negative checks failed as intended: a hand-edited generated file and an unregenerated schema edit failed `contracts-check`, and removing `/readyz` failed `contracts-breaking` (`api-path-removed-without-deprecation`). Real-socket check: `fetchWithTimeout` passes POST bodies and aborts with `TimeoutError`. `make test-int` passes (Jest 2, pytest 9).
- Review fixes (independent review, 0 high and 5 medium findings):
  - The MessageGroupId is the group id, since SQS rejects Alertmanager group keys.
  - The Python `GatewayClient` lost approve and reject.
  - Bad action kinds are now rejected in both languages, and a type-level enum parity check was added.
  - `contracts-breaking` now also fails on edited or deleted message versions.
  - oasdiff runs with `--network none` and only `.git` and `contracts/` mounted.
  - `/graphql` is documented as needing X-API-Key.
  - 401 responses carry `WWW-Authenticate`.
  - The response `Health` schemas allow unknown members.
  - The TS gateway client type excludes `/v1/alerts`.
- Review fix validation: everything above re-passed (Jest 21 including ts-clients 11, pytest 21) from both shells. Removing a GraphQL enum value failed `tsc` at the parity tuple. A scratch git repo confirmed the message-version rule: an edited or deleted version fails, and a new or staged file passes. The oasdiff container sees only `.git` and `contracts` and cannot resolve hosts.
- Outstanding: No service serves the new routes or enforces their credentials yet. Per-kind action parameters and the report message are open (docs/todo.md). GraphQL query resolvers are step 9. The question of a separate credential for approve and reject was settled by the approver credential entry above.

### 2026-10-14: ElasticMQ message persistence

- Changes: Enabled ElasticMQ `messages-storage` (H2) on a named volume `elasticmq-data` in both compose files; planned a recovery sweeper under step 4.
- Files: infra/elasticmq.conf, infra/docker-compose.yml, infra/docker-compose.prod.yml, docs/todo.md, docs/context.md, docs/memory.md
- Validation: A queued message survived a container restart and a recreate, with its receive count and the redrive policy intact. `make test-int` passes (Jest 2, pytest 9). The merged prod config mounts the volume and publishes no queue port.
- Outstanding: Recovery sweeper (docs/todo.md, step 4).

### 2026-10-14: Prompt 1 completion items

- Changes: Config validation moved to zod and pydantic-settings with one-line startup errors; gitleaks pre-commit hook; production compose override; ElasticMQ probe tests; turbo agent guidance turned off and its AGENTS.md block removed.
- Files: packages/ts-config, packages/py-config, apps/gateway (config, main, drizzle.config), apps/web/src/lib/config.ts, services/agent, services/executor, sim, .pre-commit-config.yaml, Makefile, pyproject.toml, infra/docker-compose.prod.yml, turbo.json, AGENTS.md, README.md, docs/
- Validation: `make lint`, `typecheck`, `test` (Jest 10, pytest 9) and `test-int` (Jest 2, pytest 9) pass. Unset `GATEWAY_DATABASE_URL` or `AGENT_DATABASE_URL` stops startup with exit code 1 and a one-line error. The hook blocked a staged fake token and passed all 116 project files. The merged prod config publishes no Postgres or ElasticMQ ports. `docker compose ps` host ports match the context.md table.
- Outstanding: None.

### 2026-10-14: Step 1 scaffolding

- Changes: Polyglot monorepo (pnpm + Turborepo TS apps and packages, uv Python services and packages), contracts v0 with codegen, pinned infra compose (Postgres + pgvector, ElasticMQ, Toxiproxy, observability profile), per-service Postgres roles, root Makefile. ElasticMQ, TypeScript 6.0.3 and ESLint + Prettier chosen by the user. Fixes from an independent review: gateway CORS and GraphiQL off, agent consumer survives handler errors, gateway DB ping timeout, SWC parses `.ts` without TSX, shared configs hashed by turbo.
- Files: Makefile, package.json, pnpm-workspace.yaml, turbo.json, pyproject.toml, .env.example, infra/, contracts/, apps/, services/, sim/, packages/, evals/, runbooks/, README.md, docs/
- Validation: `make lint`, `typecheck`, `test` (Jest 10, pytest 9), `test-int` (Jest 2, pytest 3), `build`, `migrate` and `codegen` (deterministic) pass from PowerShell and Git Bash. Compose healthy, observability profile included. Every `/healthz` and `/readyz` returns 200 under `make dev`. Traces from gateway and agent reach Jaeger.
- Outstanding: See docs/todo.md.

### 2026-10-14: Plan updated for free-tier models, production readiness, and deployment

- Changes: Switched to free-tier LLMs behind a provider switch, added production-readiness requirements, added steps 9 (review UI) and 10 (deploy).
- Files: AGENTS.md, docs/context.md, docs/memory.md, docs/todo.md
- Validation: Documentation only. No code changed.
- Outstanding: See docs/todo.md.

### 2026-10-13: Project created from template

- Changes: Copied `_project-template`, filled project-specific rules in `AGENTS.md`, added `CLAUDE.md` importing `AGENTS.md`, recorded design in `docs/context.md`, decisions in `docs/memory.md`, build plan in `docs/todo.md`.
- Files: AGENTS.md, CLAUDE.md, README.md, docs/*
- Validation: Documentation only. No code to test.
- Outstanding: None.
