"""Investigation steps as versioned StepRecorded events (docs/migrations.md, story B).

Stored rows keep the version they were written with; reads upcast them to the current one. The
StepRecorded v3 queue message (contracts/messages) carries a stored v2 event unchanged.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, column, func, insert, select, table, update
from sqlalchemy.dialects.postgresql import JSONB

from agent.db import Investigation, InvestigationStep
from py_clients.generated.messages.step_recorded_v3_schema import StepRecordedV3

StepKind = Literal["tool_call", "hypothesis", "action", "report"]


# Each version is spelled out in full, never derived from another, so it cannot drift.
class StepRecordedV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: StepKind
    summary: str
    tool_name: str | None
    tool_call_id: str | None
    observation: str | None


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input: Annotated[int, Field(ge=0)]
    output: Annotated[int, Field(ge=0)]


class StepRecordedV2(BaseModel):
    """v2 renames observation to result and adds latency_ms and tokens.

    Its limits are StepRecorded v3's, so every v2 event that can be built is a valid v3 payload.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: StepKind
    # Python counts code points, as JSON Schema's maxLength does.
    summary: Annotated[str, Field(min_length=1, max_length=2000)]
    tool_name: str | None
    tool_call_id: str | None
    result: str | None
    latency_ms: Annotated[int, Field(ge=0)] | None
    tokens: TokenUsage | None


CURRENT_VERSION = 2


def _v1_to_v2(event: dict[str, Any]) -> dict[str, Any]:
    v1 = StepRecordedV1.model_validate(event)
    # v1 never measured latency or tokens: unknown, not zero.
    return {
        **v1.model_dump(exclude={"observation"}),
        "result": v1.observation,
        "latency_ms": None,
        "tokens": None,
    }


# Upcaster from each old version to the next one; upcast chains them up to CURRENT_VERSION.
UPCASTERS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {1: _v1_to_v2}


def upcast(schema_version: int, event: dict[str, Any]) -> StepRecordedV2:
    for version in range(schema_version, CURRENT_VERSION):
        event = UPCASTERS[version](event)
    return StepRecordedV2.model_validate(event)


def replay(conn: Connection, investigation_id: UUID) -> list[StepRecordedV2]:
    """The investigation's steps in order as current-version events; stored rows stay unchanged."""
    rows = conn.execute(
        select(InvestigationStep.schema_version, InvestigationStep.event)
        .where(InvestigationStep.investigation_id == investigation_id)
        .order_by(InvestigationStep.seq)
    )
    return [upcast(version, event) for version, event in rows]


def step_message(
    investigation_id: UUID, seq: int, recorded_at: datetime, event: StepRecordedV2
) -> StepRecordedV3:
    """The StepRecorded v3 message for a stored step: the event unchanged, plus where and when."""
    envelope = {"investigation_id": investigation_id, "seq": seq, "recorded_at": recorded_at}
    return StepRecordedV3.model_validate({"schema_version": 3, **envelope, **event.model_dump()})


# Story A's dual-write target: the partitioned copy before the swap, a view on the old table after.
_SHADOW = table(
    "investigation_steps_p",
    column("investigation_id"),
    column("seq"),
    column("created_at"),
    column("schema_version"),
    column("event", JSONB),
    schema="agent",
)


def record_step(
    conn: Connection, investigation_id: UUID, event: StepRecordedV2, *, dual_write: bool
) -> StepRecordedV3:
    """Appends the step as the investigation's next seq, in the caller's transaction.

    Returns the StepRecorded v3 message to publish once that transaction commits. The seq comes
    from investigations.step_count under its row lock: the partitioned table cannot enforce
    unique (investigation_id, seq) across months, so the numbering has to.
    """
    seq, now = conn.execute(
        update(Investigation)
        .where(Investigation.id == investigation_id)
        .values(step_count=Investigation.step_count + 1, updated_at=func.now())
        .returning(Investigation.step_count, func.now())
    ).one()
    # Built before the rows: an event that skipped validation (model_construct, model_copy) and
    # breaks a v3 limit raises here, and the caller's transaction rolls back without storing it.
    message = step_message(investigation_id, seq, now, event)
    row = {
        "investigation_id": investigation_id,
        "seq": seq,
        "created_at": now,
        "schema_version": CURRENT_VERSION,
        "event": event.model_dump(mode="json"),
    }
    conn.execute(insert(InvestigationStep), row)
    # After the live table, as the swap migration locks them, so the two never deadlock.
    if dual_write:
        conn.execute(insert(_SHADOW), row)
    return message
