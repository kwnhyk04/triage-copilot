"""The check on proposals, on real tool results from the in-process sim (dev scenarios only). A
rollback or a flag-off is stored only when the investigation's own results show the change and a
metric window that reaches back to it. A restart or a scale is stored only when they show its target
out of its own memory or CPU, or its node's CPU, and never for a database."""

import asyncio
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from agent.job import PROMPT_SHA, State, refusal
from agent.prompt import CallRow
from agent.steps import StepRecordedV2, TokenUsage
from agent.tools import ProposeActionArgs, ToolContext, Tools
from py_clients.clients import AlertContextClient
from sim.main import Settings as SimSettings
from sim.main import create_apps

SCENARIOS = Path(__file__).resolve().parents[3] / "evals" / "scenarios" / "v1"
# One sim for the module: each activation builds a new world, so tests share nothing else.
APPS = create_apps(SimSettings(sim_scenarios_dir=SCENARIOS, log_level="warn"))
FIRED = datetime(2026, 10, 15, 12, tzinfo=UTC)
Call = tuple[str, dict[str, Any]]
DEPLOYS: Call = ("list_deploys", {})
RESTART, SCALE = "restart_service", "scale_service"


def errors(service: str, minutes_before: int = 60) -> Call:
    query = f'http_error_ratio{{service="{service}"}}'
    return ("query_metrics", {"query": query, "minutes_before": minutes_before})


def metric(name: str, **labels: str) -> Call:
    selector = ", ".join(f'{k}="{v}"' for k, v in labels.items())
    return ("query_metrics", {"query": f"{name}{{{selector}}}"})


def resources(service: str) -> list[Call]:
    """Every series that could show the service out of its own memory or CPU, or its node's."""
    names = ("memory_usage_ratio", "cpu_usage_ratio", "node_cpu_usage_ratio")
    return [metric(name, service=service) for name in (*names, "container_restarts_total")]


def peak(state: State, call: int) -> float:
    """The max of the first series in the result of the call-th read."""
    found = re.search(r", max ([\d.]+),", state.steps[2 * call + 1].result or "")
    assert found is not None, state.steps[2 * call + 1].result
    return float(found[1])


def investigate(scenario: str, *calls: Call) -> State:
    """What the job knows once these read tool calls ran, in order, against the scenario."""
    activated = TestClient(APPS.admin).post(
        f"/admin/scenarios/{scenario}/activate", json={"anchor": FIRED.isoformat()}
    )
    assert activated.status_code == 200, activated.text
    state = State("", FIRED, [], {}, 0, None, 0, 0, 150_000)

    async def run() -> None:
        transport = httpx2.ASGITransport(app=APPS.data)
        context = AlertContextClient("http://gateway.test", "token")
        async with httpx2.AsyncClient(transport=transport, base_url="http://sim") as sim:
            tools = Tools(sim, context, None, None).all()
            for n, (name, args) in enumerate(calls):
                call_id, result, error = f"tc_{n}", None, None
                try:
                    ctx = ToolContext(uuid.uuid4(), FIRED, 0, PROMPT_SHA)
                    out = await tools[name](ctx, args)
                    result = out if isinstance(out, str) else out.text
                except Exception as failure:  # A backend the scenario marks down.
                    error = f"{type(failure).__name__}: {failure}"
                for tokens, text in ((TokenUsage(input=1, output=1), None), (None, result)):
                    step = StepRecordedV2(
                        kind="tool_call",
                        summary=f"Run {name}.",
                        tool_name=name,
                        tool_call_id=call_id,
                        result=text,
                        latency_ms=None,
                        tokens=tokens,
                    )
                    state.steps.append(step)
                state.calls[call_id] = CallRow(name, args, error, None)
        context.close()

    asyncio.run(run())
    return state


def refused(state: State, kind: str, target: str, **params: str) -> str | None:
    args = {
        "kind": kind,
        "target": target,
        "params": params,
        "rationale": "r",
        "evidence_ids": ["tc_0"],
    }
    return refusal(state, ProposeActionArgs.model_validate(args))


def test_a_rollback_needs_a_metric_window_that_reaches_back_to_its_deploy() -> None:
    # bad-deploy-1: checkout v1.7.3 -> v1.8.0 6 minutes before the alert, notifications v6.2.2 ->
    # v6.3.0 116 minutes before; the default metric window starts 60 minutes before.
    state = investigate("bad-deploy-1", DEPLOYS, errors("checkout"))
    assert refused(state, "rollback_deploy", "checkout", to_version="v1.7.3") is None
    reason = refused(state, "rollback_deploy", "notifications", to_version="v6.2.2")
    assert reason is not None and "minutes_before of at least 116" in reason
    wider = investigate("bad-deploy-1", DEPLOYS, errors("checkout"), errors("checkout", 120))
    assert refused(wider, "rollback_deploy", "notifications", to_version="v6.2.2") is None
    # A window that matched no series shows nothing, and so does no metric result at all.
    empty = investigate("bad-deploy-1", DEPLOYS, errors("checkout"), errors("no-such", 120))
    assert refused(empty, "rollback_deploy", "notifications", to_version="v6.2.2") is not None
    assert refused(investigate("bad-deploy-1", DEPLOYS), "rollback_deploy", "checkout") is not None
    # A restart reverts nothing, but it needs its own evidence too (below).
    assert refused(state, "restart_service", "notifications") is not None


def test_a_deploy_older_than_any_metric_window_is_never_rolled_back() -> None:
    state = investigate("bad-deploy-1", ("list_deploys", {"limit": 20}), errors("checkout", 360))
    old = [line.split() for line in (state.steps[1].result or "").splitlines()][-2]
    assert old[1].startswith("(-") and int(old[1][2:].split("m")[0]) > 360, old
    reason = refused(state, "rollback_deploy", old[2], to_version=old[3])
    assert reason is not None and "propose nothing" in reason and "minutes_before" not in reason


def test_with_deploys_down_a_rollout_log_line_shows_the_deploy() -> None:
    # bad-deploy-2 marks its deploys backend down; search's rollout lines name the version replaced.
    rollout: Call = ("query_logs", {"query": '{service="search"} |= "Rolling update"'})
    state = investigate("bad-deploy-2", DEPLOYS, rollout, errors("search"))
    assert state.calls["tc_0"].error is not None
    assert refused(state, "rollback_deploy", "search", to_version="v5.1.4") is None
    # notifications v2.16.0 replaced v2.15.7, but nothing here shows that deploy.
    reason = refused(state, "rollback_deploy", "notifications", to_version="v2.15.7")
    assert reason is not None and reason.startswith("no result")


def test_a_flag_off_needs_a_log_line_naming_the_flag() -> None:
    # bad-config-1: checkout's new-tax-engine flag went to 100% about 4 minutes before the alert.
    flags: Call = ("query_logs", {"query": '{service="checkout", level="info"} |= "feature flag"'})
    state = investigate("bad-config-1", flags, errors("checkout"))
    assert refused(state, "disable_feature_flag", "checkout", flag="new-tax-engine") is None
    reason = refused(state, "disable_feature_flag", "checkout", flag="dark-mode")
    assert reason is not None and "dark-mode" in reason
    assert refused(state, "disable_feature_flag", "checkout") is not None


@pytest.mark.parametrize(
    ("scenario", "database"),
    [
        # A held lock fills the orders pool: orders-db's lock wait and connections climb.
        ("db-pool-exhaustion-1", "orders-db"),
        # orders-db stays healthy while orders leaks memory, or while its node starves orders.
        ("memory-leak-1", "orders-db"),
        ("noisy-neighbor-3", "orders-db"),
    ],
)
def test_a_database_is_never_restarted_or_scaled(scenario: str, database: str) -> None:
    state = investigate(scenario, metric("", service=database), *resources(database))
    for kind in (RESTART, SCALE):
        reason = refused(state, kind, database)
        assert reason is not None and reason.startswith(f"{database} is a database"), reason


def test_a_database_at_its_cpu_limit_is_never_restarted_or_scaled() -> None:
    # db-pool-exhaustion-3: a bad plan takes inventory-db's CPU to 0.93, past the limit a service
    # needs, but it is a database.
    cpu = metric("cpu_usage_ratio", service="inventory-db")
    slow = metric("db_slow_queries_per_second", service="inventory-db")
    state = investigate("db-pool-exhaustion-3", cpu, slow)
    assert peak(state, 0) >= 0.9
    for kind in (RESTART, SCALE):
        reason = refused(state, kind, "inventory-db")
        assert reason is not None and reason.startswith("inventory-db is a database"), reason


@pytest.mark.parametrize(
    ("scenario", "kind", "target"),
    [
        # Every restart or scale of a service that is not a database that a dev scenario forbids:
        # the service waits on a database, a dependency, DNS or a certificate, so its own memory,
        # CPU and node CPU stay normal, its restart counter stays flat, or no series of it exists.
        ("db-pool-exhaustion-1", RESTART, "orders"),
        ("db-pool-exhaustion-1", SCALE, "orders"),
        ("dns-partition-1", RESTART, "coredns"),
        ("dns-partition-1", SCALE, "payments"),
        ("expired-cert-1", RESTART, "payments"),
        ("expired-cert-1", RESTART, "checkout"),
        ("expired-cert-4", RESTART, "events-broker"),
        ("expired-cert-4", SCALE, "notifications"),
        ("slow-dependency-1", SCALE, "payments"),
        ("slow-dependency-2", RESTART, "shipping"),
        ("slow-dependency-2", SCALE, "shipping"),
        ("bad-deploy-2", RESTART, "search-index"),
        ("bad-config-2", RESTART, "search-index"),
        ("bad-config-2", SCALE, "search-index"),
        ("noisy-neighbor-1", RESTART, "search-index"),
    ],
)
def test_a_service_within_its_limits_is_never_restarted_or_scaled(
    scenario: str, kind: str, target: str
) -> None:
    reason = refused(investigate(scenario, *resources(target)), kind, target)
    assert reason is not None and reason.startswith("no query_metrics result"), reason


def test_a_cpu_blip_below_the_limit_is_no_reason_to_restart() -> None:
    # memory-leak-1: catalog's CPU rose to 0.81 from 50 to 42 minutes before the alert and settled.
    state = investigate("memory-leak-1", metric("cpu_usage_ratio", service="catalog"))
    assert 0.75 <= peak(state, 0) < 0.9
    assert refused(state, RESTART, "catalog") is not None


def test_a_restart_needs_a_metric_result_of_its_own_target() -> None:
    # memory-leak-1: orders runs out of memory, but none of these results shows a series of it.
    logs: Call = ("query_logs", {"query": '{service="orders", level="error"}'})
    nothing = metric("memory_usage_ratio", service="no-such")
    state = investigate("memory-leak-1", DEPLOYS, logs, nothing)
    assert refused(state, RESTART, "orders") is not None
    # noisy-neighbor-1: nightly-reindex at its limit shows nothing of search-index.
    state = investigate("noisy-neighbor-1", metric("cpu_usage_ratio", service="nightly-reindex"))
    assert peak(state, 0) >= 0.9
    assert refused(state, RESTART, "search-index") is not None


@pytest.mark.parametrize(
    ("scenario", "series", "kind", "target", "params"),
    [
        # Every restart and scale a dev scenario accepts, each after one result of the target's own
        # series. memory-leak-1 and -3: memory climbs to 0.97, and each OOM kill adds a restart.
        ("memory-leak-1", "memory_usage_ratio", RESTART, "orders", {}),
        ("memory-leak-1", "container_restarts_total", SCALE, "orders", {}),
        ("memory-leak-3", "memory_usage_ratio", SCALE, "recommendations", {}),
        ("memory-leak-3", "container_restarts_total", RESTART, "recommendations", {}),
        # noisy-neighbor-1 and -3: a batch job takes its node's CPU (0.96), and the node (0.99)
        # starves the service on it.
        ("noisy-neighbor-1", "cpu_usage_ratio", SCALE, "nightly-reindex", {"replicas": "0"}),
        ("noisy-neighbor-1", "node_cpu_usage_ratio", SCALE, "search", {}),
        ("noisy-neighbor-3", "cpu_usage_ratio", SCALE, "log-compactor", {"replicas": "0"}),
        ("noisy-neighbor-3", "node_cpu_usage_ratio", SCALE, "orders", {}),
    ],
)
def test_a_service_out_of_its_own_memory_or_cpu_or_its_nodes_can_be_restarted_or_scaled(
    scenario: str, series: str, kind: str, target: str, params: dict[str, str]
) -> None:
    state = investigate(scenario, metric(series, service=target))
    assert refused(state, kind, target, **params) is None


@pytest.mark.parametrize(
    ("scenario", "queries", "kind", "target", "params"),
    [
        # The dev run cache's restarts and scales, each after the metric reads its run made first.
        # memory-leak-1 run 1 on Gemini: orders to 3 replicas.
        (
            "memory-leak-1",
            [
                'http_error_ratio{service="orders"}',
                'upstream_error_ratio{service="orders"}',
                'memory_usage_ratio{service="orders"}',
                'container_restarts_total{service="orders"}',
            ],
            SCALE,
            "orders",
            {"replicas": "3"},
        ),
        # memory-leak-3 run 1 on Gemini: restart recommendations, twice; the first cited unknown
        # result ids, so it never reached the check.
        (
            "memory-leak-3",
            [
                'http_error_ratio{service="recommendations"}',
                'upstream_error_ratio{service="recommendations"}',
                'upstream_latency_p99_seconds{service="recommendations"}',
                'memory_usage_ratio{service="recommendations"}',
                'container_restarts_total{service="recommendations"}',
                'cpu_usage_ratio{service="recommendations"}',
            ],
            RESTART,
            "recommendations",
            {},
        ),
        # noisy-neighbor-3 run 1 on Haiku: log-compactor to 0, found among the node's workloads.
        (
            "noisy-neighbor-3",
            [
                'http_latency_p99_seconds{service="orders"}',
                'upstream_latency_p99_seconds{service="orders"}',
                'cpu_usage_ratio{service="orders"}',
                'node_cpu_usage_ratio{service="orders"}',
                'node_cpu_usage_ratio{node="node-5"}',
                'cpu_usage_ratio{node="node-5"}',
            ],
            SCALE,
            "log-compactor",
            {"replicas": "0"},
        ),
    ],
)
def test_the_dev_caches_restarts_and_scales_are_still_stored(
    scenario: str, queries: list[str], kind: str, target: str, params: dict[str, str]
) -> None:
    reads: list[Call] = [("query_metrics", {"query": query}) for query in queries]
    assert refused(investigate(scenario, *reads), kind, target, **params) is None
