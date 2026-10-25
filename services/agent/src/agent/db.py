"""Agent tables at the head revision, as the Alembic revisions in services/agent/migrations end."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import UserDefinedType


class Base(DeclarativeBase):
    metadata = MetaData(
        schema="agent", naming_convention={"ix": "ix_%(table_name)s_%(column_0_name)s"}
    )
    type_annotation_map = {  # noqa: RUF012
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        str: Text,
    }


class Vector(UserDefinedType[list[float]]):
    """pgvector column. DDL only: nothing reads or writes embeddings yet."""

    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **_kw: Any) -> str:
        return f"vector({self.dimensions})"


class Investigation(Base):
    __tablename__ = "investigations"

    # The id the gateway assigned in InvestigationRequested.
    id: Mapped[UUID] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(
        Enum(
            "queued",
            # No longer written: budget waits now park as queued with waiting_reason "budget".
            "queued_budget",
            "running",
            "awaiting_approval",
            "resolved",
            "failed",
            "cancelled",
            name="investigation_status",
        ),
        server_default="queued",
    )
    budget_tokens_used: Mapped[int] = mapped_column(server_default="0")
    # Seq of the last recorded step; recording a step increments it under the row lock.
    step_count: Mapped[int] = mapped_column(server_default="0")
    final_report: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # From InvestigationRequested v2 (null for v1), recorded when the investigation parks.
    tenant_id: Mapped[UUID | None]
    # Set while parked: its message is deleted, and the gateway's recovery sweeper re-queues it.
    waiting_reason: Mapped[str | None] = mapped_column(
        Enum("tenant_cap", "budget", name="wait_reason")
    )
    waiting_since: Mapped[datetime | None]
    # When the investigation first took a slot; its 5-minute wall clock counts from here.
    started_at: Mapped[datetime | None]


class InvestigationStep(Base):
    """Append-only step events, range-partitioned by month on created_at (agent.partitions)."""

    __tablename__ = "investigation_steps"
    __table_args__ = {"postgresql_partition_by": "RANGE (created_at)"}  # noqa: RUF012

    investigation_id: Mapped[UUID] = mapped_column(
        ForeignKey(Investigation.id, ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(primary_key=True)
    # In the key because a partitioned table's unique keys must include the partition column.
    created_at: Mapped[datetime] = mapped_column(primary_key=True, server_default=func.now())
    schema_version: Mapped[int]
    event: Mapped[dict[str, Any]]


class ToolCall(Base):
    __tablename__ = "tool_calls"

    # Tool result id such as tc_01J9ZK3Q7M; step events and report evidence cite it.
    id: Mapped[str] = mapped_column(primary_key=True)
    investigation_id: Mapped[UUID] = mapped_column(
        ForeignKey(Investigation.id, ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    args: Mapped[dict[str, Any]]
    # Where the full, possibly paginated result is kept; null when the call failed.
    result_ref: Mapped[str | None]
    # Null until the call returns: the row is written when the model asks for the call.
    latency_ms: Mapped[int | None]
    error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Opaque provider data the model must get back with this call, such as a thought signature.
    provider_state: Mapped[str | None]


class RunbookChunk(Base):
    __tablename__ = "runbook_chunks"
    __table_args__ = (UniqueConstraint("runbook", "chunk"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # Path under runbooks/ and the chunk's position in it.
    runbook: Mapped[str]
    chunk: Mapped[int]
    content: Mapped[str]
    # ponytail: no ANN index, exact search is fast for a few thousand chunks; add HNSW past ~100k.
    embedding: Mapped[list[float]] = mapped_column(Vector(768))


class ProposedAction(Base):
    __tablename__ = "proposed_actions"

    # action_id in ActionProposed; the executor uses it as its own idempotency key.
    id: Mapped[UUID] = mapped_column(primary_key=True)
    investigation_id: Mapped[UUID] = mapped_column(
        ForeignKey(Investigation.id, ondelete="CASCADE"), index=True
    )
    # Derived from the proposing step (investigation id and seq), so a retried proposal adds no row.
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    kind: Mapped[str] = mapped_column(
        Enum(
            "rollback_deploy",
            "restart_service",
            "scale_service",
            "disable_feature_flag",
            name="action_kind",
        )
    )
    target: Mapped[str]
    params: Mapped[dict[str, Any]]
    rationale: Mapped[str]
    status: Mapped[str] = mapped_column(
        Enum("pending", "approved", "rejected", name="action_status"), server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Ids of the tool results the proposal rests on.
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))


class DeadLetter(Base):
    """Queue messages that failed schema validation; `python -m agent.dlq replay` re-sends them."""

    __tablename__ = "dead_letters"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    message_id: Mapped[str]
    # The SQS MessageGroupId, reused when the message is replayed.
    group_id: Mapped[str]
    body: Mapped[str]
    error: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    replayed_at: Mapped[datetime | None]


class AiUsage(Base):
    """Model tokens spent per UTC day, input and output combined; the budget guard reads it."""

    __tablename__ = "ai_usage"

    day: Mapped[date] = mapped_column(primary_key=True)
    tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")


class ModelCall(Base):
    """Every request to a model provider: the limiter counts them, and evals read their usage."""

    __tablename__ = "model_calls"
    __table_args__ = (Index(None, "model", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    investigation_id: Mapped[UUID] = mapped_column(
        ForeignKey(Investigation.id, ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(Enum("primary", "fallback", "small", name="model_role"))
    provider: Mapped[str]
    model: Mapped[str]
    outcome: Mapped[str] = mapped_column(
        Enum(
            "ok",
            "rate_limited",
            "quota_exhausted",
            "model_exhausted",
            "credits_exhausted",
            "error",
            # The agent sent no request (since 0012): spent() never reads it as a stop.
            "skipped",
            name="model_call_outcome",
        )
    )
    # All input tokens, cached ones included.
    input_tokens: Mapped[int]
    # Thinking tokens included: they are billed as output.
    output_tokens: Mapped[int]
    # Of the input: read from the provider's prompt cache, and written to it (since 0009).
    cached_tokens: Mapped[int]
    cache_write_tokens: Mapped[int] = mapped_column(server_default="0")
    thought_tokens: Mapped[int]
    # The hard cap's upper bound on input_tokens, kept to check that it held.
    input_bound: Mapped[int | None]
    max_output_tokens: Mapped[int | None]
    latency_ms: Mapped[int]
    error: Mapped[str | None]
    # sha256 of the prompt the call was made with (agent.job.prompt_sha); null before 0008.
    prompt_sha: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Switch(Base):
    """An operator's kill switch (agent.switches); a switch with no row is at its default."""

    __tablename__ = "switches"
    __table_args__ = (CheckConstraint("name IN ('live', 'claude')", name="switches_name_check"),)

    name: Mapped[str] = mapped_column(primary_key=True)
    enabled: Mapped[bool]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Outbox(Base):
    """Events committed with the rows that caused them; the relay sends them to the gateway."""

    __tablename__ = "outbox"
    __table_args__ = (Index(None, "next_attempt_at", postgresql_where=text("failed_at IS NULL")),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # The SQS message attribute message_type: step-recorded or report-recorded.
    message_type: Mapped[str]
    group_id: Mapped[str]
    deduplication_id: Mapped[str]
    body: Mapped[dict[str, Any]]
    attempts: Mapped[int] = mapped_column(server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_error: Mapped[str | None]
    # Set after too many failed sends; such rows wait for a human.
    failed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
