"""The check on proposals that revert a change, on real tool results from the in-process sim (dev
scenarios only): a rollback or a flag-off is stored only when the investigation's own results show
the change and a metric window that reaches back to it."""

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
from fastapi.testclient import TestClient

from agent.job import State, refusal
from agent.prompt import CallRow
from agent.steps import StepRecordedV2, TokenUsage
from agent.tools import ProposeActionArgs, ToolContext, Tools
from py_clients.clients import AlertContextClient
from sim.main import Settings as SimSettings
from sim.main import create_apps

SCENARIOS = Path(__file__).resolve().parents[3] / "evals" / "scenarios" / "v1"
FIRED = datetime(2026, 10, 15, 12, tzinfo=UTC)
Call = tuple[str, dict[str, Any]]
DEPLOYS: Call = ("list_deploys", {})


def errors(service: str, minutes_before: int = 60) -> Call:
    query = f'http_error_ratio{{service="{service}"}}'
    return ("query_metrics", {"query": query, "minutes_before": minutes_before})


def investigate(scenario: str, *calls: Call) -> State:
    """What the job knows once these read tool calls ran, in order, against the scenario."""
    apps = create_apps(SimSettings(sim_scenarios_dir=SCENARIOS, log_level="warn"))
    activated = TestClient(apps.admin).post(
        f"/admin/scenarios/{scenario}/activate", json={"anchor": FIRED.isoformat()}
    )
    assert activated.status_code == 200, activated.text
    state = State("", FIRED, [], {}, 0, None, 0, 0, 150_000)

    async def run() -> None:
        transport = httpx2.ASGITransport(app=apps.data)
        context = AlertContextClient("http://gateway.test", "token")
        async with httpx2.AsyncClient(transport=transport, base_url="http://sim") as sim:
            tools = Tools(sim, context, None, None).all()
            for n, (name, args) in enumerate(calls):
                call_id, result, error = f"tc_{n}", None, None
                try:
                    out = await tools[name](ToolContext(uuid.uuid4(), FIRED, 0), args)
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
    # Restarts and scaling revert nothing, so the check leaves them alone.
    assert refused(state, "restart_service", "notifications") is None


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
