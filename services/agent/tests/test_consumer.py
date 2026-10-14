from typing import Any, cast

from types_boto3_sqs import SQSClient

from agent.consumer import poll_once
from py_clients.generated.messages.investigation_requested_v1_schema import InvestigationRequestedV1

VALID = (
    '{"schema_version": 1, "investigation_id": "1b4e28ba-2fa1-11d2-883f-0016d3cca427",'
    ' "group_key": "checkout-errors", "requested_at": "2026-10-14T12:00:00Z"}'
)


class FakeSqs:
    def __init__(self, bodies: list[str]) -> None:
        self.messages = [
            {"MessageId": str(i), "ReceiptHandle": f"receipt-{i}", "Body": body}
            for i, body in enumerate(bodies)
        ]
        self.deleted: list[str] = []

    def receive_message(self, **_: Any) -> dict[str, Any]:
        return {"Messages": self.messages}

    def delete_message(self, *, QueueUrl: str, ReceiptHandle: str) -> None:
        self.deleted.append(ReceiptHandle)


def test_deletes_valid_messages_and_leaves_invalid_ones_for_redrive() -> None:
    sqs = FakeSqs([VALID, '{"schema_version": 2}', "not json"])
    handled: list[InvestigationRequestedV1] = []

    assert poll_once(cast(SQSClient, sqs), "queue-url", handled.append) == 1
    assert sqs.deleted == ["receipt-0"]
    assert [request.group_key for request in handled] == ["checkout-errors"]


def test_handler_failure_leaves_message_for_redrive_and_keeps_going() -> None:
    sqs = FakeSqs([VALID, VALID.replace("checkout-errors", "db-latency")])
    handled: list[str] = []

    def handle(request: InvestigationRequestedV1) -> None:
        if request.group_key == "checkout-errors":
            raise RuntimeError("tool failed")
        handled.append(request.group_key)

    assert poll_once(cast(SQSClient, sqs), "queue-url", handle) == 1
    assert sqs.deleted == ["receipt-1"]
    assert handled == ["db-latency"]
