"""Agent tables; investigation_steps starts unpartitioned.

Revision ID: 0001
Revises:
Create Date: 2026-10-14
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        CREATE TYPE agent.investigation_status AS ENUM
            ('queued', 'running', 'awaiting_approval', 'resolved', 'failed', 'cancelled');
        CREATE TYPE agent.action_kind AS ENUM
            ('rollback_deploy', 'restart_service', 'scale_service', 'disable_feature_flag');
        CREATE TYPE agent.action_status AS ENUM ('pending', 'approved', 'rejected');

        CREATE TABLE agent.investigations (
            id uuid PRIMARY KEY,
            status agent.investigation_status NOT NULL DEFAULT 'queued',
            budget_tokens_used integer NOT NULL DEFAULT 0,
            step_count integer NOT NULL DEFAULT 0,
            final_report jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE agent.investigation_steps (
            investigation_id uuid NOT NULL REFERENCES agent.investigations (id) ON DELETE CASCADE,
            seq integer NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            schema_version integer NOT NULL,
            event jsonb NOT NULL,
            PRIMARY KEY (investigation_id, seq)
        );

        CREATE TABLE agent.tool_calls (
            id text PRIMARY KEY,
            investigation_id uuid NOT NULL REFERENCES agent.investigations (id) ON DELETE CASCADE,
            name text NOT NULL,
            args jsonb NOT NULL,
            result_ref text,
            latency_ms integer NOT NULL,
            error text,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_tool_calls_investigation_id ON agent.tool_calls (investigation_id);

        CREATE TABLE agent.runbook_chunks (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            runbook text NOT NULL,
            chunk integer NOT NULL,
            content text NOT NULL,
            embedding vector(768) NOT NULL,
            UNIQUE (runbook, chunk)
        );

        CREATE TABLE agent.proposed_actions (
            id uuid PRIMARY KEY,
            investigation_id uuid NOT NULL REFERENCES agent.investigations (id) ON DELETE CASCADE,
            idempotency_key text NOT NULL UNIQUE,
            kind agent.action_kind NOT NULL,
            target text NOT NULL,
            params jsonb NOT NULL,
            rationale text NOT NULL,
            status agent.action_status NOT NULL DEFAULT 'pending',
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_proposed_actions_investigation_id
            ON agent.proposed_actions (investigation_id);
    """)


def downgrade() -> None:
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        DROP TABLE agent.proposed_actions, agent.runbook_chunks, agent.tool_calls,
            agent.investigation_steps, agent.investigations;
        DROP TYPE agent.action_status, agent.action_kind, agent.investigation_status;
    """)
