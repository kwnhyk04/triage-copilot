"""Story A, expand: investigation_steps_p, partitioned by month, beside investigation_steps.

Then: set STEPS_DUAL_WRITE=true in every agent and run `python -m agent.partitions backfill`.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-14
"""

from datetime import UTC, date, datetime

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _next_month(month: date) -> date:
    return date(month.year + month.month // 12, month.month % 12 + 1, 1)


def upgrade() -> None:
    op.execute("""
        -- The foreign key locks investigations against step writes; never wait long for that.
        SET LOCAL lock_timeout = '3s';
        CREATE TABLE agent.investigation_steps_p (
            investigation_id uuid NOT NULL,
            seq integer NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            schema_version integer NOT NULL,
            event jsonb NOT NULL,
            -- Unique keys of a partitioned table must include the partition column.
            CONSTRAINT investigation_steps_p_pkey PRIMARY KEY (investigation_id, seq, created_at),
            -- The old table's foreign key name, so the final table keeps it after the swap.
            CONSTRAINT investigation_steps_investigation_id_fkey FOREIGN KEY (investigation_id)
                REFERENCES agent.investigations (id) ON DELETE CASCADE
        ) PARTITION BY RANGE (created_at);
        CREATE TABLE agent.investigation_steps_default
            PARTITION OF agent.investigation_steps_p DEFAULT;

        -- Backfill cursor: the last (investigation_id, seq) copied, so a rerun resumes after it.
        CREATE TABLE agent.steps_backfill (
            last_investigation_id uuid NOT NULL,
            last_seq integer NOT NULL
        );
        INSERT INTO agent.steps_backfill VALUES ('00000000-0000-0000-0000-000000000000', 0);
    """)
    # This month and the next, so dual-writes never land in the default partition.
    month = datetime.now(UTC).date().replace(day=1)
    for start in (month, _next_month(month)):
        op.execute(
            f"CREATE TABLE agent.investigation_steps_y{start:%Y}m{start:%m} "
            "PARTITION OF agent.investigation_steps_p "
            f"FOR VALUES FROM ('{start}T00:00Z') TO ('{_next_month(start)}T00:00Z')"
        )


def downgrade() -> None:
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        DROP TABLE agent.steps_backfill;
        DROP TABLE agent.investigation_steps_p;
    """)
