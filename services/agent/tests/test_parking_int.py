"""End to end: the gateway process (intake, outbox relay, recovery sweeper) and two agents.

Investigations over the tenant cap or the model budget park and give up their message, so they
hold no worker slot and use up no receives. The sweeper re-queues them once they can run.
"""

import asyncio
import contextlib
import hashlib
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Generator, Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx2
import psycopg
import pytest
import uvicorn
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, create_engine, delete, insert, select
from test_workers_int import REPORT, agent, depth, eventually, make_consumer, make_job
from types_boto3_sqs import SQSClient

from agent.api import add_internal_api
from agent.db import AiUsage, DeadLetter, Investigation
from agent.job import UTC_TODAY, Job
from agent.model import ModelTurn, ScriptedModel, TurnRequest
from agent.steps import TokenUsage
from py_clients.clients import AlertsClient
from py_clients.generated.gateway import AlertmanagerWebhook

if TYPE_CHECKING:
    from conftest import ScratchDb

pytestmark = pytest.mark.integration

GATEWAY_DIR = Path(__file__).resolve().parents[3] / "apps" / "gateway"
SERVICE_TOKEN = secrets.token_urlsafe(32)
EXECUTOR_TOKEN = secrets.token_urlsafe(32)
APPROVER_KEY = secrets.token_urlsafe(32)
CAP = 2


class Slow:
    """The report after a pause, so the first investigations hold their slots a while."""

    def __init__(self, seconds: float, tokens: TokenUsage | None = None) -> None:
        self.seconds = seconds
        self.model = ScriptedModel([REPORT], tokens)

    async def turn(self, request: TurnRequest) -> ModelTurn:
        await asyncio.sleep(self.seconds)
        return await self.model.turn(request)

    def available(self, conn: Connection) -> bool:
        return True


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@contextlib.contextmanager
def internal_api(job: Job) -> Generator[str]:
    """The agent's internal API on a free port, as any agent serves it."""
    app = FastAPI()
    add_internal_api(app, job, SecretStr(SERVICE_TOKEN))
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "the internal API did not start"
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(10)


def ready(url: str) -> bool:
    try:
        return httpx2.get(f"{url}/readyz", timeout=1).status_code == 200
    except httpx2.HTTPError:
        return False


@contextlib.contextmanager
def gateway(
    queue_url: str,
    agent_url: str,
    internal_port: int | None = None,
    *,
    extra: dict[str, str] | None = None,
    printed: list[str] | None = None,
) -> Generator[str]:
    """The gateway process on the scratch database, sweeping every second.

    Its internal listener, for the executor, takes internal_port, or a free port of its own.
    `extra` sets more of its environment, and `printed` gets its output once it stopped.
    """
    port = free_port()
    env = (
        os.environ
        | {
            "GATEWAY_PORT": str(port),
            "GATEWAY_INTERNAL_PORT": str(internal_port or free_port()),
            "INVESTIGATIONS_QUEUE": queue_url.rsplit("/", 1)[1],
            "ACTIONS_QUEUE": "actions-unused-by-these-tests.fifo",
            # Never the dev queue: these agents publish no events, and the dev gateway owns those.
            "AGENT_EVENTS_QUEUE": "agent-events-unused-by-these-tests.fifo",
            "AGENT_URL": agent_url,
            "AGENT_SERVICE_TOKEN": SERVICE_TOKEN,
            "EXECUTOR_SERVICE_TOKEN": EXECUTOR_TOKEN,
            "SWEEPER_INTERVAL_SECONDS": "1",
            "MAX_NEW_INVESTIGATIONS": "5",
            "APPROVER_API_KEY": APPROVER_KEY,
            "GATEWAY_API_KEY": secrets.token_urlsafe(32),
            "LOG_LEVEL": "warn",
        }
        | (extra or {})
    )
    command = [shutil.which("node") or "node", "--import", "./src/telemetry.ts", "src/main.ts"]
    url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryFile() as output:
        # A fixed command from this file, never outside input.
        process = subprocess.Popen(  # noqa: S603
            command, cwd=GATEWAY_DIR, env=env, stdout=output, stderr=subprocess.STDOUT
        )
        try:
            deadline = time.monotonic() + 30
            while not ready(url):
                if process.poll() is not None or time.monotonic() > deadline:
                    output.seek(0)
                    pytest.fail(f"gateway did not start:\n{output.read().decode(errors='replace')}")
                time.sleep(0.1)
            yield url
        finally:
            process.terminate()
            process.wait(10)
            if printed is not None:
                output.seek(0)
                printed.append(output.read().decode(errors="replace"))


def new_tenant() -> str:
    """A tenant's Alertmanager webhook token, stored as `pnpm tenant:token` stores it."""
    token = secrets.token_urlsafe(32)
    with psycopg.connect(os.environ["GATEWAY_DATABASE_URL"]) as conn:
        conn.execute(
            "INSERT INTO gateway.tenants (name, webhook_token_sha256) VALUES (%s, %s)",
            (f"tenant-{secrets.token_hex(4)}", hashlib.sha256(token.encode()).hexdigest()),
        )
    return token


def post_alerts(url: str, token: str, keys: Sequence[str], count: int) -> None:
    with AlertsClient(url, token) as client:
        for n in range(count):
            # Labels of their own: the gateway deduplicates on the fingerprint the labels give.
            alert = {
                "status": "firing",
                "labels": {
                    "alertname": "HighErrorRate",
                    "severity": "critical",
                    "instance": f"instance-{n}",
                },
                "annotations": {},
                "startsAt": "2026-10-15T11:54:12.481Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus.example:9090/graph",
                "fingerprint": f"alert-{n}",
            }
            payload: dict[str, Any] = {
                "version": "4",
                "groupKey": keys[n % len(keys)],
                "status": "firing",
                "receiver": "triage-copilot",
                "groupLabels": {},
                "commonLabels": {},
                "commonAnnotations": {},
                "externalURL": "http://alertmanager.example:9093",
                "alerts": [alert],
            }
            client.post(AlertmanagerWebhook.model_validate(payload))


def groups() -> list[tuple[str, int]]:
    """Status and re-queue count of each investigated alert group."""
    with psycopg.connect(os.environ["GATEWAY_DATABASE_URL"]) as conn:
        rows = conn.execute(
            "SELECT status::text, requeue_count FROM gateway.alert_groups"
            " WHERE investigation_id IS NOT NULL ORDER BY requeue_count"
        )
        return [(status, count) for status, count in rows]


def investigations(engine: Engine) -> list[tuple[str, int, str | None]]:
    query = select(Investigation.status, Investigation.step_count, Investigation.waiting_reason)
    with engine.connect() as conn:
        return [(status, steps, reason) for status, steps, reason in conn.execute(query)]


def dead_letters(engine: Engine) -> int:
    with engine.connect() as conn:
        return len(conn.execute(select(DeadLetter.id)).all())


@pytest.fixture
def engines(scratch_db: "ScratchDb") -> Iterator[list[Engine]]:
    """Both schemas migrated; one engine per agent, and one for the internal API and checks."""
    scratch_db.migrate_gateway()
    scratch_db.migrate_agent("head")
    engines = [create_engine(scratch_db.agent_url) for _ in range(3)]
    yield engines
    for engine in engines:
        engine.dispose()


def test_a_storm_over_the_tenant_cap_parks_and_every_parked_investigation_runs(
    engines: list[Engine], sqs: SQSClient, queues: tuple[str, str]
) -> None:
    queue_url, dlq_url = queues
    token = new_tenant()
    # Every agent holds a slot for 2 s per investigation; the API answers with the same cap.
    first, second, api = (make_job(engine, {}, Slow(2), tenant_cap=CAP) for engine in engines)
    services = ["checkout", "payments", "inventory", "cart", "search", "auth", "ads", "db"]
    keys = [f'{{}}:{{alertname="HighErrorRate", service="{s}"}}' for s in services]

    async def main(url: str) -> None:
        async with (
            agent(make_consumer(sqs, queue_url, first)),
            agent(make_consumer(sqs, queue_url, second)),
        ):
            # 40 alerts over 8 groups: the gateway starts 5 investigations, 3 over the cap.
            await asyncio.to_thread(post_alerts, url, token, keys, 40)
            resolved = [("resolved", 1, None)] * 5
            await eventually(lambda: investigations(api.engine) == resolved, within=90)
        # The sweeper records each finished investigation on its group.
        await eventually(lambda: [status for status, _ in groups()] == ["resolved"] * 5)

    with internal_api(api) as agent_url, gateway(queue_url, agent_url) as url:
        asyncio.run(main(url))

    # Two ran at once; the other three parked, gave up their messages, and each ran after one
    # re-queue. Every investigation took exactly one model turn.
    assert [count for _, count in groups()] == [0, 0, 1, 1, 1]
    assert investigations(api.engine) == [("resolved", 1, None)] * 5
    # No message used up its receives: nothing reached the DLQ, and the queue is empty.
    assert depth(sqs, dlq_url) == 0
    assert depth(sqs, queue_url) == 0
    assert dead_letters(api.engine) == 0


def test_a_budget_parked_investigation_resumes_after_the_budget_resets(
    engines: list[Engine], sqs: SQSClient, queues: tuple[str, str]
) -> None:
    queue_url, dlq_url = queues
    token = new_tenant()
    tokens = TokenUsage(input=10, output=0)
    first, second, api = (
        make_job(engine, {}, Slow(0, tokens), daily_token_budget=100) for engine in engines
    )
    with api.engine.begin() as conn:
        conn.execute(insert(AiUsage).values(day=UTC_TODAY, tokens=100))  # Today's is spent.

    def parked_for_budget() -> bool:
        return investigations(api.engine) == [("queued", 0, "budget")]

    async def main(url: str) -> None:
        async with (
            agent(make_consumer(sqs, queue_url, first)),
            agent(make_consumer(sqs, queue_url, second)),
        ):
            await asyncio.to_thread(post_alerts, url, token, ['{}:{alertname="Budget"}'], 1)
            await eventually(lambda: parked_for_budget() and depth(sqs, queue_url) == 0)
            await asyncio.sleep(3)  # Three sweeps while the budget stays spent.
            assert parked_for_budget()
            assert groups() == [("queued", 0)]
            with api.engine.begin() as conn:
                conn.execute(delete(AiUsage))  # A new UTC day.
            await eventually(lambda: investigations(api.engine) == [("resolved", 1, None)])

    with internal_api(api) as agent_url, gateway(queue_url, agent_url) as url:
        asyncio.run(main(url))

    assert [count for _, count in groups()] == [1]
    assert depth(sqs, dlq_url) == 0
