"""The actions switch (P3.2): the gateway asks for it before every claim of the executor. A switch
with no row is at its default, so actions starts off and the migration seeds nothing.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-02
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand only: a wider check, so no existing row or reader changes.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        ALTER TABLE agent.switches DROP CONSTRAINT switches_name_check;
        ALTER TABLE agent.switches ADD CONSTRAINT switches_name_check
            CHECK (name IN ('live', 'claude', 'planner', 'actions'));
    """)


def downgrade() -> None:
    # The actions switch goes with its name, back to off, which a missing row also means.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        DELETE FROM agent.switches WHERE name = 'actions';
        ALTER TABLE agent.switches DROP CONSTRAINT switches_name_check;
        ALTER TABLE agent.switches ADD CONSTRAINT switches_name_check
            CHECK (name IN ('live', 'claude', 'planner'));
    """)
