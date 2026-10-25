"""Kill switches: agent.switches, which the operator flips to hold live investigations or stop
every Claude call without a restart. A switch with no row is at its default: live off, claude on.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-25
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand only: a new table, so no existing row or reader changes.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        CREATE TABLE agent.switches (
            name text PRIMARY KEY CONSTRAINT switches_name_check CHECK (name IN ('live', 'claude')),
            enabled boolean NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        );
    """)


def downgrade() -> None:
    # The switches go back to their defaults with the table.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        DROP TABLE agent.switches;
    """)
