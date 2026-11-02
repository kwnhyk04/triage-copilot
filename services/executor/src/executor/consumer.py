"""The executor's consumer: ActionApproved from actions.fifo, one message at a time, deleted once
it is handled. A message whose handling raised stays and comes back after its visibility timeout;
its redelivery finds its claim, so nothing runs twice, and after 5 receives it moves to
actions-dlq.fifo."""

import logging
import threading
from collections.abc import Callable
from typing import Any, Protocol

log = logging.getLogger(__name__)

# Longer than any restart (executor.restart's 120 s timeout), so a message is not redelivered while
# its action still runs.
VISIBILITY_SECONDS = 300


class Queue(Protocol):
    def receive_message(self, **ask: Any) -> Any: ...
    def delete_message(self, **ask: Any) -> Any: ...


def consume(
    sqs: Queue,
    queue_url: str,
    handle: Callable[[str], object],
    stopping: threading.Event,
    *,
    wait_seconds: int = 20,
) -> None:
    """Long-polls until `stopping` is set."""
    while not stopping.is_set():
        try:
            response = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=wait_seconds,
                VisibilityTimeout=VISIBILITY_SECONDS,
            )
        except Exception:  # The queue is down: try again shortly, as the agent's consumer does.
            log.exception("could not receive from the actions queue")
            stopping.wait(5)
            continue
        for message in response.get("Messages", []):
            try:
                handle(message["Body"])
            except Exception:
                log.exception("handling an ActionApproved raised; the message stays")
                continue
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
