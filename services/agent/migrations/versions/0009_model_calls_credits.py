"""Model calls: the credits_exhausted outcome, which keeps Claude off until the month ends, and
the tokens a call wrote to the provider's prompt cache, which bill at their own rate.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-17
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand only. Postgres 12+ adds an enum value inside a transaction; nothing uses it before the
    # commit. A column with a constant default changes the catalog and rewrites no rows.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        ALTER TYPE agent.model_call_outcome ADD VALUE 'credits_exhausted' AFTER 'quota_exhausted';
        ALTER TABLE agent.model_calls ADD COLUMN cache_write_tokens integer NOT NULL DEFAULT 0;
    """)


def downgrade() -> None:
    # Postgres cannot drop an enum value, so the type is rebuilt without it; a credits stop is
    # kept as a spent quota, which ends at midnight Pacific instead of the month's end.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        ALTER TABLE agent.model_calls DROP COLUMN cache_write_tokens;
        UPDATE agent.model_calls SET outcome = 'quota_exhausted'
            WHERE outcome = 'credits_exhausted';
        ALTER TYPE agent.model_call_outcome RENAME TO model_call_outcome_old;
        CREATE TYPE agent.model_call_outcome AS ENUM
            ('ok', 'rate_limited', 'quota_exhausted', 'error');
        ALTER TABLE agent.model_calls
            ALTER COLUMN outcome TYPE agent.model_call_outcome
                USING outcome::text::agent.model_call_outcome;
        DROP TYPE agent.model_call_outcome_old;
    """)
