# To-Do

Concise, verifiable tasks. Move items between sections as status changes. Trim old completed items when the list grows large; the activity log keeps the history.

Each step maps to a Claude Code prompt in the design doc. Done when the check after the colon passes.

## Pending

- [ ] (manual) Get free API keys: Google AI Studio (Gemini) and NVIDIA build. Put them in `.env`, never in the repo.
- [ ] (manual, before step 7) GitHub repo created; provider keys added as Actions secrets.
- [ ] (manual, before step 10) Vercel and Tailscale accounts created.
- [ ] Enforce the remaining contract credentials in code: the gateway's X-API-Key (on `/v1` and `/graphql`; the web helpers send it) and Alertmanager bearer token, and the agent's service token, read from env vars listed in `.env.example`. A request without valid credentials gets a 401 problem details response with `WWW-Authenticate`. The approver key is done.
- [ ] Replace the gateway's placeholder decision store (`apps/gateway/src/main.ts`, every action id unknown) with the `proposed_actions` and `approvals` tables; a second decision hits the `approvals` primary key and gets 409. Recording a decision and queueing ActionApproved must happen together: add a transactional outbox with step 4's relay.
- [ ] Before step 9: the API's `Step.tool_call` needs `status` and `duration_ms`, but StepRecorded v3 has no status and a nullable `latency_ms`. Derive them in the gateway, or add them in a stored v3 event and message v4.
- [ ] Per-kind parameter schemas for ActionProposed and ActionApproved (for example `rollback_deploy` requires `to_version`): the executor rejects unknown or missing parameters.
- [ ] Before step 6: choose how the structured report (ranked hypotheses with root-cause class, confidence and evidence) reaches the gateway, for example a ReportRecorded v1 message. No current message carries it.
- [ ] 4. Workers: queue job with retries and dead-letter handling, backpressure and crash-recovery tests pass
  - [ ] Recovery sweeper: re-enqueues alert groups stuck in `queued` with no investigation progress for more than N minutes (N from env): a test that simulates queue loss (purge the queue after enqueueing) shows each stuck group re-enqueued exactly once, and a second sweep re-enqueues nothing
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

## Blocked

None.
