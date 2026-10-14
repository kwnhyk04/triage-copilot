# Migrations

Schema changes follow expand, migrate, contract. Record each change here, newest first.

## 2026-10-14: StepRecorded v3 message

No DDL; a message contract change (user decision). It replaces the earlier plan to move producers to message v2.

- Expand: `contracts/messages/step-recorded.v3.schema.json`.
  - Its payload is the stored StepRecorded v2 event field for field: `kind`, `summary`, `tool_name`, `tool_call_id`, `result`, `latency_ms`, `tokens` (`{"input", "output"}`).
  - The payload has the same nullability and the same limits: `summary` of 1 to 2,000 characters (Unicode code points), `latency_ms` and both token counts not negative.
  - The stored `StepRecordedV2` model gained these limits, so an event that breaks one cannot be built or written. `record_step` builds the v3 message before inserting, which also stops events that skipped validation.
  - Existing rows already satisfy the limits: on the dev database summaries run from 16 to 145 characters, none is empty, and no value is negative. Replay validates the same limits, so a stored v1 summary over 2,000 characters would fail replay rather than yield an invalid v3; none exists.
  - Around it are `schema_version` 3, `investigation_id`, `seq` and `recorded_at`.
  - The union `step-recorded.schema.json` accepts v1, v2 and v3, and marks v2 `"deprecated": true` next to its `$ref`. The released v1 and v2 files are byte-identical.
- Migrate: producers emit v3 only. `record_step` returns the v3 message (built by `agent.steps.step_message`) to publish after its transaction commits. The agent loop (build step 6) is the first producer.
- Contract: once the queue holds no v1 or v2 messages, drop them from the union, then delete their files.
- Tests:
  - Both contract suites accept every version through the union, and pin v2 as the only deprecated one.
  - `services/agent/tests/test_steps.py` checks that v3's payload fields equal the stored v2 fields. It also checks that every stored event, upcast v1 events included, becomes a v3 message unchanged.
  - Each limit is tested at its boundary: on the stored model and the v3 message in Python, and on the v3 schema through ajv in TypeScript. An integration test shows that `record_step` stores nothing for an over-long summary that skipped validation.

## 2026-10-14: Story B, StepRecorded v2 step events

No DDL. `agent.investigation_steps.event` holds a StepRecorded event; `schema_version` records which version wrote it.

- v2 renames `observation` to `result` and adds `latency_ms` and `tokens` (`{"input": n, "output": n}`).
- Stored v1 rows are never rewritten. Reads go through `agent.steps.upcast`, which chains the functions in `UPCASTERS` up to `CURRENT_VERSION`, so `replay` returns v2 objects for every row. An upcast v1 step has `latency_ms` and `tokens` set to null (never measured), not zero.
- Next version: add its model next to the old ones without changing them, add an upcaster from the previous version, and bump `CURRENT_VERSION`. `services/agent/tests/golden/replay-v1-investigation.json` pins the v1 replay; `UPDATE_GOLDEN=1` rewrites it after an intended change.
- These are the agent's stored events. The queue messages in `contracts/messages` are versioned separately; message v3 (entry above) carries a stored v2 event unchanged.

## 2026-10-14: Story A, investigation_steps partitioned online

Agent revisions 0002 to 0004, each in its own release. On an empty database, `make migrate` applies them all at once, each revision in its own transaction. On a live database, upgrade one revision at a time (`uv run alembic -c services/agent/alembic.ini upgrade 0003`). `upgrade head` would run 0004 right after the swap, before every agent has turned the flag off. Every step sets `lock_timeout` 3 s, so none queues for long ahead of the step writers.

1. Expand (0002): `investigation_steps_p`, partitioned by month on `created_at` (UTC bounds), with this and next month's partitions, a default partition, and the backfill cursor table `steps_backfill`. Its primary key is `(investigation_id, seq, created_at)`: unique keys of a partitioned table must include the partition column. `(investigation_id, seq)` stays unique because `record_step` takes `seq` from `investigations.step_count` under the row lock.
2. Dual-write: set `STEPS_DUAL_WRITE=true` in every agent. `record_step` then writes each step to both tables in one transaction, live table first.
3. Backfill: `uv run python -m agent.partitions backfill` first creates the partitions the old rows need. It then copies 5,000 rows per transaction in `(investigation_id, seq)` order, with `lock_timeout` 3 s and `ON CONFLICT DO NOTHING`. Each batch commits together with the cursor, so a stopped run resumes after the last batch; `--from-start` copies everything again.
4. Swap (0003): counts rows per month in both tables in one statement, and fails without changing anything if a month differs. Then, in the same transaction:
   - Takes the partition job's advisory lock. A job attaching a partition waits on `investigations` while writers wait on the swap, so the two could deadlock.
   - Locks both tables, old first, which is the dual-write's order.
   - Renames the old table to `investigation_steps_old` and the partitioned one to `investigation_steps`.
   - Creates a view `investigation_steps_p` over the old table. Agents still dual-writing now fill the old table, which stays complete for a rollback. The downgrade checks the counts the same way.
5. Contract (0004, a later release): set `STEPS_DUAL_WRITE=false` everywhere first. 0004 checks that every row of the old table is in the partitioned one, and fails without dropping anything otherwise: a writer without dual-write could add a row between 0003's count and its lock. It then drops the view, the old table and the cursor table, and renames the primary key to `investigation_steps_pkey`. Its downgrade copies the rows back into a new old table; stop the writers first.

Partition job: the agent creates this and next month's partitions at startup and then daily, hourly after a failure (`agent.partitions.maintain_partitions`; by hand, `uv run python -m agent.partitions ensure`).
- Each partition is built as a plain table and then attached. `ATTACH PARTITION` locks the parent only in SHARE UPDATE EXCLUSIVE mode, where `CREATE TABLE ... PARTITION OF` takes ACCESS EXCLUSIVE.
- The attach still holds up step writes briefly: it locks the default partition, and it locks `investigations` to clone the foreign key. `lock_timeout` caps the wait.
- The attach fails if the default partition holds rows of the new month; move them out first.

`services/agent/tests/test_partitioning_int.py` seeds 2,000,000 steps at revision 0001. It then runs 0002, the backfill, 0003, the flag change, 0004 and the partition job while a thread records steps back to back. The test requires zero writer errors, writes in every phase, and no write slower than the 3 s lock timeout. Two more tests check the guards: 0003 refuses before the backfill finishes, and 0004 refuses while the old table holds a row the new one lacks.

## 2026-10-14: Baseline tables

- Gateway `0000_init.sql`, generated from `apps/gateway/src/schema.ts`: tenants, services, service_dependencies, alert_groups, alerts, proposed_actions, approvals. drizzle-kit wrote `CREATE SCHEMA "gateway"`, which fails because `infra/postgres-init.sql` creates the schema; the file says `IF NOT EXISTS` instead.
- Agent 0001: investigations, investigation_steps (unpartitioned, primary key `(investigation_id, seq)`), tool_calls, runbook_chunks (`vector(768)`), proposed_actions. Revisions are plain SQL; `alembic check` compares the result with the models in `services/agent/src/agent/db.py`.

## 2026-10-14: Message contracts

No database change. Queue messages follow the same three phases:

- StepRecorded v2 is in the expand phase. `contracts/messages/step-recorded.schema.json` accepts v1 and v2, and consumers validate against it.
- Migrate: producers switch to v2 (the agent loop, build step 6).
- Contract: once no v1 producer runs and the queues hold no v1 messages, drop v1 from the union, then delete `step-recorded.v1.schema.json`.
- `investigation-requested.schema.json` became `investigation-requested.v1.schema.json` (generated type `InvestigationRequestedV1`). The wire format is unchanged.

## 2026-10-14: Baseline

- `infra/postgres-init.sql` runs once on an empty data volume. It creates roles `gateway` and `agent`, schemas `gateway` and `agent` owned by their roles, and the `vector` extension. `gateway` also gets CREATE on the database for drizzle-kit.
- Version tracking: `gateway.__drizzle_migrations` (Drizzle, `apps/gateway/drizzle/`) and `agent.alembic_version` (Alembic, `services/agent/migrations/`). No tables yet.
