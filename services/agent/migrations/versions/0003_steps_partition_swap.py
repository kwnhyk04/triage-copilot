"""Story A, swap: once backfilled, the partitioned table takes the investigation_steps name.

Changes nothing and fails while any month's row count differs between the two tables.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _check_month_counts(source: str, copy: str) -> None:
    """Fails unless both tables hold the same number of rows in every month (UTC)."""
    # One statement reads both tables in one snapshot; dual-writes keep them equal afterwards.
    differing = (
        op.get_bind()
        .execute(
            sa.text(f"""
            SELECT coalesce(s.month, c.month) AS month, s.n AS source_rows, c.n AS copy_rows
            FROM (SELECT date_trunc('month', created_at, 'UTC') AS month, count(*) AS n
                  FROM agent.{source} GROUP BY 1) AS s
            FULL JOIN (SELECT date_trunc('month', created_at, 'UTC') AS month, count(*) AS n
                       FROM agent.{copy} GROUP BY 1) AS c ON c.month = s.month
            WHERE s.n IS DISTINCT FROM c.n
            ORDER BY 1
        """)  # noqa: S608 - table names are constants in this file
        )
        .all()
    )
    if differing:
        raise RuntimeError(f"agent.{copy} is missing rows of agent.{source}: {differing}")


def _lock_out_partition_job() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    # The partition job's advisory lock: its ATTACH waits on investigations while writers wait on
    # this swap, so the two must never overlap.
    op.execute("SELECT pg_advisory_xact_lock(hashtext('agent.ensure_partitions'))")


def upgrade() -> None:
    _check_month_counts("investigation_steps", "investigation_steps_p")
    _lock_out_partition_job()
    op.execute("""
        -- Old table first, like the dual-write, so a writer between its inserts cannot deadlock.
        LOCK TABLE agent.investigation_steps, agent.investigation_steps_p IN ACCESS EXCLUSIVE MODE;
        ALTER TABLE agent.investigation_steps RENAME TO investigation_steps_old;
        ALTER TABLE agent.investigation_steps_p RENAME TO investigation_steps;
        -- Agents still dual-writing now fill the old table, which stays complete for a rollback.
        CREATE VIEW agent.investigation_steps_p AS SELECT * FROM agent.investigation_steps_old;
    """)


def downgrade() -> None:
    _check_month_counts("investigation_steps", "investigation_steps_old")
    _lock_out_partition_job()
    op.execute("""
        LOCK TABLE agent.investigation_steps, agent.investigation_steps_p IN ACCESS EXCLUSIVE MODE;
        DROP VIEW agent.investigation_steps_p;
        ALTER TABLE agent.investigation_steps RENAME TO investigation_steps_p;
        ALTER TABLE agent.investigation_steps_old RENAME TO investigation_steps;
    """)
