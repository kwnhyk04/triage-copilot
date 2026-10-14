"""Long-polls investigations.fifo and runs each request as a resumable job.

A message is deleted only once its final report is committed. One that fails schema validation
goes to agent.dead_letters and is deleted at once, so it does not use up the 5 receives that move
a message to investigations-dlq.fifo. Any other failure leaves the message to SQS redelivery.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING, Protocol

import boto3
from botocore.config import Config
from pydantic import ValidationError

from agent.job import Outcome, Request, sleep_unless
from py_clients.generated.messages.investigation_requested_schema import InvestigationRequested

if TYPE_CHECKING:
    from uuid import UUID

    # Stub-only package from the dev group; never imported at runtime.
    from types_boto3_sqs import SQSClient
    from types_boto3_sqs.type_defs import MessageTypeDef

    from agent.settings import Settings

log = logging.getLogger(__name__)

WAIT_SECONDS = 20
HEARTBEAT_SECONDS = 30.0
# Each heartbeat hides the message this long again, so one missed beat does not expose it.
VISIBILITY_SECONDS = 60
# maxReceiveCount of investigations.fifo in infra/elasticmq.conf.
MAX_RECEIVES = 5
BUSY_BACKOFF_SECONDS = (60.0, 120.0)


def sqs_client(settings: Settings) -> SQSClient:
    # The read timeout outlasts the long poll.
    config = Config(connect_timeout=5, read_timeout=WAIT_SECONDS + 10, retries={"max_attempts": 2})
    client: SQSClient = boto3.client(
        "sqs",
        endpoint_url=settings.sqs_endpoint_url,
        region_name=settings.aws_region,
        config=config,
    )
    return client


class Runner(Protocol):
    async def run(self, request: Request, stopping: asyncio.Event) -> Outcome: ...
    async def dead_letter(self, message_id: str, group_id: str, body: str, error: str) -> None: ...
    async def mark_failed(self, investigation_id: UUID) -> None: ...


class Consumer:
    def __init__(
        self,
        sqs: SQSClient,
        queue_name: str,
        job: Runner,
        *,
        max_in_flight: int = 5,
        wait_seconds: int = WAIT_SECONDS,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        visibility_seconds: int = VISIBILITY_SECONDS,
        busy_backoff_seconds: tuple[float, float] = BUSY_BACKOFF_SECONDS,
    ) -> None:
        self.sqs = sqs
        self.queue_name = queue_name
        self.queue_url = ""
        self.job = job
        self.max_in_flight = max_in_flight
        self.wait_seconds = wait_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.visibility_seconds = visibility_seconds
        self.busy_backoff_seconds = busy_backoff_seconds

    async def run(self, stopping: asyncio.Event) -> None:
        """Receives until `stopping` is set, then waits for each job to finish its step and exit.

        Each receive asks only for as many messages as there are free slots, so at most
        max_in_flight messages are ever in flight.
        """
        slots = asyncio.Semaphore(self.max_in_flight)
        async with asyncio.TaskGroup() as jobs:
            while True:
                await slots.acquire()
                if stopping.is_set():
                    break
                free = 1
                while free < self.max_in_flight and not slots.locked():
                    await slots.acquire()
                    free += 1
                try:
                    messages = await asyncio.to_thread(self._receive, free)
                except Exception:
                    log.exception("queue receive failed")
                    messages = []
                    await sleep_unless(stopping, 5)
                # Messages received after shutdown began redeliver after their visibility timeout.
                started = [] if stopping.is_set() else messages
                for _ in range(free - len(started)):
                    slots.release()
                groups: set[str] = set()
                for message in started:
                    group = message.get("Attributes", {}).get("MessageGroupId", "")
                    if group and group in groups:
                        # A batch can hold several messages of one FIFO group. The later ones go
                        # back at once, and SQS holds them until the earlier one is deleted.
                        jobs.create_task(self._hand_back(message, slots))
                        continue
                    groups.add(group)
                    jobs.create_task(self._process(message, stopping, slots))

    def _receive(self, count: int) -> list[MessageTypeDef]:
        if not self.queue_url:
            self.queue_url = self.sqs.get_queue_url(QueueName=self.queue_name)["QueueUrl"]
        response = self.sqs.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=count,
            WaitTimeSeconds=self.wait_seconds,
            VisibilityTimeout=self.visibility_seconds,
            MessageSystemAttributeNames=["ApproximateReceiveCount", "MessageGroupId"],
        )
        return response.get("Messages", [])

    async def _process(
        self, message: MessageTypeDef, stopping: asyncio.Event, slots: asyncio.Semaphore
    ) -> None:
        receipt = message.get("ReceiptHandle", "")
        attributes = message.get("Attributes", {})
        message_id = message.get("MessageId", "")
        try:
            body = message.get("Body", "")
            try:
                request = InvestigationRequested.model_validate_json(body).root
            except ValidationError as error:
                problems = error.json(include_input=False, include_url=False)
                group_id = attributes.get("MessageGroupId", "")
                await self.job.dead_letter(message_id, group_id, body, problems)
                await self._delete(receipt)
                log.warning("invalid message dead-lettered", extra={"message_id": message_id})
                return
            heartbeat = asyncio.create_task(self._heartbeat(receipt, stopping))
            try:
                outcome = await self.job.run(request, stopping)
            except Exception:
                investigation_id = str(request.investigation_id)
                log.exception("investigation failed", extra={"investigation_id": investigation_id})
                if int(attributes.get("ApproximateReceiveCount", "1")) >= MAX_RECEIVES:
                    await self.job.mark_failed(request.investigation_id)
                return
            finally:
                heartbeat.cancel()
            if outcome == "done":
                await self._delete(receipt)
            elif outcome == "busy":
                # ponytail: cap and duplicate waits use up receives, so a tenant over its cap for
                # about 10 minutes (several agents) dead-letters work. Park it in Postgres then.
                delay = round(random.uniform(*self.busy_backoff_seconds))  # noqa: S311 - jitter
                await self._change_visibility(receipt, delay)
        except Exception:
            # Redelivery covers a failed delete or visibility change; the job is idempotent.
            log.exception("message handling failed", extra={"message_id": message_id})
        finally:
            slots.release()

    async def _hand_back(self, message: MessageTypeDef, slots: asyncio.Semaphore) -> None:
        try:
            await self._change_visibility(message.get("ReceiptHandle", ""), 0)
        except Exception:
            log.warning("hand-back failed; the message returns after its timeout", exc_info=True)
        finally:
            slots.release()

    async def _heartbeat(self, receipt: str, stopping: asyncio.Event) -> None:
        """Keeps the message hidden while its job runs, and stops at shutdown so it redelivers."""
        while not await sleep_unless(stopping, self.heartbeat_seconds):
            try:
                await self._change_visibility(receipt, self.visibility_seconds)
            except Exception:
                log.warning("visibility heartbeat failed", exc_info=True)

    async def _delete(self, receipt: str) -> None:
        await asyncio.to_thread(
            self.sqs.delete_message, QueueUrl=self.queue_url, ReceiptHandle=receipt
        )

    async def _change_visibility(self, receipt: str, seconds: int) -> None:
        await asyncio.to_thread(
            self.sqs.change_message_visibility,
            QueueUrl=self.queue_url,
            ReceiptHandle=receipt,
            VisibilityTimeout=seconds,
        )
