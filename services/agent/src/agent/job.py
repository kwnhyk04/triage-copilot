"""One investigation, resumable at every step.

Each model turn and each tool result is recorded as a step, in one transaction with its tool_calls
row and its StepRecorded event for the gateway (agent.outbox). A redelivered message replays the
recorded steps and goes on after the last one, so no recorded tool call runs twice. A tool_call
step that carries tokens is the model's request: its tool_calls row holds the arguments, and the
result step after it completes the row. As StepRecorded v3 defines them, `result` and
`latency_ms` belong to tool results only. Every model request is recorded in agent.model_calls
before the step it produced, with its tokens added to the investigation's budget.

An investigation that must wait for a slot under its tenant's cap or for model budget parks: it is
marked waiting and its message is deleted, so it holds no worker slot and uses up no receives. The
gateway's recovery sweeper re-queues it once `Job.resumable` says its slot or budget is free.

The loop stops at the model's report, or at MAX_TOOL_CALLS tool calls, MAX_SECONDS since it
started, or when the next turn could leave too few of MAX_TOKENS for a final report. Then a forced
turn that may only call submit_report writes the report from what is known; if even that cannot
fit or fails, the job writes a partial report itself. MAX_TOKENS is a hard cap: every call's
output is limited to the tokens its input leaves (agent.model.TurnBudget).
"""

import asyncio
import logging
import random
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import ValidationError
from sqlalchemy import Connection, Date, Engine, cast, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from agent.db import AiUsage, DeadLetter, Investigation, ModelCall, ProposedAction, ToolCall
from agent.model import (
    MESSAGE_OVERHEAD,
    BudgetRefused,
    Message,
    Model,
    ModelError,
    ModelTurn,
    QuotaExhausted,
    Report,
    ReportDraft,
    ToolRequest,
    TurnBudget,
    TurnRequest,
    is_model_turn,
)
from agent.model import ModelCall as Call
from agent.outbox import REPORT, STEP, enqueue
from agent.prompt import (
    FORCED,
    PROPOSE_TOOL,
    REPORT_TOOL,
    SYSTEM,
    CallRow,
    brief,
    history,
    message_bytes,
    prompt_bytes,
)
from agent.steps import StepRecordedV2, TokenUsage, record_step, replay
from agent.tools import TOOL_SPECS, ProposeActionArgs, Tool, ToolContext, ToolInputError, ToolResult
from py_clients.generated.messages.investigation_requested_v1_schema import InvestigationRequestedV1
from py_clients.generated.messages.investigation_requested_v2_schema import InvestigationRequestedV2
from py_clients.generated.messages.report_recorded_v1_schema import ReportRecordedV1
from py_clients.generated.messages.step_recorded_v3_schema import StepRecordedV3

log = logging.getLogger(__name__)

Request = InvestigationRequestedV1 | InvestigationRequestedV2
Outcome = Literal["done", "stopped", "busy", "parked"]
"""done: the final report is committed. stopped: shutdown began. busy: another agent runs it.
parked: it waits for a tenant slot or model budget, so its message can go."""
WaitReason = Literal["tenant_cap", "budget"]
Status = Literal["resolved", "awaiting_approval"]

MODEL_RETRIES = 3
TOOL_TIMEOUT = 10.0
# Log pages after the first wait for the small model; the first search loads the embedding model.
TOOL_TIMEOUTS = {"query_logs": 130.0, "search_runbooks": 60.0}
TOOL_RETRIES = 3
# Hard caps per investigation.
MAX_TOOL_CALLS = 15
MAX_TOKENS = 150_000
MAX_SECONDS = 300
# Output tokens a tool-call turn and the final report may use, and the least each needs.
TURN_OUTPUT_CAP = 2_048
MIN_OUTPUT = 256
REPORT_OUTPUT_CAP = 4_096
REPORT_MIN_OUTPUT = 1_024
# A tool result enters the context as at most this many UTF-8 bytes.
RESULT_BYTES = 24_000
# Highest confidence a hypothesis may claim, and the lower ceiling once evidence is missing.
CONFIDENCE_CEILING = 0.95
DEGRADED_CEILING = 0.6
# Not "failed": a failed investigation resumes when its message is replayed from the DLQ.
FINISHED = ("awaiting_approval", "resolved", "cancelled")
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PROPOSALS = UUID("2f3d0f7e-4a51-4c0e-9b8f-6d1f3c2a5e70")
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
# The last turn whose output is in the history: the next input bound builds on its exact count.
LAST_TURN = text("""
    SELECT model, input_tokens, output_tokens FROM agent.model_calls
    WHERE investigation_id = :id AND outcome = 'ok' AND role <> 'small'
    ORDER BY id DESC LIMIT 1
""")


class Stopped(Exception):
    """Shutdown began during a wait between attempts."""


class ReportRejected(Exception):
    """The report cites tool results that do not exist or failed."""


@dataclass
class State:
    """What the job knows of an investigation between steps; all of it is in the database."""

    opening: str
    fired_at: datetime
    steps: list[StepRecordedV2]
    calls: dict[str, CallRow]
    used: int
    last_model: str | None
    last_input: int
    last_output: int
    started_at: datetime | None = None


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
        on_commit: Callable[[], None] | None = None,
    ) -> None:
        self.engine = engine
        self.model = model
        self.tools = tools
        self.ai_enabled = ai_enabled
        self.daily_token_budget = daily_token_budget
        self.tenant_cap = tenant_cap
        self.dual_write = dual_write
        # Wakes the event relay once a step or report has committed.
        self.on_commit = on_commit or (lambda: None)

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
        status, state = await asyncio.to_thread(_load, conn, request)
        if status in FINISHED:
            return "done"
        tenant = request.tenant_id if isinstance(request, InvestigationRequestedV2) else None
        # The budget check comes before the tenant slot, so parking for budget never held a slot.
        if not state.steps and not await asyncio.to_thread(self._model_available, conn, True):
            return await asyncio.to_thread(_park, conn, inv, "budget", tenant)
        if not await asyncio.to_thread(_take_tenant_slot, conn, tenant, self.tenant_cap):
            return await asyncio.to_thread(_park, conn, inv, "tenant_cap", tenant)
        state.started_at = await asyncio.to_thread(_start, conn, inv)
        try:
            while not stopping.is_set():
                last = state.steps[-1] if state.steps else None
                if last is not None and last.kind == "tool_call" and is_model_turn(last):
                    await self._call_tool(conn, inv, state, last, stopping)
                    continue
                # With AI_ENABLED=false even a resumed investigation parks at its next model turn.
                if not await asyncio.to_thread(self._model_available, conn, not state.steps):
                    return await asyncio.to_thread(_park, conn, inv, "budget", tenant)
                messages = history(state.opening, state.steps, state.calls)
                reason = _stop_reason(state, messages)
                if reason is not None:
                    return await self._finish(conn, inv, state, messages, reason, stopping, tenant)

                def build(messages: list[Message] = messages) -> TurnRequest:
                    budget = _budget(state, messages, TURN_OUTPUT_CAP, MIN_OUTPUT)
                    return TurnRequest(state.steps, SYSTEM, messages, TOOL_SPECS, budget)

                try:
                    turn = await self._model_turn(conn, inv, state, build, stopping)
                except QuotaExhausted:
                    return await asyncio.to_thread(_park, conn, inv, "budget", tenant)
                except BudgetRefused:
                    reason = f"it is at its {MAX_TOKENS:,} token cap"
                    return await self._finish(conn, inv, state, messages, reason, stopping, tenant)
                if await self._take_turn(conn, inv, state, turn):
                    return "done"
        except Stopped:
            pass
        return "stopped"

    def _model_available(self, conn: Connection, new: bool) -> bool:
        """Model calls are on, a model has quota today, and, for a new investigation, today's
        token budget is not spent."""
        if not self.ai_enabled:
            return False
        if new and _tokens_today(conn) >= self.daily_token_budget:
            return False
        return self.model.available(conn)

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

    async def _model_turn(
        self,
        conn: Connection,
        inv: UUID,
        state: State,
        build: Callable[[], TurnRequest],
        stopping: asyncio.Event,
    ) -> ModelTurn:
        """One model turn; retries a failure, after the provider's Retry-After if it sent one.

        Every request is recorded, and its tokens counted, whether it succeeded or not; each
        attempt is built anew, so it sees what the failed ones spent.
        """
        for attempt in range(MODEL_RETRIES + 1):
            try:
                turn = await self.model.turn(build())
            except (QuotaExhausted, BudgetRefused) as error:
                await self._record_calls(conn, inv, state, error.calls)
                raise
            except ModelError as error:
                await self._record_calls(conn, inv, state, error.calls)
                if not error.retryable or attempt == MODEL_RETRIES:
                    raise
                delay = backoff(attempt) if error.retry_after is None else error.retry_after
                log.warning("model call failed; retrying", extra={"delay_s": delay})
                # ponytail: a long Retry-After holds this message's slot; hand the message back
                # to SQS with ChangeMessageVisibility if such waits become common.
                if await sleep_unless(stopping, delay):
                    raise Stopped from error
                continue
            await self._record_calls(conn, inv, state, turn.calls)
            return turn
        raise AssertionError("unreachable")

    async def _record_calls(
        self, conn: Connection, inv: UUID, state: State, calls: Sequence[Call]
    ) -> None:
        if calls:
            state.used += await asyncio.to_thread(_store_calls, conn, inv, calls)

    async def _take_turn(self, conn: Connection, inv: UUID, state: State, turn: ModelTurn) -> bool:
        """Records the turn; True when it ended the investigation with a report."""
        action = turn.action
        if isinstance(action, ReportDraft):
            actions = await asyncio.to_thread(_proposals, conn, inv)
            try:
                report, status = build_report(action, state, actions, partial=False)
            except ReportRejected as error:
                # Recorded as a failed submit_report call, so the model sees why and can fix it.
                args = action.model_dump(mode="json")
                request = ToolRequest(tool=REPORT_TOOL, args=args, reason="Submit the report.")
                call_id = await self._record_request(conn, inv, state, turn, request)
                await self._record_result(
                    conn, inv, state, call_id, None, f"ToolInputError: {error}", 0, None
                )
                return False
            await self._record_report(conn, inv, state, report, status, turn.tokens)
            return True
        await self._record_request(conn, inv, state, turn, action)
        return False

    async def _finish(
        self,
        conn: Connection,
        inv: UUID,
        state: State,
        messages: list[Message],
        reason: str,
        stopping: asyncio.Event,
        tenant: UUID | None,
    ) -> Outcome:
        """Writes the final report at a budget: the model's, from what it knows, or the job's."""
        actions = await asyncio.to_thread(_proposals, conn, inv)
        forced = [*messages, Message("user", text=FORCED.format(reason=reason))]

        def build() -> TurnRequest:
            budget = _budget(state, forced, REPORT_OUTPUT_CAP, REPORT_MIN_OUTPUT)
            allowed = (REPORT_TOOL,)
            return TurnRequest(state.steps, SYSTEM, forced, TOOL_SPECS, budget, allowed)

        draft: ReportDraft | None = None
        tokens: TokenUsage | None = None
        try:
            turn = await self._model_turn(conn, inv, state, build, stopping)
        except QuotaExhausted:
            return await asyncio.to_thread(_park, conn, inv, "budget", tenant)
        except (BudgetRefused, ModelError) as error:
            log.warning(
                "forced report failed; writing a partial one",
                extra={"investigation_id": str(inv), "error": str(error)[:200]},
            )
        else:
            tokens = turn.tokens
            draft = turn.action if isinstance(turn.action, ReportDraft) else None
        if draft is not None:
            note = f"The investigation stopped early: {reason}."
            report, status = build_report(draft, state, actions, partial=True, notes=[note])
        else:
            report, status = partial_report(state, reason, actions)
        await self._record_report(conn, inv, state, report, status, tokens)
        log.info(
            "investigation stopped at its budget",
            extra={"investigation_id": str(inv), "reason": reason, "tokens": state.used},
        )
        return "done"

    async def _call_tool(
        self,
        conn: Connection,
        inv: UUID,
        state: State,
        requested: StepRecordedV2,
        stopping: asyncio.Event,
    ) -> None:
        """Runs the tool call the last step asked for and records its result."""
        call_id = requested.tool_call_id or ""
        row = state.calls[call_id]
        started = time.perf_counter()
        proposal: tuple[UUID, ProposeActionArgs] | None = None
        if row.name == PROPOSE_TOOL:
            result, error, proposal = _propose(state, call_id, row.args)
        elif row.name == REPORT_TOOL:
            result, error = None, f"ToolInputError: {_report_problems(row.args)}"
        else:
            ctx = ToolContext(inv, state.fired_at, _tool_allowance(state))
            out, error = await self._invoke(row.name, row.args, ctx, stopping)
            result = None if out is None else _clip(out.text)
            if out is not None:
                await self._record_calls(conn, inv, state, out.calls)
        await self._record_result(
            conn, inv, state, call_id, result, error, _elapsed_ms(started), proposal
        )

    async def _invoke(
        self, name: str, args: dict[str, Any], ctx: ToolContext, stopping: asyncio.Event
    ) -> tuple[ToolResult | None, str | None]:
        """(result, None), or (None, error) once retries are spent; the investigation goes on.

        A ToolInputError (wrong arguments or query) is not retried: the model has to fix it.
        """
        tool = self.tools.get(name)
        if tool is None:
            return None, f"ToolInputError: unknown tool {name}"
        timeout = TOOL_TIMEOUTS.get(name, TOOL_TIMEOUT)
        for attempt in range(TOOL_RETRIES + 1):
            try:
                out = await asyncio.wait_for(tool(ctx, args), timeout)
                return (out if isinstance(out, ToolResult) else ToolResult(out)), None
            except ToolInputError as error:
                return None, f"ToolInputError: {error}"[:500]
            except Exception as error:  # Tools only read, so any other failure is safe to retry.
                if attempt == TOOL_RETRIES:
                    return None, f"{type(error).__name__}: {error}"[:500]
                if await sleep_unless(stopping, backoff(attempt)):
                    raise Stopped from error
        raise AssertionError("unreachable")

    async def _record_request(
        self, conn: Connection, inv: UUID, state: State, turn: ModelTurn, action: ToolRequest
    ) -> str:
        event = StepRecordedV2(
            kind="tool_call",
            summary=action.reason,
            tool_name=action.tool,
            tool_call_id=_tool_call_id(),
            result=None,
            latency_ms=None,
            tokens=turn.tokens,
        )
        await asyncio.to_thread(
            _store_request, conn, inv, event, action.args, turn.provider_state, self.dual_write
        )
        call_id = event.tool_call_id or ""
        state.steps.append(event)
        state.calls[call_id] = CallRow(action.tool, action.args, None, turn.provider_state)
        state.last_model, state.last_input = turn.model, turn.tokens.input
        state.last_output = turn.tokens.output
        self.on_commit()
        return call_id

    async def _record_result(
        self,
        conn: Connection,
        inv: UUID,
        state: State,
        call_id: str,
        result: str | None,
        error: str | None,
        latency_ms: int,
        proposal: tuple[UUID, ProposeActionArgs] | None,
    ) -> None:
        name = state.calls[call_id].name
        summary = f"{name} returned" if error is None else f"{name} failed: {error}"
        event = StepRecordedV2(
            kind="tool_call",
            summary=summary[:2000],
            tool_name=name,
            tool_call_id=call_id,
            result=result,
            latency_ms=latency_ms,
            tokens=None,
        )
        await asyncio.to_thread(_store_result, conn, inv, event, error, proposal, self.dual_write)
        state.steps.append(event)
        state.calls[call_id] = replace(state.calls[call_id], error=error)
        self.on_commit()

    async def _record_report(
        self,
        conn: Connection,
        inv: UUID,
        state: State,
        report: Report,
        status: Status,
        tokens: TokenUsage | None,
    ) -> None:
        event = StepRecordedV2(
            kind="report",
            summary=report.summary,
            tool_name=None,
            tool_call_id=None,
            result=None,
            latency_ms=None,
            tokens=tokens,
        )
        await asyncio.to_thread(_store_report, conn, inv, event, report, status, self.dual_write)
        state.steps.append(event)
        self.on_commit()

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


def usage(steps: Sequence[StepRecordedV2]) -> tuple[int, int]:
    """Tool calls the model asked for and the tokens its turns spent, from the recorded steps."""
    calls = sum(step.kind == "tool_call" and is_model_turn(step) for step in steps)
    tokens = sum(s.tokens.input + s.tokens.output for s in steps if s.tokens is not None)
    return calls, tokens


def _budget(
    state: State, messages: Sequence[Message], output_cap: int, min_output: int
) -> TurnBudget:
    assistant = [i for i, message in enumerate(messages) if message.call is not None]
    last = assistant[-1] if assistant else None
    after = messages[last + 1 :] if last is not None else messages
    return TurnBudget(
        remaining=MAX_TOKENS - state.used,
        prompt_bytes=prompt_bytes(SYSTEM, TOOL_SPECS, messages),
        delta_bytes=sum(map(message_bytes, after)),
        messages=len(messages),
        new_messages=len(messages) - last if last is not None else len(messages),
        output_cap=output_cap,
        min_output=min_output,
        last_model=state.last_model,
        last_input=state.last_input,
        last_output=state.last_output,
    )


def _forced_reserve(bound: int) -> int:
    """What a forced report needs after one more tool result: its input and least output."""
    nudge = len(FORCED.encode()) + 200
    return bound + RESULT_BYTES + nudge + 4 * MESSAGE_OVERHEAD + REPORT_MIN_OUTPUT


def _stop_reason(state: State, messages: Sequence[Message]) -> str | None:
    """Why the next turn must be the final report, if it must."""
    calls, _ = usage(state.steps)
    if calls >= MAX_TOOL_CALLS:
        return f"it used all {MAX_TOOL_CALLS} tool calls"
    if state.started_at is not None:
        elapsed = (datetime.now(UTC) - state.started_at).total_seconds()
        if elapsed >= MAX_SECONDS:
            return f"it reached its {MAX_SECONDS // 60}-minute limit"
    budget = _budget(state, messages, TURN_OUTPUT_CAP, MIN_OUTPUT)
    # The same model is likely to answer; any other one gets the whole-prompt bound.
    bound = budget.bound(budget.last_model or "")
    after_turn = budget.remaining - bound - TURN_OUTPUT_CAP
    if after_turn < _forced_reserve(bound + TURN_OUTPUT_CAP):
        return f"another turn would leave too few of its {MAX_TOKENS:,} tokens for the report"
    return None


def _tool_allowance(state: State) -> int:
    """Tokens a tool may spend on model calls while the final report's reserve stays intact."""
    messages = history(state.opening, state.steps, state.calls)
    budget = _budget(state, messages, REPORT_OUTPUT_CAP, REPORT_MIN_OUTPUT)
    return max(0, budget.remaining - _forced_reserve(budget.bound(budget.last_model or "")))


def _clip(result: str) -> str:
    """At most RESULT_BYTES of UTF-8, cut at a character boundary."""
    data = result.encode()
    if len(data) <= RESULT_BYTES:
        return result
    return data[: RESULT_BYTES - 40].decode(errors="ignore") + "\n(truncated)"


def _evidence_ids(state: State) -> set[str]:
    """Ids of the read tools' successful results: what a report or a proposal may cite."""
    return {
        step.tool_call_id
        for step in state.steps
        if step.kind == "tool_call"
        and not is_model_turn(step)
        and step.result is not None
        and step.tool_call_id is not None
        and step.tool_name not in (PROPOSE_TOOL, REPORT_TOOL)
    }


def _missing_tools(state: State) -> list[str]:
    """Read tools whose backend failed (not the model's own argument errors): missing evidence."""
    missing: list[str] = []
    for step in state.steps:
        if step.kind != "tool_call" or is_model_turn(step) or step.result is not None:
            continue
        call_id, name = step.tool_call_id or "", step.tool_name or ""
        error = state.calls[call_id].error if call_id in state.calls else None
        if name in (PROPOSE_TOOL, REPORT_TOOL) or (error or "").startswith("ToolInputError"):
            continue
        missing.append(f"{name} failed ({call_id}): {error}"[:500])
    return missing


def build_report(
    draft: ReportDraft,
    state: State,
    actions: list[dict[str, Any]],
    *,
    partial: bool,
    notes: Sequence[str] = (),
) -> tuple[Report, Status]:
    """The final report from the model's draft, with what the job enforces in code.

    Evidence must name successful read-tool results: the model's own report is rejected over an
    unknown id, a forced one drops it. Failed tools become missing evidence and cap confidence at
    DEGRADED_CEILING; no hypothesis may claim more than CONFIDENCE_CEILING. Proposals come from
    the propose_action rows, never from the draft.
    """
    known = _evidence_ids(state)
    unknown = sorted({i for h in draft.hypotheses for i in h.evidence_ids if i not in known})
    if unknown and not partial:
        raise ReportRejected(
            f"evidence_ids name no successful tool result: {', '.join(unknown)}. "
            "Cite the ids in brackets at the start of the results."
        )
    missing_tools = _missing_tools(state)
    ceiling = DEGRADED_CEILING if missing_tools else CONFIDENCE_CEILING
    hypotheses = [
        h.model_dump()
        | {
            "confidence": min(h.confidence, ceiling),
            "evidence_ids": [i for i in h.evidence_ids if i in known],
        }
        for h in sorted(draft.hypotheses, key=lambda h: -h.confidence)
    ]
    dropped = [f"Dropped evidence ids that name no result: {', '.join(unknown)}."]
    if not unknown:
        dropped = []
    missing = [*draft.missing_evidence, *missing_tools, *notes, *dropped]
    report = Report.model_validate(
        {
            "summary": draft.summary,
            "hypotheses": hypotheses,
            "missing_evidence": [m[:500] for m in dict.fromkeys(missing)][:20],
            "proposed_actions": actions,
            "partial": partial,
        }
    )
    return report, "awaiting_approval" if actions else "resolved"


def partial_report(
    state: State, reason: str, actions: list[dict[str, Any]]
) -> tuple[Report, Status]:
    """The job's own report when the model could not write one: no hypotheses, what is known."""
    calls, _ = usage(state.steps)
    summary = (
        f"Stopped at the investigation budget after {calls} tool calls and {state.used} model "
        f"tokens (caps: {MAX_TOOL_CALLS} tool calls, {MAX_TOKENS} tokens): {reason}. The "
        "findings are incomplete."
    )
    missing = [f"The investigation stopped early: {reason}.", *_missing_tools(state)]
    report = Report.model_validate(
        {
            "summary": summary[:2000],
            "hypotheses": [],
            "missing_evidence": [m[:500] for m in dict.fromkeys(missing)][:20],
            "proposed_actions": actions,
            "partial": True,
        }
    )
    return report, "awaiting_approval" if actions else "resolved"


def _propose(
    state: State, call_id: str, args: dict[str, Any]
) -> tuple[str | None, str | None, tuple[UUID, ProposeActionArgs] | None]:
    """Checks a proposal; the result step's transaction stores it. Nothing ever runs here."""
    try:
        proposal = ProposeActionArgs.model_validate(args)
    except ValidationError as error:
        return None, f"ToolInputError: invalid arguments: {_problems(error)}"[:500], None
    unknown = [i for i in proposal.evidence_ids if i not in _evidence_ids(state)]
    if unknown:
        error = f"evidence_ids name no successful tool result: {', '.join(unknown)}"
        return None, f"ToolInputError: {error}"[:500], None
    # Derived from the call, so a retried transaction proposes the same action.
    action_id = uuid5(PROPOSALS, call_id)
    result = (
        f"Proposed {proposal.kind} on {proposal.target} as action {action_id}. It waits for a "
        "human to approve it; nothing has run."
    )
    return result, None, (action_id, proposal)


def _report_problems(args: dict[str, Any]) -> str:
    try:
        ReportDraft.model_validate(args)
    except ValidationError as error:
        return f"invalid report: {_problems(error)}"[:480]
    return "invalid report"


def _problems(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}"
        for e in error.errors(include_url=False, include_input=False)
    )


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


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


def _load(conn: Connection, request: Request) -> tuple[str, State]:
    """The checkpoint: the investigation's status and what the job knows; creates it when new."""
    inv = request.investigation_id
    with conn.begin():
        conn.execute(pg_insert(Investigation).values(id=inv).on_conflict_do_nothing())
        found = select(Investigation.status, Investigation.budget_tokens_used)
        status, used = conn.execute(found.where(Investigation.id == inv)).one()
        steps = replay(conn, inv)
        columns = (
            ToolCall.id,
            ToolCall.name,
            ToolCall.args,
            ToolCall.error,
            ToolCall.provider_state,
        )
        rows = conn.execute(select(*columns).where(ToolCall.investigation_id == inv))
        calls = {r.id: CallRow(r.name, r.args, r.error, r.provider_state) for r in rows}
        last = conn.execute(LAST_TURN, {"id": inv}).one_or_none()
    state = State(
        opening=brief(request.group_key, request.requested_at),
        fired_at=request.requested_at,
        steps=steps,
        calls=calls,
        used=used,
        last_model=last.model if last else None,
        last_input=last.input_tokens if last else 0,
        last_output=last.output_tokens if last else 0,
    )
    return status, state


def _proposals(conn: Connection, inv: UUID) -> list[dict[str, Any]]:
    """The investigation's proposals as the report carries them, oldest first."""
    a = ProposedAction
    columns = (a.id, a.kind, a.target, a.params, a.rationale, a.evidence_ids)
    with conn.begin():
        rows = conn.execute(
            select(*columns).where(a.investigation_id == inv).order_by(a.created_at, a.id)
        )
        return [
            {
                "action_id": str(r.id),
                "kind": r.kind,
                "target": r.target,
                "parameters": r.params,
                "rationale": r.rationale,
                "evidence_ids": r.evidence_ids,
            }
            for r in rows
        ]


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


def _start(conn: Connection, inv: UUID) -> datetime:
    """It holds a tenant slot and has model budget, so it no longer waits. Returns when it first
    started, which its wall-clock limit counts from."""
    with conn.begin():
        started = conn.execute(
            update(Investigation)
            .where(Investigation.id == inv)
            .values(
                status="running",
                waiting_reason=None,
                waiting_since=None,
                started_at=func.coalesce(Investigation.started_at, func.now()),
                updated_at=func.now(),
            )
            .returning(Investigation.started_at)
        ).scalar_one()
    if started is None:
        raise RuntimeError("coalesce with now() left started_at null")
    return started


def _tokens_today(conn: Connection) -> int:
    with conn.begin():
        used = conn.execute(select(AiUsage.tokens).where(AiUsage.day == UTC_TODAY))
        return used.scalar_one_or_none() or 0


def _store_calls(conn: Connection, inv: UUID, calls: Sequence[Call]) -> int:
    """Records model requests and adds their tokens to the investigation and the day."""
    spent = sum(c.usage.input + c.usage.output for c in calls)
    with conn.begin():
        conn.execute(
            insert(ModelCall),
            [
                {
                    "investigation_id": inv,
                    "role": c.role,
                    "provider": c.provider,
                    "model": c.model,
                    "outcome": c.outcome,
                    "input_tokens": c.usage.input,
                    "output_tokens": c.usage.output,
                    "cached_tokens": c.usage.cached,
                    "thought_tokens": c.usage.thoughts,
                    "input_bound": c.input_bound,
                    "max_output_tokens": c.max_output_tokens,
                    "latency_ms": c.latency_ms,
                    "error": c.error,
                }
                for c in calls
            ],
        )
        if spent:
            conn.execute(
                update(Investigation)
                .where(Investigation.id == inv)
                .values(budget_tokens_used=Investigation.budget_tokens_used + spent)
            )
            conn.execute(
                pg_insert(AiUsage)
                .values(day=UTC_TODAY, tokens=spent)
                .on_conflict_do_update(
                    index_elements=[AiUsage.day], set_={"tokens": AiUsage.tokens + spent}
                )
            )
    return spent


def _step(conn: Connection, inv: UUID, event: StepRecordedV2, dual_write: bool) -> StepRecordedV3:
    """Records a step with its StepRecorded event for the gateway, in the caller's transaction."""
    message = record_step(conn, inv, event, dual_write=dual_write)
    body = message.model_dump(mode="json")
    enqueue(conn, STEP, str(inv), f"step-{inv}-{message.seq}", body)
    return message


def _store_request(
    conn: Connection,
    inv: UUID,
    event: StepRecordedV2,
    args: dict[str, Any],
    provider_state: str | None,
    dual_write: bool,
) -> None:
    """Records a model turn that asks for a tool, with the tool_calls row holding its arguments."""
    with conn.begin():
        _step(conn, inv, event, dual_write)
        call = {"id": event.tool_call_id, "name": event.tool_name, "args": args}
        conn.execute(
            insert(ToolCall).values(**call, investigation_id=inv, provider_state=provider_state)
        )


def _store_result(
    conn: Connection,
    inv: UUID,
    event: StepRecordedV2,
    error: str | None,
    proposal: tuple[UUID, ProposeActionArgs] | None,
    dual_write: bool,
) -> None:
    """Records the tool's result step, completes its tool_calls row and stores a proposal."""
    with conn.begin():
        seq = _step(conn, inv, event, dual_write).seq
        conn.execute(
            update(ToolCall)
            .where(ToolCall.id == event.tool_call_id)
            .values(
                # The full result is the step's `result`.
                result_ref=None if error else f"investigation_steps/{seq}",
                latency_ms=event.latency_ms,
                error=error,
            )
        )
        if proposal is not None:
            action_id, args = proposal
            row = {
                "id": action_id,
                "investigation_id": inv,
                "idempotency_key": event.tool_call_id,
                "kind": args.kind,
                "target": args.target,
                "params": args.params,
                "rationale": args.rationale,
                "evidence_ids": args.evidence_ids,
            }
            conn.execute(pg_insert(ProposedAction).values(row).on_conflict_do_nothing())


def _store_report(
    conn: Connection,
    inv: UUID,
    event: StepRecordedV2,
    report: Report,
    status: Status,
    dual_write: bool,
) -> None:
    """Records the report step, ends the investigation, and queues ReportRecorded after it."""
    with conn.begin():
        recorded_at = _step(conn, inv, event, dual_write).recorded_at
        conn.execute(
            update(Investigation)
            .where(Investigation.id == inv)
            .values(final_report=report.model_dump(mode="json"), status=status)
        )
        message = ReportRecordedV1.model_validate(
            {
                "schema_version": 1,
                "investigation_id": inv,
                "status": status,
                "report": report.model_dump(mode="json"),
                "recorded_at": recorded_at,
            }
        )
        enqueue(conn, REPORT, str(inv), f"report-{inv}", message.model_dump(mode="json"))
