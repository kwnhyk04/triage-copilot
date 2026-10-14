"""Consumer message handling against fakes; test_workers_int.py runs it against ElasticMQ."""

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

import pytest
from types_boto3_sqs import SQSClient

from agent.consumer import MAX_RECEIVES, Consumer
from agent.job import Outcome, Request

INVESTIGATION_ID = "1b4e28ba-2fa1-41d2-883f-0016d3cca427"
VALID = json.dumps(
    {
        "schema_version": 2,
        "investigation_id": INVESTIGATION_ID,
        "tenant_id": "6f1c2a9e-8b3d-4e5f-9a7b-2c4d6e8f0a1b",
        "group_key": "checkout-errors",
        "requested_at": "2026-10-14T12:00:00Z",
    }
)


class FakeSqs:
    """Hands out queued messages, and records deletes and visibility changes with their time."""

    def __init__(
        self, bodies: list[str], receive_count: int = 1, groups: list[str] | None = None
    ) -> None:
        """Each message gets its own FIFO group unless `groups` names them."""
        self.waiting = [
            {
                "MessageId": f"m{i}",
                "ReceiptHandle": f"r{i}",
                "Body": body,
                "Attributes": {
                    "ApproximateReceiveCount": str(receive_count),
                    "MessageGroupId": groups[i] if groups else f"g{i}",
                },
            }
            for i, body in enumerate(bodies)
        ]
        self.asked: list[int] = []
        self.deleted: list[str] = []
        self.visibility: list[tuple[str, int, float]] = []

    def get_queue_url(self, **_: Any) -> dict[str, str]:
        return {"QueueUrl": "queue-url"}

    def receive_message(self, *, MaxNumberOfMessages: int, **_: Any) -> dict[str, Any]:
        self.asked.append(MaxNumberOfMessages)
        batch = self.waiting[:MaxNumberOfMessages]
        del self.waiting[:MaxNumberOfMessages]
        if not batch:
            time.sleep(0.01)  # An empty long poll.
        return {"Messages": batch}

    def delete_message(self, *, ReceiptHandle: str, **_: Any) -> None:
        self.deleted.append(ReceiptHandle)

    def change_message_visibility(
        self, *, ReceiptHandle: str, VisibilityTimeout: int, **_: Any
    ) -> None:
        self.visibility.append((ReceiptHandle, VisibilityTimeout, time.monotonic()))


class FakeJob:
    def __init__(self, outcome: Outcome | Exception = "done", seconds: float = 0) -> None:
        self.outcome: Outcome | Exception = outcome
        self.seconds = seconds
        self.running = 0
        self.most_running = 0
        self.ran: list[Request] = []
        self.dead_letters: list[tuple[str, str, str]] = []
        self.failed: list[UUID] = []
        self.stopped_at: float | None = None

    async def run(self, request: Request, stopping: asyncio.Event) -> Outcome:
        self.ran.append(request)
        self.running += 1
        self.most_running = max(self.most_running, self.running)
        try:
            await asyncio.sleep(self.seconds)
        finally:
            self.running -= 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def dead_letter(self, message_id: str, group_id: str, body: str, error: str) -> None:
        assert error
        self.dead_letters.append((message_id, group_id, body))

    async def mark_failed(self, investigation_id: UUID) -> None:
        self.failed.append(investigation_id)


class StepJob(FakeJob):
    """Runs until shutdown, then takes a while to finish its current step."""

    async def run(self, request: Request, stopping: asyncio.Event) -> Outcome:
        await stopping.wait()
        self.stopped_at = time.monotonic()
        await asyncio.sleep(0.3)
        return "stopped"


def consume(sqs: FakeSqs, job: FakeJob, until: Callable[[], bool], **options: Any) -> None:
    """Runs a consumer until `until()` holds or 5 s pass, then shuts it down."""

    async def main() -> None:
        stopping = asyncio.Event()
        consumer = Consumer(
            cast(SQSClient, sqs), "investigations.fifo", job, wait_seconds=0, **options
        )
        task = asyncio.create_task(consumer.run(stopping))
        for _ in range(500):  # 5 s at most
            if until():
                break
            await asyncio.sleep(0.01)
        stopping.set()
        await asyncio.wait_for(task, 5)

    asyncio.run(main())


def test_runs_valid_messages_and_dead_letters_invalid_ones() -> None:
    sqs = FakeSqs([VALID, '{"schema_version": 3}', "not json"])
    job = FakeJob("done")
    consume(sqs, job, until=lambda: len(sqs.deleted) == 3)

    # Invalid messages are deleted at once, so they never use up receives on the way to the DLQ.
    assert sorted(sqs.deleted) == ["r0", "r1", "r2"]
    assert sorted(job.dead_letters) == [
        ("m1", "g1", '{"schema_version": 3}'),
        ("m2", "g2", "not json"),
    ]
    assert [request.group_key for request in job.ran] == ["checkout-errors"]


@pytest.mark.parametrize("outcome", ["stopped", "busy", RuntimeError("model down")])
def test_keeps_the_message_unless_the_report_is_committed(outcome: Outcome | Exception) -> None:
    sqs = FakeSqs([VALID])
    job = FakeJob(outcome)
    consume(sqs, job, until=lambda: bool(job.ran), busy_backoff_seconds=(60, 120))

    assert sqs.deleted == []
    if outcome == "busy":
        [(receipt, seconds, _)] = sqs.visibility
        assert receipt == "r0"
        assert 60 <= seconds <= 120
    else:
        assert sqs.visibility == []
    assert job.failed == []


def test_runs_one_message_per_fifo_group_and_hands_back_the_rest() -> None:
    sqs = FakeSqs([VALID, VALID, VALID], groups=["a", "a", "b"])
    job = FakeJob("done", seconds=0.05)
    consume(sqs, job, until=lambda: len(sqs.deleted) == 2)

    assert sorted(sqs.deleted) == ["r0", "r2"]
    # SQS holds the group's second message until the first is deleted, then redelivers it.
    assert [(receipt, seconds) for receipt, seconds, _ in sqs.visibility] == [("r1", 0)]
    assert len(job.ran) == 2


def test_marks_the_investigation_failed_on_its_last_receive() -> None:
    sqs = FakeSqs([VALID], receive_count=MAX_RECEIVES)
    job = FakeJob(RuntimeError("model down"))
    consume(sqs, job, until=lambda: bool(job.failed))
    assert job.failed == [UUID(INVESTIGATION_ID)]
    assert sqs.deleted == []


def test_never_has_more_than_max_in_flight_messages() -> None:
    sqs = FakeSqs([VALID] * 12)
    job = FakeJob("done", seconds=0.05)
    consume(sqs, job, until=lambda: len(sqs.deleted) == 12, max_in_flight=5)

    assert len(sqs.deleted) == 12
    assert job.most_running == 5
    assert max(sqs.asked) == 5


def test_heartbeat_extends_visibility_until_shutdown() -> None:
    sqs = FakeSqs([VALID])
    job = StepJob()
    consume(
        sqs,
        job,
        until=lambda: len(sqs.visibility) >= 3,
        heartbeat_seconds=0.05,
        visibility_seconds=7,
    )

    assert job.stopped_at is not None
    assert {(receipt, seconds) for receipt, seconds, _ in sqs.visibility} == {("r0", 7)}
    # The job spent 0.3 s (6 beats) on its last step after shutdown began. At most one extension,
    # already in flight then, lands afterwards.
    assert sum(at > job.stopped_at for _, _, at in sqs.visibility) <= 1
    assert sqs.deleted == []
