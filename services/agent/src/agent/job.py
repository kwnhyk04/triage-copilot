"""One investigation, resumable at every step.

Each model turn and each tool result is recorded as a step, in one transaction with its tool_calls
row. A redelivered message replays the recorded steps and goes on after the last one, so no
recorded tool call runs twice. A tool_call step that carries tokens is the model's request: its
tool_calls row, written with it, holds the arguments, and the result step after it completes the
row. As StepRecorded v3 defines them, `result` and `latency_ms` belong to tool results only.

An investigation that must wait for a slot under its tenant's cap or for model budget parks: it is
marked waiting and its message is deleted, so it holds no worker slot and uses up no receives. The
gateway's recovery sweeper re-queues it once `Job.resumable` says its slot or budget is free.

At MAX_TOOL_CALLS tool calls or MAX_TOKENS model tokens an investigation ends with a partial report
(`Report.partial`) instead of another model turn; that is a finished investigation, not an error.
"""

import asyncio
import logging
import random
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Connection, Date, Engine, cast, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from agent.db import AiUsage, DeadLetter, Investigation, ToolCall
from agent.model import Model, ModelError, ModelTurn, Report, ToolRequest, is_model_turn
from agent.steps import StepRecordedV2, record_step, replay
from py_clients.generated.messages.investigation_requested_v1_schema import InvestigationRequestedV1
from py_clients.generated.messages.investigation_requested_v2_schema import InvestigationRequestedV2

log = logging.getLogger(__name__)

Request = InvestigationRequestedV1 | InvestigationRequestedV2
Outcome = Literal["done", "stopped", "busy", "parked"]
"""done: the final report is committed. stopped: shutdown began. busy: another agent runs it.
parked: it waits for a tenant slot or model budget, so its message can go."""
WaitReason = Literal["tenant_cap", "budget"]
Tool = Callable[[dict[str, Any]], Awaitable[str]]
"""A read-only tool: arguments in, result text out."""

MODEL_TIMEOUT = 60.0
MODEL_RETRIES = 3
TOOL_TIMEOUT = 10.0
TOOL_RETRIES = 3
# Hard caps per investigation: a model turn starts only while both are under them.
MAX_TOOL_CALLS = 15
MAX_TOKENS = 150_000
# Not "failed": a failed investigation resumes when its message is replayed from the DLQ.
FINISHED = ("awaiting_approval", "resolved", "cancelled")
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
UTC_TODAY = cast(func.timezone("UTC", func.now()), Date)
# Session locks: one per investigation, and `cap` slots per tenant.
LOCK_INVESTIGATION = "SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"
LOCK_TENANT_SLOT = "SELECT pg_try_advisory_lock(hashtext(:key), :slot)"
# Slots of a tenant's cap that no job holds; pg_locks shows LOCK_TENANT_SLOT locks this way.
FREE_SLOTS = text("""
    SELECT :cap - count(*) FROM pg_locks
    WHERE locktype = 'advisory' AND granted AND objsubid = 2 AND classid = hashtext(:key)::oid
      AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
""")
# Investigations of the same tenant that parked for a slot earlier: first parked, first resumed.
PARKED_AHEAD = text("""
    SELECT count(*) FROM agent.investigations w JOIN agent.investigations me ON me.id = :id
    WHERE w.waiting_reason = 'tenant_cap' AND w.tenant_id IS NOT DISTINCT FROM me.tenant_id
      AND (w.waiting_since, w.id) < (me.waiting_since, me.id)
""")


class Stopped(Exception):
    """Shutdown began during a wait between attempts."""


async def sleep_unless(stopping: asyncio.Event, seconds: float) -> bool:
    """Waits up to `seconds`; returns True if shutdown began meanwhile."""
    try:
        await asyncio.wait_for(stopping.wait(), seconds)
    except TimeoutError:
        return False
    return True


def backoff(attempt: int) -> float:
    """Full jitter: 0 to 2**attempt seconds."""
    return random.uniform(0, 2**attempt)  # noqa: S311 - jitter, not cryptography


class Job:
    """Runs investigations; one instance serves every message of the agent process."""

    def __init__(
        self,
        engine: Engine,
        model: Model,
        tools: Mapping[str, Tool],
        *,
        ai_enabled: bool,
        daily_token_budget: int,
        tenant_cap: int,
        dual_write: bool = False,
    ) -> None:
        self.engine = engine
        self.model = model
        self.tools = tools
        self.ai_enabled = ai_enabled
        self.daily_token_budget = daily_token_budget
        self.tenant_cap = tenant_cap
        self.dual_write = dual_write

    async def run(self, request: Request, stopping: asyncio.Event) -> Outcome:
        """Runs the investigation to its final report, or until shutdown.

        Holds one database session for the whole run. Its advisory locks keep a duplicate delivery
        from running the same investigation and cap each tenant, and they end with the session if
        the process dies.
        """
        conn = await asyncio.to_thread(self.engine.connect)
        try:
            return await self._run(conn, request, stopping)
        finally:
            await asyncio.to_thread(_release, conn)

    async def _run(self, conn: Connection, request: Request, stopping: asyncio.Event) -> Outcome:
        inv = request.investigation_id
        if not await asyncio.to_thread(_try_lock, conn, LOCK_INVESTIGATION, key=str(inv)):
            return "busy"
        status, steps = await asyncio.to_thread(_load, conn, inv)
        if status in FINISHED:
            return "done"
        tenant = request.tenant_id if isinstance(request, InvestigationRequestedV2) else None
        # The budget check comes before the tenant slot, so parking for budget never held a slot.
        if not steps and not await asyncio.to_thread(self._model_available, conn, True):
            return await asyncio.to_thread(_park, conn, inv, "budget", tenant)
        if not await asyncio.to_thread(_take_tenant_slot, conn, tenant, self.tenant_cap):
            return await asyncio.to_thread(_park, conn, inv, "tenant_cap", tenant)
        await asyncio.to_thread(_start, conn, inv)
        try:
            while not stopping.is_set():
                last = steps[-1] if steps else None
                if last is not None and last.kind == "tool_call" and is_model_turn(last):
                    steps.append(await self._call_tool(conn, inv, last, stopping))
                    continue
                # ponytail: checked before each turn, so the last turn can pass MAX_TOKENS by its
                # own tokens; step 6 can cap a turn's output at the tokens left.
                calls, tokens = usage(steps)
                if calls >= MAX_TOOL_CALLS or tokens >= MAX_TOKENS:
                    await asyncio.to_thread(
                        _record_partial_report, conn, inv, calls, tokens, self.dual_write
                    )
                    return "done"
                # With AI_ENABLED=false even a resumed investigation parks at its next model turn.
                if not await asyncio.to_thread(self._model_available, conn, not steps):
                    return await asyncio.to_thread(_park, conn, inv, "budget", tenant)
                turn = await self._model_turn(steps, stopping)
                event = await asyncio.to_thread(_record_turn, conn, inv, turn, self.dual_write)
                steps.append(event)
                if event.kind == "report":
                    return "done"
        except Stopped:
            pass
        return "stopped"

    def _model_available(self, conn: Connection, new: bool) -> bool:
        """Model calls are on and, for a new investigation, today's token budget is not spent."""
        return self.ai_enabled and (not new or _tokens_today(conn) < self.daily_token_budget)

    def resumable(
        self, conn: Connection, inv: UUID, reason: str | None, tenant: UUID | None, new: bool
    ) -> bool:
        """Whether a parked investigation can run now.

        Budget: model calls are available again. Tenant cap: a slot is free, and fewer of the
        tenant's investigations parked for one before it than there are free slots. A re-queued
        investigation stays parked until it takes its slot, so it keeps its place in that line.
        """
        if reason == "budget":
            return self._model_available(conn, new)
        if reason != "tenant_cap":
            return False
        with conn.begin():
            free = conn.execute(FREE_SLOTS, {"key": tenant_key(tenant), "cap": self.tenant_cap})
            ahead = conn.execute(PARKED_AHEAD, {"id": inv})
            return ahead.scalar_one() < free.scalar_one()

    async def _model_turn(self, steps: list[StepRecordedV2], stopping: asyncio.Event) -> ModelTurn:
        """One model call; retries a failure, after the provider's Retry-After if it sent one."""
        for attempt in range(MODEL_RETRIES + 1):
            try:
                return await asyncio.wait_for(self.model.turn(steps), MODEL_TIMEOUT)
            except (ModelError, TimeoutError) as error:
                if attempt == MODEL_RETRIES:
                    raise
                retry_after = error.retry_after if isinstance(error, ModelError) else None
                delay = backoff(attempt) if retry_after is None else retry_after
                log.warning("model call failed; retrying", extra={"delay_s": delay})
                # ponytail: a long Retry-After holds this message's slot; hand the message back
                # to SQS with ChangeMessageVisibility if daily-quota waits become common.
                if await sleep_unless(stopping, delay):
                    raise Stopped from error
        raise AssertionError("unreachable")

    async def _call_tool(
        self, conn: Connection, inv: UUID, requested: StepRecordedV2, stopping: asyncio.Event
    ) -> StepRecordedV2:
        """Runs the tool call the last step asked for and records its result."""
        call_id = requested.tool_call_id or ""
        name, args = await asyncio.to_thread(_requested_call, conn, call_id)
        started = time.monotonic()
        result, error = await self._invoke(name, args, stopping)
        summary = f"{name} returned" if error is None else f"{name} failed: {error}"
        event = StepRecordedV2(
            kind="tool_call",
            summary=summary[:2000],
            tool_name=name,
            tool_call_id=call_id,
            result=result,
            latency_ms=_elapsed_ms(started),
            tokens=None,
        )
        await asyncio.to_thread(_record_tool_call, conn, inv, event, error, self.dual_write)
        return event

    async def _invoke(
        self, name: str, args: dict[str, Any], stopping: asyncio.Event
    ) -> tuple[str | None, str | None]:
        """(result, None), or (None, error) once retries are spent; the investigation goes on."""
        tool = self.tools.get(name)
        if tool is None:
            return None, f"unknown tool {name}"
        for attempt in range(TOOL_RETRIES + 1):
            try:
                return await asyncio.wait_for(tool(args), TOOL_TIMEOUT), None
            except Exception as error:  # Tools only read, so any failure is safe to retry.
                if attempt == TOOL_RETRIES:
                    return None, f"{type(error).__name__}: {error}"[:500]
                if await sleep_unless(stopping, backoff(attempt)):
                    raise Stopped from error
        raise AssertionError("unreachable")

    async def dead_letter(self, message_id: str, group_id: str, body: str, error: str) -> None:
        """Keeps a message that failed schema validation, for inspection and replay."""

        def write() -> None:
            with self.engine.begin() as conn:
                row = {"message_id": message_id, "group_id": group_id, "body": body}
                conn.execute(insert(DeadLetter).values(**row, error=error))

        await asyncio.to_thread(write)

    async def mark_failed(self, investigation_id: UUID) -> None:
        """Marks an investigation whose message is about to move to the DLQ."""

        def write() -> None:
            unfinished = Investigation.status.not_in(FINISHED)
            failed = {"status": "failed", "waiting_reason": None, "waiting_since": None}
            with self.engine.begin() as conn:
                conn.execute(
                    update(Investigation)
                    .where(Investigation.id == investigation_id, unfinished)
                    .values(**failed, updated_at=func.now())
                )

        await asyncio.to_thread(write)


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def usage(steps: Sequence[StepRecordedV2]) -> tuple[int, int]:
    """Tool calls the model asked for and the tokens it spent, from the recorded steps."""
    calls = sum(step.kind == "tool_call" and is_model_turn(step) for step in steps)
    tokens = sum(s.tokens.input + s.tokens.output for s in steps if s.tokens is not None)
    return calls, tokens


def _record_partial_report(
    conn: Connection, inv: UUID, calls: int, tokens: int, dual_write: bool
) -> None:
    """Ends an investigation at its budget with a partial report instead of an error."""
    summary = (
        f"Stopped at the investigation budget after {calls} tool calls and {tokens} model tokens "
        f"(caps: {MAX_TOOL_CALLS} tool calls, {MAX_TOKENS} tokens). The findings are incomplete."
    )
    report = Report(summary=summary, hypotheses=[], partial=True)
    event = StepRecordedV2(
        kind="report",
        summary=summary,
        tool_name=None,
        tool_call_id=None,
        result=None,
        latency_ms=None,
        tokens=None,
    )
    with conn.begin():
        record_step(conn, inv, event, dual_write=dual_write)
        conn.execute(
            update(Investigation)
            .where(Investigation.id == inv)
            .values(final_report=report.model_dump(mode="json"), status="resolved")
        )
    log.info(
        "investigation stopped at its budget",
        extra={"investigation_id": str(inv), "tool_calls": calls, "tokens": tokens},
    )


def _tool_call_id() -> str:
    return "tc_" + "".join(secrets.choice(CROCKFORD) for _ in range(10))


def _try_lock(conn: Connection, sql: str, **params: object) -> bool:
    with conn.begin():
        return bool(conn.execute(text(sql), params).scalar_one())


def tenant_key(tenant: UUID | None) -> str:
    """Advisory lock key of a tenant's slots; v1 messages carry no tenant and share one key."""
    return f"tenant:{tenant or ''}"


def _take_tenant_slot(conn: Connection, tenant: UUID | None, cap: int) -> bool:
    """Takes one of the tenant's `cap` slots, so at most `cap` of its investigations run at once."""
    key = tenant_key(tenant)
    return any(_try_lock(conn, LOCK_TENANT_SLOT, key=key, slot=slot) for slot in range(cap))


def _release(conn: Connection) -> None:
    try:
        with conn.begin():
            conn.execute(text("SELECT pg_advisory_unlock_all()"))
    except Exception:
        conn.invalidate()  # Closing the session drops its locks instead.
    conn.close()


def _load(conn: Connection, inv: UUID) -> tuple[str, list[StepRecordedV2]]:
    """The checkpoint: the investigation's status and recorded steps; creates it when new."""
    with conn.begin():
        conn.execute(pg_insert(Investigation).values(id=inv).on_conflict_do_nothing())
        status = conn.execute(select(Investigation.status).where(Investigation.id == inv))
        return status.scalar_one(), replay(conn, inv)


def _requested_call(conn: Connection, call_id: str) -> tuple[str, dict[str, Any]]:
    """The tool and arguments the model asked for, from the call's tool_calls row."""
    with conn.begin():
        row = conn.execute(select(ToolCall.name, ToolCall.args).where(ToolCall.id == call_id))
        name, args = row.one()
        return name, args


def _park(conn: Connection, inv: UUID, reason: WaitReason, tenant: UUID | None) -> Outcome:
    """Marks the investigation waiting, so its message can go; the recovery sweeper re-queues it."""
    with conn.begin():
        conn.execute(
            update(Investigation)
            .where(Investigation.id == inv)
            .values(
                status="queued",
                waiting_reason=reason,
                waiting_since=func.now(),
                tenant_id=tenant,
                updated_at=func.now(),
            )
        )
    log.info("investigation parked", extra={"investigation_id": str(inv), "waiting_for": reason})
    return "parked"


def _start(conn: Connection, inv: UUID) -> None:
    """It holds a tenant slot and has model budget, so it no longer waits."""
    with conn.begin():
        conn.execute(
            update(Investigation)
            .where(Investigation.id == inv)
            .values(
                status="running", waiting_reason=None, waiting_since=None, updated_at=func.now()
            )
        )


def _tokens_today(conn: Connection) -> int:
    with conn.begin():
        used = conn.execute(select(AiUsage.tokens).where(AiUsage.day == UTC_TODAY))
        return used.scalar_one_or_none() or 0


def _record_turn(conn: Connection, inv: UUID, turn: ModelTurn, dual_write: bool) -> StepRecordedV2:
    """Records a model turn and its token usage, with the tool_calls row it asks for.

    A report also ends the investigation.
    """
    action = turn.action
    used = turn.tokens.input + turn.tokens.output
    values: dict[str, Any] = {"budget_tokens_used": Investigation.budget_tokens_used + used}
    with conn.begin():
        if isinstance(action, ToolRequest):
            event = StepRecordedV2(
                kind="tool_call",
                summary=action.reason,
                tool_name=action.tool,
                tool_call_id=_tool_call_id(),
                result=None,
                latency_ms=None,
                tokens=turn.tokens,
            )
            record_step(conn, inv, event, dual_write=dual_write)
            call = {"id": event.tool_call_id, "name": action.tool, "args": action.args}
            conn.execute(insert(ToolCall).values(**call, investigation_id=inv))
        else:
            event = StepRecordedV2(
                kind="report",
                summary=action.summary,
                tool_name=None,
                tool_call_id=None,
                result=None,
                latency_ms=None,
                tokens=turn.tokens,
            )
            record_step(conn, inv, event, dual_write=dual_write)
            values |= {"final_report": action.model_dump(mode="json"), "status": "resolved"}
        conn.execute(update(Investigation).where(Investigation.id == inv).values(values))
        conn.execute(
            pg_insert(AiUsage)
            .values(day=UTC_TODAY, tokens=used)
            .on_conflict_do_update(
                index_elements=[AiUsage.day], set_={"tokens": AiUsage.tokens + used}
            )
        )
    return event


def _record_tool_call(
    conn: Connection, inv: UUID, event: StepRecordedV2, error: str | None, dual_write: bool
) -> None:
    """Records the tool's result step and completes its tool_calls row, together."""
    with conn.begin():
        step = record_step(conn, inv, event, dual_write=dual_write)
        conn.execute(
            update(ToolCall)
            .where(ToolCall.id == event.tool_call_id)
            .values(
                # The full result is the step's `result`.
                result_ref=None if error else f"investigation_steps/{step.seq}",
                latency_ms=event.latency_ms,
                error=error,
            )
        )
