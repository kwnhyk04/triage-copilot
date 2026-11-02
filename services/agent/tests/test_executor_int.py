"""End to end (P3.2): a restart plan approved as the web approves it, through the gateway process,
its outbox and relay, a scratch actions queue, the executor's consumer, and a fake docker on PATH.
Only an approved, catalog-listed restart reaches docker, once; a dry run records its command; the
actions switch off claims nothing; a code fix's approval queues nothing.

It sits with the agent's integration tests, which hold the harness both services need: scratch
databases and queues and the gateway process (test_parking_int.gateway). The agent's switch is a
loopback fake that records the token the gateway sends.
"""

import json
import os
import secrets
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx2
import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_parking_int import APPROVER_KEY, EXECUTOR_TOKEN, SERVICE_TOKEN, free_port, gateway

from executor.consumer import consume
from executor.restart import restart_effector
from executor.run import run_action
from py_clients.clients import ExecutionsClient

if TYPE_CHECKING:
    from conftest import ScratchDb
    from types_boto3_sqs import SQSClient

pytestmark = pytest.mark.integration

FAKE_DOCKER = r"""
import json, os, sys
from pathlib import Path
with (Path(__file__).parent / "calls.jsonl").open("a", encoding="utf-8") as calls:
    calls.write(json.dumps({"argv": sys.argv[1:], "env": sorted(os.environ)}) + "\n")
"""


class Agent:
    """The agent's GET /internal/switches/actions, on loopback, with the switch in `on`."""

    def __init__(self) -> None:
        self.on, self.tokens = True, list[str]()
        agent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                agent.tokens.append(self.headers.get("authorization", ""))
                body = json.dumps({"name": "actions", "on": agent.on}).encode()
                found = self.path == "/internal/switches/actions"
                self.send_response(200 if found else 404)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                """Quiet."""

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture
def docker(tmp_path: Path) -> Path:
    (tmp_path / "fake_docker.py").write_text(FAKE_DOCKER, encoding="utf-8")
    python = f'"{sys.executable}" -I -S "{tmp_path / "fake_docker.py"}"'
    if os.name == "nt":
        (tmp_path / "docker.cmd").write_text(f"@{python} %*\r\n", encoding="utf-8")
    else:
        (tmp_path / "docker").write_text(f'#!/bin/sh\nexec {python} "$@"\n', encoding="utf-8")
        (tmp_path / "docker").chmod(0o755)
    return tmp_path


def live_tenant() -> UUID:
    with psycopg.connect(os.environ["GATEWAY_DATABASE_URL"]) as conn:
        row = conn.execute(
            "INSERT INTO gateway.tenants (name) VALUES (%s) RETURNING id",
            (f"filing-lens-{secrets.token_hex(4)}",),
        ).fetchone()
    assert row is not None
    return row[0]


def plan(tenant: UUID, kind: str, target: str) -> UUID:
    """A live triage's plan as the gateway stores it from PlanRecorded, still pending."""
    plan_id, investigation = uuid.uuid4(), uuid.uuid4()
    files = ["services/api/app/main.py"] if kind == "code_fix" else []
    with psycopg.connect(os.environ["GATEWAY_DATABASE_URL"]) as conn:
        conn.execute(
            "INSERT INTO gateway.alert_groups (tenant_id, group_key, window_start, severity,"
            " status, investigation_id) VALUES (%s, %s, now(), 'critical', 'resolved', %s)",
            (tenant, f"group-{investigation}", investigation),
        )
        conn.execute(
            "INSERT INTO gateway.plans (id, tenant_id, investigation_id, kind, target, files,"
            " rationale, models, recorded_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())",
            (
                plan_id,
                tenant,
                investigation,
                kind,
                target,
                Jsonb(files),
                "Restart it, then run `docker compose down -v` to be sure [tc_01J9ZK3Q7M].",
                Jsonb([]),
            ),
        )
    return plan_id


def executions() -> dict[UUID, tuple[str, bool, str | None]]:
    with psycopg.connect(os.environ["GATEWAY_DATABASE_URL"]) as conn:
        query = "SELECT plan_id, state::text, dry_run, detail FROM gateway.plan_executions"
        rows = conn.execute(query)
        return {plan_id: (state, dry, detail) for plan_id, state, dry, detail in rows}


def calls(folder: Path) -> list[dict[str, Any]]:
    path = folder / "calls.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return [json.loads(line) for line in lines]


@pytest.fixture
def agent() -> Iterator[Agent]:
    fake = Agent()
    yield fake
    fake.server.shutdown()


def test_only_approved_catalog_restarts_run_once_and_a_dry_run_only_records(
    scratch_db: "ScratchDb",
    sqs: "SQSClient",
    queues: tuple[str, str],
    agent: Agent,
    docker: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch_db.migrate_gateway()
    queue_url, _ = queues
    tenant = live_tenant()
    with monkeypatch.context() as patch:
        patch.setenv("PATH", str(docker))
        effectors = {"restart_service": restart_effector()}
    mode = {"dry_run": True}
    outcomes: list[str] = []
    internal = free_port()
    extra = {
        "ACTIONS_QUEUE": queue_url.rsplit("/", 1)[1],
        "LIVE_TENANTS": json.dumps({str(tenant): "filing-lens"}),
    }
    with gateway("http://127.0.0.1:1/unused.fifo", agent.url, internal, extra=extra) as url:
        client = ExecutionsClient(f"http://127.0.0.1:{internal}", EXECUTOR_TOKEN, timeout=5)

        def handle(body: str) -> None:
            options: dict[str, Any] = {"effectors": effectors, "stack": "dev"}
            outcomes.append(run_action(body, client, dry_run=mode["dry_run"], **options))

        stopping = threading.Event()
        args = (sqs, queue_url, handle, stopping)
        thread = threading.Thread(target=consume, args=args, kwargs={"wait_seconds": 1})
        thread.start()

        def approve(plan_id: UUID) -> Callable[[], bool]:
            response = httpx2.post(
                f"{url}/v1/plans/{plan_id}/approve",
                headers={"x-approver-key": APPROVER_KEY},
                json={"approver": "operator"},
                timeout=5,
            )
            assert response.status_code == 200, response.text
            seen = len(outcomes)
            return lambda: len(outcomes) > seen

        def settled(done: Callable[[], bool]) -> str:
            deadline = time.monotonic() + 30
            while not done():
                assert time.monotonic() < deadline, "the executor got no message"
                time.sleep(0.2)
            return outcomes[-1]

        try:
            dry = plan(tenant, "restart_service", "api")
            assert settled(approve(dry)) == "dry_run"
            mode["dry_run"] = False
            real = plan(tenant, "restart_service", "api")
            assert settled(approve(real)) == "executed"
            # A database is never restarted, whatever was approved.
            database = plan(tenant, "restart_service", "postgres")
            assert settled(approve(database)) == "refused"
            # The operator's switch off: nothing is claimed.
            agent.on = False
            held = plan(tenant, "restart_service", "api")
            assert settled(approve(held)) == "refused"
            # A code fix's approval queues nothing for the executor.
            fix = plan(tenant, "code_fix", "api")
            assert approve(fix)() is False
            time.sleep(2)
        finally:
            stopping.set()
            thread.join(10)
            client.close()

    assert outcomes == ["dry_run", "executed", "refused", "refused"]
    assert executions() == {
        dry: ("succeeded", True, "dry run: docker compose -p filinglens restart api"),
        real: ("succeeded", False, None),
    }
    [call] = calls(docker)
    assert call["argv"] == ["compose", "-p", "filinglens", "restart", "api"]
    assert "EXECUTOR_SERVICE_TOKEN" not in call["env"]
    # The gateway asked the switch before every claim, with the agent's service token.
    assert set(agent.tokens) == {f"Bearer {SERVICE_TOKEN}"}
    # Every message was handled and deleted: nothing waits in the queue.
    attributes = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["All"])["Attributes"]
    assert attributes["ApproximateNumberOfMessages"] == "0"
    assert attributes["ApproximateNumberOfMessagesNotVisible"] == "0"
