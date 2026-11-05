"""What a failed model call may have cost: agent.model_calls.estimated_usd, which the dollar caps
add and the token budgets never do. A timeout or a lost connection leaves no usage to record, and
Claude bills what it generated.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-06
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand only: a constant default rewrites no row, and an older agent's inserts leave it 0.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        ALTER TABLE agent.model_calls
            ADD COLUMN estimated_usd double precision NOT NULL DEFAULT 0;
    """)


def downgrade() -> None:
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        ALTER TABLE agent.model_calls DROP COLUMN estimated_usd;
    """)
