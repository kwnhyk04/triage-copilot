# To-Do

Concise, verifiable tasks. Move items between sections as status changes. Trim old completed items when the list grows large; the activity log keeps the history.

Each step maps to a Claude Code prompt in the design doc. Done when the check after the colon passes.

## Pending

- [ ] (manual) Get free API keys: Google AI Studio (Gemini) and NVIDIA build. Put them in `.env`, never in the repo.
- [ ] (manual, before step 7) GitHub repo created; provider keys added as Actions secrets.
- [ ] (manual, before step 10) Vercel and Tailscale accounts created.
- [ ] Enforce the remaining contract credentials in code: the gateway's X-API-Key (on `/v1` and `/graphql`; the web helpers send it) and Alertmanager bearer token, and the agent's service token, read from env vars listed in `.env.example`. A request without valid credentials gets a 401 problem details response with `WWW-Authenticate`. The approver key is done.
- [ ] Step 3 replaces the gateway's placeholder decision store (`apps/gateway/src/main.ts`, every action id unknown) with the actions table. Recording a decision and queueing ActionApproved must happen together.
- [ ] Per-kind parameter schemas for ActionProposed and ActionApproved (for example `rollback_deploy` requires `to_version`): the executor rejects unknown or missing parameters.
- [ ] Before step 6: choose how the structured report (ranked hypotheses with root-cause class, confidence and evidence) reaches the gateway, for example a ReportRecorded v1 message. No current message carries it.
- [ ] 3. Data: schema and migrations apply from empty, expand/contract series tested, seed scripts run
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

## Blocked

None.
