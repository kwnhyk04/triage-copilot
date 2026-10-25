"""End to end on fixtures, with no model call and no real app (P1.8, P1.9).

watch reads FreightAudit through in-process fakes of its docker, git and HTTP readers, fed by the
synthetic fixture logs, and posts to the gateway process. The agent triages on a scripted model
with the live tools, and the gateway's mirror ends with ReportRecorded v2, its verdict and its
models. No canary planted in the fixtures reaches any stored row or log line. A storm through
watch starts at most 30 investigations a day and records every alert.
"""

import asyncio
import hashlib
import importlib.util
import logging
import os
import re
from collections.abc import Collection, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, LiteralString, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from test_parking_int import SERVICE_TOKEN, free_port, gateway, internal_api, new_tenant
from test_workers_int import agent, eventually, make_consumer, make_job
from types_boto3_sqs import SQSClient

from agent.live import LiveReportDraft, live_tools
from agent.model import HypothesisDraft, ScriptedModel, ToolRequest
from agent.outbox import Relay, sqs_sender
from agent.switches import set_switch
from py_clients.clients import AlertContextClient
from watch.catalog import CATALOGS
from watch.poll import Target, Watcher
from watch.readers import Answer, Commit, Container

if TYPE_CHECKING:
    from conftest import ScratchDb

pytestmark = pytest.mark.integration

FA = CATALOGS["freight-audit"]
WATCH_TESTS = Path(__file__).resolve().parents[2] / "watch" / "tests"
FIXTURES = WATCH_TESTS / "fixtures"
LOGS = {"api": "freight-audit-api.log", "worker": "freight-audit-worker.log"}
NOW = datetime(2026, 10, 25, 9, 55, tzinfo=UTC)


def planted() -> list[str]:
    """The canaries planted in watch's fixtures, from services/watch/tests/watch_fixtures.py."""
    spec = importlib.util.spec_from_file_location(
        "watch_fixtures", WATCH_TESTS / "watch_fixtures.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(cast("dict[str, str]", module.CANARIES).values())


CANARIES = planted()
CANARY = re.compile("|".join(map(re.escape, CANARIES)))
HEALTH = "http://freight-audit.test/readyz"
REPORT = LiveReportDraft(
    summary="freight-audit's worker paused extraction, and the api fails its readiness check.",
    hypotheses=[
        HypothesisDraft(
            root_cause_class="database",
            service="postgres",
            confidence=0.6,
            summary="The api's readiness check fails on postgres while the worker pauses.",
            evidence_ids=[],
        )
    ],
    verdict="real",
)


def fixture_lines(name: str) -> list[tuple[datetime, str]]:
    """A fixture log as `docker logs --timestamps` prints it, as (time, line) pairs."""
    pairs: list[tuple[datetime, str]] = []
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        stamp, _, text = line.partition(" ")
        at = datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
        pairs.append((at, text))
    return pairs


class FakeDocker:
    """docker in process: every container of FreightAudit's prod stack up, and each service's
    fixture log the first time its container is read."""

    def __init__(self, logs: dict[str, str]) -> None:
        self.unread = {service: fixture_lines(name) for service, name in logs.items()}

    def ps(self, project: str, names: Collection[str]) -> list[Container]:
        created = datetime(2026, 10, 20, 8, tzinfo=UTC)
        return [
            Container(name, f"ghcr.io/example/{name}:1.4.0", "running", "Up 5 days", created)
            for name in names
        ]

    def logs(
        self, container: Container, since: datetime, until: datetime, tail: int
    ) -> list[tuple[datetime, str]]:
        service = container.name.removeprefix(f"{FA.projects['prod']}-").removesuffix("-1")
        return self.unread.pop(service, [])


class FakeHttp:
    """The health URLs in process: 200 unless `status` says otherwise."""

    def __init__(self) -> None:
        self.status: dict[str, int] = {}

    def get(self, url: str) -> Answer:
        status = self.status.get(url, 200)
        body = '{"status":"ok"}' if status == 200 else '{"status":"unavailable"}'
        return Answer(status, body, 5, None)

    def close(self) -> None:
        pass


class FakeGit:
    def commits(self, since: datetime, limit: int) -> list[Commit]:
        subject = "Worker: drop ghp_canarycanarycanary05 from the config"
        return [Commit("abc1234", NOW - timedelta(hours=1), subject, ("worker/src/extract.ts",))]


def watcher(
    monkeypatch: pytest.MonkeyPatch,
    target: Target,
    url: str,
    docker: FakeDocker,
    http: FakeHttp,
) -> Watcher:
    """watch on the fakes: its real readers are never built, so nothing real is read."""

    def make_http(urls: Collection[str]) -> FakeHttp:
        return http

    def make_git(repo: Path) -> FakeGit:
        return FakeGit()

    monkeypatch.setattr("watch.poll.Docker", lambda: docker)
    monkeypatch.setattr("watch.poll.Http", make_http)
    monkeypatch.setattr("watch.poll.Git", make_git)
    return Watcher(target, url)


@pytest.fixture
def engine(scratch_db: "ScratchDb") -> Iterator[Engine]:
    scratch_db.migrate_gateway()
    scratch_db.migrate_agent("head")
    engine = create_engine(scratch_db.agent_url)
    yield engine
    engine.dispose()


@pytest.fixture
def events(sqs: SQSClient) -> Iterator[tuple[str, str]]:
    """The names of a throwaway agent-events FIFO queue and its DLQ."""
    name = f"events-{uuid4().hex[:8]}"
    urls = [
        sqs.create_queue(QueueName=queue, Attributes={"FifoQueue": "true"})["QueueUrl"]
        for queue in (f"{name}.fifo", f"{name}-dlq.fifo")
    ]
    yield f"{name}.fifo", f"{name}-dlq.fifo"
    for url in urls:
        sqs.delete_queue(QueueUrl=url)


def gateway_rows(query: LiteralString, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(os.environ["GATEWAY_DATABASE_URL"]) as conn:
        return conn.execute(query, params).fetchall()


def tenant_of(token: str) -> str:
    [(tenant,)] = gateway_rows(
        "SELECT id::text FROM gateway.tenants WHERE webhook_token_sha256 = %s",
        hashlib.sha256(token.encode()).hexdigest(),
    )
    return str(tenant)


def canaries(url: str, schema: str) -> list[str]:
    """Every row of every table in the schema that holds a canary, as its table and its start."""
    found: list[str] = []
    with psycopg.connect(url) as conn:
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema = %s AND table_type = 'BASE TABLE'",
            (schema,),
        ).fetchall()
        for (table,) in tables:
            rows = conn.execute(
                sql.SQL("SELECT t::text FROM {}.{} t").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            )
            for (text,) in rows:
                if match := CANARY.search(text):
                    found.append(f"{table}: {text[max(0, match.start() - 80) : match.end() + 40]}")
    return found


def test_a_live_alert_reaches_a_report_v2_through_watch_and_the_gateway_with_no_canary(
    scratch_db: "ScratchDb",
    engine: Engine,
    sqs: SQSClient,
    queues: tuple[str, str],
    events: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    queue_url, _ = queues
    token = new_tenant()
    tenant = tenant_of(token)
    with engine.begin() as conn:
        set_switch(conn, "live", True)
    internal_port = free_port()
    alerts = AlertContextClient(f"http://127.0.0.1:{internal_port}", SERVICE_TOKEN)
    script = [
        ToolRequest(tool="get_alert_context", args={}, reason="See the alert group."),
        ToolRequest(tool="read_snapshot", args={"alert": 1}, reason="Read what watch saw."),
        ToolRequest(tool="get_dependencies", args={"service": "worker"}, reason="Its needs."),
        REPORT,
    ]
    relay = Relay(engine, sqs_sender(sqs, events[0]), interval=0.2)
    job = make_job(
        engine,
        {},
        ScriptedModel([]),
        live_tenants={UUID(tenant): "freight-audit"},
        live_tools=live_tools(alerts, None),
        live_model=ScriptedModel(script),
        on_commit=relay.nudge,
    )
    docker, http = FakeDocker(LOGS), FakeHttp()
    http.status[HEALTH] = 503
    target = Target(FA, "prod", token, {"api": HEALTH}, repo=Path("repo"))
    posted: list[str] = []

    def reported() -> bool:
        return bool(
            gateway_rows(
                "SELECT 1 FROM gateway.investigation_reports r JOIN gateway.alert_groups g"
                " ON g.investigation_id = r.investigation_id WHERE g.tenant_id = %s",
                tenant,
            )
        )

    async def main(url: str) -> None:
        stopping = asyncio.Event()
        relaying = asyncio.create_task(relay.run(stopping))
        try:
            async with agent(make_consumer(sqs, queue_url, job)):
                watch = watcher(monkeypatch, target, url, docker, http)
                for n in range(3):
                    sent = await asyncio.to_thread(watch.poll, NOW + timedelta(seconds=30 * n))
                    posted.extend(f"{state} {key.alertname}" for state, key in sent)
                watch.close()
                await eventually(reported, within=60)
        finally:
            stopping.set()
            await relaying

    printed: list[str] = []
    events_env = {"AGENT_EVENTS_QUEUE": events[0], "AGENT_EVENTS_DLQ": events[1]}
    with (
        internal_api(job) as agent_url,
        gateway(queue_url, agent_url, internal_port, extra=events_env, printed=printed) as url,
    ):
        asyncio.run(main(url))
    alerts.close()

    # Three log signals on the first poll, the failed health check on the third: one group.
    assert sorted(posted) == [
        "firing ClaudeSwitchedOff",
        "firing ExtractPaused",
        "firing HealthCheckFailed",
        "firing ReadinessCheckFailed",
    ]
    [(investigation, stored)] = gateway_rows(
        "SELECT g.investigation_id::text,"
        " (SELECT count(*) FROM gateway.alerts a WHERE a.group_id = g.id)"
        " FROM gateway.alert_groups g WHERE g.tenant_id = %s",
        tenant,
    )
    assert stored == 4
    [(verdict, models, summary)] = gateway_rows(
        "SELECT verdict::text, models, report->>'summary' FROM gateway.investigation_reports"
        " WHERE investigation_id = %s",
        investigation,
    )
    assert (verdict, summary) == ("real", REPORT.summary)
    assert models == [
        {
            "part": "triage",
            "provider": "scripted",
            "model": "scripted",
            "calls": 4,
            "failed": 0,
            "reason": None,
        }
    ]
    steps = gateway_rows(
        "SELECT tool_name, tool_status::text, result FROM gateway.investigation_steps"
        " WHERE investigation_id = %s AND tokens_input IS NULL AND tool_name IS NOT NULL"
        " ORDER BY seq",
        investigation,
    )
    assert [(name, status) for name, status, _ in steps] == [
        ("get_alert_context", "ok"),
        ("read_snapshot", "ok"),
        ("get_dependencies", "ok"),
    ]
    snapshot = steps[1][2]
    assert snapshot.startswith("Snapshot of freight-audit on the prod stack")
    # Alert 1, the api's failed readiness check, with the api's own scrubbed lines.
    assert '"msg":"readiness check failed"' in snapshot
    assert "worker (worker) depends on: postgres (database)" in steps[2][2]

    # No canary reached the gateway's tables, the agent's, the gateway's output or TC's logs.
    agent_url = make_url(scratch_db.agent_url).set(drivername="postgresql")
    stored_canaries = [
        *canaries(os.environ["GATEWAY_DATABASE_URL"], "gateway"),
        *canaries(agent_url.render_as_string(hide_password=False), "agent"),
    ]
    assert stored_canaries == []
    assert [line for line in "".join(printed).splitlines() if CANARY.search(line)] == []
    logged = [str(vars(record)) for record in caplog.records]
    assert [line for line in logged if CANARY.search(line)] == []
    # The logs watch read do hold canaries, so the scans above could have found them.
    raw = "".join((FIXTURES / name).read_text(encoding="utf-8") for name in LOGS.values())
    assert CANARY.search(raw)


def test_a_storm_through_watch_starts_at_most_30_triages_a_day_and_records_every_alert(
    engine: Engine,
    sqs: SQSClient,
    queues: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue_url, _ = queues
    token = new_tenant()
    tenant = tenant_of(token)
    services = [service.name for service in FA.services]
    health = {name: f"http://freight-audit.test/{name}/health" for name in services}
    http = FakeHttp()
    target = Target(FA, "prod", token, health)
    fired = 0
    # The five-minute cap far off, so only the daily one binds; no sweep during the storm.
    extra = {"MAX_NEW_INVESTIGATIONS": "100", "SWEEPER_INTERVAL_SECONDS": "3600"}
    with gateway(queue_url, "http://127.0.0.1:9", extra=extra) as url:
        watch = watcher(monkeypatch, target, url, FakeDocker({}), http)
        when = NOW
        # 40 failures, each service in turn: three failed checks fire one, two passed resolve it.
        for n in range(40):
            down = health[services[n % len(services)]]
            for status, polls in ((503, 3), (200, 2)):
                http.status[down] = status
                for _ in range(polls):
                    fired += sum(state == "firing" for state, _ in watch.poll(when))
                    when += timedelta(seconds=30)
            # Five minutes pass, so the next failure opens the next group.
            with psycopg.connect(os.environ["GATEWAY_DATABASE_URL"]) as conn:
                conn.execute(
                    "UPDATE gateway.alert_groups SET window_start = window_start"
                    " - interval '5 minutes' WHERE tenant_id = %s",
                    (tenant,),
                )
            when += timedelta(minutes=5)
        watch.close()

    assert fired == 40
    groups = gateway_rows(
        "SELECT investigation_id IS NOT NULL, not_investigated::text, count(*)"
        " FROM gateway.alert_groups WHERE tenant_id = %s GROUP BY 1, 2 ORDER BY 1",
        tenant,
    )
    assert groups == [(False, "daily_cap", 10), (True, None, 30)]
    # Every alert is recorded, triaged or not: each firing once, resolved, with its snapshot.
    stored = gateway_rows(
        "SELECT status::text, annotations ? 'snapshot' FROM gateway.alerts WHERE tenant_id = %s",
        tenant,
    )
    assert stored == [("resolved", True)] * 40
