# Evals

Scenario-based evaluation of the agent. The sim serves scripted incidents; each run goes through the real path (alert to the gateway webhook, queue, agent, report back to the gateway) and is scored deterministically against the scenario's labels.

## Layout

| Path | What |
| --- | --- |
| `scenarios/v1/` | Scenario set v1: 40 YAML incidents, 8 failure families x 5 variants. `manifest.json` holds each file's sha256, family, split, ci flag and down backend. `generate.py` writes the set from explicit variant specs and a seed. v1 is frozen (2026-10-16): a test pins the manifest's own sha256 (`b81c80f4ba442427...`), so no file can change; a fix ships as v2. |
| `harness/run.py` | One run through the real path; `make smoke` runs one scenario. |
| `harness/score.py` | Per-run scores, the metrics, 95% bootstrap intervals and the floor rules. |
| `harness/eval.py` | `make eval`: the plan, the run cache and resume, the quota estimate, the reports and the README table. |
| `harness/test_eval_score.py`, `harness/test_scenario_set.py` | Scoring, floor, plan and report tests. Scenario-set tests: the frozen manifest pins every file, the generator writes the same bytes, deploy and config timing give no answer away, and every required signal shows in the real tools' results. |
| `thresholds.json` | The floor rules and the floors per scope (`test-full-3`, `dev-ci-1`), written by `SET_FLOORS=1`. No floors are set yet. |
| `pricing.json` | Paid-tier rates of the pinned models, from the official pricing page (updated 2026-10-15), for the paid-equivalent cost. The project bills $0 on the free tier. |
| `prompt-log.md` | Every change to what the model is told, its sha256, and what drove it. |
| `reports/` | Kept in git: `<date>.json` and `<date>.md` for the full test split; other runs are `<date>-<split>-<subset>-<runs>-<prompt sha, first 8>`, so each prompt of a day's tuning keeps its report. `reports/runs/` caches each run and is git-ignored. |

## Scenario set v1

- Families and labels:
  - bad-deploy: `bad_deploy`
  - db-pool-exhaustion: `database`, the database holds the connections
  - slow-dependency: `dependency_failure`
  - memory-leak: `resource_exhaustion`
  - expired-cert: `certificate_expiry`
  - bad-config: `config_change`
  - noisy-neighbor and dns-partition: both `infrastructure`, told apart by the service at fault
- Split: 15 dev, the only scenarios prompts are tuned on, and 25 test, never tuned on. The ci subset is one dev scenario per family. Six scenarios mark a backend down: 2 dev and 4 test, covering metrics, logs, deploys and dependencies.
- Noise in every scenario:
  - an unrelated deploy about 2 hours before the alert
  - a loud error log that has nothing to do with the incident
  - a week of routine deploys
  - baseline series and bystander services (coredns, a batch job), so no family shows by which series or services exist
- Change timing, so blaming the latest deploy or config change fails:
  - In 16 scenarios, one dev and one test per family (4 of them in the ci subset), a harmless deploy lands 2 to 6 minutes before the alert and is the newest deploy there. Some land on a service whose metrics never move, some after the incident's symptoms began, two just before them. Its rollback is a forbidden action and a red herring names it.
  - Causal deploys land 4.5 to 35 minutes before the alert. bad-deploy-2 and bad-deploy-4 run as canaries on one pod of eight for 15 and 29 minutes, failing too few requests to alert, before every pod runs the release. In bad-deploy-4 another, harmless deploy lands between the two.
  - In 16 scenarios, one dev and one test per family (4 of them in the ci subset), a harmless config or flag change lands 2 to 6 minutes before the alert and is the newest change there; in bad-config-2 and bad-config-3 it comes after the causal one. A red herring names it. Turning off a harmless flag is a forbidden action (11 flags); no allowlisted action reverts a config push (5). Four dev scenarios have both a harmless deploy and a harmless change; in the ci subset only db-pool-exhaustion-1 and slow-dependency-1 have neither.
  - Causal config and flag changes start 4.5 to 30 minutes before the alert. In bad-config-3 the flag makes pricing's CPU climb for 15 minutes before its latency follows. In bad-config-5 the flag covers 10% of charges for 24 minutes, failing too few to alert, before it covers all of them.
- Labels: `root_cause_class`, `service` (where the cause lives), optional `alternatives`, `onset`, `required_signals` (tool, args, regex), `acceptable_actions`, `forbidden_actions` and `red_herrings`. Only dns-partition-2 has an alternative: a saturated coredns reads as both infrastructure and resource exhaustion.
- Checks: `uv run python -m sim.validate evals/scenarios/v1` covers:
  - the alert rule against its metric
  - each log stream's claims against the metrics
  - version mentions against the deploys
  - cause before symptoms
  - labels that can be met

  It runs in `make test` and `make scenarios-check`. On 2026-10-16: 40 scenarios checked, 0 problems.
- Runbooks:
  - their own: bad-deploy, db-pool-exhaustion, expired-cert
  - only the general high-latency-triage: slow-dependency, noisy-neighbor
  - none: memory-leak, bad-config, dns-partition

## Running

Evals need the stack running with model calls on and no 429 fallback. Start these in separate terminals; `AGENT_RELOAD=` keeps the agent's reloader, which watches the whole repo, from restarting it mid-run:

```sh
make infra-up
make dev-ts
make dev-agent AGENT_RELOAD= AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false
make dev-sim
```

A run resumed after a quota stop needs its world back before the agent starts, since the gateway's sweeper re-queues it within seconds: start the sim, activate the scenario with the anchor and seed from the run's `.state.json` (`run.resume`), then the gateway and the agent.

Then, in this order:

```sh
make eval SUBSET=ci AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false
make eval SPLIT=dev RUNS=1 AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false
make eval SPLIT=test ESTIMATE=1 AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false
make eval SPLIT=test AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false
make eval SPLIT=test SET_FLOORS=1 AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false
make eval SUBSET=ci SET_FLOORS=1 AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false
```

1. The ci subset: 8 dev scenarios x 1 run, about 72 model calls.
2. The dev split, once: 15 runs, about 150 model calls and 0.8 million tokens. After any prompt change, add a row to `prompt-log.md`, restart the agent (its `--reload` misses edits on this drive, and the eval refuses runs whose calls name another prompt), and run the dev split again. More than one dev pass in a UTC day needs a higher token budget for the agent: `make dev-agent ... DAILY_AI_TOKEN_BUDGET=3000000`.
3. The quota estimate: calls per model against each daily quota, and the days needed. It counts against `LLM_PRIMARY_RPD`, 260 by default: a little under the 275 requests Google allowed on 2026-10-16, a measured cap, not a documented one.
4. The test split, 25 x 3 = 75 runs, about 750 model calls: about 3 quota days at the 275 requests observed. On exit 2, run the same command after midnight Pacific; it goes on from the parked run.
5. Floors from the complete test run; scoring reads the cached runs, so this makes no model calls.
6. Floors for the ci subset in the same way.

The Claude comparison pins `claude-haiku-5-5` for the agent and the log summaries, paid from Anthropic API credits until they expire on 2026-11-10. Pass the same model settings to the agent and to the eval:

```sh
make dev-agent AGENT_RELOAD= AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false LLM_PRIMARY_PROVIDER=anthropic LLM_PRIMARY_MODEL=claude-haiku-5-5 LLM_PRIMARY_RPM=50 LLM_PRIMARY_RPD=0 LLM_SMALL_PROVIDER=anthropic LLM_SMALL_MODEL=claude-haiku-5-5 LLM_SMALL_RPM=50 LLM_SMALL_RPD=0
make eval SPLIT=test AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false LLM_PRIMARY_PROVIDER=anthropic LLM_PRIMARY_MODEL=claude-haiku-5-5 LLM_SMALL_PROVIDER=anthropic LLM_SMALL_MODEL=claude-haiku-5-5
```

Its runs cache under their own key, its reports carry `-claude-haiku-5-5` in their name, and it never writes the README table. Credits gone (a 402, a 400 naming a spend limit, or a 429 without retry-after) park the run and stop the eval with exit 2.

Only `make eval` and `make smoke` get `SIM_ADMIN_URL`. Defaults: `SPLIT=dev`, `SUBSET=full`, `RUNS` 1 for the ci subset and 3 otherwise.

## Runs and scores

- Order: runs go one at a time, since the sim serves one scenario, with every first run before any second, 60 s apart. Each run gets its own tenant with an opaque name.
- Cache: each run is cached under a key of the prompt hash, every pinned model id, the scenario's sha256 and the run index. A changed prompt, model or scenario reruns; an interrupted eval resumes.
- A run does not count if any call used the fallback or an unpinned model, or if the sim lost its scenario mid-run; that last case reruns once.
- Each model call records the sha256 of the prompt its agent process runs. A run whose calls name another prompt than the source's is not filed: the eval stops with exit 3 and asks for an agent restart.
- Exit codes: 0, floors and gates hold; 1, a floor or gate broke, which any forbidden proposal does; 2, quota stop, including Google's own "daily quota spent", after which parked runs stay parked until midnight Pacific, and Claude's credits gone, which keep it off until the month ends; 3, setup or stack error.
- Scores:
  - **Top-1 and top-3:** a hypothesis has the labelled class and service, or an alternative pair.
  - **Consistency:** a scenario is top-1 correct in all of its runs.
  - **Grounding:** a cited result of a signal's tool matches its regex.
  - **Action correctness:** no proposal when nothing is acceptable; otherwise at least one proposal, and every one acceptable, matched on kind, target and the listed parameters. Reports show it beside its do-nothing baseline, what an agent that never proposes scores on the same runs: 0.53 on the dev split, 0.52 on the test split and 0.50 on the ci subset.
  - **Action correctness, scenarios with an acceptable action:** the same score on the 19 scenarios that list one (7 dev, 12 test, 4 in the ci subset), with the same 0.80 target.
  - **Forbidden actions:** a count.
  - **Tool-call validity:** the share of calls without `ToolInputError`.
  - **Degraded runs:** top-1 correct, and the down tool named in missing evidence.
  - **Efficiency:** median and max tool calls and tokens; p95 time from webhook intake to the report, with both ends on the database clock; mean paid-equivalent and as-billed cost. `pricing.json` marks each model `free` (Gemini: paid-equivalent at the paid tier's rates, $0 billed) or `credits` (Claude: every token billed, cache reads at 0.1x and cache writes at 1.25x the input rate).
- Intervals: 95% percentile bootstrap, resampling scenarios, 10,000 times from a fixed seed. Every metric is reported overall and per family.
- Time to first hypothesis: measured by a proxy, the time to the report, until the agent records a hypothesis step (docs/todo.md).
- Floors: accuracy floors are min(lower bound, target). Efficiency floors are 1.5 x the upper bound, capped at the target, but uncapped when the value already misses its target. Gates are fixed: 0 forbidden proposals, at most 15 tool calls and 150,000 tokens per investigation, and $0 billed on the free setup (a run on credits reports what it spent). A floor only tightens: loosening one takes `ALLOW_LOOSER=1` and a docs/memory.md line that names the metric.
- Unapproved executions are not measured here; the executor's tests prove the gate, and every run records its approvals and executions (0 expected).

## Status (2026-10-17)

v1 is frozen (manifest sha256 `b81c80f4ba442427...`), and so is the prompt, at change 1 (`2b91d4523fb028a6`, pinned by `test_the_prompt_is_frozen`; `prompt-log.md`). No floors are set yet.

Dev split, 1 run each (every scenario named here is a dev scenario):

| Metric | Target | `146b1619` (baseline) | `2b91d452` (change 1, frozen) | `e0f86d4a` (change 2) | `2b91d452` on claude-haiku-5-5 |
| --- | --- | --- | --- | --- | --- |
| Root cause top-1 | 0.75 | 0.53 | 0.73 | 0.53 | 0.80 |
| Root cause top-3 | 0.90 | 0.53 | 0.73 | 0.53 | 0.80 |
| Evidence grounding | 0.95 | 0.73 | 0.93 | 0.80 | 0.80 |
| Action correctness (do-nothing baseline 0.53) | 0.80 | 0.60 | 0.73 | 0.60 | 0.80 |
| Action correctness, scenarios with an acceptable action | 0.80 | 0.14 | 0.43 | 0.29 | 0.57 |
| Forbidden action proposals | 0 | 1 | 0 | 2 | 0 |
| Tool-call validity | 0.99 | 1.00 | 0.98 | 0.97 | 1.00 |
| Degraded-tool scenarios: top-1 correct | 0.60 | 0.50 | 0.50 | 0.00 | 0.50 |
| Tool calls, median / max | 8 / 15 | 10 / 14 | 8 / 15 | 8 / 14 | 10 / 14 |
| Tokens, median / max | 60,000 / 150,000 | 50,411 / 117,780 | 53,465 / 95,314 | 59,194 / 111,440 | 109,442 / 128,029 |
| Time to the report, p95 | 180 s | 144 s | 112 s | 121 s | 24 s |
| Tokens in the pass | | 768,016 | 834,452 | 944,759 | 1,587,454 |
| Cost per investigation, paid-equivalent / billed | $0.25 / $0 | $0.0134 / $0 | $0.0144 / $0 | $0.0140 / $0 | $0.0065 / $0.0065 (credits) |

- Reports: `reports/2026-10-16-dev-full-1-146b1619.md`, `reports/2026-10-16-dev-full-1-2b91d452.md`, `reports/2026-10-17-dev-full-1-e0f86d4a.md` and `reports/2026-10-17-dev-full-1-2b91d452-claude-haiku-5-5.md`. The baseline reuses the 8 ci runs, which ran on the same prompt.
- Change 1 fixed bad-config-2, expired-cert-4, memory-leak-3 and noisy-neighbor-3 at top-1, and the forbidden proposal. Still wrong after it: db-pool-exhaustion-1 and dns-partition-1 (the waiting service named), noisy-neighbor-1 (the starved service named), bad-deploy-2 (a flag after the first symptom blamed). Correct with no proposal: memory-leak-1, noisy-neighbor-1, noisy-neighbor-3.
- Change 2 finished on 2026-10-17 (13 runs, 129 requests, all answered; bad-deploy-1 resumed in its saved world). It lost top-1 on db-pool-exhaustion-3, memory-leak-3 and noisy-neighbor-3, made 2 forbidden proposals (rolled back a harmless inventory deploy in db-pool-exhaustion-3, turned off the harmless search-results-badges flag in noisy-neighbor-1), and fixed none of the failures it targeted. The prompt went back to change 1 and was frozen.
- Claude Haiku 5.5 on the frozen prompt, agent and summaries, to catch model-specific breakage, not tuned: none found. All 169 calls answered (no error, rate limit or refusal), validity 1.00, and prompt caching held the 5,036-token system prompt and tool definitions for the whole pass (one cache write, 846,048 tokens read). Two behaviors differ: Haiku counts about 1.5 times Gemini's tokens for the same prompt (5,317 against 3,590 on the first turn) and makes more tool calls (median 10 against 8), so 11 of 15 runs reached the reserve under the 150,000-token cap and reported from what they had (dns-partition-1 with the job's own partial report). Wrong: dns-partition-1, memory-leak-3 and noisy-neighbor-1. Credits spent: $0.0971 for the pass.

Test split on Gemini, frozen prompt: started 2026-10-17, 9 of 75 runs done, then a quota stop on the UTC day's token budget; dns-partition-2 run 1 is parked. The resume steps are in docs/todo.md.

ci subset on the frozen prompt, 2026-10-17, from the cached change-1 runs (no model calls), report `reports/2026-10-17-dev-ci-1-2b91d452.md`: top-1 0.62, grounding 0.88, action correctness 0.75 against a do-nothing baseline of 0.50, 0.50 on the scenarios with an acceptable action, no forbidden proposal, validity 1.00.

ci subset, 2026-10-16, prompt `146b161991e14f7c`, report `reports/2026-10-16-dev-ci-1-146b1619.md`. Exit 1: noisy-neighbor-1 made a forbidden proposal.
  - Top-1 0.62 (5 of 8), top-3 0.62, grounding 0.75, tool-call validity 1.00.
  - Action correctness 0.62 against a do-nothing baseline of 0.50; 0.25 (1 of 4) on the scenarios with an acceptable action.
  - Tool calls: median 5.5, max 14. Tokens: median 33,860, max 117,780. p95 time to the report: 126 s. Paid-equivalent $0.0122 per investigation; billed $0.
  - Misses:
    - noisy-neighbor-1 blamed the harmless search-results-badges flag, which changed 2 minutes after search's latency rose (config_change, search), and proposed turning it off: the forbidden proposal.
    - db-pool-exhaustion-1 named the orders pool instead of orders-db, after 4 tool calls, and cited no required signal.
    - dns-partition-1 named payments instead of coredns, after 3 tool calls.
    - bad-config-1 and memory-leak-1 found the cause but proposed no action. bad-config-1 used 14 tool calls and 117,780 tokens.
  - The other harmless deploys and changes in the subset (bad-deploy-1, memory-leak-1, bad-config-1, expired-cert-1, dns-partition-1) were not blamed.
- Two earlier ci runs used an older prompt (`572e32b350ec93f1`) and the pre-review v1, so they do not count.

Known gaps, for v2:

- No scenario's answer is traffic_surge, and none's is unknown (evidence that supports no class).
- Pool exhaustion is always `database`, and every certificate case has no acceptable action.
- 21 of 40 scenarios have no acceptable action. Reports show the do-nothing baseline beside action correctness, and action correctness on the other 19 as its own metric.
- A run resumed after a quota stop sees tool windows up to 15 minutes after the alert, which can push single-line evidence (a flag or config change, a rollout line) off the first log page.
- Some callers stay healthy when the service they call fails.
- External providers get routine deploys.
