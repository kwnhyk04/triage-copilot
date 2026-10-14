"""Agent tables at the head revision, as the Alembic revisions in services/agent/migrations end."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    MetaData,
    Text,
    UniqueConstraint,
    func,
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
    latency_ms: Mapped[int]
    error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


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
