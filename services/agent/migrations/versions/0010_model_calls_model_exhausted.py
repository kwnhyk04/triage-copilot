"""Model calls: the model_exhausted outcome, Anthropic's 429 without retry-after, which keeps only
that model off until the month ends; credits_exhausted now keeps every model of its provider off.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-25
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand only. Postgres 12+ adds an enum value inside a transaction; nothing uses it before the
    # commit.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        ALTER TYPE agent.model_call_outcome ADD VALUE 'model_exhausted' AFTER 'quota_exhausted';
    """)


def downgrade() -> None:
    # Postgres cannot drop an enum value, so the type is rebuilt without it; a model stop is kept
    # as a credits stop, which the agent before 0010 read per model until the month ends.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        UPDATE agent.model_calls SET outcome = 'credits_exhausted'
            WHERE outcome = 'model_exhausted';
        ALTER TYPE agent.model_call_outcome RENAME TO model_call_outcome_old;
        CREATE TYPE agent.model_call_outcome AS ENUM
            ('ok', 'rate_limited', 'quota_exhausted', 'credits_exhausted', 'error');
        ALTER TABLE agent.model_calls
            ALTER COLUMN outcome TYPE agent.model_call_outcome
                USING outcome::text::agent.model_call_outcome;
        DROP TYPE agent.model_call_outcome_old;
    """)
