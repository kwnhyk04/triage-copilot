# Triage Copilot

Triage Copilot investigates production alerts the way an on-call engineer would. It reads metrics, logs, deploys and runbooks with read-only tools, ranks root-cause hypotheses with the evidence for each, and drafts a fix plan that code checks and a second model review before a human decides. It triages a simulator's scripted incidents for its evals, and real alerts from two live apps, FreightAudit and FilingLens.

## How it works

- **Gateway** (TypeScript: Fastify, GraphQL Yoga, Postgres) takes Alertmanager webhooks, groups and dedupes them, and queues investigations on SQS.
- **Agent** (Python: FastAPI) investigates in a capped loop of read-only tool calls and stores a structured report; it can change nothing. Claude models triage, plan and review until 2026-11-10. Gemini's and NVIDIA's free tiers are the fallback, and then triage and plan alone.
- **watch** polls the real apps (`docker ps`, logs, git) and posts their state changes as alerts, each with a scrubbed snapshot.
- **Web** (Next.js) shows reports and plans, with Approve and Reject; the browser talks only to its own server.
- **Executor**, the only service that acts: after a recorded approval it restarts an app or worker container from the app's catalog, as a dry run by default. Production deploys none.
- **Contracts** (OpenAPI, GraphQL, SQS message schemas) generate the clients in both languages.

## Run it

Needs Node 24 (pnpm through corepack), uv, Docker and GNU make.

```sh
cp .env.example .env
make install
make dev   # Postgres, ElasticMQ and Toxiproxy, then every app
```

Sign-in needs two settings, made once: `pnpm --filter @triage/web hash-password --write-env`, then `pnpm --filter @triage/web session-secret`. Then open `http://127.0.0.1:3200`; `/demo` works without them. `make test`, `make test-int` and `make test-e2e` run the tests. Every command, the production deploy and its alerting: [docs/guide.md](docs/guide.md).

## Results so far

- **Evals** ([evals/README.md](evals/README.md)): 40 simulated incidents in 8 failure families; the test split's 25 ran 3 times each and were never tuned on. On Gemini 3.5 Flash-Lite's free tier the exam failed: root cause top-1 0.60 against a 0.75 target, and one forbidden proposal. Claude Haiku 5.5 reached 0.75, then 0.85 with a larger token budget, which also brought 3 forbidden restarts. Version 2 of the proposal check, built from dev scenarios only, passed both its dev passes with none.
- **Plan review:** on 16 fresh plans, no trusted plan was refused and 3 of 4 bad steps were caught.
- **Shadow run** (live triage, nothing acted on), 2026-11-01 to 2026-11-03, times UTC. watch covered FreightAudit from 15:02 on 2026-11-01, when live triage went on, and FilingLens from its go-live at 17:17 that day.
  - **Alerts:** watch raised two, both FilingLens's, and Claude Haiku 5.5 reported on each within 40 s. ReadinessCheckFailed on api (2026-11-01 21:56): real, postgres the likely cause at confidence 0.5, unconfirmed because the snapshot held no postgres lines (watch keeps them now). HealthCheckFailed on api (2026-11-02 16:35): not real, one failed check on the tailnet path while the api answered locally, at 0.6. FreightAudit raised none while watched. Three drills posted by hand were judged not real; the one with Claude switched off fell to Gemini 3.5 Flash-Lite and gave the reason. Every alert got a report and none a plan: the planner came on after the one real verdict.
  - **Verdicts:** all five fit their evidence. One summary says a later check passed, which its evidence does not show.
  - **Spend:** $0.0336 of Claude in production in November: $0.0195 on 2026-11-01, $0.0141 on 2026-11-02 and nothing since, against a $0.40 daily cap.
  - **Canary scan:** a data-only dump of the production database matched no key, token, email or tailnet shape.
  - **Gaps:** watch was ready for 45.7 of the 72 hours. It was not before it started at 15:02 on 2026-11-01; from 16:58 to 17:17 that day (FilingLens's go-live restart); from 23:10 to 09:16 the next morning (cause not confirmed); and from 16:43 to 17:31 on 2026-11-03, when Docker hung and the PC crashed. Nothing in those hours was seen. A go-live silence muted WatchNotReady until 12:52 on 2026-11-02, and the crash took Prometheus and Alertmanager down too, so no outage reached Discord or email. TC's other alerts, ClaudeOff for the drill and one EgressDenied, reached Discord, which delivered throughout. Email alerts failed from go-live until 2026-11-04, because the Gmail login and its app password belonged to different accounts.

## Future work

- **Phase 4, planned, not built:** approved plans that run their actions automatically. Today an approved restart only records its command (a dry run), and an approved code fix runs nothing. Phase 4 starts with the coder: an approved code fix becomes a branch in a hardened local sandbox, never pushed or merged, for a human to review.

## Licence

[MIT](LICENSE).
