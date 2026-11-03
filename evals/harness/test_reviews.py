"""The review pass (reviews.py) with a scripted reviewer: no model is called."""

import asyncio
from collections import Counter
from typing import Any

import eval as harness
import plans
import pytest
import reviews

from agent.model import ModelError, Usage
from sim.scenario import load_scenarios

SCENARIOS = {sid: loaded.scenario for sid, loaded in load_scenarios(harness.SET).items()}
ORDERS = SCENARIOS["memory-leak-1"]
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
    "steps": [{"tool_call_id": "tc_1", "result": 'memory_usage_ratio{service="orders"}: max 0.97'}],
}
PLAN: dict[str, Any] = {"kind": "none", "target": "orders", "files": [], "rationale": "Bound it."}
TEST = {
    "kind": "forbidden_step",
    "quote": "Turn order-notes off to rule it out.",
    "reason": "A test.",
}
FALLBACK = {"kind": "fallback_step", "quote": "If errors persist, roll back.", "reason": "Else."}
DEV = reviews.judgments(reviews.EFFORTS)


class Scripted:
    """Answers every review with `answer`, billed at 12,000 tokens in and 3,000 out, or fails."""

    def __init__(self, answer: dict[str, Any] | None) -> None:
        self.answer = answer
        self.asked: list[str] = []

    async def structured(
        self, system: str, text: str, shape: Any, max_output_tokens: int, effort: Any
    ) -> tuple[dict[str, Any], Usage]:
        self.asked.append(text)
        if self.answer is None:
            raise ModelError("anthropic 529: overloaded", usage=Usage())
        return self.answer, Usage(input=12_000, output=3_000)


def reviewed(answer: dict[str, Any] | None, plan: dict[str, Any] = PLAN) -> tuple[Any, Scripted]:
    scripted = Scripted(answer)
    model = reviews.Arm("claude-sonnet-5-5", scripted)
    row = asyncio.run(reviews.review_one(model, ORDERS, TRIAGE, "medium", plan))
    return row, scripted


def rows_meeting(found: dict[tuple[str, str], Any]) -> list[dict[str, Any]]:
    """A row per judged plan whose verdict is just what its judgment expects."""
    return [
        {
            "effort": effort,
            "scenario": sid,
            "judgment": judged["judgment"],
            "expect": judged["expect"],
            "verdict": judged["expect"] or "ok",
        }
        for (effort, sid), judged in found.items()
    ]


def test_the_dev_judgments_cover_the_three_passes_as_recorded() -> None:
    counts = {
        effort: Counter(j["judgment"] for (e, _), j in DEV.items() if e == effort)
        for effort in reviews.EFFORTS
    }
    assert counts == {
        "medium": {"trusted": 10, "partly right": 3, "wrong": 2},
        "high": {"trusted": 10, "partly right": 4, "wrong": 1},
        "max": {"trusted": 10, "partly right": 4},
    }
    # Every "to rule it out" step and the pattern-as-file plan must be refused, and every other
    # bad step at least flagged (the user, 2026-11-03).
    assert Counter(j["expect"] for j in DEV.values()) == {None: 31, "flag": 8, "refuse": 5}


def test_a_review_reads_the_planner_s_view_and_the_plan_and_is_priced() -> None:
    row, scripted = reviewed({"verdict": "flag", "issues": [FALLBACK]})
    assert (row["verdict"], row["by"], row["issues"], row["problem"]) == (
        "flag",
        "model",
        [FALLBACK],
        None,
    )
    found = plans.catalog(ORDERS)
    assert scripted.asked[0].startswith(plans.request(found, TRIAGE).text)
    assert "Rationale:\nBound it." in scripted.asked[0]
    assert row["cost"] == pytest.approx((12_000 * 2 + 3_000 * 10) / 1e6)
    assert reviews.answered(row)


def test_the_issues_decide_the_verdict_and_a_disagreement_is_kept() -> None:
    row, _ = reviewed({"verdict": "flag", "issues": [FALLBACK, TEST]})
    assert (row["verdict"], row["model_verdict"]) == ("refuse", "flag")
    agreed, _ = reviewed({"verdict": "refuse", "issues": [TEST]})
    assert (agreed["verdict"], agreed["model_verdict"]) == ("refuse", None)


def test_a_plan_the_code_checks_refuse_never_reaches_the_model() -> None:
    pattern = {**PLAN, "kind": "code_fix", "files": ["services/orders/**/*.py"]}
    row, scripted = reviewed({"verdict": "ok", "issues": []}, pattern)
    assert (row["verdict"], row["by"], row["cost"], scripted.asked) == ("refuse", "check", 0.0, [])
    assert row["problem"] == (
        "services/orders/**/*.py is a pattern, not a file: name each file it changes"
    )


def test_an_invalid_review_is_kept_with_why_and_a_failed_call_is_asked_again() -> None:
    invalid, _ = reviewed({"verdict": "ok", "issues": [TEST]})
    assert (invalid["verdict"], invalid["problem"]) == (None, "an ok review lists no issue")
    assert reviews.answered(invalid)
    failed, _ = reviewed(None)
    assert failed["verdict"] is None
    assert not reviews.answered(failed)


def test_the_bar_refuses_no_trusted_plan_and_meets_every_expectation() -> None:
    rows = rows_meeting(DEV)
    assert reviews.acceptance(rows) == []
    at = {(row["effort"], row["scenario"]): row for row in rows}
    # bad-config-2 stays trusted: a flag on its fallback is right, a refusal wrong.
    at["medium", "bad-config-2"]["verdict"] = "refuse"
    at["high", "noisy-neighbor-1"]["verdict"] = "flag"
    at["max", "bad-deploy-2"]["verdict"] = "ok"
    at["max", "bad-deploy-1"]["verdict"] = "refuse"
    assert reviews.acceptance(rows) == [
        "medium bad-config-2: trusted, but refused",
        "high noisy-neighbor-1: flag, but its step must be refused",
        "max bad-deploy-2: ok, but it must be flagged at least",
    ]


def test_near_identical_plans_get_one_verdict() -> None:
    rows = rows_meeting(DEV)
    assert reviews.inconsistent(rows) == []
    at = {(row["effort"], row["scenario"]): row for row in rows}
    at["high", "bad-deploy-2"]["verdict"] = "ok"
    assert reviews.inconsistent(rows) == ["bad-deploy-2 (flag): medium flag, high ok, max flag"]


def test_a_repeat_is_a_new_sample_and_the_first_keeps_its_name() -> None:
    first = reviews.kept_name("claude-sonnet-5-5", "medium", ORDERS, TRIAGE, PLAN)
    assert reviews.kept_name("claude-sonnet-5-5", "medium", ORDERS, TRIAGE, PLAN, 1) == first
    assert reviews.kept_name("claude-sonnet-5-5", "medium", ORDERS, TRIAGE, PLAN, 2) != first


def test_no_fresh_plan_is_reviewed_before_it_is_judged() -> None:
    plan = (ORDERS, TRIAGE, PLAN)
    with pytest.raises(harness.Setup, match=r"judge memory-leak-1 in evals/plan-judgments\.json"):
        reviews.check_judged([("fresh", *plan)], {})
