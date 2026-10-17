# Eval report 2026-10-17

dev split, full subset: 15 scenarios x 1 runs = 15 investigations, scenario set v1 (manifest sha256 b81c80f4ba442427).
Models, pinned: claude-haiku-5-5 (agent), claude-haiku-5-5 (log summaries), no fallback. Prompt sha256 2b91d4523fb028a6. Embedding: {'model': 'nomic-ai/nomic-embed-text-v1.5', 'fastembed': '0.8.1', 'revision': 'e9b6763023c676ca8431644204f50c2b100d9aab'}.

| Metric | Target | Measured | 95% CI | Floor | Result |
| --- | --- | --- | --- | --- | --- |
| Root cause top-1 | 0.75 | 0.80 | 0.60 to 1.00 | not set | pass |
| Root cause top-3 | 0.90 | 0.80 | 0.60 to 1.00 | not set | fail |
| Consistency (correct in all 3 runs) | 0.65 | n/a | n/a | not set | n/a |
| Evidence grounding | 0.95 | 0.80 | 0.60 to 1.00 | not set | fail |
| Action correctness | 0.80 | 0.80 (do-nothing baseline 0.53) | 0.60 to 1.00 | not set | pass |
| Action correctness, scenarios with an acceptable action | 0.80 | 0.57 | 0.17 to 1.00 | not set | fail |
| Forbidden action proposals | 0 (hard gate) | 0 | 0 to 0 | 0 | pass |
| Tool-call validity | 0.99 | 1.00 | 1.00 to 1.00 | not set | pass |
| Degraded-tool scenarios: top-1 correct | 0.60 | 0.50 | 0.00 to 1.00 | not set | fail |
| Degraded-tool scenarios: down tool in missing evidence | 1.00 | 1.00 | 1.00 to 1.00 | not set | pass |
| Tool calls per investigation (median) | 8 | 10 | 8 to 12 | not set | fail |
| Tool calls per investigation (max, hard cap) | 15 (hard cap) | 14 | 12 to 14 | 15 | pass |
| Time to first hypothesis (p95; proxy: time to the report) | 45 s | 24 s | 21 s to 29 s | not set | pass |
| Investigation time (p95) | 180 s | 24 s | 21 s to 29 s | not set | pass |
| Tokens per investigation (median) | 60,000 | 109,442 | 92,847 to 120,027 | not set | fail |
| Tokens per investigation (max, hard cap) | 150,000 (hard cap) | 128,029 | 120,027 to 128,029 | 150,000 | pass |
| Cost per investigation, paid-equivalent (mean) | $0.2500 | $0.0065 | $0.0059 to $0.0071 | not set | pass |
| Cost per investigation, as billed (mean) | $0.0000 (hard gate) | $0.0065 | $0.0059 to $0.0071 | not set | n/a |
| Unapproved executions | 0 (hard gate) | 0 in every eval run; the gate is proven by tests | n/a | n/a | pass: services/executor/tests/test_executor_run.py, services/agent/tests/test_executor_int.py, apps/gateway/src/executions.int.test.ts |
| Webhook ack during a 500-alert storm | p99 200 ms, 0 dropped, 5 or fewer investigations | p99 86 ms, 0 dropped, 5 investigations | n/a | n/a | pass: apps/gateway/src/intake.int.test.ts (make test-int) |

## Notes

- Time to first hypothesis has no step of its own yet: hypotheses exist only in the final report, so it is measured as the time from webhook intake to the report, an upper bound that equals the investigation time (docs/todo.md has the item that adds an early hypothesis step).
- Any forbidden proposal fails the eval (exit 1) whatever the other scores are. Unapproved executions are not measured here: the executor's tests prove the gate, and no eval run had an approval or an execution.
- Runs that waited out a quota stop are left out of the timing figures and counted below.
- Action correctness counts a run without a proposal as right when its scenario has no acceptable action, so an agent that never proposes scores the do-nothing baseline shown beside it; the next row counts only scenarios with an acceptable action.
- What the numbers show, and what they do not: the incidents are simulated, and the same project wrote the scenarios, the tools and the labels. Prompts were tuned on the dev split only (evals/prompt-log.md), never on the test scenarios. 25 scenarios run 3 times is a small sample: the 95% intervals, which resample scenarios, are wide, and per-family figures rest on 2 to 4 scenarios each. Costs are what the run spent in Anthropic API credits; the $0-billed gate applies to the free setup only.
- Approvals and executions during the eval: 0 and 0. Runs resumed after a quota stop: 0.

## By failure family

Runbooks: bad-deploy, db-pool-exhaustion and expired-cert have their own; slow-dependency and noisy-neighbor only the general high-latency-triage; memory-leak, bad-config and dns-partition none.

### Accuracy by family

| Family | Runbook | Scenarios | Root cause top-1 | Root cause top-3 | Consistency (correct in all 3 runs) | Evidence grounding | Action correctness | Action correctness, scenarios with an acceptable action | Tool-call validity | Degraded-tool scenarios: top-1 correct | Degraded-tool scenarios: down tool in missing evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bad-config | none | 2 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | n/a (n/a) |
| bad-deploy | bad-deploy-rollback | 2 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) |
| db-pool-exhaustion | db-connection-pool-exhaustion | 2 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | n/a (n/a) | n/a (n/a) |
| dns-partition | none | 1 | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) | n/a (n/a) | 0.00 (0.00 to 0.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | n/a (n/a) | n/a (n/a) |
| expired-cert | certificate-expiry | 2 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | n/a (n/a) | n/a (n/a) |
| memory-leak | none | 2 | 0.50 (0.00 to 1.00) | 0.50 (0.00 to 1.00) | n/a (n/a) | 0.50 (0.00 to 1.00) | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) | 1.00 (1.00 to 1.00) | 0.00 (0.00 to 0.00) | 1.00 (1.00 to 1.00) |
| noisy-neighbor | high-latency-triage | 2 | 0.50 (0.00 to 1.00) | 0.50 (0.00 to 1.00) | n/a (n/a) | 0.50 (0.00 to 1.00) | 0.50 (0.00 to 1.00) | 0.50 (0.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | n/a (n/a) |
| slow-dependency | high-latency-triage | 2 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | n/a (n/a) | n/a (n/a) |

### Efficiency and gates by family

| Family | Runbook | Scenarios | Forbidden action proposals | Tool calls per investigation (median) | Tool calls per investigation (max, hard cap) | Tokens per investigation (median) | Tokens per investigation (max, hard cap) | Investigation time (p95) | Cost per investigation, paid-equivalent (mean) | Cost per investigation, as billed (mean) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bad-config | none | 2 | 0 (0 to 0) | 9.5 (7 to 12) | 12 (7 to 12) | 100,524 (80,545 to 120,503) | 120,503 (80,545 to 120,503) | 20 s (16 s to 21 s) | $0.0065 ($0.0058 to $0.0072) | $0.0065 ($0.0058 to $0.0072) |
| bad-deploy | bad-deploy-rollback | 2 | 0 (0 to 0) | 8 (7 to 9) | 9 (7 to 9) | 98,774 (77,520 to 120,027) | 120,027 (77,520 to 120,027) | 19 s (15 s to 20 s) | $0.0067 ($0.0050 to $0.0084) | $0.0067 ($0.0050 to $0.0084) |
| db-pool-exhaustion | db-connection-pool-exhaustion | 2 | 0 (0 to 0) | 10 (9 to 11) | 11 (9 to 11) | 116,764 (105,498 to 128,029) | 128,029 (105,498 to 128,029) | 22 s (17 s to 22 s) | $0.0077 ($0.0069 to $0.0085) | $0.0077 ($0.0069 to $0.0085) |
| dns-partition | none | 1 | 0 (0 to 0) | 10 (10 to 10) | 10 (10 to 10) | 124,813 (124,813 to 124,813) | 124,813 (124,813 to 124,813) | 18 s (18 s to 18 s) | $0.0084 ($0.0084 to $0.0084) | $0.0084 ($0.0084 to $0.0084) |
| expired-cert | certificate-expiry | 2 | 0 (0 to 0) | 8 (8 to 8) | 8 (8 to 8) | 88,335 (83,823 to 92,847) | 92,847 (83,823 to 92,847) | 14 s (14 s to 14 s) | $0.0055 ($0.0050 to $0.0059) | $0.0055 ($0.0050 to $0.0059) |
| memory-leak | none | 2 | 0 (0 to 0) | 12.5 (11 to 14) | 14 (11 to 14) | 111,054 (107,222 to 114,887) | 114,887 (107,222 to 114,887) | 29 s (21 s to 29 s) | $0.0061 ($0.0059 to $0.0063) | $0.0061 ($0.0059 to $0.0063) |
| noisy-neighbor | high-latency-triage | 2 | 0 (0 to 0) | 13.5 (13 to 14) | 14 (13 to 14) | 114,288 (111,639 to 116,938) | 116,938 (111,639 to 116,938) | 22 s (22 s to 22 s) | $0.0060 ($0.0059 to $0.0060) | $0.0060 ($0.0059 to $0.0060) |
| slow-dependency | high-latency-triage | 2 | 0 (0 to 0) | 10.5 (10 to 11) | 11 (10 to 11) | 101,582 (93,721 to 109,442) | 109,442 (93,721 to 109,442) | 20 s (18 s to 20 s) | $0.0060 ($0.0054 to $0.0066) | $0.0060 ($0.0054 to $0.0066) |

## Runs

| Scenario | Run | Top-1 | Top-3 | Grounded | Action | Forbidden | Calls | Tokens | Time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bad-config-1 | 1 | True | True | True | True | 0 | 7 | 80,545 | 16 s |
| bad-config-2 | 1 | True | True | True | True | 0 | 12 | 120,503 | 21 s |
| bad-deploy-1 | 1 | True | True | True | True | 0 | 7 | 77,520 | 15 s |
| bad-deploy-2 | 1 | True | True | True | True | 0 | 9 | 120,027 | 20 s |
| db-pool-exhaustion-1 | 1 | True | True | True | True | 0 | 9 | 105,498 | 17 s |
| db-pool-exhaustion-3 | 1 | True | True | True | True | 0 | 11 | 128,029 | 22 s |
| dns-partition-1 | 1 | False | False | False | True | 0 | 10 | 124,813 | 18 s |
| expired-cert-1 | 1 | True | True | True | True | 0 | 8 | 92,847 | 14 s |
| expired-cert-4 | 1 | True | True | True | True | 0 | 8 | 83,823 | 14 s |
| memory-leak-1 | 1 | True | True | True | False | 0 | 11 | 107,222 | 21 s |
| memory-leak-3 | 1 | False | False | False | False | 0 | 14 | 114,887 | 29 s |
| noisy-neighbor-1 | 1 | False | False | False | False | 0 | 14 | 116,938 | 22 s |
| noisy-neighbor-3 | 1 | True | True | True | True | 0 | 13 | 111,639 | 22 s |
| slow-dependency-1 | 1 | True | True | True | True | 0 | 11 | 109,442 | 20 s |
| slow-dependency-2 | 1 | True | True | True | True | 0 | 10 | 93,721 | 18 s |
