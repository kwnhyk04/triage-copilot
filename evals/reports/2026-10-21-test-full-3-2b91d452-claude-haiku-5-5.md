# Eval report 2026-10-21

test split, full subset: 25 scenarios x 3 runs = 75 investigations, scenario set v1 (manifest sha256 b81c80f4ba442427).
Models, pinned: claude-haiku-5-5 (agent), claude-haiku-5-5 (log summaries), no fallback. Prompt sha256 2b91d4523fb028a6. Embedding: {'model': 'nomic-ai/nomic-embed-text-v1.5', 'fastembed': '0.8.1', 'revision': 'e9b6763023c676ca8431644204f50c2b100d9aab'}.

| Metric | Target | Measured | 95% CI | Floor | Result |
| --- | --- | --- | --- | --- | --- |
| Root cause top-1 | 0.75 | 0.75 | 0.63 to 0.85 | 0.44 | fail |
| Root cause top-3 | 0.90 | 0.77 | 0.65 to 0.88 | 0.44 | fail |
| Consistency (correct in all 3 runs) | 0.65 | 0.48 | 0.28 to 0.68 | 0.24 | fail |
| Evidence grounding | 0.95 | 0.75 | 0.60 to 0.88 | 0.60 | fail |
| Action correctness | 0.80 | 0.59 (do-nothing baseline 0.52) | 0.40 to 0.76 | 0.55 | fail |
| Action correctness, scenarios with an acceptable action | 0.80 | 0.14 | 0.00 to 0.33 | 0.19 | fail |
| Forbidden action proposals | 0 (hard gate) | 0 | 0 to 0 | 0 | pass |
| Tool-call validity | 0.99 | 1.00 | 0.99 to 1.00 | 0.97 | pass |
| Degraded-tool scenarios: top-1 correct | 0.60 | 0.75 | 0.33 to 1.00 | 0.00 | pass |
| Degraded-tool scenarios: down tool in missing evidence | 1.00 | 1.00 | 1.00 to 1.00 | 0.00 | pass |
| Tool calls per investigation (median) | 8 | 10 | 9 to 12 | 16.5 | fail |
| Tool calls per investigation (max, hard cap) | 15 (hard cap) | 14 | 14 to 14 | 15 | pass |
| Time to first hypothesis (p95; proxy: time to the report) | 45 s | 31 s | 27 s to 33 s | 147 s | pass |
| Investigation time (p95) | 180 s | 31 s | 27 s to 33 s | 147 s | pass |
| Tokens per investigation (median) | 60,000 | 112,178 | 108,563 to 114,178 | 122,376 | fail |
| Tokens per investigation (max, hard cap) | 150,000 (hard cap) | 125,936 | 120,848 to 125,936 | 150,000 | pass |
| Cost per investigation, paid-equivalent (mean) | $0.2500 | $0.0066 | $0.0063 to $0.0070 | $0.0246 | pass |
| Cost per investigation, as billed (mean) | $0.0000 (hard gate) | $0.0066 | $0.0063 to $0.0070 | not set | n/a |
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
| bad-config | none | 3 | 0.67 (0.00 to 1.00) | 0.67 (0.00 to 1.00) | 0.67 (0.00 to 1.00) | 1.00 (1.00 to 1.00) | 0.44 (0.00 to 1.00) | 0.17 (0.00 to 0.33) | 1.00 (1.00 to 1.00) | n/a (n/a) | n/a (n/a) |
| bad-deploy | bad-deploy-rollback | 3 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 0.44 (0.00 to 1.00) | 0.44 (0.00 to 1.00) | 0.97 (0.96 to 1.00) | n/a (n/a) | n/a (n/a) |
| db-pool-exhaustion | db-connection-pool-exhaustion | 3 | 0.78 (0.67 to 1.00) | 0.78 (0.67 to 1.00) | 0.33 (0.00 to 1.00) | 0.56 (0.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) |
| dns-partition | none | 4 | 0.42 (0.17 to 0.67) | 0.58 (0.17 to 1.00) | 0.00 (0.00 to 0.00) | 0.25 (0.00 to 0.75) | 0.75 (0.25 to 1.00) | 0.00 (0.00 to 0.00) | 1.00 (1.00 to 1.00) | 0.33 (0.33 to 0.33) | 1.00 (1.00 to 1.00) |
| expired-cert | certificate-expiry | 3 | 0.89 (0.67 to 1.00) | 0.89 (0.67 to 1.00) | 0.67 (0.00 to 1.00) | 0.89 (0.67 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 0.67 (0.67 to 0.67) | 1.00 (1.00 to 1.00) |
| memory-leak | none | 3 | 0.78 (0.67 to 1.00) | 0.78 (0.67 to 1.00) | 0.33 (0.00 to 1.00) | 0.78 (0.67 to 1.00) | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | n/a (n/a) |
| noisy-neighbor | high-latency-triage | 3 | 0.56 (0.33 to 0.67) | 0.56 (0.33 to 0.67) | 0.00 (0.00 to 0.00) | 0.67 (0.33 to 1.00) | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | n/a (n/a) |
| slow-dependency | high-latency-triage | 3 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) |

### Efficiency and gates by family

| Family | Runbook | Scenarios | Forbidden action proposals | Tool calls per investigation (median) | Tool calls per investigation (max, hard cap) | Tokens per investigation (median) | Tokens per investigation (max, hard cap) | Investigation time (p95) | Cost per investigation, paid-equivalent (mean) | Cost per investigation, as billed (mean) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bad-config | none | 3 | 0 (0 to 0) | 10 (8 to 10) | 11 (9 to 11) | 111,358 (105,376 to 114,060) | 119,300 (111,409 to 119,300) | 22 s (20 s to 23 s) | $0.0074 ($0.0070 to $0.0076) | $0.0074 ($0.0070 to $0.0076) |
| bad-deploy | bad-deploy-rollback | 3 | 0 (0 to 0) | 9 (8 to 10) | 11 (8 to 11) | 103,935 (87,299 to 113,207) | 120,108 (88,307 to 120,108) | 23 s (17 s to 23 s) | $0.0065 ($0.0053 to $0.0075) | $0.0065 ($0.0053 to $0.0075) |
| db-pool-exhaustion | db-connection-pool-exhaustion | 3 | 0 (0 to 0) | 11 (9 to 14) | 14 (9 to 14) | 114,396 (104,611 to 120,843) | 125,936 (104,663 to 125,936) | 34 s (20 s to 35 s) | $0.0067 ($0.0063 to $0.0072) | $0.0067 ($0.0063 to $0.0072) |
| dns-partition | none | 4 | 0 (0 to 0) | 9 (8 to 10) | 10 (9 to 10) | 110,368 (108,030 to 115,184) | 116,424 (110,650 to 116,424) | 30 s (20 s to 30 s) | $0.0075 ($0.0074 to $0.0076) | $0.0075 ($0.0074 to $0.0076) |
| expired-cert | certificate-expiry | 3 | 0 (0 to 0) | 9 (7 to 9) | 10 (7 to 10) | 103,039 (68,850 to 108,563) | 120,296 (69,968 to 120,296) | 24 s (15 s to 24 s) | $0.0058 ($0.0037 to $0.0074) | $0.0058 ($0.0037 to $0.0074) |
| memory-leak | none | 3 | 0 (0 to 0) | 12 (11 to 14) | 14 (12 to 14) | 110,938 (99,760 to 116,969) | 120,410 (103,184 to 120,410) | 29 s (24 s to 29 s) | $0.0059 ($0.0052 to $0.0064) | $0.0059 ($0.0052 to $0.0064) |
| noisy-neighbor | high-latency-triage | 3 | 0 (0 to 0) | 14 (13 to 14) | 14 (13 to 14) | 119,525 (112,334 to 120,124) | 122,029 (119,625 to 122,029) | 27 s (26 s to 28 s) | $0.0063 ($0.0063 to $0.0064) | $0.0063 ($0.0063 to $0.0064) |
| slow-dependency | high-latency-triage | 3 | 0 (0 to 0) | 12 (11 to 13) | 14 (11 to 14) | 117,985 (112,863 to 121,048) | 121,479 (117,985 to 121,479) | 32 s (25 s to 33 s) | $0.0069 ($0.0062 to $0.0076) | $0.0069 ($0.0062 to $0.0076) |

## Runs

| Scenario | Run | Top-1 | Top-3 | Grounded | Action | Forbidden | Calls | Tokens | Time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bad-config-3 | 1 | True | True | True | False | 0 | 10 | 110,441 | 23 s |
| bad-config-4 | 1 | False | False | True | True | 0 | 10 | 110,211 | 17 s |
| bad-config-5 | 1 | True | True | True | True | 0 | 9 | 113,169 | 20 s |
| bad-deploy-3 | 1 | True | True | True | True | 0 | 8 | 89,774 | 17 s |
| bad-deploy-4 | 1 | True | True | True | False | 0 | 11 | 120,108 | 22 s |
| bad-deploy-5 | 1 | True | True | True | True | 0 | 7 | 74,660 | 16 s |
| db-pool-exhaustion-2 | 1 | False | False | False | True | 0 | 9 | 104,611 | 17 s |
| db-pool-exhaustion-4 | 1 | True | True | True | True | 0 | 11 | 108,487 | 33 s |
| db-pool-exhaustion-5 | 1 | False | False | False | True | 0 | 13 | 125,936 | 26 s |
| dns-partition-2 | 1 | True | True | False | False | 0 | 10 | 113,766 | 30 s |
| dns-partition-3 | 1 | True | True | True | True | 0 | 8 | 108,297 | 24 s |
| dns-partition-4 | 1 | True | True | False | True | 0 | 9 | 110,650 | 19 s |
| dns-partition-5 | 1 | False | False | False | True | 0 | 9 | 108,890 | 20 s |
| expired-cert-2 | 1 | True | True | True | True | 0 | 9 | 103,039 | 18 s |
| expired-cert-3 | 1 | True | True | True | True | 0 | 10 | 120,296 | 20 s |
| expired-cert-5 | 1 | True | True | True | True | 0 | 6 | 57,781 | 12 s |
| memory-leak-2 | 1 | True | True | True | False | 0 | 13 | 110,938 | 21 s |
| memory-leak-4 | 1 | True | True | True | False | 0 | 11 | 114,035 | 23 s |
| memory-leak-5 | 1 | True | True | True | False | 0 | 11 | 92,063 | 28 s |
| noisy-neighbor-2 | 1 | True | True | True | False | 0 | 12 | 112,334 | 21 s |
| noisy-neighbor-4 | 1 | True | True | True | False | 0 | 14 | 119,625 | 24 s |
| noisy-neighbor-5 | 1 | True | True | True | False | 0 | 14 | 122,029 | 27 s |
| slow-dependency-3 | 1 | True | True | True | True | 0 | 12 | 112,863 | 21 s |
| slow-dependency-4 | 1 | True | True | True | True | 0 | 12 | 114,546 | 33 s |
| slow-dependency-5 | 1 | True | True | True | True | 0 | 11 | 121,048 | 19 s |
| bad-config-3 | 2 | True | True | True | False | 0 | 11 | 119,300 | 21 s |
| bad-config-4 | 2 | False | False | True | True | 0 | 10 | 111,409 | 20 s |
| bad-config-5 | 2 | True | True | True | False | 0 | 8 | 104,891 | 17 s |
| bad-deploy-3 | 2 | True | True | True | False | 0 | 9 | 103,935 | 20 s |
| bad-deploy-4 | 2 | True | True | True | False | 0 | 9 | 112,178 | 18 s |
| bad-deploy-5 | 2 | True | True | True | True | 0 | 8 | 88,307 | 17 s |
| db-pool-exhaustion-2 | 2 | True | True | True | True | 0 | 8 | 91,936 | 18 s |
| db-pool-exhaustion-4 | 2 | True | True | True | True | 0 | 14 | 117,859 | 35 s |
| db-pool-exhaustion-5 | 2 | True | True | False | True | 0 | 12 | 114,396 | 22 s |
| dns-partition-2 | 2 | False | True | False | False | 0 | 10 | 115,687 | 20 s |
| dns-partition-3 | 2 | False | False | True | True | 0 | 8 | 107,733 | 25 s |
| dns-partition-4 | 2 | False | True | False | True | 0 | 9 | 110,086 | 17 s |
| dns-partition-5 | 2 | False | False | False | True | 0 | 10 | 114,729 | 15 s |
| expired-cert-2 | 2 | True | True | True | True | 0 | 9 | 108,191 | 24 s |
| expired-cert-3 | 2 | True | True | True | True | 0 | 9 | 108,563 | 23 s |
| expired-cert-5 | 2 | True | True | True | True | 0 | 7 | 68,850 | 14 s |
| memory-leak-2 | 2 | True | True | True | False | 0 | 14 | 120,410 | 29 s |
| memory-leak-4 | 2 | False | False | False | False | 0 | 13 | 112,536 | 24 s |
| memory-leak-5 | 2 | True | True | True | False | 0 | 11 | 99,760 | 20 s |
| noisy-neighbor-2 | 2 | False | False | False | False | 0 | 13 | 112,141 | 23 s |
| noisy-neighbor-4 | 2 | True | True | True | False | 0 | 12 | 111,351 | 25 s |
| noisy-neighbor-5 | 2 | False | False | True | False | 0 | 14 | 115,955 | 23 s |
| slow-dependency-3 | 2 | True | True | True | True | 0 | 14 | 117,985 | 25 s |
| slow-dependency-4 | 2 | True | True | True | True | 0 | 12 | 119,673 | 30 s |
| slow-dependency-5 | 2 | True | True | True | True | 0 | 11 | 120,012 | 26 s |
| bad-config-3 | 3 | True | True | True | False | 0 | 9 | 114,060 | 19 s |
| bad-config-4 | 3 | False | False | True | True | 0 | 10 | 111,358 | 20 s |
| bad-config-5 | 3 | True | True | True | False | 0 | 8 | 105,376 | 17 s |
| bad-deploy-3 | 3 | True | True | True | False | 0 | 10 | 118,476 | 23 s |
| bad-deploy-4 | 3 | True | True | True | False | 0 | 10 | 113,207 | 22 s |
| bad-deploy-5 | 3 | True | True | True | True | 0 | 8 | 87,299 | 17 s |
| db-pool-exhaustion-2 | 3 | True | True | True | True | 0 | 9 | 104,663 | 20 s |
| db-pool-exhaustion-4 | 3 | True | True | True | True | 0 | 14 | 120,848 | 31 s |
| db-pool-exhaustion-5 | 3 | True | True | False | True | 0 | 11 | 120,843 | 20 s |
| dns-partition-2 | 3 | True | True | False | False | 0 | 10 | 116,424 | 21 s |
| dns-partition-3 | 3 | False | False | True | True | 0 | 8 | 107,762 | 30 s |
| dns-partition-4 | 3 | True | True | False | True | 0 | 9 | 109,898 | 17 s |
| dns-partition-5 | 3 | False | False | False | True | 0 | 10 | 115,640 | 19 s |
| expired-cert-2 | 3 | True | True | True | True | 0 | 7 | 77,692 | 14 s |
| expired-cert-3 | 3 | False | False | False | True | 0 | 9 | 107,510 | 24 s |
| expired-cert-5 | 3 | True | True | True | True | 0 | 7 | 69,968 | 15 s |
| memory-leak-2 | 3 | False | False | False | False | 0 | 14 | 116,969 | 22 s |
| memory-leak-4 | 3 | True | True | True | False | 0 | 12 | 108,828 | 21 s |
| memory-leak-5 | 3 | True | True | True | False | 0 | 12 | 103,184 | 23 s |
| noisy-neighbor-2 | 3 | False | False | False | False | 0 | 13 | 119,813 | 26 s |
| noisy-neighbor-4 | 3 | False | False | False | False | 0 | 14 | 119,525 | 27 s |
| noisy-neighbor-5 | 3 | True | True | True | False | 0 | 14 | 120,124 | 28 s |
| slow-dependency-3 | 3 | True | True | True | True | 0 | 13 | 112,717 | 22 s |
| slow-dependency-4 | 3 | True | True | True | True | 0 | 12 | 114,178 | 30 s |
| slow-dependency-5 | 3 | True | True | True | True | 0 | 11 | 121,479 | 21 s |
