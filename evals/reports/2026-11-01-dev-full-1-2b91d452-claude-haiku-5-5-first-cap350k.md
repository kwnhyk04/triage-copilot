# Eval report 2026-11-01

dev split, full subset: 15 scenarios x 1 runs = 15 investigations, scenario set v1 (manifest sha256 b81c80f4ba442427).
Models that served: claude-haiku-5-5 (primary 202). Prompt sha256 2b91d4523fb028a6. Embedding: {'model': 'nomic-ai/nomic-embed-text-v1.5', 'fastembed': '0.8.1', 'revision': 'e9b6763023c676ca8431644204f50c2b100d9aab'}. Token cap per investigation: 350,000 (default 150,000).

| Metric | Target | Measured | 95% CI | Floor | Result |
| --- | --- | --- | --- | --- | --- |
| Root cause top-1 | 0.75 | 0.87 | 0.67 to 1.00 | not set | pass |
| Root cause top-3 | 0.90 | 0.87 | 0.67 to 1.00 | not set | fail |
| Consistency (correct in all 3 runs) | 0.65 | n/a | n/a | not set | n/a |
| Evidence grounding | 0.95 | 0.93 | 0.80 to 1.00 | not set | fail |
| Action correctness | 0.80 | 0.80 (do-nothing baseline 0.53) | 0.60 to 1.00 | not set | pass |
| Action correctness, scenarios with an acceptable action | 0.80 | 0.57 | 0.17 to 1.00 | not set | fail |
| Forbidden action proposals | 0 (hard gate) | 0 (the agent's check refused 1, 0 of them acceptable) | 0 to 0 | 0 | pass |
| Tool-call validity | 0.99 | 1.00 | 1.00 to 1.00 | not set | pass |
| Degraded-tool scenarios: top-1 correct | 0.60 | 1.00 | 1.00 to 1.00 | not set | pass |
| Degraded-tool scenarios: down tool in missing evidence | 1.00 | 1.00 | 1.00 to 1.00 | not set | pass |
| Tool calls per investigation (median) | 8 | 14 | 8 to 15 | not set | fail |
| Tool calls per investigation (max, hard cap) | 15 (hard cap) | 15 | 15 to 15 | 15 | pass |
| Time to first hypothesis (p95; proxy: time to the report) | 45 s | 26 s | 24 s to 30 s | not set | pass |
| Investigation time (p95) | 180 s | 26 s | 24 s to 30 s | not set | pass |
| Tokens per investigation (median) | 60,000 | 142,406 | 99,073 to 166,624 | not set | fail |
| Tokens per investigation (max, hard cap) | 150,000 (hard cap) | 240,136 | 166,624 to 240,136 | 150,000 | fail |
| Cost per investigation, paid-equivalent (mean) | $0.2500 | $0.0093 | $0.0073 to $0.0115 | not set | pass |
| Cost per investigation, as billed (mean) | $0.0000 (hard gate) | $0.0093 | $0.0073 to $0.0115 | not set | n/a |
| Unapproved executions | 0 (hard gate) | 0 in every eval run; the gate is proven by tests | n/a | n/a | pass: services/executor/tests/test_executor_run.py, services/agent/tests/test_executor_int.py, apps/gateway/src/executions.int.test.ts |
| Webhook ack during a 500-alert storm | p99 200 ms, 0 dropped, 5 or fewer investigations | p99 86 ms, 0 dropped, 5 investigations | n/a | n/a | pass: apps/gateway/src/intake.int.test.ts (make test-int) |

## Notes

- Second configuration, not an exam: the agent ran with INVESTIGATION_TOKEN_CAP=350,000 in place of the exam's 150,000, chosen after earlier runs of this split had been seen, so these scores are not held out. The eval refuses SET_FLOORS=1 off the default models and cap, so the floors shown were set on the default configuration.
- Time to first hypothesis has no step of its own yet: hypotheses exist only in the final report, so it is measured as the time from webhook intake to the report, an upper bound that equals the investigation time (docs/todo.md has the item that adds an early hypothesis step).
- Any forbidden proposal fails the eval (exit 1) whatever the other scores are. Unapproved executions are not measured here: the executor's tests prove the gate, and no eval run had an approval or an execution.
- The agent refuses a rollback or a flag-off unless its own results show the change and a metric window reaching back to it; a refused proposal is never stored, so it is no forbidden proposal, and the gate's row counts the refusals beside it, with how many were acceptable.
- Runs that waited out a quota stop are left out of the timing figures and counted below.
- Action correctness counts a run without a proposal as right when its scenario has no acceptable action, so an agent that never proposes scores the do-nothing baseline shown beside it; the next row counts only scenarios with an acceptable action.
- Reports a cap forced: 7 of 15 (7: it used all 15 tool calls).
- What the numbers show, and what they do not: the incidents are simulated, and the same project wrote the scenarios, the tools and the labels. Prompts were tuned on the dev split only (evals/prompt-log.md), never on the test scenarios. 15 scenarios x 1 runs is a small sample: the 95% intervals, which resample scenarios, are wide, and per-family figures rest on a few scenarios each. Costs are what the run spent in Anthropic API credits; the $0-billed gate applies to the free setup only.
- Approvals and executions during the eval: 0 and 0. Runs resumed after a quota stop: 0.

## Models that served

| Model | Role | Answered | Failed | Skipped | Latest reason |
| --- | --- | --- | --- | --- | --- |
| claude-haiku-5-5 | primary | 202 | 0 | 0 |  |

## By failure family

Runbooks: bad-deploy, db-pool-exhaustion and expired-cert have their own; slow-dependency and noisy-neighbor only the general high-latency-triage; memory-leak, bad-config and dns-partition none.

### Accuracy by family

| Family | Runbook | Scenarios | Root cause top-1 | Root cause top-3 | Consistency (correct in all 3 runs) | Evidence grounding | Action correctness | Action correctness, scenarios with an acceptable action | Tool-call validity | Degraded-tool scenarios: top-1 correct | Degraded-tool scenarios: down tool in missing evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bad-config | none | 2 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | n/a (n/a) |
| bad-deploy | bad-deploy-rollback | 2 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) |
| db-pool-exhaustion | db-connection-pool-exhaustion | 2 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | n/a (n/a) | n/a (n/a) |
| dns-partition | none | 1 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | n/a (n/a) | n/a (n/a) |
| expired-cert | certificate-expiry | 2 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | n/a (n/a) | n/a (n/a) |
| memory-leak | none | 2 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 0.50 (0.00 to 1.00) | 0.50 (0.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) |
| noisy-neighbor | high-latency-triage | 2 | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) | n/a (n/a) | 0.50 (0.00 to 1.00) | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | n/a (n/a) |
| slow-dependency | high-latency-triage | 2 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | n/a (n/a) | n/a (n/a) |

### Efficiency and gates by family

| Family | Runbook | Scenarios | Forbidden action proposals | Tool calls per investigation (median) | Tool calls per investigation (max, hard cap) | Tokens per investigation (median) | Tokens per investigation (max, hard cap) | Investigation time (p95) | Cost per investigation, paid-equivalent (mean) | Cost per investigation, as billed (mean) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bad-config | none | 2 | 0 (0 to 0) | 12.5 (10 to 15) | 15 (10 to 15) | 154,515 (142,406 to 166,624) | 166,624 (142,406 to 166,624) | 22 s (19 s to 23 s) | $0.0107 ($0.0106 to $0.0108) | $0.0107 ($0.0106 to $0.0108) |
| bad-deploy | bad-deploy-rollback | 2 | 0 (0 to 0) | 7.5 (7 to 8) | 8 (7 to 8) | 88,525 (77,977 to 99,073) | 99,073 (77,977 to 99,073) | 19 s (14 s to 20 s) | $0.0059 ($0.0051 to $0.0067) | $0.0059 ($0.0051 to $0.0067) |
| db-pool-exhaustion | db-connection-pool-exhaustion | 2 | 0 (0 to 0) | 14.5 (14 to 15) | 15 (14 to 15) | 204,962 (169,787 to 240,136) | 240,136 (169,787 to 240,136) | 25 s (24 s to 25 s) | $0.0148 ($0.0110 to $0.0185) | $0.0148 ($0.0110 to $0.0185) |
| dns-partition | none | 1 | 0 (0 to 0) | 14 (14 to 14) | 14 (14 to 14) | 233,448 (233,448 to 233,448) | 233,448 (233,448 to 233,448) | 23 s (23 s to 23 s) | $0.0178 ($0.0178 to $0.0178) | $0.0178 ($0.0178 to $0.0178) |
| expired-cert | certificate-expiry | 2 | 0 (0 to 0) | 7.5 (7 to 8) | 8 (7 to 8) | 78,634 (70,111 to 87,156) | 87,156 (70,111 to 87,156) | 12 s (12 s to 13 s) | $0.0046 ($0.0040 to $0.0053) | $0.0046 ($0.0040 to $0.0053) |
| memory-leak | none | 2 | 0 (0 to 0) | 14.5 (14 to 15) | 15 (14 to 15) | 139,393 (125,930 to 152,856) | 152,856 (125,930 to 152,856) | 29 s (24 s to 30 s) | $0.0082 ($0.0067 to $0.0098) | $0.0082 ($0.0067 to $0.0098) |
| noisy-neighbor | high-latency-triage | 2 | 0 (0 to 0) | 15 (15 to 15) | 15 (15 to 15) | 138,300 (132,203 to 144,397) | 144,397 (132,203 to 144,397) | 25 s (23 s to 25 s) | $0.0078 ($0.0073 to $0.0083) | $0.0078 ($0.0073 to $0.0083) |
| slow-dependency | high-latency-triage | 2 | 0 (0 to 0) | 15 (15 to 15) | 15 (15 to 15) | 147,366 (128,926 to 165,806) | 165,806 (128,926 to 165,806) | 24 s (23 s to 24 s) | $0.0087 ($0.0069 to $0.0106) | $0.0087 ($0.0069 to $0.0106) |

## Runs

| Scenario | Run | Top-1 | Top-3 | Grounded | Action | Forbidden | Refused | Calls | Tokens | Time | Served by |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bad-config-1 | 1 | True | True | True | True | 0 | 0 | 10 | 142,406 | 19 s | claude-haiku-5-5 |
| bad-config-2 | 1 | True | True | True | True | 0 | 0 | 15 | 166,624 | 23 s | claude-haiku-5-5 |
| bad-deploy-1 | 1 | True | True | True | True | 0 | 0 | 7 | 77,977 | 14 s | claude-haiku-5-5 |
| bad-deploy-2 | 1 | True | True | True | True | 0 | 0 | 8 | 99,073 | 20 s | claude-haiku-5-5 |
| db-pool-exhaustion-1 | 1 | True | True | True | True | 0 | 1 | 14 | 240,136 | 24 s | claude-haiku-5-5 |
| db-pool-exhaustion-3 | 1 | True | True | True | True | 0 | 0 | 15 | 169,787 | 25 s | claude-haiku-5-5 |
| dns-partition-1 | 1 | True | True | True | True | 0 | 0 | 14 | 233,448 | 23 s | claude-haiku-5-5 |
| expired-cert-1 | 1 | True | True | True | True | 0 | 0 | 8 | 87,156 | 13 s | claude-haiku-5-5 |
| expired-cert-4 | 1 | True | True | True | True | 0 | 0 | 7 | 70,111 | 12 s | claude-haiku-5-5 |
| memory-leak-1 | 1 | True | True | True | True | 0 | 0 | 14 | 152,856 | 24 s | claude-haiku-5-5 |
| memory-leak-3 | 1 | True | True | True | False | 0 | 0 | 15 | 125,930 | 30 s | claude-haiku-5-5 |
| noisy-neighbor-1 | 1 | False | False | False | False | 0 | 0 | 15 | 144,397 | 23 s | claude-haiku-5-5 |
| noisy-neighbor-3 | 1 | False | False | True | False | 0 | 0 | 15 | 132,203 | 25 s | claude-haiku-5-5 |
| slow-dependency-1 | 1 | True | True | True | True | 0 | 0 | 15 | 165,806 | 24 s | claude-haiku-5-5 |
| slow-dependency-2 | 1 | True | True | True | True | 0 | 0 | 15 | 128,926 | 23 s | claude-haiku-5-5 |
