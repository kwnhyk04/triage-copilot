"""The plan reviewer (Part B): model calls of role reviewer, the reviewer switch, and
agent.plans.review, the review of a plan a model gave (by the code checks or the reviewer's Claude).
A switch with no row is at its default, so reviewer starts on and the migration seeds nothing.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-03
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand only: a new enum value (Postgres 12+ adds one inside a transaction; nothing uses it
    # before the commit), a wider check and a nullable column, so no existing row or reader changes.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        ALTER TYPE agent.model_role ADD VALUE IF NOT EXISTS 'reviewer';
        ALTER TABLE agent.switches DROP CONSTRAINT switches_name_check;
        ALTER TABLE agent.switches ADD CONSTRAINT switches_name_check
            CHECK (name IN ('live', 'claude', 'planner', 'actions', 'reviewer'));
        ALTER TABLE agent.plans ADD COLUMN review jsonb;
    """)


def downgrade() -> None:
    # Postgres cannot drop an enum value, so the type is rebuilt without it; a review's calls are
    # kept as the planner's, whose daily and monthly caps then count them, and the reviewer switch
    # and the reviews go.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        ALTER TABLE agent.plans DROP COLUMN review;
        DELETE FROM agent.switches WHERE name = 'reviewer';
        ALTER TABLE agent.switches DROP CONSTRAINT switches_name_check;
        ALTER TABLE agent.switches ADD CONSTRAINT switches_name_check
            CHECK (name IN ('live', 'claude', 'planner', 'actions'));
        UPDATE agent.model_calls SET role = 'planner' WHERE role = 'reviewer';
        ALTER TYPE agent.model_role RENAME TO model_role_old;
        CREATE TYPE agent.model_role AS ENUM ('primary', 'fallback', 'small', 'planner');
        ALTER TABLE agent.model_calls
            ALTER COLUMN role TYPE agent.model_role USING role::text::agent.model_role;
        DROP TYPE agent.model_role_old;
    """)
