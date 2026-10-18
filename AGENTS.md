# AGENTS.md: Triage Copilot

Instructions for any AI coding agent working in this project. Read this file first in every session.

## Project-Specific Instructions

- Polyglot: TypeScript gateway and web (pnpm + Turborepo, Fastify, GraphQL Yoga, Next.js, Jest), Python agent, executor and sim (uv, FastAPI, SQLAlchemy 2 + Alembic, pytest). SQS FIFO via ElasticMQ in dev.
- The gateway owns the `gateway` Postgres schema (Drizzle). The agent owns the `agent` schema (Alembic). No cross-schema writes; services talk through APIs and messages.
- The agent may only read. Only `services/executor` may perform mutating actions, and only after a recorded human approval. Enforce this in code and tests, never only in prompts.
- The agent's final report is structured output so evals stay deterministic.
- Every tool result gets an id. Every external call has a timeout.
- Never put log lines or secrets from tool results into span attributes.
- Schema changes follow expand, migrate, contract. Document them in `docs/migrations.md`.
- LLM calls go through the provider layer. Free tiers by default: Gemini Flash primary, NVIDIA NIM fallback, a small model for log summaries. Do not use Groq for the agent loop (its free tier allows only 8K tokens per minute). Tests use the scripted fake model.
- Production foundations apply to all code: every setting comes from env vars validated at startup, `.env.example` stays current, secrets never go into code, images, logs or spans, every service has `/healthz` and `/readyz`, logs are structured JSON.
- The browser never calls the API directly. Next.js server routes call it with a server-only API key (backend-for-frontend).
- Deployment target: UI on Vercel, backend on the user's PC via Tailscale Funnel, using the same Docker images as local development plus a `docker-compose.prod.yml` override.
- Three projects (freight-audit, filing-lens, triage-copilot) may run at the same time on this PC. Use only this project's ports (see Local ports in `docs/context.md`), bind them to 127.0.0.1, and keep them configurable through `.env`.
- Docker is shared with the other projects: only start, stop or remove this project's compose services. Never run `docker system prune`, `docker volume prune`, `docker network prune`, or `docker compose down -v` without asking. Never stop or remove containers that belong to another compose project.
- Set a fixed compose project name (`name: triagecopilot` at the top of the compose file) so this project's containers, volumes and networks are grouped and named predictably.
- Monitoring services (OpenTelemetry Collector, Jaeger, Prometheus, Grafana) sit behind a compose profile named `observability`, so the default `docker compose up` starts only what the app needs.
- If a port is already taken, report which one and stop; do not change another project's ports.

## Session Start

1. Read `AGENTS.md` (this file).
2. Read `docs/context.md`.
3. Read `docs/memory.md`.
4. Review relevant entries in `docs/activity-log.md`.
5. Check `docs/todo.md`.
6. Inspect relevant source files before modifying code.

Never assume documentation is more accurate than the source code. Resolve discrepancies carefully and report them.

## Development Workflow

1. **Understand:** Identify the requested outcome.
2. **Review:** Read relevant context, memory, and source files if they exist.
3. **Plan:** Define the minimal necessary changes and update `docs/todo.md`.
4. **Approve:** Ask for permission before major changes (see below).
5. **Implement:** Write clean, focused code.
6. **Validate:** Run relevant tests and checks when available.
7. **Document:** Update context, memory, and activity log only when appropriate.
8. **Report:** Summarize changes, validation results, and remaining issues.

For small, clearly authorized changes, proceed without unnecessary confirmation. Do not use documentation maintenance as a reason to delay straightforward work.

### Requires Approval

- Major architectural changes
- Destructive changes (deleting files, data, or history)
- Significant refactoring
- Major feature additions
- Adding or upgrading dependencies beyond what the task clearly needs

## General Principles

- Generate concise, short solutions for new modules and code.
- Prefer simple implementations over elaborate architectures.
- Avoid over-engineering and unnecessary abstraction.
- Monitor oversized files and suggest refactoring when justified.
- Maintain consistency with the project's existing syntax and style.
- Watch for obvious bugs, regressions, and logical mistakes.
- Prioritize concise, precise code and documentation changes.
- Use no emojis or decorative special characters in code comments.
- Keep comments to one line and one sentence whenever practical.
- Use kebab-case for Markdown filenames, except standard names such as `README.md` and `AGENTS.md`.
- Make a to-do list before substantial development work.
- Write and edit files with the file editing tools, never through shell echo, heredocs or redirects.
- Disk: all caches, model downloads, browser installs and tool data must live on H:. Never rely on a default path on C:. If a new tool needs a cache, set its location under H:\.cache and document it in docs/context.md.

## Code Quality

- Choose the right data structures and algorithms.
- Follow the principle of least privilege.
- Never expose sensitive or unnecessary data.
- Avoid external libraries unless genuinely necessary.
- Check the project's dependency file before adding or upgrading dependencies.
- Use versions compatible with the project's existing stack.
- Avoid redundant implementations unless redundancy meaningfully improves usability.
- Keep functions and modules focused.
- Do not add complexity for hypothetical future requirements.
- Preserve existing functionality unless a change is explicitly requested.

## Architecture

- Prefer modular, maintainable code.
- Avoid excessively large files.
- Separate responsibilities only when the separation provides a clear benefit.
- Do not introduce design patterns without a practical reason.
- Avoid premature optimization.
- Favor readability and correctness over cleverness.
- Do not duplicate code without justification.
- Keep project dependencies isolated to this project.

## Context Isolation

- This project is independent. Do not read, copy, or reference code, config, dependencies, or docs from other projects unless explicitly requested.
- Never assume this project shares technologies or requirements with another project.
- Project-specific decisions stay in this project's `docs/` folder.

## Documentation Rules

| File | Purpose | When to update |
| --- | --- | --- |
| `README.md` | Short description, setup, usage | When setup or usage changes |
| `docs/context.md` | Current state of the project | After meaningful technical or functional changes |
| `docs/memory.md` | Decisions, preferences, pitfalls, lessons | When an important decision or lesson emerges |
| `docs/activity-log.md` | Chronological record of significant work | After significant development activity |
| `docs/todo.md` | Verifiable task list | When planning and when progress changes |

- Document only confirmed information. Never invent history or decisions.
- Do not rewrite all documentation after every interaction.
- Do not log trivial modifications such as typo or formatting fixes; the small events in the next rule are never trivial.
- Every entry in `docs/activity-log.md`, and in `docs/memory.md` when it records a problem or a decision, includes small events too: a failed command, a skipped check, a tool or prompt that behaved unexpectedly, a setting changed. Each entry states three things:
  1. What happened.
  2. Why it happened (write "not confirmed" when it's a guess).
  3. What we did: the exact steps in order (commands, buttons, where each setting lives) and why that was the best option.
- Mistakes are logged the same way and never edited out later. Don't rewrite past entries.

## Git

- Do not initialize Git or create commits unless requested.
- Do not automatically stage or commit documentation or activity logs.
- Shipping: Claude Code never commits, pushes, opens or merges pull requests, and never runs gh (the GitHub CLI) for anything, read-only commands included. The user ships with the ship command (H:\Projects\projects\_tools\ship.ps1): it commits, pushes, opens the pull request, waits for every check and merges only when all pass. When you need a CI or pull request result, ask the user.
