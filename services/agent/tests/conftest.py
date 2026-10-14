"""Scratch databases set up like infra/postgres-init.sql, and throwaway queues like ours."""

import json
import os
import shutil
import subprocess
import sys
import warnings
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import boto3
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url
from types_boto3_sqs import SQSClient

from agent.consumer import MAX_RECEIVES
from agent.settings import Settings
from py_config import load_settings

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def sqs() -> SQSClient:
    settings = load_settings(Settings)
    client: SQSClient = boto3.client(
        "sqs", endpoint_url=settings.sqs_endpoint_url, region_name=settings.aws_region
    )
    return client


@pytest.fixture
def queues(sqs: SQSClient) -> Iterator[tuple[str, str]]:
    """URLs of a FIFO queue and its DLQ, with the same redrive limit as investigations.fifo."""
    name = f"probe-{uuid4().hex[:8]}"
    dlq = sqs.create_queue(QueueName=f"{name}-dlq.fifo", Attributes={"FifoQueue": "true"})
    dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq["QueueUrl"], AttributeNames=["QueueArn"])
    redrive = {
        "deadLetterTargetArn": dlq_arn["Attributes"]["QueueArn"],
        "maxReceiveCount": MAX_RECEIVES,
    }
    queue = sqs.create_queue(
        QueueName=f"{name}.fifo",
        Attributes={"FifoQueue": "true", "RedrivePolicy": json.dumps(redrive)},
    )
    yield queue["QueueUrl"], dlq["QueueUrl"]
    sqs.delete_queue(QueueUrl=queue["QueueUrl"])
    sqs.delete_queue(QueueUrl=dlq["QueueUrl"])


def _run(args: list[str]) -> None:
    # Fixed commands from this file, never outside input.
    result = subprocess.run(args, cwd=REPO, capture_output=True, text=True, check=False)  # noqa: S603
    assert result.returncode == 0, result.stdout + result.stderr


@dataclass(frozen=True)
class ScratchDb:
    """Both chains and the seed, pointed at the scratch database through the environment."""

    agent_url: str
    alembic: Config

    def migrate_gateway(self) -> None:
        _run([shutil.which("pnpm") or "pnpm", "--filter", "@triage/gateway", "db:migrate"])

    def migrate_agent(self, revision: str) -> None:
        command.upgrade(self.alembic, revision)

    def downgrade_agent(self, revision: str) -> None:
        command.downgrade(self.alembic, revision)

    def check_agent_models(self) -> None:
        """Fails when the SQLAlchemy models and the migrated schema differ (`alembic check`)."""
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", "Did not recognize type 'vector'")
            command.check(self.alembic)

    def seed(self, *args: str) -> None:
        _run([sys.executable, str(REPO / "scripts" / "seed.py"), *args])

    def partitions(self, *args: str) -> None:
        _run([sys.executable, "-m", "agent.partitions", *args])


@pytest.fixture
def scratch_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[ScratchDb]:
    """An empty database with the gateway and agent schemas and pgvector, dropped afterwards."""
    password = os.environ.get("POSTGRES_PASSWORD") or pytest.fail(
        "POSTGRES_PASSWORD is not set; run through make test-int, which loads .env"
    )
    agent_url = make_url(os.environ["AGENT_DATABASE_URL"])
    admin = agent_url.set(drivername="postgresql", username="postgres", password=password)
    name = f"triage_test_{uuid4().hex[:12]}"
    db = sql.Identifier(name)

    def admin_dsn(database: str) -> str:
        return admin.set(database=database).render_as_string(hide_password=False)

    with psycopg.connect(admin_dsn("postgres"), autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(db))
    try:
        with psycopg.connect(admin_dsn(name), autocommit=True) as conn:
            conn.execute(
                sql.SQL("""
                    REVOKE ALL ON DATABASE {db} FROM PUBLIC;
                    GRANT CONNECT ON DATABASE {db} TO gateway, agent;
                    GRANT CREATE ON DATABASE {db} TO gateway;
                    CREATE SCHEMA gateway AUTHORIZATION gateway;
                    CREATE SCHEMA agent AUTHORIZATION agent;
                    CREATE EXTENSION vector;
                """).format(db=db)
            )
        url = agent_url.set(database=name).render_as_string(hide_password=False)
        gateway_url = make_url(os.environ["GATEWAY_DATABASE_URL"]).set(database=name)
        monkeypatch.setenv("AGENT_DATABASE_URL", url)
        monkeypatch.setenv(
            "GATEWAY_DATABASE_URL", gateway_url.render_as_string(hide_password=False)
        )
        yield ScratchDb(url, Config(str(REPO / "services" / "agent" / "alembic.ini")))
    finally:
        with psycopg.connect(admin_dsn("postgres"), autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(db))
