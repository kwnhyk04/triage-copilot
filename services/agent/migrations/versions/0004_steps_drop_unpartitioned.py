"""Story A, contract (a later release): drop the unpartitioned table.

Turn STEPS_DUAL_WRITE off in every agent first: writes to investigation_steps_p fail after this.
Fails without dropping anything if a row of the old table is missing from the partitioned one.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The drop cannot be undone. A writer without dual-write could slip a row into the old table
    # between 0003's count check and its lock, so compare every row, not counts.
    missing = (
        op.get_bind()
        .execute(
            sa.text("""
            SELECT count(*) FROM agent.investigation_steps_old o
            WHERE NOT EXISTS (
                SELECT FROM agent.investigation_steps s
                WHERE s.investigation_id = o.investigation_id AND s.seq = o.seq
                  AND s.created_at = o.created_at
            )
        """)
        )
        .scalar_one()
    )
    if missing:
        raise RuntimeError(
            f"{missing} rows of agent.investigation_steps_old are not in agent.investigation_steps"
        )
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        DROP VIEW agent.investigation_steps_p;
        DROP TABLE agent.investigation_steps_old;
        DROP TABLE agent.steps_backfill;
        ALTER TABLE agent.investigation_steps
            RENAME CONSTRAINT investigation_steps_p_pkey TO investigation_steps_pkey;
    """)


def downgrade() -> None:
    # Copies every row back, so 0003's downgrade finds the counts equal. Stop the writers first.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        ALTER TABLE agent.investigation_steps
            RENAME CONSTRAINT investigation_steps_pkey TO investigation_steps_p_pkey;
        CREATE TABLE agent.investigation_steps_old (
            investigation_id uuid NOT NULL,
            seq integer NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            schema_version integer NOT NULL,
            event jsonb NOT NULL,
            CONSTRAINT investigation_steps_pkey PRIMARY KEY (investigation_id, seq),
            CONSTRAINT investigation_steps_investigation_id_fkey FOREIGN KEY (investigation_id)
                REFERENCES agent.investigations (id) ON DELETE CASCADE
        );
        INSERT INTO agent.investigation_steps_old
            (investigation_id, seq, created_at, schema_version, event)
        SELECT investigation_id, seq, created_at, schema_version, event
        FROM agent.investigation_steps;
        CREATE VIEW agent.investigation_steps_p AS SELECT * FROM agent.investigation_steps_old;
        CREATE TABLE agent.steps_backfill (
            last_investigation_id uuid NOT NULL,
            last_seq integer NOT NULL
        );
        INSERT INTO agent.steps_backfill VALUES ('00000000-0000-0000-0000-000000000000', 0);
    """)
