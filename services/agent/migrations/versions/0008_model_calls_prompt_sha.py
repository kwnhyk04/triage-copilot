"""Model calls record the sha256 of the prompt the agent ran, so an eval files a run only under
the prompt its calls used.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-16
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand only: a nullable column changes the catalog and rewrites no rows.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        ALTER TABLE agent.model_calls ADD COLUMN prompt_sha text;
    """)


def downgrade() -> None:
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        ALTER TABLE agent.model_calls DROP COLUMN prompt_sha;
    """)
