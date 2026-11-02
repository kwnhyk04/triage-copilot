"""The executor's queue consumer with a fake SQS: one message at a time, deleted once handled."""

import threading
from typing import Any

from executor.consumer import consume


class Queue:
    """receive_message and delete_message over a list of bodies; stops the consumer when empty."""

    def __init__(self, bodies: list[str], stopping: threading.Event) -> None:
        self.bodies, self.stopping = list(bodies), stopping
        self.deleted: list[str] = []
        self.asks: list[dict[str, Any]] = []

    def receive_message(self, **ask: Any) -> dict[str, Any]:
        self.asks.append(ask)
        if not self.bodies:
            self.stopping.set()
            return {}
        body = self.bodies.pop(0)
        return {"Messages": [{"Body": body, "ReceiptHandle": f"r-{body}"}]}

    def delete_message(self, **ask: Any) -> None:
        self.deleted.append(ask["ReceiptHandle"])


def test_each_message_is_handled_alone_and_deleted_once_handled() -> None:
    stopping = threading.Event()
    queue, handled = Queue(["a", "boom", "b"], stopping), list[str]()

    def handle(body: str) -> None:
        handled.append(body)
        if body == "boom":
            raise RuntimeError("a bug")

    consume(queue, "url", handle, stopping, wait_seconds=0)

    assert handled == ["a", "boom", "b"]
    # A message whose handling raised stays, so its redelivery finds its claim.
    assert queue.deleted == ["r-a", "r-b"]
    assert {ask["MaxNumberOfMessages"] for ask in queue.asks} == {1}
    assert {ask["VisibilityTimeout"] for ask in queue.asks} == {300}
