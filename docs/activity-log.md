# Activity Log

Chronological record of significant development activity, newest first. Skip trivial changes. Do not auto-commit this file.

## Entry Format

```
### YYYY-MM-DD: <task performed>

- Changes: <important changes>
- Files: <relevant files>
- Validation: <tests or checks run and results>
- Outstanding: <open issues, or "None">
```

## Entries

### 2026-10-14: Plan updated for free-tier models, production readiness, and deployment

- Changes: Switched to free-tier LLMs behind a provider switch, added production-readiness requirements, added steps 9 (review UI) and 10 (deploy).
- Files: AGENTS.md, docs/context.md, docs/memory.md, docs/todo.md
- Validation: Documentation only. No code changed.
- Outstanding: See docs/todo.md.

### 2026-10-13: Project created from template

- Changes: Copied `_project-template`, filled project-specific rules in `AGENTS.md`, added `CLAUDE.md` importing `AGENTS.md`, recorded design in `docs/context.md`, decisions in `docs/memory.md`, build plan in `docs/todo.md`.
- Files: AGENTS.md, CLAUDE.md, README.md, docs/*
- Validation: Documentation only. No code to test.
- Outstanding: None.
