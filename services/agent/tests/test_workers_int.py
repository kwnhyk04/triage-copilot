"""The worker against ElasticMQ and a scratch Postgres: restarts, the DLQ, the budget guard and the
tenant cap. Each test gets throwaway queues set up like investigations.fifo and its DLQ.
"""

import asyncio
import contextlib
import secrets
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, create_engine, delete, func, insert, select, text, update
from types_boto3_sqs import SQSClient

from agent.api import add_internal_api
from agent.consumer import Consumer
from agent.db import AiUsage, DeadLetter, Investigation, ModelCall, ToolCall
from agent.dlq import replay_dead_letters, replay_dlq
from agent.job import MAX_TOOL_CALLS, UTC_TODAY, Job, Outcome, Request
from agent.llm import Binding, Limiter, Router
from agent.model import (
    HypothesisDraft,
    Model,
    ModelTurn,
    QuotaExhausted,
    Report,
    ReportDraft,
    ScriptedModel,
    ToolRequest,
    TurnRequest,
    is_model_turn,
)
from agent.providers import Provider
from agent.settings import Role
from agent.steps import StepRecordedV2, TokenUsage, record_step, replay
from agent.tools import Tool, ToolContext
from py_clients.generated.messages.investigation_requested_v2_schema import InvestigationRequestedV2

if TYPE_CHECKING:
    from conftest import ScratchDb

pytestmark = pytest.mark.integration

TENANT = UUID("6f1c2a9e-8b3d-4e5f-9a7b-2c4d6e8f0a1b")
TOOLS = ("query_metrics", "search_logs", "get_deploys")
REPORT = ReportDraft(
    summary="Checkout errors follow the v1.8.0 deploy.",
    hypotheses=[
        HypothesisDraft(
            root_cause_class="bad_deploy",
            service="checkout",
            confidence=0.8,
            summary="v1.8.0 broke checkout.",
            evidence_ids=[],
        )
    ],
)
SCRIPT: list[ToolRequest | ReportDraft] = [
    *(
        ToolRequest(tool=name, args={"service": "checkout"}, reason=f"Run {name}.")
        for name in TOOLS
    ),
    REPORT,
]
ONE_CALL: list[ToolRequest | ReportDraft] = [SCRIPT[0], REPORT]
STEP_SEQS = text(
    "SELECT seq FROM agent.investigation_steps WHERE investigation_id = :id ORDER BY 1"
)
SERVICE_TOKEN = secrets.token_urlsafe(32)


class Tools:
    """Read-only fakes. `calls` is what the outside world sees, across agent restarts."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def all(self, **behaviors: Callable[[], Awaitable[None]]) -> dict[str, Tool]:
        def make(name: str) -> Tool:
            async def call(ctx: ToolContext, args: dict[str, Any]) -> str:
                self.calls.append(name)
                if name in behaviors:
                    await behaviors[name]()
                return f"{name} on {args['service']}: ok"

            return call

        return {name: make(name) for name in TOOLS}


class Counting:
    """Counts model calls."""

    def __init__(self, model: Model) -> None:
        self.model = model
        self.calls = 0

    async def turn(self, request: TurnRequest) -> ModelTurn:
        self.calls += 1
        return await self.model.turn(request)

    def available(self, conn: Connection) -> bool:
        return True


class BrokenModel:
    def __init__(self) -> None:
        self.calls = 0

    async def turn(self, request: TurnRequest) -> ModelTurn:
        self.calls += 1
        raise RuntimeError("model bug")

    def available(self, conn: Connection) -> bool:
        return True


# The default INVESTIGATION_TOKEN_CAP.
CAP = 150_000


class CountedJob(Job):
    runs = 0

    async def run(self, request: Request, stopping: asyncio.Event) -> Outcome:
        self.runs += 1
        return await super().run(request, stopping)


@pytest.fixture
def engine(scratch_db: "ScratchDb") -> Iterator[Engine]:
    scratch_db.migrate_agent("head")
    engine = create_engine(scratch_db.agent_url)
    yield engine
    engine.dispose()


def make_job(
    engine: Engine,
    tools: dict[str, Tool],
    model: Model | None = None,
    *,
    cls: type[Job] = Job,
    **options: Any,
) -> Job:
    settings: dict[str, Any] = {
        "ai_enabled": True,
        "daily_token_budget": 1_000_000,
        "tenant_cap": 5,
        "token_cap": CAP,
    }
    return cls(engine, model or ScriptedModel(SCRIPT), tools, **(settings | options))


def make_consumer(sqs: SQSClient, queue_url: str, job: Job, visibility: int = 2) -> Consumer:
    name = queue_url.rsplit("/", 1)[1]
    return Consumer(
        sqs,
        name,
        job,
        wait_seconds=1,
        heartbeat_seconds=0.5,
        visibility_seconds=visibility,
        busy_backoff_seconds=(1, 1),
    )


@contextlib.asynccontextmanager
async def agent(consumer: Consumer) -> AsyncGenerator[None]:
    """A running agent; leaving the block shuts it down the way SIGTERM does."""
    stopping = asyncio.Event()
    task = asyncio.create_task(consumer.run(stopping))
    try:
        yield
    finally:
        stopping.set()
        await asyncio.wait_for(task, 30)


async def eventually(condition: Callable[[], bool], within: float = 30) -> None:
    deadline = time.monotonic() + within
    while not await asyncio.to_thread(condition):
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.1)


def new_request(tenant: UUID = TENANT) -> InvestigationRequestedV2:
    return InvestigationRequestedV2(
        schema_version=2,
        investigation_id=uuid.uuid4(),
        tenant_id=tenant,
        group_key="checkout-errors",
        requested_at=datetime.now(UTC),
    )


def send(sqs: SQSClient, queue_url: str, body: str, group: str = "g") -> None:
    dedup = uuid.uuid4().hex
    sqs.send_message(
        QueueUrl=queue_url, MessageBody=body, MessageGroupId=group, MessageDeduplicationId=dedup
    )


def depth(sqs: SQSClient, queue_url: str) -> int:
    """Messages in the queue, visible or in flight."""
    attributes = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    visible = attributes.get("ApproximateNumberOfMessages", "0")
    return int(visible) + int(attributes.get("ApproximateNumberOfMessagesNotVisible", "0"))


def status(engine: Engine, inv: UUID) -> str | None:
    with engine.connect() as conn:
        found = conn.execute(select(Investigation.status).where(Investigation.id == inv))
        return found.scalar_one_or_none()


def waiting(engine: Engine, inv: UUID) -> tuple[str, str | None, UUID | None]:
    """Status, what the investigation waits for, and the tenant it recorded."""
    query = select(Investigation.status, Investigation.waiting_reason, Investigation.tenant_id)
    with engine.connect() as conn:
        found, reason, tenant = conn.execute(query.where(Investigation.id == inv)).one()
        return found, reason, tenant


def state_api(job: Job) -> TestClient:
    """The internal API over the job's database, sending the service token."""
    app = FastAPI()
    add_internal_api(app, job, SecretStr(SERVICE_TOKEN))
    return TestClient(app, headers={"Authorization": f"Bearer {SERVICE_TOKEN}"})


def state(api: TestClient, inv: UUID) -> dict[str, Any]:
    response = api.get(f"/internal/investigations/{inv}/state")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def recorded(engine: Engine, inv: UUID) -> list[tuple[int, StepRecordedV2]]:
    with engine.connect() as conn:
        seqs = conn.execute(STEP_SEQS, {"id": inv}).scalars()
        return list(zip(seqs, replay(conn, inv), strict=True))


def tool_call_refs(engine: Engine, inv: UUID) -> dict[str, str | None]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(ToolCall.id, ToolCall.result_ref).where(ToolCall.investigation_id == inv)
        )
        return {call_id: ref for call_id, ref in rows}


def tokens_today(engine: Engine) -> int:
    with engine.connect() as conn:
        used = conn.execute(select(AiUsage.tokens).where(AiUsage.day == UTC_TODAY))
        return used.scalar_one_or_none() or 0


def test_shutdown_mid_investigation_resumes_at_the_next_step(
    engine: Engine, sqs: SQSClient, queues: tuple[str, str]
) -> None:
    queue_url, dlq_url = queues
    request = new_request()
    inv = request.investigation_id
    send(sqs, queue_url, request.model_dump_json())
    tools = Tools()

    async def main() -> None:
        running = asyncio.Event()

        async def slow() -> None:
            running.set()
            await asyncio.sleep(0.5)  # Still running when SIGTERM arrives.

        consumer = make_consumer(sqs, queue_url, make_job(engine, tools.all(search_logs=slow)))
        async with agent(consumer):
            await asyncio.wait_for(running.wait(), 30)
        # The agent finished and recorded the search_logs call, then left the message in flight.
        results = [step.tokens is None for _, step in recorded(engine, inv)]
        assert results == [False, True, False, True]
        assert status(engine, inv) == "running"

        async with agent(make_consumer(sqs, queue_url, make_job(engine, tools.all()))):
            await eventually(lambda: depth(sqs, queue_url) == 0)

    asyncio.run(main())

    assert tools.calls == list(TOOLS)
    steps = recorded(engine, inv)
    assert [seq for seq, _ in steps] == list(range(1, 8))
    assert [step.kind for _, step in steps] == ["tool_call"] * 6 + ["report"]
    assert status(engine, inv) == "resolved"
    # Each result step was written with its tool_calls row, which points back at it.
    results = {
        step.tool_call_id: f"investigation_steps/{seq}"
        for seq, step in steps
        if step.kind == "tool_call" and step.tokens is None
    }
    assert tool_call_refs(engine, inv) == results
    # As StepRecorded v3 defines them, only tool results carry a result and a latency.
    requests = [step for _, step in steps if step.tokens is not None]
    assert all(step.result is None and step.latency_ms is None for step in requests)
    assert depth(sqs, dlq_url) == 0


def test_killed_mid_tool_call_repeats_only_that_call(
    engine: Engine, sqs: SQSClient, queues: tuple[str, str]
) -> None:
    queue_url, _ = queues
    request = new_request()
    inv = request.investigation_id
    send(sqs, queue_url, request.model_dump_json())
    tools = Tools()

    async def main() -> None:
        hung = asyncio.Event()

        async def hang() -> None:
            hung.set()
            await asyncio.Event().wait()

        consumer = make_consumer(sqs, queue_url, make_job(engine, tools.all(search_logs=hang)))
        killed = asyncio.create_task(consumer.run(asyncio.Event()))
        await asyncio.wait_for(hung.wait(), 30)
        killed.cancel()  # Like kill -9: the running step never finishes.
        with contextlib.suppress(asyncio.CancelledError):
            await killed
        assert len(recorded(engine, inv)) == 3  # search_logs was asked for; no result recorded.

        async with agent(make_consumer(sqs, queue_url, make_job(engine, tools.all()))):
            await eventually(lambda: status(engine, inv) == "resolved")

    asyncio.run(main())

    # Only the interrupted call runs again: its first attempt never recorded a result.
    assert tools.calls == ["query_metrics", "search_logs", "search_logs", "get_deploys"]
    assert [seq for seq, _ in recorded(engine, inv)] == list(range(1, 8))
    assert len(tool_call_refs(engine, inv)) == 3


def test_a_message_that_fails_five_times_lands_in_the_dlq(
    engine: Engine, sqs: SQSClient, queues: tuple[str, str]
) -> None:
    queue_url, dlq_url = queues
    request = new_request()
    send(sqs, queue_url, request.model_dump_json())
    model = BrokenModel()

    async def main() -> None:
        job = make_job(engine, Tools().all(), model)
        async with agent(make_consumer(sqs, queue_url, job, visibility=1)):
            await eventually(lambda: depth(sqs, dlq_url) == 1, within=60)

    asyncio.run(main())

    assert model.calls == 5
    assert depth(sqs, queue_url) == 0
    assert status(engine, request.investigation_id) == "failed"

    # Once the cause is fixed, a replay from the DLQ resumes the failed investigation.
    assert replay_dlq(sqs, queue_url) == 1
    assert depth(sqs, dlq_url) == 0

    async def resume() -> None:
        job = make_job(engine, Tools().all(), ScriptedModel([REPORT]))
        async with agent(make_consumer(sqs, queue_url, job)):
            await eventually(lambda: depth(sqs, queue_url) == 0)

    asyncio.run(resume())
    assert status(engine, request.investigation_id) == "resolved"


def test_poison_messages_are_dead_lettered_at_once_and_can_be_replayed(
    engine: Engine, sqs: SQSClient, queues: tuple[str, str]
) -> None:
    queue_url, dlq_url = queues
    send(sqs, queue_url, "not json", group="poison")

    def dead_letters() -> list[tuple[str, str, datetime | None]]:
        query = select(DeadLetter.body, DeadLetter.group_id, DeadLetter.replayed_at)
        with engine.connect() as conn:
            rows = conn.execute(query.order_by(DeadLetter.id))
            return [(body, group, replayed) for body, group, replayed in rows]

    async def main() -> None:
        async with agent(make_consumer(sqs, queue_url, make_job(engine, Tools().all()))):
            await eventually(lambda: len(dead_letters()) == 1 and depth(sqs, queue_url) == 0)
            assert await asyncio.to_thread(replay_dead_letters, engine, sqs, queue_url) == 1
            await eventually(lambda: len(dead_letters()) == 2)

    asyncio.run(main())

    first, again = dead_letters()
    assert first[:2] == again[:2] == ("not json", "poison")
    assert first[2] is not None
    assert again[2] is None
    assert depth(sqs, dlq_url) == 0  # Never redriven: deleted on its first receive each time.


def test_heartbeat_keeps_a_long_investigation_from_redelivering(
    engine: Engine, sqs: SQSClient, queues: tuple[str, str]
) -> None:
    queue_url, _ = queues
    send(sqs, queue_url, new_request().model_dump_json())

    async def slow() -> None:
        await asyncio.sleep(5)  # Longer than the 2 s visibility timeout.

    tools = Tools().all(query_metrics=slow)
    job = make_job(engine, tools, ScriptedModel(ONE_CALL), cls=CountedJob)

    async def main() -> None:
        async with agent(make_consumer(sqs, queue_url, job)):
            await eventually(lambda: depth(sqs, queue_url) == 0)

    asyncio.run(main())
    assert isinstance(job, CountedJob)
    assert job.runs == 1


def test_budget_parks_new_investigations_and_counts_tokens(engine: Engine) -> None:
    model = Counting(ScriptedModel(ONE_CALL, TokenUsage(input=100, output=10)))
    job = make_job(engine, Tools().all(), model, daily_token_budget=1000)
    with engine.begin() as conn:
        conn.execute(insert(AiUsage).values(day=UTC_TODAY, tokens=1000))
    request = new_request()
    inv = request.investigation_id

    async def main() -> None:
        # It parks at once, before it takes a tenant slot, so its message can go.
        assert await job.run(request, asyncio.Event()) == "parked"
        assert waiting(engine, inv) == ("queued", "budget", TENANT)
        assert model.calls == 0
        with engine.begin() as conn:
            conn.execute(delete(AiUsage))  # A new UTC day.
        # Re-queued by the gateway's sweeper, it runs and stops waiting.
        assert await job.run(request, asyncio.Event()) == "done"

    asyncio.run(main())

    assert model.calls == 2
    assert waiting(engine, inv) == ("resolved", None, TENANT)
    assert tokens_today(engine) == 220
    with engine.connect() as conn:
        used = conn.execute(select(Investigation.budget_tokens_used).where(Investigation.id == inv))
        assert used.scalar_one() == 220


def test_resumed_investigations_pass_the_budget_but_not_ai_off(engine: Engine) -> None:
    request = new_request()
    inv = request.investigation_id
    asked = StepRecordedV2(
        kind="tool_call",
        summary="Run query_metrics.",
        tool_name="query_metrics",
        tool_call_id="tc_0000000001",
        result=None,
        latency_ms=None,
        tokens=TokenUsage(input=100, output=10),
    )
    with engine.begin() as conn:
        conn.execute(insert(Investigation).values(id=inv))
        record_step(conn, inv, asked, dual_write=False)
        call = {"id": "tc_0000000001", "name": "query_metrics", "args": {"service": "checkout"}}
        conn.execute(insert(ToolCall).values(**call, investigation_id=inv))
        conn.execute(insert(AiUsage).values(day=UTC_TODAY, tokens=10_000))
    model = Counting(ScriptedModel(ONE_CALL))

    async def main() -> None:
        # With model calls off, even a resumed investigation parks once it needs the model.
        off = make_job(engine, Tools().all(), model, daily_token_budget=1000, ai_enabled=False)
        assert await off.run(request, asyncio.Event()) == "parked"
        assert waiting(engine, inv)[:2] == ("queued", "budget")
        assert model.calls == 0
        # Over budget with model calls on, it goes on: only new investigations wait.
        on = make_job(engine, Tools().all(), model, daily_token_budget=1000)
        assert await on.run(request, asyncio.Event()) == "done"

    asyncio.run(main())

    assert model.calls == 1
    assert [step.kind for _, step in recorded(engine, inv)] == ["tool_call"] * 2 + ["report"]


def test_the_tool_call_cap_ends_the_loop_with_a_partial_report(engine: Engine) -> None:
    # A model that never reports: without the cap it would run off the end of its script. The
    # forced final turn finds no report in the script either, so the job writes its own.
    script: list[ToolRequest | ReportDraft] = [
        ToolRequest(tool=TOOLS[n % 3], args={"service": "checkout"}, reason=f"Look again ({n}).")
        for n in range(MAX_TOOL_CALLS + 5)
    ]
    model = Counting(ScriptedModel(script, TokenUsage(input=1_000, output=100)))
    tools = Tools()
    request = new_request()

    assert asyncio.run(make_job(engine, tools.all(), model).run(request, asyncio.Event())) == "done"

    # 15 tool-call turns, then the forced report turn, which fails without spending tokens.
    assert (model.calls, len(tools.calls)) == (MAX_TOOL_CALLS + 1, MAX_TOOL_CALLS)
    query = select(
        Investigation.status, Investigation.final_report, Investigation.budget_tokens_used
    )
    with engine.connect() as conn:
        found, final, used = conn.execute(
            query.where(Investigation.id == request.investigation_id)
        ).one()
    report = Report.model_validate(final)
    assert (found, used, report.partial, report.hypotheses) == ("resolved", 16_500, True, [])
    assert f"after {MAX_TOOL_CALLS} tool calls and 16500 model tokens" in report.summary
    assert report.missing_evidence[0].root.startswith("The investigation stopped early")
    steps = [step.kind for _, step in recorded(engine, request.investigation_id)]
    assert steps == ["tool_call"] * (2 * MAX_TOOL_CALLS) + ["report"]


def test_tenant_cap_parks_investigations_over_it(engine: Engine) -> None:
    tools = Tools()

    async def main() -> None:
        gate = asyncio.Event()

        async def wait() -> None:
            await gate.wait()

        model = ScriptedModel(ONE_CALL)
        job = make_job(engine, tools.all(query_metrics=wait), model, tenant_cap=2)
        stopping = asyncio.Event()
        first = [asyncio.create_task(job.run(new_request(), stopping)) for _ in range(2)]
        await eventually(lambda: len(tools.calls) == 2)  # Both hold a slot, inside a tool call.
        third = new_request()
        assert await job.run(third, stopping) == "parked"
        assert waiting(engine, third.investigation_id) == ("queued", "tenant_cap", TENANT)
        other_tenant = asyncio.create_task(job.run(new_request(UUID(int=2)), stopping))
        await eventually(lambda: len(tools.calls) == 3)
        gate.set()
        assert await asyncio.gather(*first, other_tenant) == ["done"] * 3
        # Re-queued once a slot is free, it runs and stops waiting.
        assert await job.run(third, stopping) == "done"
        assert waiting(engine, third.investigation_id) == ("resolved", None, TENANT)

    asyncio.run(main())


def test_state_reports_parked_investigations_and_when_they_can_run(engine: Engine) -> None:
    job = make_job(engine, {}, ScriptedModel([REPORT]), tenant_cap=2, daily_token_budget=1000)
    api = state_api(job)
    assert api.get(f"/internal/investigations/{uuid.uuid4()}/state").status_code == 404

    # Two running investigations of the tenant, as far as the cap can tell.
    key = f"tenant:{TENANT}"
    holder = engine.connect()
    with holder.begin():
        for slot in (0, 1):
            holder.execute(text("SELECT pg_advisory_lock(hashtext(:k), :s)"), {"k": key, "s": slot})
    requests = [new_request() for _ in range(3)]

    async def run(*batch: Request) -> list[Outcome]:
        return [await job.run(request, asyncio.Event()) for request in batch]

    assert asyncio.run(run(*requests)) == ["parked"] * 3
    first, second, third = (request.investigation_id for request in requests)
    parked = state(api, first)
    assert (parked["status"], parked["waiting_for"]) == ("queued", "tenant_cap")
    assert parked["waiting_since"] is not None

    def resumable() -> list[bool]:
        return [state(api, inv)["resumable"] for inv in (first, second, third)]

    assert resumable() == [False, False, False]

    # Each free slot goes to the one that parked earliest and is not resumed yet.
    unlock = text("SELECT pg_advisory_unlock(hashtext(:k), :s)")
    with holder.begin():
        holder.execute(unlock, {"k": key, "s": 0})
    assert resumable() == [True, False, False]
    with holder.begin():
        holder.execute(unlock, {"k": key, "s": 1})
    assert resumable() == [True, True, False]
    holder.close()
    assert asyncio.run(run(requests[0], requests[1])) == ["done", "done"]
    assert resumable() == [False, False, True]
    finished = state(api, first)
    assert finished["status"] == "resolved"
    assert (finished["waiting_for"], finished["waiting_since"]) == (None, None)
    assert finished["steps_taken"] == 1

    # Budget: a new investigation over today's budget parks, and can run once the budget resets.
    with engine.begin() as conn:
        conn.execute(delete(AiUsage))
        conn.execute(insert(AiUsage).values(day=UTC_TODAY, tokens=1000))
    over = new_request()
    assert asyncio.run(run(over)) == ["parked"]
    assert state(api, over.investigation_id)["waiting_for"] == "budget"
    assert state(api, over.investigation_id)["resumable"] is False
    with engine.begin() as conn:
        conn.execute(delete(AiUsage))
    assert state(api, over.investigation_id)["resumable"] is True


class SpentAfter:
    """The day's quota runs out once `turns` model turns are recorded, the first time only."""

    def __init__(self, model: Model, turns: int) -> None:
        self.model = model
        self.turns = turns
        self.spent = False

    async def turn(self, request: TurnRequest) -> ModelTurn:
        if not self.spent and sum(map(is_model_turn, request.steps)) == self.turns:
            self.spent = True
            raise QuotaExhausted("all requests for today are spent")
        return await self.model.turn(request)

    def available(self, conn: Connection) -> bool:
        return True


def test_a_run_resumed_after_a_quota_park_keeps_the_time_it_had_left(engine: Engine) -> None:
    tools = Tools()
    request = new_request()
    inv = request.investigation_id
    job = make_job(engine, tools.all(), SpentAfter(ScriptedModel(SCRIPT), 1))
    assert asyncio.run(job.run(request, asyncio.Event())) == "parked"
    # It ran for a minute, then waited nine for the quota: more than MAX_SECONDS in all.
    with engine.begin() as conn:
        conn.execute(
            update(Investigation)
            .where(Investigation.id == inv)
            .values(
                started_at=func.now() - timedelta(minutes=10),
                waiting_since=func.now() - timedelta(minutes=9),
            )
        )
    assert asyncio.run(job.run(request, asyncio.Event())) == "done"
    with engine.connect() as conn:
        report, ran = conn.execute(
            select(Investigation.final_report, func.now() - Investigation.started_at).where(
                Investigation.id == inv
            )
        ).one()
    assert Report.model_validate(report).partial is False
    assert tools.calls == list(TOOLS)
    assert timedelta(minutes=1) <= ran < timedelta(minutes=2)


def test_a_day_the_provider_called_spent_holds_parked_work_until_midnight_pacific(
    engine: Engine,
) -> None:
    """Google's "daily quota spent" ends the quota day whatever RPD allows: a parked
    investigation, resumed or new, is not resumable until midnight Pacific, so the sweeper
    leaves it alone and an eval sees a quota stop instead of a re-send every sweep."""
    unused = cast("Provider", object())
    router = Router(
        Binding(Role("primary", "gemini", "m", None, rpm=0, rpd=260), unused),
        None,
        Binding(Role("small", "gemini", "s", None, rpm=0, rpd=0), unused),
        fallback_on_rate_limit=False,
        limiter=Limiter(None),
    )
    job = make_job(engine, Tools().all(), router)
    inv = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(insert(Investigation).values(id=inv))
        conn.execute(
            insert(ModelCall).values(
                investigation_id=inv,
                role="primary",
                provider="gemini",
                model="m",
                outcome="quota_exhausted",
                input_tokens=0,
                output_tokens=0,
                cached_tokens=0,
                thought_tokens=0,
                latency_ms=400,
                error="QuotaExhausted: gemini 429: daily quota spent",
            )
        )
    with engine.connect() as conn:
        assert not job.resumable(conn, inv, "budget", TENANT, new=False)
        assert not job.resumable(conn, inv, "budget", TENANT, new=True)
    with engine.begin() as conn:
        conn.execute(update(ModelCall).values(created_at=func.now() - timedelta(days=1)))
    with engine.connect() as conn:
        assert job.resumable(conn, inv, "budget", TENANT, new=False)


def test_claude_credits_gone_hold_parked_work_until_the_month_ends(engine: Engine) -> None:
    """Credits gone keep Claude off for the rest of the month (UTC), and its credits' end date
    for good: an eval pinned to Claude stays parked and stops with exit 2, with no second call."""
    unused = cast("Provider", object())
    today = datetime.now(UTC).date()

    def job_until(until: date) -> Job:
        haiku = Role("primary", "anthropic", "claude-haiku-5-5", None, 0, 0, until=until)
        small = Role("small", "anthropic", "claude-haiku-5-5", None, 0, 0, until=until)
        router = Router(
            Binding(haiku, unused),
            None,
            Binding(small, unused),
            fallback_on_rate_limit=False,
            limiter=Limiter(None),
        )
        return make_job(engine, Tools().all(), router)

    job = job_until(today + timedelta(days=60))
    inv = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(insert(Investigation).values(id=inv))
        conn.execute(
            insert(ModelCall).values(
                investigation_id=inv,
                role="primary",
                provider="anthropic",
                model="claude-haiku-5-5",
                outcome="credits_exhausted",
                input_tokens=0,
                output_tokens=0,
                cached_tokens=0,
                thought_tokens=0,
                latency_ms=300,
                error="CreditsExhausted: anthropic 402: credits or spend limit used up",
            )
        )
    month = "date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
    with engine.begin() as conn:
        # Still this month: a credits stop outlasts the quota day.
        conn.execute(update(ModelCall).values(created_at=text(month)))
    with engine.connect() as conn:
        assert not job.resumable(conn, inv, "budget", TENANT, new=False)
    with engine.begin() as conn:
        last_month = text(f"{month} - interval '1 second'")
        conn.execute(update(ModelCall).values(created_at=last_month))
    with engine.connect() as conn:
        assert job.resumable(conn, inv, "budget", TENANT, new=False)
        # From the day the credits expire, Claude is spent whatever the calls say.
        assert not job_until(today).resumable(conn, inv, "budget", TENANT, new=False)
