"""Live triage without a database: its own prompt and sha, its read-only tools, Claude's prices."""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest

from agent.job import PROMPT_SHA
from agent.live import LIVE_PROMPT_SHA, LIVE_SPECS, LiveReportDraft, live_prompt_sha, live_tools
from agent.pricing import RATES, cost, worst
from agent.tools import ToolContext, ToolInputError, ToolUnavailable
from py_clients.clients import AlertContextClient

PRICING = Path(__file__).resolve().parents[3] / "evals" / "pricing.json"
FIRED = datetime(2026, 10, 25, 9, 55, tzinfo=UTC)
CTX = ToolContext(uuid.uuid4(), FIRED, 0, LIVE_PROMPT_SHA, "freight-audit")
SNAPSHOT: dict[str, Any] = {
    "app": "freight-audit",
    "stack": "prod",
    "taken_at": "2026-10-25T09:56:00Z",
    "alert": {
        "alertname": "HealthCheckFailed",
        "service": "api",
        "source": "health",
        "detail": "GET https://[tailnet]/readyz answered 503",
    },
    "health": [
        {
            "service": "api",
            "url": "https://[tailnet]/readyz",
            "status": 503,
            "ms": 12,
            "error": None,
            "body": '{"status":"unavailable"}',
        }
    ],
    "containers": [
        {
            "service": "postgres",
            "name": "freightaudit-prod-postgres-1",
            "image": "postgres:18",
            "state": "restarting",
            "status": "Restarting (1) 4 seconds ago",
            "created": "2026-10-25T09:50:00Z",
        }
    ],
    "logs": {
        "api": [
            '{"level":40,"msg":"readiness check failed","check":"postgres"}',
            '{"level":30,"msg":"request completed"}',
        ],
        "postgres": ["FATAL:  the database system is starting up"],
    },
    "changes": {
        "recreated": [{"service": "postgres", "created": "2026-10-25T09:50:00Z", "image": "pg"}],
        "commits": [
            {
                "sha": "abc1234",
                "at": "2026-10-25T09:00:00Z",
                "subject": "Api: retry the readiness check",
                "paths": ["apps/api/src/health.ts"],
            }
        ],
    },
    "problems": ["git log: git timed out after 10 s"],
}


def group(*annotations: dict[str, str] | None, status: int = 200) -> AlertContextClient:
    """The gateway's internal alerts route, answering one alert per annotations given."""
    alerts = [
        {
            "fingerprint": f"f{n}",
            "labels": {"alertname": "HealthCheckFailed", "app": "freight-audit", "service": "api"},
            "starts_at": "2026-10-25T09:55:00Z",
            "status": "firing",
            **({} if notes is None else {"annotations": notes}),
        }
        for n, notes in enumerate(annotations)
    ]
    context = {
        "investigation_id": str(CTX.investigation_id),
        "group_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "group_key": '{}:{app="freight-audit"}',
        "window_start": "2026-10-25T09:55:00Z",
        "severity": "critical",
        "alert_count": len(alerts),
        "alerts": alerts,
    }
    body = context if status == 200 else {"title": "Unavailable", "status": status}
    transport = httpx2.MockTransport(lambda _: httpx2.Response(status, json=body))
    return AlertContextClient("http://gateway.test", "token", transport=transport)


def call(client: AlertContextClient, tool: str, args: dict[str, Any]) -> str:
    async def run() -> object:
        return await live_tools(client, None)[tool](CTX, args)

    result = asyncio.run(run())
    assert isinstance(result, str)
    return result


def read(client: AlertContextClient, args: dict[str, Any]) -> str:
    return call(client, "read_snapshot", args)


def test_the_live_prompt_has_its_own_sha_and_leaves_the_sim_s_alone() -> None:
    assert live_prompt_sha() == LIVE_PROMPT_SHA
    assert LIVE_PROMPT_SHA != PROMPT_SHA
    # The sim's sha is pinned by evals/harness/test_eval_score.py::test_the_prompt_is_frozen.
    assert PROMPT_SHA.startswith("2b91d4523fb028a6")


def test_live_tools_only_read_and_the_report_carries_a_verdict() -> None:
    names = [spec.name for spec in LIVE_SPECS]
    assert names == [
        "get_alert_context",
        "read_snapshot",
        "get_dependencies",
        "search_runbooks",
        "submit_report",
    ]
    assert set(live_tools(group(), None)) == set(names) - {"submit_report"}
    report = LIVE_SPECS[-1].parameters
    assert "verdict" in report["required"]
    assert report["properties"]["verdict"]["enum"] == ["real", "not_real"]
    for spec in LIVE_SPECS:
        text = json.dumps(spec.parameters)
        assert not [part for part in ('"$ref"', '"title"', '"default"') if part in text]
        if spec.name != "submit_report":
            assert spec.parameters["required"][0] == "reason"
    with pytest.raises(ValueError, match="verdict"):
        LiveReportDraft.model_validate({"summary": "s", "hypotheses": []})


def test_the_snapshot_shows_every_section_and_the_newest_log_lines_first() -> None:
    lines = read(group({"summary": "s", "snapshot": json.dumps(SNAPSHOT)}), {}).splitlines()
    assert lines[0] == (
        "Snapshot of freight-audit on the prod stack, taken 2026-10-25T09:56:00Z (+1m00s):"
    )
    assert (
        "Alert: HealthCheckFailed on api, seen by health: GET https://[tailnet]/readyz "
        "answered 503" in lines
    )
    assert (
        '- api: GET https://[tailnet]/readyz answered 503 in 12 ms: {"status":"unavailable"}'
        in lines
    )
    assert (
        "- postgres (freightaudit-prod-postgres-1): restarting, Restarting (1) 4 seconds ago; "
        "image postgres:18; created 2026-10-25T09:50:00Z" in lines
    )
    assert "- postgres recreated at 2026-10-25T09:50:00Z, image pg" in lines
    assert (
        "- abc1234 2026-10-25T09:00:00Z Api: retry the readiness check (apps/api/src/health.ts)"
        in lines
    )
    assert "- git log: git timed out after 10 s" in lines
    logs = lines[lines.index("api, 2 lines, newest first:") :]
    assert logs[1:3] == [
        '{"level":30,"msg":"request completed"}',
        '{"level":40,"msg":"readiness check failed","check":"postgres"}',
    ]
    assert "FATAL:  the database system is starting up" in lines


def test_naming_a_service_reads_only_its_log_lines() -> None:
    text = read(group({"snapshot": json.dumps(SNAPSHOT)}), {"service": "postgres"})
    assert text.splitlines() == [
        "postgres, 1 line, newest first:",
        "FATAL:  the database system is starting up",
    ]
    absent = read(group({"snapshot": json.dumps(SNAPSHOT)}), {"service": "minio"})
    assert absent == "The snapshot holds no log lines of minio; it holds: api, postgres."


def test_alerts_without_a_snapshot_or_out_of_range_say_so() -> None:
    assert read(group({"summary": "recovered"}), {}) == (
        "Alert 1 (HealthCheckFailed on api) has no snapshot: watch attaches one when an alert "
        "fires."
    )
    cut: dict[str, Any] = {"app": "freight-audit", "taken_at": "t", "alert": {}, "truncated": True}
    assert "cut to the alert alone" in read(group({"snapshot": json.dumps(cut)}), {})
    with pytest.raises(ToolInputError, match="the group has 1 alert"):
        read(group(None), {"alert": 2})
    with pytest.raises(ToolUnavailable):
        read(group(status=503), {})


def test_dependencies_come_from_the_app_s_catalog() -> None:
    api = call(group(), "get_dependencies", {"service": "api"})
    assert api == (
        "api (app) depends on: postgres (database), redis (queue), minio (storage). "
        "Depended on by: tailscale (proxy)."
    )
    postgres = call(group(), "get_dependencies", {"service": "postgres"})
    assert postgres == (
        "postgres (database) depends on: nothing. Depended on by: api (app), worker (worker)."
    )
    with pytest.raises(ToolInputError, match="freight-audit has no service checkout"):
        call(group(), "get_dependencies", {"service": "checkout"})
    with pytest.raises(ToolInputError):
        call(group(), "get_dependencies", {"service": "../admin"})


def test_claude_s_prices_agree_with_the_eval_s() -> None:
    models = json.loads(PRICING.read_text(encoding="utf-8"))["models"]
    for name, rates in RATES.items():
        listed = models[name]
        assert (rates.input, rates.cached_input, rates.cache_write, rates.output) == (
            listed["input"],
            listed["cached_input"],
            listed["cache_write"],
            listed["output"],
        )


def test_a_call_is_charged_its_real_cost_and_gated_on_its_worst() -> None:
    haiku = RATES["claude-haiku-5-5"]
    # 10,000 input tokens: 2,000 read from the cache and 5,000 written to it.
    real = cost(haiku, input=10_000, cached=2_000, cache_writes=5_000, output=500)
    assert real == pytest.approx((3_000 * 0.10 + 2_000 * 0.01 + 5_000 * 0.125 + 500 * 0.50) / 1e6)
    # The worst case bills every input token at the cache-write rate: 100,000 in and 2,048 out.
    assert worst(haiku, 100_000, 2_048) == pytest.approx(0.0125 + 0.001024)
