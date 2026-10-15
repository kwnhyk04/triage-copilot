"""Scratch databases set up like infra/postgres-init.sql, and throwaway queues like ours.

Tests never touch the dev database. Each scratch database is named SCRATCH_PREFIX, this run's id
and its own id, and is dropped when its test ends. One over an hour old was left by a crashed run
and the next run drops it; nothing without the prefix is ever dropped. Loading this file points the
database URL variables at a database that does not exist until a scratch_db fixture repoints them,
so no test reaches the dev database by accident.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
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
from psycopg.rows import TupleRow
from sqlalchemy.engine import make_url
from types_boto3_sqs import SQSClient

from agent.consumer import MAX_RECEIVES
from agent.settings import Settings
from py_config import load_settings

REPO = Path(__file__).resolve().parents[3]
SCRATCH_PREFIX = "triage_test_"
REFUSED_DATABASE = "tests_never_use_the_dev_database"
# Unix time the run started, so a later run can tell a stale database from one still in use.
RUN_ID = f"{int(time.time())}_{uuid4().hex[:6]}"
STALE_AFTER_SECONDS = 3600
# SCRATCH_PREFIX, start time, run id and database id, as scratch_db names them.
SCRATCH_NAME = re.compile(r"triage_test_(\d+)_[0-9a-f]{6}_[0-9a-f]{8}")
# The dev database URLs, kept only to reach their server; the variables name REFUSED_DATABASE.
DEV_URLS = {
    name: os.environ.get(name, "") for name in ("AGENT_DATABASE_URL", "GATEWAY_DATABASE_URL")
}
for _name, _url in DEV_URLS.items():
    if _url:
        _refused = make_url(_url).set(database=REFUSED_DATABASE)
        os.environ[_name] = _refused.render_as_string(hide_password=False)


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


def _drop_stale(admin: psycopg.Connection[TupleRow], dev_database: str) -> None:
    """Drops prefixed databases over STALE_AFTER_SECONDS old, which crashed runs left behind."""
    query = "SELECT datname FROM pg_database WHERE starts_with(datname, %s)"
    for (name,) in admin.execute(query, (SCRATCH_PREFIX,)).fetchall():
        # Only names scratch_db makes; a hand-made triage_test_ database is never touched.
        scratch = SCRATCH_NAME.fullmatch(name)
        if scratch is None or name == dev_database:
            continue
        if time.time() - int(scratch[1]) > STALE_AFTER_SECONDS:
            # IF EXISTS: another run may drop it first.
            drop = sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)")
            admin.execute(drop.format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def scratch_server() -> str:
    """The superuser DSN of the dev server, once stale scratch databases are gone."""
    password = os.environ.get("POSTGRES_PASSWORD") or pytest.fail(
        "POSTGRES_PASSWORD is not set; run through make test-int, which loads .env"
    )
    dev = make_url(DEV_URLS["AGENT_DATABASE_URL"] or pytest.fail("AGENT_DATABASE_URL is not set"))
    if dev.database is None or dev.database.startswith(SCRATCH_PREFIX):
        pytest.fail(f"refusing to run: the dev database {dev.database!r} has the scratch prefix")
    admin = dev.set(drivername="postgresql", username="postgres", password=password)
    dsn = admin.set(database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(dsn, autocommit=True) as conn:
        _drop_stale(conn, dev.database)
    return dsn


@pytest.fixture
def scratch_db(monkeypatch: pytest.MonkeyPatch, scratch_server: str) -> Iterator[ScratchDb]:
    """An empty database with the gateway and agent schemas and pgvector, dropped afterwards."""
    name = f"{SCRATCH_PREFIX}{RUN_ID}_{uuid4().hex[:8]}"
    db = sql.Identifier(name)

    def admin_dsn(database: str) -> str:
        return make_url(scratch_server).set(database=database).render_as_string(hide_password=False)

    with psycopg.connect(scratch_server, autocommit=True) as conn:
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
        url = make_url(DEV_URLS["AGENT_DATABASE_URL"]).set(database=name)
        url = url.render_as_string(hide_password=False)
        gateway_dev = DEV_URLS["GATEWAY_DATABASE_URL"] or pytest.fail(
            "GATEWAY_DATABASE_URL is not set"
        )
        gateway_url = make_url(gateway_dev).set(database=name)
        monkeypatch.setenv("AGENT_DATABASE_URL", url)
        monkeypatch.setenv(
            "GATEWAY_DATABASE_URL", gateway_url.render_as_string(hide_password=False)
        )
        yield ScratchDb(url, Config(str(REPO / "services" / "agent" / "alembic.ini")))
    finally:
        with psycopg.connect(scratch_server, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(db))
