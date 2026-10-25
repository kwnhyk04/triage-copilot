import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from watch_fakes import FakeServer, FakeTools, Route, ps_row
from watch_fixtures import CANARIES, LOGS, log_text

from watch.catalog import CATALOGS, Stack
from watch.poll import SNAPSHOT_BYTES, Target, Watcher, fingerprint

FA = CATALOGS["freight-audit"]
BEARER = "fa-webhook-credential-for-tests-only-0123456789"
NOW = datetime(2026, 10, 25, 9, 55, tzinfo=UTC)
LABELS = ["alertname", "app", "service", "severity", "source"]
ACCEPTED = json.dumps({"group_id": "6f1c2a1e-0000-4000-8000-000000000001", "deduped": False})


@pytest.fixture
def gateway() -> Iterator[FakeServer]:
    """A stub of the gateway's webhook that accepts every alert and records it."""
    stub = FakeServer()
    _accept(stub)
    yield stub
    stub.close()


def _accept(stub: FakeServer) -> None:
    accepted = Route(202, ACCEPTED.encode(), {"content-type": "application/json"})
    stub.route("/v1/alerts", accepted, method="POST")


def _posts(gateway: FakeServer) -> list[dict[str, Any]]:
    return [json.loads(request.body) for request in gateway.requests]


def _alerts(gateway: FakeServer) -> list[dict[str, Any]]:
    return [alert for post in _posts(gateway) for alert in post["alerts"]]


def _named(gateway: FakeServer, alertname: str) -> dict[str, Any]:
    [alert] = [a for a in _alerts(gateway) if a["labels"]["alertname"] == alertname]
    return alert


def _snapshot(alert: dict[str, Any]) -> dict[str, Any]:
    text = alert["annotations"]["snapshot"]
    assert len(text.encode()) <= SNAPSHOT_BYTES
    return json.loads(text)


def _stack(
    tools: FakeTools,
    *,
    stack: Stack = "prod",
    logs: dict[str, str] | None = None,
    states: dict[str, tuple[str, str]] | None = None,
    created: dict[str, str] | None = None,
    git: str = "",
) -> None:
    """FreightAudit's containers on `stack`, up and healthy unless `states` says otherwise."""
    rows: list[str] = []
    answers: dict[str, dict[str, str]] = {}
    for service in FA.services:
        if name := FA.container(service, stack):
            state, status = (states or {}).get(service.name, ("running", "Up 2 days (healthy)"))
            when = (created or {}).get(service.name, "2026-10-20 08:00:00 +0000 UTC")
            rows.append(ps_row(name, FA.projects[stack], state=state, status=status, created=when))
            answers[name] = {"stdout": (logs or {}).get(service.name, "")}
    tools.scenario(
        {
            "docker": {"ps": {"stdout": "\n".join(rows) + "\n"}, "logs": answers},
            "git": {"log": {"stdout": git}},
        }
    )


def _watch(
    server: FakeServer,
    gateway: FakeServer,
    *,
    stack: Stack = "prod",
    repo: Path | None = None,
    prometheus: str | None = None,
) -> Watcher:
    health = {"api": server.url + "/readyz"}
    target = Target(FA, stack, BEARER, health, repo=repo, prometheus=prometheus)
    return Watcher(target, gateway.url)


def test_the_fingerprint_is_prometheus_s_as_the_gateway_computes_it() -> None:
    labels = {
        "alertname": "CheckoutHighErrorRate",
        "pod": "checkout-7d9f8b6c5-q8m2z",
        "service": "checkout",
        "severity": "critical",
    }
    assert fingerprint(labels) == "2f09d8d93d1d4d16"
    other = {**labels, "Zone": "eu-west-1", "pod": "checkout-7d9f8b6c5-x2k4p", "team": "über-ops"}
    assert fingerprint(other) == "70fb7a16eb9a8e33"


def test_a_failing_health_check_fires_once_and_resolves_once(
    tools: FakeTools, server: FakeServer, gateway: FakeServer, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, "watch")
    _stack(tools)
    readyz = server.route("/readyz", Route(503, b'{"status":"unavailable"}'))
    watcher = _watch(server, gateway)
    polls = [NOW + timedelta(seconds=30 * i) for i in range(8)]
    for when in polls[:5]:
        watcher.poll(when)
    server.route("/readyz", Route(200, b'{"status":"ok"}'))
    for when in polls[5:]:
        watcher.poll(when)
    # Each transition is logged; no extra field may take the JSON log's own `service` (watch).
    logged = [record for record in caplog.records if record.getMessage().startswith("alert ")]
    assert [record.getMessage() for record in logged] == ["alert firing", "alert resolved"]
    assert not [record for record in logged if "service" in vars(record)]
    fired, resolved = _alerts(gateway)
    labels = {
        "alertname": "HealthCheckFailed",
        "app": "freight-audit",
        "service": "api",
        "severity": "critical",
        "source": "health",
    }
    assert fired["labels"] == resolved["labels"] == labels
    assert fired["fingerprint"] == resolved["fingerprint"] == fingerprint(labels)
    # Three failed checks in a row fire it, two passed ones resolve it.
    assert (fired["status"], fired["startsAt"], fired["endsAt"]) == (
        "firing",
        "2026-10-25T09:56:00Z",
        "0001-01-01T00:00:00Z",
    )
    assert (resolved["status"], resolved["startsAt"], resolved["endsAt"]) == (
        "resolved",
        "2026-10-25T09:56:00Z",
        "2026-10-25T09:58:00Z",
    )
    assert fired["annotations"]["summary"] == f"freight-audit api: GET {readyz} answered 503"
    snapshot = _snapshot(fired)
    assert (snapshot["app"], snapshot["stack"], snapshot["alert"]["alertname"]) == (
        "freight-audit",
        "prod",
        "HealthCheckFailed",
    )
    assert [(h["service"], h["status"]) for h in snapshot["health"]] == [("api", 503)]
    assert {c["service"] for c in snapshot["containers"]} == {
        s.name for s in FA.services if FA.container(s, "prod")
    }
    assert "snapshot" not in resolved["annotations"]
    first = _posts(gateway)[0]
    assert (first["groupKey"], first["groupLabels"]) == (
        '{}:{app="freight-audit"}',
        {"app": "freight-audit"},
    )
    assert gateway.requests[0].headers["authorization"] == f"Bearer {BEARER}"


def test_log_signals_and_container_states_fire_with_stable_labels(
    tools: FakeTools, server: FakeServer, gateway: FakeServer
) -> None:
    restarting = {"redis": ("restarting", "Restarting (1) 4 seconds ago")}
    worker, api = (log_text(LOGS["freight-audit"][name]) for name in ("worker", "api"))
    _stack(tools, logs={"worker": worker, "api": api}, states=restarting)
    server.route("/readyz", Route(200))
    watcher = _watch(server, gateway)
    watcher.poll(NOW)
    fired = {(a["labels"]["alertname"], a["labels"]["service"]) for a in _alerts(gateway)}
    assert fired == {
        ("ExtractPaused", "worker"),
        ("ClaudeSwitchedOff", "worker"),
        ("ReadinessCheckFailed", "api"),
    }
    assert _named(gateway, "ExtractPaused")["labels"]["severity"] == "critical"
    # No new lines: the signals hold, and the restarting redis fires on its second poll.
    _stack(tools, states=restarting)
    watcher.poll(NOW + timedelta(seconds=30))
    assert [(a["labels"]["alertname"], a["status"]) for a in _alerts(gateway)[3:]] == [
        ("ContainerDown", "firing")
    ]
    assert _named(gateway, "ContainerDown")["annotations"]["summary"] == (
        "freight-audit redis: container restarting (Restarting (1) 4 seconds ago)"
    )
    # Ten minutes after its last line a signal is quiet, and two quiet polls resolve it.
    watcher.poll(NOW + timedelta(minutes=9))
    watcher.poll(NOW + timedelta(minutes=9, seconds=30))
    assert sorted((a["labels"]["alertname"], a["status"]) for a in _alerts(gateway)[4:]) == [
        ("ClaudeSwitchedOff", "resolved"),
        ("ExtractPaused", "resolved"),
        ("ReadinessCheckFailed", "resolved"),
    ]
    assert all(sorted(alert["labels"]) == LABELS for alert in _alerts(gateway))
    sent = "\n".join(request.body.decode() for request in gateway.requests)
    assert [name for name, canary in CANARIES.items() if canary in sent] == []


def test_the_snapshot_stays_within_48_kb_and_keeps_the_newest_lines(
    tools: FakeTools, server: FakeServer, gateway: FakeServer
) -> None:
    base = datetime(2026, 10, 25, 9, 45, tzinfo=UTC)
    noise = "".join(
        f"{base + timedelta(seconds=i):%Y-%m-%dT%H:%M:%S}.000000000Z "
        + json.dumps({"level": 30, "time": i, "msg": f"line {i:03d} " + "x" * 900})
        + "\n"
        for i in range(300)
    )
    paused = log_text(LOGS["freight-audit"]["worker"]).splitlines()[1]
    stores = {"postgres": noise, "redis": noise, "minio": noise}
    _stack(tools, logs={"worker": noise + paused + "\n", **stores})
    server.route("/readyz", Route(200))
    _watch(server, gateway).poll(NOW)
    logs = _snapshot(_named(gateway, "ExtractPaused"))["logs"]
    assert '"alert":"extract_paused"' in logs["worker"][-1]
    assert not [line for lines in logs.values() for line in lines if "line 000 " in line]


def test_an_alert_the_gateway_refused_is_posted_again_next_poll(
    tools: FakeTools, server: FakeServer, gateway: FakeServer
) -> None:
    _stack(tools, logs={"worker": log_text(LOGS["freight-audit"]["worker"])})
    server.route("/readyz", Route(200))
    gateway.route("/v1/alerts", Route(503, headers={"retry-after": "1"}), method="POST")
    watcher = _watch(server, gateway)
    watcher.poll(NOW)
    assert not watcher.ok
    refused = len(gateway.requests)
    assert refused == 2
    _accept(gateway)
    _stack(tools)
    watcher.poll(NOW + timedelta(seconds=30))
    retried = _alerts(gateway)[refused:]
    assert sorted(a["labels"]["alertname"] for a in retried) == [
        "ClaudeSwitchedOff",
        "ExtractPaused",
    ]
    assert {a["startsAt"] for a in retried} == {"2026-10-25T09:55:30Z"}
    assert watcher.ok
    watcher.poll(NOW + timedelta(seconds=60))
    assert len(gateway.requests) == refused + 2


def test_the_snapshot_lists_recent_commits_and_recreated_containers(
    tools: FakeTools, server: FakeServer, gateway: FakeServer, tmp_path: Path
) -> None:
    commit = (
        "\x1eabc1234\x1f2026-10-25T09:00:00+00:00\x1f"
        "Worker: drop ghp_canarycanarycanary05 from the config\n\nworker/src/extract.ts\n"
    )
    _stack(
        tools,
        logs={"worker": log_text(LOGS["freight-audit"]["worker"])},
        created={"worker": "2026-10-25 09:30:00 +0000 UTC"},
        git=commit,
    )
    server.route("/readyz", Route(200))
    _watch(server, gateway, repo=tmp_path).poll(NOW)
    changes = _snapshot(_named(gateway, "ExtractPaused"))["changes"]
    assert changes["commits"] == [
        {
            "sha": "abc1234",
            "at": "2026-10-25T09:00:00Z",
            "subject": "Worker: drop [key] from the config",
            "paths": ["worker/src/extract.ts"],
        }
    ]
    assert [(c["service"], c["created"]) for c in changes["recreated"]] == [
        ("worker", "2026-10-25T09:30:00Z")
    ]
    [git] = [call for call in tools.calls() if call["tool"] == "git"]
    assert "--since=2026-10-24T09:55:00Z" in git["argv"]


def test_docker_out_of_reach_is_a_problem_not_an_alert(
    tools: FakeTools, server: FakeServer, gateway: FakeServer
) -> None:
    stderr = "Cannot connect to the Docker daemon. Is the docker daemon running?\n"
    tools.scenario({"docker": {"ps": {"exit": 1, "stderr": stderr}}})
    server.route("/readyz", Route(200))
    watcher = _watch(server, gateway)
    for i in range(3):
        watcher.poll(NOW + timedelta(seconds=30 * i))
    assert gateway.requests == []
    assert not watcher.ok
    assert watcher.problems == [
        "docker ps: docker exit 1: Cannot connect to the Docker daemon."
        " Is the docker daemon running?"
    ]


def test_a_dev_stack_s_prometheus_alerts_post_with_the_catalog_s_labels(
    tools: FakeTools, server: FakeServer, gateway: FakeServer
) -> None:
    _stack(tools, stack="dev")
    server.route("/readyz", Route(200))
    alerts = [
        {
            "labels": {"alertname": "ExtractCircuitOpen", "severity": "page", "instance": "x"},
            "annotations": {"summary": "Extraction paused; ask canary.person@example.com"},
            "state": "firing",
            "activeAt": "2026-10-25T09:40:00Z",
            "value": "1e+00",
        },
        {"labels": {"alertname": "DeadLettersWaiting"}, "state": "pending"},
        {"labels": {"alertname": "MonitoringTargetDown"}, "state": "firing"},
    ]
    body = json.dumps({"status": "success", "data": {"alerts": alerts}}).encode()
    server.route("/api/v1/alerts", Route(200, body))
    result = [{"metric": {"queue": "extract"}, "value": [1, "1"]}]
    vector = json.dumps({"status": "success", "data": {"resultType": "vector", "result": result}})
    server.route("/api/v1/query", Route(200, vector.encode()))
    _watch(server, gateway, stack="dev", prometheus=server.url).poll(NOW)
    [alert] = _alerts(gateway)
    assert alert["labels"] == {
        "alertname": "ExtractCircuitOpen",
        "app": "freight-audit",
        "service": "worker",
        "severity": "critical",
        "source": "prometheus",
    }
    prometheus = _snapshot(alert)["prometheus"]
    assert prometheus["alerts"] == [
        {
            "alertname": "ExtractCircuitOpen",
            "state": "firing",
            "active_at": "2026-10-25T09:40:00Z",
            "value": "1e+00",
            "summary": "Extraction paused; ask [email]",
        }
    ]
    assert prometheus["queries"]["max(freightaudit_extract_paused)"] == [
        {"metric": {"queue": "extract"}, "value": "1"}
    ]
    paths = [request.path.partition("?")[0] for request in server.requests]
    assert paths.count("/api/v1/query") == len(FA.queries)
