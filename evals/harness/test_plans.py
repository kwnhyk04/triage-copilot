"""The planner pass (plans.py) on dev scenarios with a scripted planner: no model is called."""

import asyncio
from collections.abc import Sequence
from typing import Any

import eval as harness
import plans
import pytest

from agent.model import ModelCall, PlanRequest, Usage
from sim.scenario import load_scenarios

SCENARIOS = {sid: loaded.scenario for sid, loaded in load_scenarios(harness.SET).items()}
TRIAGE: dict[str, Any] = {
    "key": "triage-key",
    "anchor": "2026-11-01T06:00:00+00:00",
    "report": {
        "summary": "orders leaks memory until it is killed.",
        "hypotheses": [
            {
                "root_cause_class": "resource_exhaustion",
                "service": "orders",
                "confidence": 0.8,
                "summary": "The heap grows for hours.",
                "evidence_ids": ["tc_1"],
            }
        ],
        "missing_evidence": [],
    },
    "steps": [
        {"tool_call_id": "tc_1", "result": 'memory_usage_ratio{service="orders"}: max 0.97'},
        {"tool_call_id": None, "result": None},
    ],
}


class Scripted:
    """Answers every plan with `answer`, billed at 9,000 tokens in and 2,000 out."""

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.asked: list[PlanRequest] = []

    async def plan(self, request: PlanRequest) -> tuple[dict[str, Any] | None, Sequence[ModelCall]]:
        self.asked.append(request)
        usage = Usage(input=9_000, output=2_000)
        return self.answer, [ModelCall("planner", "anthropic", "claude-opus-5-5", "ok", usage)]


def planned(scenario: str, kind: str, target: str, files: Sequence[str] = ()) -> dict[str, Any]:
    answer = {"kind": kind, "target": target, "files": list(files), "rationale": "As [tc_1] shows."}
    return asyncio.run(plans.plan_one(Scripted(answer), SCENARIOS[scenario], TRIAGE))


def test_the_fresh_set_is_the_first_two_test_incidents_of_each_family() -> None:
    manifest = harness.load(harness.SET / "manifest.json")
    test = sorted(sid for sid, entry in manifest["scenarios"].items() if entry["split"] == "test")
    families = sorted({sid.rsplit("-", 1)[0] for sid in test})
    first_two: list[str] = []
    for family in families:
        first_two += [sid for sid in test if sid.rsplit("-", 1)[0] == family][:2]
    assert tuple(first_two) == plans.FRESH
    assert len(plans.FRESH) == 16
    # Their kinds are known too, so each gets a catalog.
    assert {name for sid in plans.FRESH for name in SCENARIOS[sid].services} <= set(plans.KINDS)


def test_a_report_names_its_set_so_dev_and_fresh_passes_stay_apart() -> None:
    assert plans.report_name("2026-11-03", "claude-opus-5-5", "max", "fresh").startswith(
        "2026-11-03-fresh-plans-"
    )


def test_every_dev_service_has_a_kind_and_providers_are_left_out() -> None:
    manifest = harness.load(harness.SET / "manifest.json")
    dev = [sid for sid, entry in manifest["scenarios"].items() if entry["split"] == "dev"]
    assert {name for sid in dev for name in SCENARIOS[sid].services} <= set(plans.KINDS)
    found = plans.catalog(SCENARIOS["slow-dependency-1"])
    assert "card-network" not in [service.name for service in found.services]
    assert found.service("payments").depends_on == ("payments-db", "coredns")
    assert found.service("payments-db").kind == "database"


def test_the_plan_reads_the_report_and_its_results_and_is_priced() -> None:
    model = Scripted({"kind": "none", "target": "orders", "files": [], "rationale": "Wait."})
    row = asyncio.run(plans.plan_one(model, SCENARIOS["memory-leak-1"], TRIAGE))
    text = model.asked[0].text
    assert "orders leaks memory" in text and '[tc_1] memory_usage_ratio{service="orders"}' in text
    assert f"No snapshot could be read: {plans.NO_SNAPSHOT}." in text
    assert row["plan"]["kind"] == "none" and row["refused"] is None
    assert row["cost"] == pytest.approx(9_000 * 4e-6 + 2_000 * 20e-6)
    assert plans.answered(row)


def test_the_check_refuses_restarts_without_a_snapshot_and_any_change_to_storage() -> None:
    restart = planned("memory-leak-1", "restart_service", "orders")
    assert restart["target_kind"] == "app" and "no snapshot" in restart["refused"]
    database = planned("memory-leak-1", "restart_service", "orders-db")
    assert "orders-db is a database" in database["refused"]
    fix = planned("memory-leak-1", "code_fix", "orders", ["services/orders/export_cache.py"])
    assert fix["refused"] is None
    total = plans.summary([restart, database, fix])
    assert total["acts_on_storage"] == ["memory-leak-1"] and total["kept_on_storage"] == []
    assert total["kinds"] == {"restart_service": 2, "code_fix": 1} and total["kept"] == 1


def test_the_spend_limit_holds_the_next_plan_s_worst_case() -> None:
    assert plans.fits(None, 99.0, 99.0)
    assert plans.fits(8.0, 6.0, 2.0)
    assert not plans.fits(8.0, 6.5, 1.6)


def test_a_report_names_its_effort_so_two_passes_of_a_day_stay_apart() -> None:
    names = {plans.report_name("2026-11-03", "claude-opus-5-5", e) for e in ("high", "max")}
    assert len(names) == 2
    assert all(name.startswith("2026-11-03-dev-plans-") for name in names)
    assert any(name.endswith("-claude-opus-5-5-max") for name in names)


def test_a_call_that_failed_before_any_output_is_not_kept() -> None:
    row = planned("memory-leak-1", "none", "orders")
    row["calls"][0] |= {"outcome": "error", "usage": {"output": 0}}
    assert not plans.answered(row)
    row["calls"][0]["usage"]["output"] = 120
    assert plans.answered(row)
