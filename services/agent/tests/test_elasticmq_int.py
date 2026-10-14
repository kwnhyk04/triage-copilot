"""Probes the ElasticMQ behavior the agent relies on, using throwaway queues set up like ours."""

import threading
import time
import uuid
from collections.abc import Mapping

import pytest
from types_boto3_sqs import SQSClient
from types_boto3_sqs.type_defs import MessageAttributeValueTypeDef, MessageTypeDef

from agent.consumer import MAX_RECEIVES

pytestmark = pytest.mark.integration


def send(
    sqs: SQSClient,
    queue: str,
    body: str,
    *,
    group: str = "g1",
    dedup: str | None = None,
    attributes: Mapping[str, MessageAttributeValueTypeDef] | None = None,
) -> None:
    sqs.send_message(
        QueueUrl=queue,
        MessageBody=body,
        MessageGroupId=group,
        MessageDeduplicationId=dedup or uuid.uuid4().hex,
        MessageAttributes=attributes or {},
    )


def receive(
    sqs: SQSClient, queue: str, *, limit: int = 10, wait: int = 0, visibility: int = 30
) -> list[MessageTypeDef]:
    response = sqs.receive_message(
        QueueUrl=queue,
        MaxNumberOfMessages=limit,
        WaitTimeSeconds=wait,
        VisibilityTimeout=visibility,
        MessageAttributeNames=["All"],
        MessageSystemAttributeNames=["All"],
    )
    return response.get("Messages", [])


def bodies(messages: list[MessageTypeDef]) -> list[str]:
    return [message.get("Body", "") for message in messages]


def test_fifo_keeps_group_order_and_holds_a_group_in_flight(
    sqs: SQSClient, queues: tuple[str, str]
) -> None:
    queue, _ = queues
    send(sqs, queue, "a1", group="a")
    send(sqs, queue, "a2", group="a")
    send(sqs, queue, "b1", group="b")

    assert bodies(receive(sqs, queue, limit=1)) == ["a1"]
    # a2 waits until a1 is deleted or visible again; group b is independent.
    assert bodies(receive(sqs, queue)) == ["b1"]


def test_deduplication_id_drops_a_repeat(sqs: SQSClient, queues: tuple[str, str]) -> None:
    queue, _ = queues
    send(sqs, queue, "first", dedup="same-id")
    send(sqs, queue, "repeat", dedup="same-id")
    assert bodies(receive(sqs, queue)) == ["first"]


def test_long_poll_waits_and_returns_when_a_message_arrives(
    sqs: SQSClient, queues: tuple[str, str]
) -> None:
    queue, _ = queues
    started = time.monotonic()
    assert receive(sqs, queue, wait=1) == []
    assert time.monotonic() - started >= 0.9

    threading.Timer(0.5, send, args=(sqs, queue, "late")).start()
    started = time.monotonic()
    assert bodies(receive(sqs, queue, wait=10)) == ["late"]
    assert time.monotonic() - started < 5


def test_change_message_visibility_extends_and_releases(
    sqs: SQSClient, queues: tuple[str, str]
) -> None:
    queue, _ = queues
    send(sqs, queue, "work")
    [message] = receive(sqs, queue, visibility=1)
    receipt = message.get("ReceiptHandle", "")

    sqs.change_message_visibility(QueueUrl=queue, ReceiptHandle=receipt, VisibilityTimeout=30)
    time.sleep(1.5)
    assert receive(sqs, queue) == []  # the extension outlived the original 1 s timeout

    sqs.change_message_visibility(QueueUrl=queue, ReceiptHandle=receipt, VisibilityTimeout=0)
    assert bodies(receive(sqs, queue)) == ["work"]


def test_message_and_system_attributes_round_trip(sqs: SQSClient, queues: tuple[str, str]) -> None:
    queue, _ = queues
    attributes: dict[str, MessageAttributeValueTypeDef] = {
        "trace_id": {"DataType": "String", "StringValue": "trace-123"},
        "attempt": {"DataType": "Number", "StringValue": "2"},
    }
    send(sqs, queue, "with-attributes", group="checkout", attributes=attributes)
    [message] = receive(sqs, queue)
    custom = message.get("MessageAttributes", {})
    system = message.get("Attributes", {})

    assert custom["trace_id"].get("StringValue") == "trace-123"
    assert custom["attempt"].get("DataType") == "Number"
    assert system.get("MessageGroupId") == "checkout"
    assert system.get("ApproximateReceiveCount") == "1"


def test_redrives_to_dlq_after_five_receives(sqs: SQSClient, queues: tuple[str, str]) -> None:
    queue, dlq = queues
    send(sqs, queue, "poison")
    for attempt in range(1, MAX_RECEIVES + 1):
        [message] = receive(sqs, queue, visibility=0)
        assert message.get("Attributes", {}).get("ApproximateReceiveCount") == str(attempt)

    assert receive(sqs, queue) == []  # receive number 6 moves the message instead of returning it
    assert bodies(receive(sqs, dlq)) == ["poison"]
