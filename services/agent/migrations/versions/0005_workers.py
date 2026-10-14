"""Workers: the queued_budget status, poison-message store, and model tokens per UTC day.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-14
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres 12+ adds an enum value inside a transaction; nothing uses it before the commit.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        ALTER TYPE agent.investigation_status ADD VALUE 'queued_budget' AFTER 'queued';

        CREATE TABLE agent.dead_letters (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            message_id text NOT NULL,
            group_id text NOT NULL,
            body text NOT NULL,
            error text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            replayed_at timestamptz
        );

        CREATE TABLE agent.ai_usage (
            day date PRIMARY KEY,
            tokens bigint NOT NULL DEFAULT 0
        );

        -- A tool call is stored when the model asks for it; its latency arrives with the result.
        ALTER TABLE agent.tool_calls ALTER COLUMN latency_ms DROP NOT NULL;
    """)


def downgrade() -> None:
    # Postgres cannot drop an enum value, so the type is rebuilt without it.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        -- Calls that never returned have no latency to keep; the steps that asked for them stay.
        DELETE FROM agent.tool_calls WHERE latency_ms IS NULL;
        ALTER TABLE agent.tool_calls ALTER COLUMN latency_ms SET NOT NULL;
        DROP TABLE agent.ai_usage, agent.dead_letters;
        UPDATE agent.investigations SET status = 'queued' WHERE status = 'queued_budget';
        ALTER TYPE agent.investigation_status RENAME TO investigation_status_old;
        CREATE TYPE agent.investigation_status AS ENUM
            ('queued', 'running', 'awaiting_approval', 'resolved', 'failed', 'cancelled');
        ALTER TABLE agent.investigations
            ALTER COLUMN status DROP DEFAULT,
            ALTER COLUMN status TYPE agent.investigation_status
                USING status::text::agent.investigation_status,
            ALTER COLUMN status SET DEFAULT 'queued';
        DROP TYPE agent.investigation_status_old;
    """)
