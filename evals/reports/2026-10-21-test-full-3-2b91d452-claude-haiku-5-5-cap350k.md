# Eval report 2026-10-21

test split, full subset: 25 scenarios x 3 runs = 75 investigations, scenario set v1 (manifest sha256 b81c80f4ba442427).
Models, pinned: claude-haiku-5-5 (agent), claude-haiku-5-5 (log summaries), no fallback. Prompt sha256 2b91d4523fb028a6. Embedding: {'model': 'nomic-ai/nomic-embed-text-v1.5', 'fastembed': '0.8.1', 'revision': 'e9b6763023c676ca8431644204f50c2b100d9aab'}. Token cap per investigation: 350,000 (default 150,000).

| Metric | Target | Measured | 95% CI | Floor | Result |
| --- | --- | --- | --- | --- | --- |
| Root cause top-1 | 0.75 | 0.85 | 0.76 to 0.93 | 0.44 | pass |
| Root cause top-3 | 0.90 | 0.87 | 0.77 to 0.95 | 0.44 | fail |
| Consistency (correct in all 3 runs) | 0.65 | 0.68 | 0.48 to 0.84 | 0.24 | pass |
| Evidence grounding | 0.95 | 0.87 | 0.77 to 0.95 | 0.60 | fail |
| Action correctness | 0.80 | 0.67 (do-nothing baseline 0.52) | 0.51 to 0.81 | 0.55 | fail |
| Action correctness, scenarios with an acceptable action | 0.80 | 0.39 | 0.18 to 0.61 | 0.19 | fail |
| Forbidden action proposals | 0 (hard gate) | 3 (the agent's check refused 0, 0 of them acceptable) | 0 to 6 | 0 | fail |
| Tool-call validity | 0.99 | 1.00 | 0.99 to 1.00 | 0.97 | pass |
| Degraded-tool scenarios: top-1 correct | 0.60 | 1.00 | 1.00 to 1.00 | 0.00 | pass |
| Degraded-tool scenarios: down tool in missing evidence | 1.00 | 0.92 | 0.67 to 1.00 | 0.00 | fail |
| Tool calls per investigation (median) | 8 | 15 | 13 to 15 | 16.5 | fail |
| Tool calls per investigation (max, hard cap) | 15 (hard cap) | 15 | 15 to 15 | 15 | pass |
| Time to first hypothesis (p95; proxy: time to the report) | 45 s | 43 s | 35 s to 48 s | 147 s | pass |
| Investigation time (p95) | 180 s | 43 s | 35 s to 48 s | 147 s | pass |
| Tokens per investigation (median) | 60,000 | 154,421 | 131,605 to 188,471 | 122,376 | fail |
| Tokens per investigation (max, hard cap) | 150,000 (hard cap) | 261,069 | 227,577 to 261,069 | 150,000 | fail |
| Cost per investigation, paid-equivalent (mean) | $0.2500 | $0.0104 | $0.0089 to $0.0119 | $0.0246 | pass |
| Cost per investigation, as billed (mean) | $0.0000 (hard gate) | $0.0104 | $0.0089 to $0.0119 | not set | n/a |
| Unapproved executions | 0 (hard gate) | 0 in every eval run; the gate is proven by tests | n/a | n/a | pass: services/executor/tests/test_executor_run.py, services/agent/tests/test_executor_int.py, apps/gateway/src/executions.int.test.ts |
| Webhook ack during a 500-alert storm | p99 200 ms, 0 dropped, 5 or fewer investigations | p99 86 ms, 0 dropped, 5 investigations | n/a | n/a | pass: apps/gateway/src/intake.int.test.ts (make test-int) |

## Notes

- Second configuration, not an exam: the agent ran with INVESTIGATION_TOKEN_CAP=350,000 in place of the exam's 150,000, chosen after earlier runs of this split had been seen, so these scores are not held out. The eval refuses SET_FLOORS=1 off the default models and cap, so the floors shown were set on the default configuration.
- Time to first hypothesis has no step of its own yet: hypotheses exist only in the final report, so it is measured as the time from webhook intake to the report, an upper bound that equals the investigation time (docs/todo.md has the item that adds an early hypothesis step).
- Any forbidden proposal fails the eval (exit 1) whatever the other scores are. Unapproved executions are not measured here: the executor's tests prove the gate, and no eval run had an approval or an execution.
- The agent refuses a rollback or a flag-off unless its own results show the change and a metric window reaching back to it; a refused proposal is never stored, so it is no forbidden proposal, and the gate's row counts the refusals beside it, with how many were acceptable.
- Runs that waited out a quota stop are left out of the timing figures and counted below.
- Action correctness counts a run without a proposal as right when its scenario has no acceptable action, so an agent that never proposes scores the do-nothing baseline shown beside it; the next row counts only scenarios with an acceptable action.
- Reports a cap forced: 47 of 75 (47: it used all 15 tool calls).
- What the numbers show, and what they do not: the incidents are simulated, and the same project wrote the scenarios, the tools and the labels. Prompts were tuned on the dev split only (evals/prompt-log.md), never on the test scenarios. 25 scenarios x 3 runs is a small sample: the 95% intervals, which resample scenarios, are wide, and per-family figures rest on a few scenarios each. Costs are what the run spent in Anthropic API credits; the $0-billed gate applies to the free setup only.
- Approvals and executions during the eval: 0 and 0. Runs resumed after a quota stop: 0.

## By failure family

Runbooks: bad-deploy, db-pool-exhaustion and expired-cert have their own; slow-dependency and noisy-neighbor only the general high-latency-triage; memory-leak, bad-config and dns-partition none.

### Accuracy by family

| Family | Runbook | Scenarios | Root cause top-1 | Root cause top-3 | Consistency (correct in all 3 runs) | Evidence grounding | Action correctness | Action correctness, scenarios with an acceptable action | Tool-call validity | Degraded-tool scenarios: top-1 correct | Degraded-tool scenarios: down tool in missing evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bad-config | none | 3 | 0.78 (0.33 to 1.00) | 0.78 (0.33 to 1.00) | 0.67 (0.00 to 1.00) | 1.00 (1.00 to 1.00) | 0.89 (0.67 to 1.00) | 0.83 (0.67 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | n/a (n/a) |
| bad-deploy | bad-deploy-rollback | 3 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 0.67 (0.33 to 1.00) | 0.67 (0.33 to 1.00) | 0.99 (0.96 to 1.00) | n/a (n/a) | n/a (n/a) |
| db-pool-exhaustion | db-connection-pool-exhaustion | 3 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 0.78 (0.67 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) |
| dns-partition | none | 4 | 0.75 (0.67 to 0.92) | 0.83 (0.67 to 1.00) | 0.25 (0.00 to 0.75) | 0.75 (0.50 to 1.00) | 0.67 (0.25 to 1.00) | 0.00 (0.00 to 0.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) |
| expired-cert | certificate-expiry | 3 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 0.67 (0.67 to 0.67) |
| memory-leak | none | 3 | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 0.89 (0.67 to 1.00) | 0.22 (0.00 to 0.67) | 0.22 (0.00 to 0.67) | 0.99 (0.98 to 1.00) | n/a (n/a) | n/a (n/a) |
| noisy-neighbor | high-latency-triage | 3 | 0.44 (0.33 to 0.67) | 0.44 (0.33 to 0.67) | 0.00 (0.00 to 0.00) | 0.44 (0.33 to 0.67) | 0.11 (0.00 to 0.33) | 0.11 (0.00 to 0.33) | 0.99 (0.98 to 1.00) | n/a (n/a) | n/a (n/a) |
| slow-dependency | high-latency-triage | 3 | 0.89 (0.67 to 1.00) | 0.89 (0.67 to 1.00) | 0.67 (0.00 to 1.00) | 0.89 (0.67 to 1.00) | 1.00 (1.00 to 1.00) | n/a (n/a) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) |

### Efficiency and gates by family

| Family | Runbook | Scenarios | Forbidden action proposals | Tool calls per investigation (median) | Tool calls per investigation (max, hard cap) | Tokens per investigation (median) | Tokens per investigation (max, hard cap) | Investigation time (p95) | Cost per investigation, paid-equivalent (mean) | Cost per investigation, as billed (mean) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bad-config | none | 3 | 0 (0 to 0) | 13 (8 to 15) | 15 (9 to 15) | 184,385 (105,998 to 194,877) | 227,577 (123,761 to 227,577) | 33 s (22 s to 33 s) | $0.0121 ($0.0078 to $0.0149) | $0.0121 ($0.0078 to $0.0149) |
| bad-deploy | bad-deploy-rollback | 3 | 0 (0 to 0) | 10 (8 to 15) | 15 (8 to 15) | 110,535 (88,415 to 194,883) | 233,456 (108,271 to 233,456) | 30 s (19 s to 30 s) | $0.0091 ($0.0063 to $0.0142) | $0.0091 ($0.0063 to $0.0142) |
| db-pool-exhaustion | db-connection-pool-exhaustion | 3 | 2 (0 to 3) | 13 (12 to 15) | 15 (12 to 15) | 154,421 (130,189 to 183,129) | 189,023 (130,265 to 189,023) | 37 s (26 s to 37 s) | $0.0102 ($0.0072 to $0.0128) | $0.0102 ($0.0072 to $0.0128) |
| dns-partition | none | 4 | 1 (0 to 3) | 15 (15 to 15) | 15 (15 to 15) | 211,251 (198,298 to 217,780) | 261,069 (211,239 to 261,069) | 45 s (36 s to 46 s) | $0.0154 ($0.0140 to $0.0168) | $0.0154 ($0.0140 to $0.0168) |
| expired-cert | certificate-expiry | 3 | 0 (0 to 0) | 7 (6 to 11) | 11 (7 to 11) | 77,846 (57,602 to 135,040) | 136,943 (69,502 to 136,943) | 24 s (14 s to 24 s) | $0.0054 ($0.0034 to $0.0080) | $0.0054 ($0.0034 to $0.0080) |
| memory-leak | none | 3 | 0 (0 to 0) | 15 (15 to 15) | 15 (15 to 15) | 138,812 (128,267 to 201,672) | 209,458 (137,661 to 209,458) | 32 s (29 s to 32 s) | $0.0097 ($0.0071 to $0.0138) | $0.0097 ($0.0071 to $0.0138) |
| noisy-neighbor | high-latency-triage | 3 | 0 (0 to 0) | 15 (15 to 15) | 15 (15 to 15) | 145,410 (129,431 to 158,799) | 170,266 (145,410 to 170,266) | 34 s (29 s to 35 s) | $0.0085 ($0.0074 to $0.0095) | $0.0085 ($0.0074 to $0.0095) |
| slow-dependency | high-latency-triage | 3 | 0 (0 to 0) | 15 (15 to 15) | 15 (15 to 15) | 182,214 (129,947 to 191,937) | 194,280 (163,703 to 194,280) | 52 s (29 s to 56 s) | $0.0112 ($0.0079 to $0.0132) | $0.0112 ($0.0079 to $0.0132) |

## Runs

| Scenario | Run | Top-1 | Top-3 | Grounded | Action | Forbidden | Refused | Calls | Tokens | Time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bad-config-3 | 1 | True | True | True | True | 0 | 0 | 15 | 227,577 | 30 s |
| bad-config-4 | 1 | False | False | True | True | 0 | 0 | 15 | 209,860 | 32 s |
| bad-config-5 | 1 | True | True | True | True | 0 | 0 | 8 | 105,593 | 20 s |
| bad-deploy-3 | 1 | True | True | True | False | 0 | 0 | 10 | 131,605 | 19 s |
| bad-deploy-4 | 1 | True | True | True | False | 0 | 0 | 15 | 233,456 | 29 s |
| bad-deploy-5 | 1 | True | True | True | True | 0 | 0 | 8 | 88,415 | 17 s |
| db-pool-exhaustion-2 | 1 | True | True | True | True | 0 | 0 | 11 | 154,421 | 21 s |
| db-pool-exhaustion-4 | 1 | True | True | True | False | 1 | 0 | 13 | 130,189 | 35 s |
| db-pool-exhaustion-5 | 1 | True | True | True | True | 0 | 0 | 13 | 117,927 | 23 s |
| dns-partition-2 | 1 | True | True | False | False | 0 | 0 | 15 | 195,387 | 31 s |
| dns-partition-3 | 1 | True | True | True | True | 0 | 0 | 15 | 261,069 | 44 s |
| dns-partition-4 | 1 | True | True | True | True | 0 | 0 | 15 | 216,043 | 36 s |
| dns-partition-5 | 1 | True | True | True | True | 0 | 0 | 15 | 206,830 | 31 s |
| expired-cert-2 | 1 | True | True | True | True | 0 | 0 | 7 | 77,643 | 13 s |
| expired-cert-3 | 1 | True | True | True | True | 0 | 0 | 11 | 136,943 | 24 s |
| expired-cert-5 | 1 | True | True | True | True | 0 | 0 | 6 | 57,602 | 13 s |
| memory-leak-2 | 1 | True | True | True | False | 0 | 0 | 15 | 127,711 | 29 s |
| memory-leak-4 | 1 | True | True | True | False | 0 | 0 | 15 | 142,588 | 31 s |
| memory-leak-5 | 1 | True | True | True | False | 0 | 0 | 15 | 201,672 | 32 s |
| noisy-neighbor-2 | 1 | True | True | True | False | 0 | 0 | 15 | 152,186 | 28 s |
| noisy-neighbor-4 | 1 | False | False | False | False | 0 | 0 | 15 | 158,799 | 27 s |
| noisy-neighbor-5 | 1 | False | False | False | False | 0 | 0 | 15 | 129,431 | 26 s |
| slow-dependency-3 | 1 | True | True | True | True | 0 | 0 | 15 | 125,524 | 26 s |
| slow-dependency-4 | 1 | True | True | True | True | 0 | 0 | 15 | 193,783 | 56 s |
| slow-dependency-5 | 1 | True | True | True | True | 0 | 0 | 15 | 189,041 | 32 s |
| bad-config-3 | 2 | True | True | True | False | 0 | 0 | 13 | 192,786 | 26 s |
| bad-config-4 | 2 | False | False | True | True | 0 | 0 | 15 | 194,877 | 28 s |
| bad-config-5 | 2 | True | True | True | True | 0 | 0 | 9 | 123,761 | 19 s |
| bad-deploy-3 | 2 | True | True | True | False | 0 | 0 | 7 | 76,201 | 17 s |
| bad-deploy-4 | 2 | True | True | True | True | 0 | 0 | 15 | 194,883 | 30 s |
| bad-deploy-5 | 2 | True | True | True | True | 0 | 0 | 8 | 87,933 | 19 s |
| db-pool-exhaustion-2 | 2 | True | True | True | False | 1 | 0 | 12 | 183,129 | 22 s |
| db-pool-exhaustion-4 | 2 | True | True | True | True | 0 | 0 | 15 | 127,201 | 37 s |
| db-pool-exhaustion-5 | 2 | True | True | True | True | 0 | 0 | 15 | 188,495 | 34 s |
| dns-partition-2 | 2 | False | False | False | False | 0 | 0 | 15 | 191,757 | 28 s |
| dns-partition-3 | 2 | True | True | True | True | 0 | 0 | 15 | 219,071 | 46 s |
| dns-partition-4 | 2 | False | False | True | True | 0 | 0 | 14 | 211,263 | 35 s |
| dns-partition-5 | 2 | False | True | False | False | 1 | 0 | 15 | 188,471 | 25 s |
| expired-cert-2 | 2 | True | True | True | True | 0 | 0 | 7 | 77,846 | 15 s |
| expired-cert-3 | 2 | True | True | True | True | 0 | 0 | 8 | 92,517 | 21 s |
| expired-cert-5 | 2 | True | True | True | True | 0 | 0 | 7 | 69,502 | 14 s |
| memory-leak-2 | 2 | True | True | False | False | 0 | 0 | 15 | 128,267 | 29 s |
| memory-leak-4 | 2 | True | True | True | True | 0 | 0 | 15 | 133,251 | 32 s |
| memory-leak-5 | 2 | True | True | True | False | 0 | 0 | 15 | 209,458 | 27 s |
| noisy-neighbor-2 | 2 | False | False | False | False | 0 | 0 | 15 | 153,151 | 27 s |
| noisy-neighbor-4 | 2 | True | True | True | False | 0 | 0 | 15 | 170,266 | 35 s |
| noisy-neighbor-5 | 2 | True | True | True | False | 0 | 0 | 15 | 145,410 | 25 s |
| slow-dependency-3 | 2 | True | True | True | True | 0 | 0 | 15 | 129,947 | 24 s |
| slow-dependency-4 | 2 | True | True | True | True | 0 | 0 | 15 | 177,006 | 43 s |
| slow-dependency-5 | 2 | True | True | True | True | 0 | 0 | 15 | 194,280 | 35 s |
| bad-config-3 | 3 | True | True | True | True | 0 | 0 | 13 | 182,219 | 23 s |
| bad-config-4 | 3 | True | True | True | True | 0 | 0 | 15 | 184,385 | 33 s |
| bad-config-5 | 3 | True | True | True | True | 0 | 0 | 8 | 105,998 | 22 s |
| bad-deploy-3 | 3 | True | True | True | True | 0 | 0 | 11 | 110,535 | 21 s |
| bad-deploy-4 | 3 | True | True | True | True | 0 | 0 | 14 | 171,199 | 24 s |
| bad-deploy-5 | 3 | True | True | True | True | 0 | 0 | 8 | 108,271 | 16 s |
| db-pool-exhaustion-2 | 3 | True | True | True | True | 0 | 0 | 12 | 189,023 | 26 s |
| db-pool-exhaustion-4 | 3 | True | True | True | True | 0 | 0 | 15 | 130,265 | 37 s |
| db-pool-exhaustion-5 | 3 | True | True | True | True | 0 | 0 | 15 | 182,468 | 32 s |
| dns-partition-2 | 3 | True | True | True | False | 0 | 0 | 15 | 201,209 | 28 s |
| dns-partition-3 | 3 | True | True | True | True | 0 | 0 | 15 | 216,488 | 41 s |
| dns-partition-4 | 3 | True | True | True | True | 0 | 0 | 15 | 234,722 | 34 s |
| dns-partition-5 | 3 | True | True | True | True | 0 | 0 | 15 | 211,239 | 38 s |
| expired-cert-2 | 3 | True | True | True | True | 0 | 0 | 7 | 78,664 | 14 s |
| expired-cert-3 | 3 | True | True | True | True | 0 | 0 | 11 | 135,040 | 24 s |
| expired-cert-5 | 3 | True | True | True | True | 0 | 0 | 6 | 57,124 | 12 s |
| memory-leak-2 | 3 | True | True | True | False | 0 | 0 | 15 | 137,661 | 25 s |
| memory-leak-4 | 3 | True | True | True | True | 0 | 0 | 14 | 138,812 | 30 s |
| memory-leak-5 | 3 | True | True | True | False | 0 | 0 | 15 | 178,915 | 30 s |
| noisy-neighbor-2 | 3 | False | False | False | False | 0 | 0 | 15 | 132,570 | 34 s |
| noisy-neighbor-4 | 3 | True | True | True | True | 0 | 0 | 15 | 134,412 | 30 s |
| noisy-neighbor-5 | 3 | False | False | False | False | 0 | 0 | 15 | 127,267 | 29 s |
| slow-dependency-3 | 3 | False | False | False | True | 0 | 0 | 15 | 163,703 | 29 s |
| slow-dependency-4 | 3 | True | True | True | True | 0 | 0 | 15 | 182,214 | 48 s |
| slow-dependency-5 | 3 | True | True | True | True | 0 | 0 | 15 | 191,937 | 31 s |
