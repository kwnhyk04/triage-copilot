"""Waiting investigations: parked until a tenant slot or the model budget frees.

Their message is deleted while they wait; the gateway's recovery sweeper re-queues them.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-15
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand only: nullable columns change the catalog and rewrite no rows.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        CREATE TYPE agent.wait_reason AS ENUM ('tenant_cap', 'budget');
        ALTER TABLE agent.investigations
            ADD COLUMN tenant_id uuid,
            ADD COLUMN waiting_reason agent.wait_reason,
            ADD COLUMN waiting_since timestamptz;
    """)


def downgrade() -> None:
    # Waiting investigations have no message; let the sweeper re-queue them first.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        ALTER TABLE agent.investigations
            DROP COLUMN waiting_since, DROP COLUMN waiting_reason, DROP COLUMN tenant_id;
        DROP TYPE agent.wait_reason;
    """)
