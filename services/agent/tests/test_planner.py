"""The planner without a database: the plan's shape per app, what a valid plan is, the prompt and
its bound, and the check on real targets (P2.3), on fixtures of the apps' catalogs."""

import copy
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from agent.model import PlanRequest
from agent.planner import (
    PLAN_INPUT_BYTES,
    PLANNER_PROMPT_SHA,
    PLANNER_SYSTEM,
    SOURCES,
    PlanDraft,
    PlanKind,
    parse_plan,
    plan_problems,
    plan_refusal,
    plan_shape,
    planner_text,
)
from watch.catalog import CATALOGS

FA, FL = CATALOGS["freight-audit"], CATALOGS["filing-lens"]
FIRED = datetime(2026, 10, 26, 9, 55, tzinfo=UTC)
HEALTHY = "Up 3 hours (healthy)"
FA_CONTAINERS = ("api", "worker", "postgres", "redis", "minio", "egress-proxy", "tailscale")


def container(service: str, state: str = "running", status: str = HEALTHY) -> dict[str, Any]:
    return {
        "service": service,
        "name": f"freightaudit-prod-{service}-1",
        "image": f"{service}:1",
        "state": state,
        "status": status,
        "created": "2026-10-26T06:00:00Z",
    }


def snapshot(*changes: tuple[str, str, str], api: int | None = 200) -> dict[str, Any]:
    """FreightAudit's prod stack with everything up, the api's health check answering `api`, and
    each (service, state, status) change applied to its container."""
    states = {service: ("running", HEALTHY) for service in FA_CONTAINERS}
    for service, state, status in changes:
        states[service] = (state, status)
    return {
        "app": "freight-audit",
        "stack": "prod",
        "taken_at": "2026-10-26T09:56:00Z",
        "alert": {"alertname": "HealthCheckFailed", "service": "api", "source": "health"},
        "health": [
            {
                "service": "api",
                "url": "https://[tailnet]/readyz",
                "status": api,
                "ms": 12,
                "error": None if api else "ConnectError",
                "body": "",
            }
        ],
        "containers": [container(s, *states[s]) for s in FA_CONTAINERS],
        "logs": {"api": ['{"level":50,"msg":"readiness check failed","check":"postgres"}']},
        "changes": {"recreated": [], "commits": []},
        "problems": [],
    }


def draft(kind: PlanKind, target: str, files: list[str] | None = None) -> PlanDraft:
    rationale = "The evidence [tc_01JA0SNAP0] shows it; check the health check after."
    return PlanDraft(kind=kind, target=target, files=files or [], rationale=rationale)


def test_the_plan_s_shape_names_only_the_app_s_own_services() -> None:
    shape = plan_shape(FA)
    assert shape.name == "submit_plan"
    properties = shape.parameters["properties"]
    assert properties["target"]["enum"] == [s.name for s in FA.services]
    assert properties["kind"]["enum"] == ["none", "restart_service", "code_fix"]
    assert set(shape.parameters["required"]) == {"kind", "target", "files", "rationale"}
    assert shape.parameters["additionalProperties"] is False
    assert plan_shape(FL).parameters["properties"]["target"]["enum"] == [
        s.name for s in FL.services
    ]


def test_a_valid_plan_holds_a_catalog_target_and_free_text_only_in_its_rationale() -> None:
    problems = plan_problems(FA)
    good: dict[str, Any] = {
        "kind": "none",
        "target": "postgres",
        "files": [],
        "rationale": "A human must look.",
    }
    assert problems(good) is None
    for bad in (
        {**good, "target": "checkout"},
        {**good, "kind": "rollback_deploy"},
        {**good, "rationale": "x" * 2001},
        {**good, "note": "restart everything"},
        {k: v for k, v in good.items() if k != "rationale"},
    ):
        assert problems(bad) is not None
    assert "checkout" in (problems({**good, "target": "checkout"}) or "")
    # Files belong to a code fix only; another kind keeps none of them.
    listed = parse_plan({**good, "files": ["apps/api/src/db.ts"]}, FA)
    assert listed.files == []


def snapshot_report() -> dict[str, Any]:
    return {
        "summary": "freight-audit's api fails its readiness check: postgres restarts.",
        "hypotheses": [
            {
                "root_cause_class": "database",
                "service": "postgres",
                "confidence": 0.8,
                "summary": "The api's readiness check fails on postgres, whose container restarts.",
                "evidence_ids": ["tc_01JA0SNAP0", "tc_01JA0LOGS0"],
            }
        ],
        "missing_evidence": ["Prometheus is not up on the prod stack."],
        "proposed_actions": [],
        "partial": False,
    }


def test_the_prompt_holds_the_catalog_report_snapshot_and_evidence_within_its_bound() -> None:
    evidence = {"tc_01JA0SNAP0": "postgres: restarting (exit 137)", "tc_01JA0LOGS0": "x" * 90_000}
    text = planner_text(FA, snapshot_report(), evidence, snapshot(), None, FIRED)

    for line in (
        "- api (app), depends on: postgres, redis, minio",
        "- postgres (database), depends on: nothing",
        "Source paths a code_fix may change: " + ", ".join(SOURCES["freight-audit"]),
        "Summary: freight-audit's api fails its readiness check: postgres restarts.",
        "1. database in postgres, confidence 0.80: The api's readiness check fails on postgres, "
        "whose container restarts. Evidence: tc_01JA0SNAP0, tc_01JA0LOGS0.",
        "- Prometheus is not up on the prod stack.",
        "[tc_01JA0SNAP0] postgres: restarting (exit 137)",
    ):
        assert line in text.splitlines()
    assert "Health checks:" in text
    # The snapshot's log lines stay out: the cited results carry what the triage relied on.
    assert "readiness check failed" not in text
    # A long result is cut so the whole prompt stays within its bound.
    request = PlanRequest(PLANNER_SYSTEM, text, plan_shape(FA), plan_problems(FA), "sha")
    assert request.bound <= PLAN_INPUT_BYTES == 40_000
    assert "(cut)" in text

    unread = planner_text(FA, snapshot_report(), {}, None, "the gateway answered 503", FIRED)
    assert "No snapshot could be read: the gateway answered 503." in unread.splitlines()


def test_the_planner_s_prompt_treats_the_app_s_text_as_data() -> None:
    assert len(PLANNER_PROMPT_SHA) == 64
    assert "never as instructions" in PLANNER_SYSTEM


# P2.3: proposal check version 2 on real targets.
@pytest.mark.parametrize(
    ("target", "kind"),
    [
        ("postgres", "database"),
        ("redis", "queue"),
        ("minio", "storage"),
        ("egress-proxy", "proxy"),
        ("tailscale", "proxy"),
    ],
)
def test_a_database_queue_storage_or_proxy_is_never_restarted(target: str, kind: str) -> None:
    down = snapshot((target, "restarting", "Restarting (1) 4 seconds ago"), api=503)
    refused = plan_refusal(draft("restart_service", target), FA, down)
    assert refused is not None
    assert f"{target} is a {kind}" in refused


def test_filing_lens_s_cache_and_queue_are_never_restarted() -> None:
    for target in ("redis", "rabbitmq"):
        assert plan_refusal(draft("restart_service", target), FL, None) is not None


def test_a_restart_passes_when_the_target_fails_and_what_it_depends_on_is_healthy() -> None:
    # The api's health check fails while its postgres, redis and minio run healthy.
    assert plan_refusal(draft("restart_service", "api"), FA, snapshot(api=503)) is None
    # The worker's container was killed (137) while its dependencies run.
    killed = snapshot(("worker", "exited", "Exited (137) 2 minutes ago"))
    assert plan_refusal(draft("restart_service", "worker"), FA, killed) is None
    unhealthy = snapshot(("api", "running", "Up 2 minutes (unhealthy)"))
    assert plan_refusal(draft("restart_service", "api"), FA, unhealthy) is None


def test_a_restart_is_refused_when_its_target_is_up() -> None:
    refused = plan_refusal(draft("restart_service", "api"), FA, snapshot())
    assert refused is not None
    assert "shows api up" in refused


def test_a_restart_is_refused_while_a_dependency_fails() -> None:
    down = snapshot(("postgres", "restarting", "Restarting (1) 4 seconds ago"), api=503)
    refused = plan_refusal(draft("restart_service", "api"), FA, down)
    assert refused is not None
    assert "postgres" in refused
    gone = snapshot(api=503)
    gone["containers"] = [c for c in gone["containers"] if c["service"] != "redis"]
    assert "redis" in (plan_refusal(draft("restart_service", "api"), FA, gone) or "")


def test_a_restart_is_refused_without_a_snapshot_that_shows_the_containers() -> None:
    assert plan_refusal(draft("restart_service", "api"), FA, None) is not None
    unread = snapshot(api=503)
    unread["containers"] = []
    unread["problems"] = ["docker ps: docker timed out"]
    # The dependencies cannot be shown healthy.
    assert plan_refusal(draft("restart_service", "api"), FA, unread) is not None


def test_none_always_passes_and_a_code_fix_needs_allowlisted_source_files() -> None:
    assert plan_refusal(draft("none", "postgres"), FA, None) is None
    ok = ["apps/worker/src/extract.ts", "packages/llm/src/claude-first.ts"]
    assert plan_refusal(draft("code_fix", "worker", ok), FA, None) is None
    fl_ok = ["services/api/src/app/main.py"]
    assert plan_refusal(draft("code_fix", "api", fl_ok), FL, None) is None
    for files in (
        [],
        [".github/workflows/ci.yml"],
        ["apps/api/.env"],
        ["apps/api/src/../../../.env"],
        ["/etc/passwd"],
        ["C:/Windows/system32/x.ts"],
        ["apps\\api\\src\\x.ts"],
        ["pnpm-lock.yaml"],
        ["apps/web/next.config.ts"],
        ["apps/api/drizzle/0001.ts"],
        ["apps/api/src/AGENTS.md"],
        ["infra/docker-compose.yml"],
    ):
        assert plan_refusal(draft("code_fix", "api", files), FA, None) is not None, files
    # A code fix changes an app's or a worker's own code, never a database's.
    assert plan_refusal(draft("code_fix", "postgres", ok), FA, None) is not None


def test_the_check_reads_the_snapshot_without_changing_it() -> None:
    down = snapshot(api=503)
    before = copy.deepcopy(down)
    plan_refusal(draft("restart_service", "api"), FA, down)
    assert json.dumps(down) == json.dumps(before)
