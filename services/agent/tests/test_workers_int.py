"""The worker against ElasticMQ and a scratch Postgres: restarts, the DLQ, the budget guard and the
tenant cap. Each test gets throwaway queues set up like investigations.fifo and its DLQ.
"""

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine, delete, insert, select, text
from types_boto3_sqs import SQSClient

from agent.consumer import Consumer
from agent.db import AiUsage, DeadLetter, Investigation, ToolCall
from agent.dlq import replay_dead_letters, replay_dlq
from agent.job import UTC_TODAY, Job, Outcome, Request, Tool
from agent.model import Model, ModelTurn, Report, ScriptedModel, ToolRequest
from agent.steps import StepRecordedV2, TokenUsage, record_step, replay
from py_clients.generated.gateway import Hypothesis, RootCauseClass
from py_clients.generated.messages.investigation_requested_v2_schema import InvestigationRequestedV2

if TYPE_CHECKING:
    from conftest import ScratchDb

pytestmark = pytest.mark.integration

TENANT = UUID("6f1c2a9e-8b3d-4e5f-9a7b-2c4d6e8f0a1b")
TOOLS = ("query_metrics", "search_logs", "get_deploys")
REPORT = Report(
    summary="Checkout errors follow the v1.8.0 deploy.",
    hypotheses=[
        Hypothesis(
            rank=1,
            root_cause_class=RootCauseClass.bad_deploy,
            summary="v1.8.0 broke checkout.",
            confidence=0.8,
            evidence=[],
        )
    ],
)
SCRIPT: list[ToolRequest | Report] = [
    *(
        ToolRequest(tool=name, args={"service": "checkout"}, reason=f"Run {name}.")
        for name in TOOLS
    ),
    REPORT,
]
ONE_CALL: list[ToolRequest | Report] = [SCRIPT[0], REPORT]
STEP_SEQS = text(
    "SELECT seq FROM agent.investigation_steps WHERE investigation_id = :id ORDER BY 1"
)


class Tools:
    """Read-only fakes. `calls` is what the outside world sees, across agent restarts."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def all(self, **behaviors: Callable[[], Awaitable[None]]) -> dict[str, Tool]:
        def make(name: str) -> Tool:
            async def call(args: dict[str, Any]) -> str:
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

    async def turn(self, steps: Sequence[StepRecordedV2]) -> ModelTurn:
        self.calls += 1
        return await self.model.turn(steps)


class BrokenModel:
    def __init__(self) -> None:
        self.calls = 0

    async def turn(self, steps: Sequence[StepRecordedV2]) -> ModelTurn:
        self.calls += 1
        raise RuntimeError("model bug")


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
        "budget_poll_seconds": 0.05,
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


def test_budget_guard_holds_new_investigations_and_counts_tokens(engine: Engine) -> None:
    model = Counting(ScriptedModel(ONE_CALL, TokenUsage(input=100, output=10)))
    job = make_job(engine, Tools().all(), model, daily_token_budget=1000)
    with engine.begin() as conn:
        conn.execute(insert(AiUsage).values(day=UTC_TODAY, tokens=1000))
    request = new_request()
    inv = request.investigation_id

    async def main() -> None:
        running = asyncio.create_task(job.run(request, asyncio.Event()))
        await eventually(lambda: status(engine, inv) == "queued_budget")
        await asyncio.sleep(0.3)  # Several budget checks.
        assert model.calls == 0
        with engine.begin() as conn:
            conn.execute(delete(AiUsage))  # A new UTC day.
        assert await asyncio.wait_for(running, 30) == "done"

    asyncio.run(main())

    assert model.calls == 2
    assert status(engine, inv) == "resolved"
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
        # With model calls off, even a resumed investigation waits once it needs the model.
        stopping = asyncio.Event()
        off = make_job(engine, Tools().all(), model, daily_token_budget=1000, ai_enabled=False)
        running = asyncio.create_task(off.run(request, stopping))
        await eventually(lambda: status(engine, inv) == "queued_budget")
        stopping.set()
        assert await asyncio.wait_for(running, 30) == "stopped"
        assert model.calls == 0
        # Over budget with model calls on, it goes on: only new investigations wait.
        on = make_job(engine, Tools().all(), model, daily_token_budget=1000)
        assert await on.run(request, asyncio.Event()) == "done"

    asyncio.run(main())

    assert model.calls == 1
    assert [step.kind for _, step in recorded(engine, inv)] == ["tool_call"] * 2 + ["report"]


def test_tenant_cap_limits_running_investigations_per_tenant(engine: Engine) -> None:
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
        assert await job.run(new_request(), stopping) == "busy"
        other_tenant = asyncio.create_task(job.run(new_request(UUID(int=2)), stopping))
        await eventually(lambda: len(tools.calls) == 3)
        gate.set()
        assert await asyncio.gather(*first, other_tenant) == ["done"] * 3
        assert await job.run(new_request(), stopping) == "done"

    asyncio.run(main())
