# Memory

Persistent project knowledge for future sessions. Keep entries concise and accurate. Never invent history or decisions. Do not duplicate what is already in `context.md`.

Entry format: `- YYYY-MM-DD: <fact or decision>. Reason: <why>.`

## Decisions and Reasoning

From the approved design doc:

- 2026-10-13: Mutating actions are enforced in code via a separate executor. Reason: prompt-only safety is not verifiable.
- 2026-10-13: Structured final report with an enum root-cause class. Reason: makes evals deterministic with no LLM judge.
- 2026-10-13: SQS FIFO with MessageGroupId per alert group. Reason: one investigation per incident at a time, native redrive to DLQ.
- 2026-10-13: Service-owned schemas (gateway via Drizzle, agent via Alembic). Reason: avoids two migration tools fighting over one schema.
- 2026-10-13: No agent framework; the loop calls the provider layer's tool calling directly. Reason: keeps the loop explicit and testable.
- 2026-10-14: Default to free-tier LLMs behind a provider switch: Gemini Flash primary, NVIDIA NIM fallback (user decision). Groq is excluded from the agent loop because of its 8K tokens/minute free cap. Incidents are simulated, so free-tier training use is acceptable.
- 2026-10-14: Production foundations are built from the start, and deployment is a hybrid (UI on Vercel, backend on the PC via Tailscale Funnel, browser reaches the gateway only through Next.js). Executor and sim run on an internal Docker network. Reason: same images locally and deployed; keys stay server-side.

## Preferences and Instructions

- 2026-10-14: The three portfolio projects run in parallel Claude Code sessions on one PC, sharing Docker. Each project has its own port range and compose project name; never prune or remove another project's Docker resources (user setup).
- 2026-10-13: General coding rules live in `AGENTS.md`. `CLAUDE.md` imports it so Claude Code loads the rules.
- 2026-10-13: Markdown filenames use kebab-case (for example `docs/migrations.md`).

## Solved Problems

None yet.

## Known Issues and Pitfalls

None yet.

## Rejected Approaches

None yet.

## Lessons Learned

None yet.

## Constraints

None yet.

## Unresolved Questions

- 2026-10-13: LocalStack licensing and free-tier features have changed over time. ElasticMQ is a fallback for SQS in dev and CI.
- 2026-10-13: OpenTelemetry Demo failure-injection flag names vary by release.
- 2026-10-14: Free-tier rate limits will slow the 3-run eval (hundreds of calls). Spread runs out and check provider consoles first. Dollar cost targets apply only on a paid tier.
