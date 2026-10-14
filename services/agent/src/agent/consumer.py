"""Long-polls the investigations queue. Invalid messages are never deleted, so they hit the DLQ."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from pydantic import ValidationError

from py_clients.generated.messages.investigation_requested_v1_schema import InvestigationRequestedV1

if TYPE_CHECKING:
    # Stub-only package from the dev group; never imported at runtime.
    from types_boto3_sqs import SQSClient

log = logging.getLogger(__name__)

WAIT_SECONDS = 10
"""Long-poll length; also the longest a shutdown waits for the consumer thread."""

Handler = Callable[[InvestigationRequestedV1], None]


def poll_once(sqs: SQSClient, queue_url: str, handle: Handler) -> int:
    """Receives one batch and deletes each message `handle` processed; returns how many."""
    response = sqs.receive_message(
        QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=WAIT_SECONDS
    )
    handled = 0
    for message in response.get("Messages", []):
        message_id = message.get("MessageId")
        try:
            request = InvestigationRequestedV1.model_validate_json(message.get("Body", ""))
        except ValidationError:
            log.warning("invalid message left for redrive", extra={"message_id": message_id})
            continue
        try:
            handle(request)
        except Exception:
            # Left in the queue: it comes back after the visibility timeout, then hits the DLQ.
            log.exception("investigation failed", extra={"message_id": message_id})
            continue
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message.get("ReceiptHandle", ""))
        handled += 1
    return handled


def consume(sqs: SQSClient, queue_name: str, handle: Handler, stop: threading.Event) -> None:
    """Polls until `stop` is set. Any error is logged and retried, so the thread never dies."""
    queue_url: str | None = None
    while not stop.is_set():
        try:
            queue_url = queue_url or sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
            poll_once(sqs, queue_url, handle)
        except Exception:
            log.exception("queue poll failed")
            stop.wait(5)
