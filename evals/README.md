# Evals

Scenario-based evaluation of the agent, built in step 6.

- `scenarios/`: scripted incidents with the expected root cause and allowed actions.
- `harness/`: runs every scenario against the agent and the sim, three times each.
- `reports/`: generated results, git-ignored.

`make eval` runs the harness once it exists.
