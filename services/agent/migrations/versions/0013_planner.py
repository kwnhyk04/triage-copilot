"""The planner (P2): model calls of role planner, the planner switch, and agent.plans, one row per
real incident whose report asked for a plan, completed with the plan or the problem that left none.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-26
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand only: a new enum value (Postgres 12+ adds one inside a transaction; nothing uses it
    # before the commit), a wider check, and a new table, so no existing row or reader changes.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        ALTER TYPE agent.model_role ADD VALUE 'planner';
        ALTER TABLE agent.switches DROP CONSTRAINT switches_name_check;
        ALTER TABLE agent.switches ADD CONSTRAINT switches_name_check
            CHECK (name IN ('live', 'claude', 'planner'));
        CREATE TABLE agent.plans (
            investigation_id uuid PRIMARY KEY
                REFERENCES agent.investigations (id) ON DELETE CASCADE,
            id uuid NOT NULL UNIQUE,
            plan jsonb,
            problem text,
            created_at timestamptz NOT NULL DEFAULT now(),
            planned_at timestamptz
        );
    """)


def downgrade() -> None:
    # Postgres cannot drop an enum value, so the type is rebuilt without it; a plan's calls are
    # kept as the primary role's, and the planner switch and the plans go.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        DROP TABLE agent.plans;
        DELETE FROM agent.switches WHERE name = 'planner';
        ALTER TABLE agent.switches DROP CONSTRAINT switches_name_check;
        ALTER TABLE agent.switches ADD CONSTRAINT switches_name_check
            CHECK (name IN ('live', 'claude'));
        UPDATE agent.model_calls SET role = 'primary' WHERE role = 'planner';
        ALTER TYPE agent.model_role RENAME TO model_role_old;
        CREATE TYPE agent.model_role AS ENUM ('primary', 'fallback', 'small');
        ALTER TABLE agent.model_calls
            ALTER COLUMN role TYPE agent.model_role USING role::text::agent.model_role;
        DROP TYPE agent.model_role_old;
    """)
