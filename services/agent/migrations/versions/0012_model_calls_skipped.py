"""Model calls: the skipped outcome, a call the agent never sent (its limits, the claude switch,
live triage's dollar cap). spent() reads only what a provider answered, so a skip ends with its
reason: Claude switched back on answers at once, not after midnight Pacific.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-25
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand only. Postgres 12+ adds an enum value inside a transaction; nothing uses it before the
    # commit.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        ALTER TYPE agent.model_call_outcome ADD VALUE 'skipped' AFTER 'error';
    """)


def downgrade() -> None:
    # Postgres cannot drop an enum value, so the type is rebuilt without it; a skip is kept as a
    # spent day, as the agent before 0012 recorded it.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        UPDATE agent.model_calls SET outcome = 'quota_exhausted' WHERE outcome = 'skipped';
        ALTER TYPE agent.model_call_outcome RENAME TO model_call_outcome_old;
        CREATE TYPE agent.model_call_outcome AS ENUM
            ('ok', 'rate_limited', 'quota_exhausted', 'model_exhausted', 'credits_exhausted',
             'error');
        ALTER TABLE agent.model_calls
            ALTER COLUMN outcome TYPE agent.model_call_outcome
                USING outcome::text::agent.model_call_outcome;
        DROP TYPE agent.model_call_outcome_old;
    """)
