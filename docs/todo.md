# To-Do

Concise, verifiable tasks. Move items between sections as status changes. Trim old completed items when the list grows large; the activity log keeps the history.

Each step maps to a Claude Code prompt in the design doc. Done when the check after the colon passes.

## Pending

- [ ] (manual) Get free API keys: Google AI Studio (Gemini) and NVIDIA build. Put them in `.env`, never in the repo.
- [ ] (manual, before step 7) GitHub repo created; provider keys added as Actions secrets.
- [ ] (manual, before step 10) Vercel and Tailscale accounts created.
- [ ] Enforce the gateway's X-API-Key in code (on `/v1` and `/graphql`; the web helpers send it), read from an env var listed in `.env.example`. A request without a valid key gets a 401 problem details response with `WWW-Authenticate`. The approver key, the per-tenant webhook tokens and the agent's service token are done.
- [ ] Contract the unused `queued_budget` status: once no investigation holds it, rebuild `agent.investigation_status` without it in a maintenance window (docs/migrations.md, 2026-10-15 parking entry).
- [ ] Agent migration 0001 sets no `lock_timeout` (found 2026-10-15): add it to both directions. Also mark 0005's downgrade (a type rebuild under ACCESS EXCLUSIVE) as a maintenance-window step in docs/migrations.md.
- [ ] Replace the gateway's placeholder decision store (`apps/gateway/src/main.ts`, every action id unknown) with the `proposed_actions` and `approvals` tables; a second decision hits the `approvals` primary key and gets 409. Recording a decision and queueing ActionApproved must happen together: reuse step 4's outbox and relay (`apps/gateway/src/outbox.ts`), adding a queue column.
- [ ] Before step 9: the API's `Step.tool_call` needs `status` and `duration_ms`, but StepRecorded v3 has no status and a nullable `latency_ms`. Derive them in the gateway, or add them in a stored v3 event and message v4. A tool call is two steps (the model's request with tokens, then the result), so the gateway pairs them by `tool_call_id`.
- [ ] Before step 10: check which client address the gateway sees behind Tailscale Funnel. If it is the proxy's, set Fastify `trustProxy`, or every request without a credential shares one rate-limit count.
- [ ] Per-kind parameter schemas for ActionProposed and ActionApproved (for example `rollback_deploy` requires `to_version`): the executor rejects unknown or missing parameters.
- [ ] Before step 6: choose how the structured report (ranked hypotheses with root-cause class, confidence and evidence) reaches the gateway, for example a ReportRecorded v1 message. No current message carries it; the agent stores it in `investigations.final_report` (`agent.model.Report`).
- [ ] 5. Tests: unit and integration suites green three runs in a row
- [ ] 6. AI: pipeline built, golden dataset v1 created, eval report lists every target with pass or fail
- [ ] 7. CI/CD: GitHub Actions for test, build, docker push; actionlint passes; test PR green
- [ ] 8. Instrumentation: OpenTelemetry traces span services end to end; dashboard and alerts committed; screenshots saved
- [ ] 9. Review UI and demo mode: resolvers, login, backend-for-frontend, demo snapshot shown when the backend is offline; Playwright tests pass
- [ ] 10. Deploy: UI on Vercel, backend on the PC via Tailscale Funnel; phone test passes; 401 without API key; demo mode when the PC is off

## In Progress

None.

## Completed

- [x] Project created from template (2026-10-13).
- [x] 1. Scaffolding: monorepo layout, docker-compose infra healthy, every service answers /healthz (2026-10-14).
- [x] 2. Contracts: OpenAPI and GraphQL contracts, typed clients generated, contracts-check and breaking-change check pass (2026-10-14).
- [x] 3. Data: both migration chains apply from empty, the partition swap test passes on 2,000,000 steps while a writer loop runs with zero errors, v1 replay matches the golden snapshot, seed runs (2026-10-14).
- [x] Stored StepRecorded v2 events reach the queue as message v3, which carries them unchanged, so no mapping is needed: v3 contract, union, codegen and contract tests pass (2026-10-14).
- [x] (manual) `.prettierignore` lists `**/drizzle/meta/`; `pnpm prettier --check .` passes (2026-10-14).
- [x] v3 and the stored v2 model share the limits: a summary of 1 to 2,000 characters, and latency and token counts that are not negative. Each limit is tested in both languages (2026-10-14).
- [x] 4. Workers: queue job with retries and dead-letter handling, backpressure and crash-recovery tests pass (2026-10-15). InvestigationRequested v2 with `tenant_id`; per-tenant webhook tokens, fingerprint dedupe, 5-minute grouping, outbox relay, rate limit and input limits in the gateway; consumer, resumable job, tenant cap, budget guard, dead letters and replay in the agent. Done-when checks pass: (1) a stopped or killed agent resumes at the next step with no recorded tool call repeated; (2) a 500-alert storm over 8 groups queues 5 investigations with webhook p99 about 86 ms; (3) a message that fails 5 times lands in the DLQ, and a replay resumes it.

- [x] Step 4 follow-up, parking instead of holding (2026-10-15). A message that must wait for a tenant slot or the model budget marks its investigation waiting (`tenant_cap` or `budget`) and is deleted. The gateway's recovery sweeper re-queues, through the outbox, waiting investigations the agent reports resumable, and stuck ones: unknown to the agent, or no step for `SWEEPER_STUCK_AFTER_MINUTES`. The agent reports state at `GET /internal/investigations/{id}/state`, behind the service token. Done-when checks pass, with `make test` and `make test-int` green 3 runs in a row:
  - Two agents: a storm over the tenant cap leaves no DLQ entry, and every parked investigation runs.
  - A budget-parked investigation resumes after the budget resets.
  - Queue loss (purge after enqueueing): each stuck group is re-enqueued exactly once, and a second sweep re-enqueues nothing.
  - The internal API answers 401 without the service token.

## Blocked

None.
