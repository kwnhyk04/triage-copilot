"""Replays the agent's failure stores into investigations.fifo.

    uv run python -m agent.dlq replay

Moves every message in the queue's DLQ (investigations-dlq.fifo, read from its redrive policy)
back to the queue, then re-sends each agent.dead_letters row not replayed yet. A message that
fails again ends up where it came from. Fixed deduplication ids make a rerun within SQS's
5-minute deduplication window send nothing twice.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import TYPE_CHECKING

from sqlalchemy import Engine, create_engine, func, select, update

from agent.consumer import sqs_client
from agent.db import DeadLetter
from agent.settings import Settings
from py_config import ConfigError, load_settings
from py_telemetry import configure_logging

if TYPE_CHECKING:
    # Stub-only package from the dev group; never imported at runtime.
    from types_boto3_sqs import SQSClient

log = logging.getLogger(__name__)


def dlq_url(sqs: SQSClient, queue_url: str) -> str:
    """The URL of the queue's dead-letter queue, from its RedrivePolicy."""
    attributes = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["RedrivePolicy"])
    arn = json.loads(attributes["Attributes"]["RedrivePolicy"])["deadLetterTargetArn"]
    return sqs.get_queue_url(QueueName=arn.rsplit(":", 1)[1])["QueueUrl"]


def replay_dlq(sqs: SQSClient, queue_url: str) -> int:
    """Moves every DLQ message back to the queue, in its original message group; returns them."""
    source = dlq_url(sqs, queue_url)
    moved = 0
    while messages := sqs.receive_message(
        QueueUrl=source,
        MaxNumberOfMessages=10,
        WaitTimeSeconds=1,
        MessageSystemAttributeNames=["MessageGroupId"],
    ).get("Messages", []):
        for message in messages:
            sqs.send_message(
                QueueUrl=queue_url,
                MessageBody=message.get("Body", ""),
                MessageGroupId=message.get("Attributes", {}).get("MessageGroupId", "replayed"),
                MessageDeduplicationId=f"replay-{message.get('MessageId', '')}",
            )
            sqs.delete_message(QueueUrl=source, ReceiptHandle=message.get("ReceiptHandle", ""))
            moved += 1
    return moved


def replay_dead_letters(engine: Engine, sqs: SQSClient, queue_url: str) -> int:
    """Re-sends the dead letters not replayed yet and marks them replayed; returns them."""
    with engine.begin() as conn:
        rows = conn.execute(
            select(DeadLetter.id, DeadLetter.group_id, DeadLetter.body)
            .where(DeadLetter.replayed_at.is_(None))
            .order_by(DeadLetter.id)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            sqs.send_message(
                QueueUrl=queue_url,
                MessageBody=row.body,
                MessageGroupId=row.group_id or "dead-letters",
                MessageDeduplicationId=f"dead-letter-{row.id}",
            )
        ids = [row.id for row in rows]
        conn.execute(
            update(DeadLetter).where(DeadLetter.id.in_(ids)).values(replayed_at=func.now())
        )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="replays the agent's failure stores")
    parser.add_argument("command", choices=["replay"])
    parser.parse_args()
    try:
        settings = load_settings(Settings)
    except ConfigError as error:
        sys.exit(f"agent: {error}")
    configure_logging("agent", settings.log_level)
    engine = create_engine(settings.agent_database_url, connect_args={"connect_timeout": 5})
    sqs = sqs_client(settings)
    queue_url = sqs.get_queue_url(QueueName=settings.investigations_queue)["QueueUrl"]
    moved = replay_dlq(sqs, queue_url)
    resent = replay_dead_letters(engine, sqs, queue_url)
    log.info("replayed", extra={"from_dlq": moved, "from_dead_letters": resent})
    engine.dispose()


if __name__ == "__main__":
    main()
