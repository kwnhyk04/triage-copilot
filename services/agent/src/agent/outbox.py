"""Agent events for the gateway, through a transactional outbox.

A step or report commits together with its event row; the relay sends the rows to
AGENT_EVENTS_QUEUE in order per investigation (the MessageGroupId) and deletes each once SQS has
it. A crash between the send and the delete sends it again; SQS drops the repeat within its
5-minute deduplication window, and the gateway stores each event once anyway.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy import Connection, Engine, delete, insert, text

from agent.db import Outbox

if TYPE_CHECKING:
    from types_boto3_sqs import SQSClient

log = logging.getLogger(__name__)

STEP = "step-recorded"
REPORT = "report-recorded"
PLAN = "plan-recorded"
# Failed sends after which a row waits for a human (failed_at).
MAX_ATTEMPTS = 10
BATCH = 10
# Due rows, oldest first; a row waits while an earlier one of its group is still unsent.
DUE = text("""
    SELECT id, message_type, group_id, deduplication_id, body, attempts FROM agent.outbox o
    WHERE failed_at IS NULL AND next_attempt_at <= now()
      AND NOT EXISTS (
        SELECT FROM agent.outbox e
        WHERE e.group_id = o.group_id AND e.id < o.id AND e.failed_at IS NULL)
    ORDER BY id LIMIT :batch FOR UPDATE SKIP LOCKED
""")
FAILED = text("""
    UPDATE agent.outbox
    SET attempts = :attempts, last_error = :error,
        next_attempt_at = now() + make_interval(secs => :delay),
        failed_at = CASE WHEN :attempts >= :max THEN now() END
    WHERE id = :id
""")

Send = Callable[[str, str, str, dict[str, Any]], None]
"""(message_type, group_id, deduplication_id, body): sends one event or raises."""


def enqueue(
    conn: Connection, message_type: str, group_id: str, deduplication_id: str, body: dict[str, Any]
) -> None:
    """Adds an event in the caller's transaction."""
    row = {"group_id": group_id, "deduplication_id": deduplication_id, "body": body}
    conn.execute(insert(Outbox).values(message_type=message_type, **row))


def relay_once(engine: Engine, send: Send) -> int:
    """Sends due events and deletes the sent ones; a failure waits 2^attempts s, at most 300."""
    sent = 0
    with engine.begin() as conn:
        for row in conn.execute(DUE, {"batch": BATCH}).all():
            try:
                send(row.message_type, row.group_id, row.deduplication_id, row.body)
            except Exception as error:
                attempts = row.attempts + 1
                values = {
                    "id": row.id,
                    "attempts": attempts,
                    "error": f"{type(error).__name__}: {error}"[:500],
                    "delay": min(2**attempts, 300),
                    "max": MAX_ATTEMPTS,
                }
                conn.execute(FAILED, values)
                log.warning("event send failed", extra={"outbox_id": row.id, "attempts": attempts})
            else:
                conn.execute(delete(Outbox).where(Outbox.id == row.id))
                sent += 1
    return sent


def sqs_sender(sqs: SQSClient, queue_name: str) -> Send:
    """Sends to the FIFO queue with the event's type as the message_type attribute."""
    url = ""

    def send(message_type: str, group_id: str, deduplication_id: str, body: dict[str, Any]) -> None:
        nonlocal url
        url = url or sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
        sqs.send_message(
            QueueUrl=url,
            MessageBody=json.dumps(body),
            MessageGroupId=group_id,
            MessageDeduplicationId=deduplication_id,
            MessageAttributes={"message_type": {"DataType": "String", "StringValue": message_type}},
        )

    return send


class Relay:
    """Runs relay_once at once when nudged after a commit, otherwise every `interval` seconds."""

    def __init__(self, engine: Engine, send: Send, *, interval: float = 1.0) -> None:
        self.engine = engine
        self.send = send
        self.interval = interval
        self._wake = asyncio.Event()

    def nudge(self) -> None:
        self._wake.set()

    async def run(self, stopping: asyncio.Event) -> None:
        while not stopping.is_set():
            try:
                sent = await asyncio.to_thread(relay_once, self.engine, self.send)
            except Exception:
                log.exception("event relay failed")
                sent = 0
            if sent:
                continue
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.interval)
