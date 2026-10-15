"""AI pipeline: model calls, the events outbox, and what the loop and propose_action record.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-15
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand only: new tables, nullable columns, and a constant default, which rewrites no rows.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        CREATE TYPE agent.model_role AS ENUM ('primary', 'fallback', 'small');
        CREATE TYPE agent.model_call_outcome AS ENUM
            ('ok', 'rate_limited', 'quota_exhausted', 'error');

        CREATE TABLE agent.model_calls (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            investigation_id uuid NOT NULL
                REFERENCES agent.investigations (id) ON DELETE CASCADE,
            role agent.model_role NOT NULL,
            provider text NOT NULL,
            model text NOT NULL,
            outcome agent.model_call_outcome NOT NULL,
            input_tokens integer NOT NULL,
            output_tokens integer NOT NULL,
            cached_tokens integer NOT NULL,
            thought_tokens integer NOT NULL,
            input_bound integer,
            max_output_tokens integer,
            latency_ms integer NOT NULL,
            error text,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_model_calls_investigation_id ON agent.model_calls (investigation_id);
        CREATE INDEX ix_model_calls_model ON agent.model_calls (model, created_at);

        CREATE TABLE agent.outbox (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            message_type text NOT NULL,
            group_id text NOT NULL,
            deduplication_id text NOT NULL,
            body jsonb NOT NULL,
            attempts integer NOT NULL DEFAULT 0,
            next_attempt_at timestamptz NOT NULL DEFAULT now(),
            last_error text,
            failed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_outbox_next_attempt_at ON agent.outbox (next_attempt_at)
            WHERE failed_at IS NULL;

        ALTER TABLE agent.investigations ADD COLUMN started_at timestamptz;
        ALTER TABLE agent.tool_calls ADD COLUMN provider_state text;
        ALTER TABLE agent.proposed_actions
            ADD COLUMN evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb;
    """)


def downgrade() -> None:
    # Unsent outbox rows are lost; let the relay drain the outbox first.
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        ALTER TABLE agent.proposed_actions DROP COLUMN evidence_ids;
        ALTER TABLE agent.tool_calls DROP COLUMN provider_state;
        ALTER TABLE agent.investigations DROP COLUMN started_at;
        DROP TABLE agent.outbox, agent.model_calls;
        DROP TYPE agent.model_call_outcome, agent.model_role;
    """)
