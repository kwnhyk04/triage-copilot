"""Scoring, bootstrap intervals, floors and the eval plan, on fabricated runs."""

import json
import uuid
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID

import eval as harness
import pytest
import score

import agent.job as agent_job
from sim.scenario import Labels

PRICES = {"m": {"billing": "free", "input": 0.3, "cached_input": 0.03, "output": 2.5}}
LABELS = Labels.model_validate(
    {
        "root_cause_class": "bad_deploy",
        "service": "checkout",
        "onset": "-6m",
        "required_signals": [
            {"tool": "list_deploys", "args": {}, "pattern": r"checkout v1\.7\.3 -> v1\.8\.0"},
        ],
        "acceptable_actions": [
            {
                "kind": "rollback_deploy",
                "target": "checkout",
                "parameters": {"to_version": "v1.7.3"},
            }
        ],
        "forbidden_actions": [{"kind": "rollback_deploy", "target": "frontend"}],
        "red_herrings": [],
    }
)


def record(
    hypotheses: Sequence[tuple[str, str]] = (("bad_deploy", "checkout"),),
    cited: Sequence[str] = ("tc_1",),
    proposals: list[dict[str, Any]] | None = None,
    missing: list[str] | None = None,
    errors: list[str | None] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    steps = [
        ("list_deploys", "tc_1", "11:54 checkout v1.7.3 -> v1.8.0 by ci-bot"),
        ("query_logs", "tc_2", "checkout v1.7.3 -> v1.8.0 mentioned in a log"),
    ]
    return {
        "scenario": "bad-deploy-1",
        "run": 1,
        "report": {
            "hypotheses": [
                {"root_cause_class": c, "service": s, "evidence_ids": list(cited)}
                for c, s in hypotheses
            ],
            "proposed_actions": proposals or [],
            "missing_evidence": missing or [],
        },
        "steps": [
            {"kind": "tool_call", "tool_name": tool, "tool_call_id": i, "result": text}
            for tool, i, text in steps
        ],
        "tool_calls": [{"error": e} for e in (errors or [None, None])],
        "model_calls": [
            {"model": "m", "input_tokens": 10_000, "cached_tokens": 4_000, "output_tokens": 1_000}
        ],
        "tokens": 11_000,
        "seconds": 95.0,
        "resumed": False,
    } | extra


def scored(down: str | None = None, **kwargs: Any) -> score.Scored:
    return score.score(record(**kwargs), LABELS, "bad-deploy", down, PRICES)


def rollback(target: str = "checkout", **params: str) -> dict[str, Any]:
    return {"kind": "rollback_deploy", "target": target, "parameters": params, "evidence_ids": []}


def test_top1_needs_the_class_and_the_service_of_the_first_hypothesis() -> None:
    assert scored().top1 and scored().top3
    assert scored(hypotheses=[("bad_deploy", " Checkout ")]).top1
    wrong_service = scored(hypotheses=[("bad_deploy", "frontend"), ("bad_deploy", "checkout")])
    assert (wrong_service.top1, wrong_service.top3) == (False, True)
    assert not scored(hypotheses=[("config_change", "checkout")]).top3
    fourth = [("unknown", "x")] * 3 + [("bad_deploy", "checkout")]
    assert not scored(hypotheses=fourth).top3
    assert not scored(hypotheses=[]).top1


def test_grounding_needs_a_cited_result_of_the_signals_tool_that_matches() -> None:
    assert scored().grounded
    assert not scored(cited=[]).grounded
    # The same text in another tool's result is not the signal.
    assert not scored(cited=["tc_2"]).grounded
    cited_by_proposal = rollback(to_version="v1.7.3") | {"evidence_ids": ["tc_1"]}
    assert scored(cited=[], proposals=[cited_by_proposal]).grounded


def test_actions_are_correct_only_when_every_proposal_is_acceptable() -> None:
    assert scored(proposals=[rollback(to_version="v1.7.3")]).action_correct
    assert scored().actionable and not scored().action_correct
    assert not scored(proposals=[rollback(to_version="v1.7.2")]).action_correct
    restart: dict[str, Any] = {"kind": "restart_service", "target": "checkout", "parameters": {}}
    extra: list[dict[str, Any]] = [rollback(to_version="v1.7.3"), restart | {"evidence_ids": []}]
    assert not scored(proposals=extra).action_correct
    nothing = Labels.model_validate(LABELS.model_dump() | {"acceptable_actions": []})
    quiet = score.score(record(), nothing, "f", None, PRICES)
    assert quiet.action_correct and not quiet.actionable
    loud = score.score(record(proposals=[rollback()]), nothing, "f", None, PRICES)
    assert not loud.action_correct


def test_action_correctness_shows_the_do_nothing_baseline_and_the_actionable_share() -> None:
    nothing = Labels.model_validate(LABELS.model_dump() | {"acceptable_actions": []})
    quiet = score.score(record(), nothing, "f", None, PRICES)
    fixed = scored(proposals=[rollback(to_version="v1.7.3")])
    data = [replace(r, scenario=s) for r, s in ((quiet, "a"), (scored(), "b"), (fixed, "c"))]
    summary = score.summarize(data, resamples=200)
    assert summary["action_correctness"]["value"] == pytest.approx(2 / 3)
    assert summary["action_correctness"]["do_nothing"] == pytest.approx(1 / 3)
    assert summary["action_correctness_actionable"]["value"] == pytest.approx(1 / 2)
    assert summary["top1"]["do_nothing"] is None
    keys = ("action_correctness", "action_correctness_actionable")
    report: dict[str, Any] = {
        "metrics": {k: summary[k] | {"result": "fail"} for k in keys},
        "proven_elsewhere": [],
        "scope": {"scenarios": 3, "runs": 1},
        "date": "2026-10-16",
        "models_text": "m",
    }
    block = harness.readme_block(report)
    assert "| Action correctness | 0.80 | 0.67 (do-nothing baseline 0.33) |" in block
    assert "| Action correctness, scenarios with an acceptable action | 0.80 | 0.50 |" in block


def test_forbidden_proposals_are_counted_with_any_parameters() -> None:
    both = [rollback("frontend", to_version="v2.3.0"), rollback("frontend")]
    assert scored(proposals=both).forbidden == 2
    assert scored(proposals=[rollback(to_version="v1.7.3")]).forbidden == 0


def test_validity_counts_only_the_models_own_mistakes() -> None:
    run = scored(errors=["ToolInputError: invalid arguments", "ToolUnavailable: down", None])
    assert (run.tool_calls, run.invalid_calls) == (3, 1)


def test_a_degraded_run_must_name_the_down_tool() -> None:
    named = scored("deploys", missing=["list_deploys failed (tc_9): ToolUnavailable: x"])
    assert named.degraded and named.names_missing
    assert scored("deploys", missing=["the deploys backend is down (simulated)"]).names_missing
    assert not scored("deploys", missing=["query_logs failed"]).names_missing
    assert not scored().degraded


def test_cost_bills_cached_input_at_its_rate_and_resumed_runs_have_no_time() -> None:
    run = scored()
    assert run.cost == pytest.approx((6_000 * 0.3 + 4_000 * 0.03 + 1_000 * 2.5) / 1e6)
    assert run.billed == 0
    assert scored(resumed=True).seconds is None


def test_credits_bill_every_token_and_only_the_free_setup_must_bill_nothing() -> None:
    haiku = {
        "c": {
            "billing": "credits",
            "input": 0.1,
            "cached_input": 0.01,
            "cache_write": 0.125,
            "output": 0.5,
        }
    }
    calls = [
        {
            "model": "c",
            "input_tokens": 10_000,
            "cached_tokens": 4_000,
            "cache_write_tokens": 2_000,
            "output_tokens": 1_000,
        },
        # A run cached before agent migration 0009 has no cache writes recorded.
        {"model": "m", "input_tokens": 10_000, "cached_tokens": 4_000, "output_tokens": 1_000},
    ]
    paid, billed = score.cost(calls, PRICES | haiku)
    claude = (4_000 * 0.1 + 4_000 * 0.01 + 2_000 * 0.125 + 1_000 * 0.5) / 1e6
    assert billed == pytest.approx(claude)
    assert paid == pytest.approx(claude + (6_000 * 0.3 + 4_000 * 0.03 + 1_000 * 2.5) / 1e6)
    assert score.free(["m"], PRICES) and not score.free(["m", "c"], PRICES | haiku)
    summary: dict[str, Any] = {m.key: {"value": None, "ci": None} for m in score.METRICS}
    summary["cost_billed"]["value"] = billed
    gate = "Cost per investigation, as billed (mean)"
    assert [b.split(":")[0] for b in harness.check(summary, {}, True)] == [gate]
    assert harness.check(summary, {}, False) == []


def test_reports_on_other_models_never_take_the_official_name() -> None:
    gemini = harness.DEFAULTS
    haiku = gemini | {"primary": "claude-haiku-5-5", "small": "claude-haiku-5-5"}
    day, sha = "2026-10-17", "e0f86d4ad3b4"
    assert harness.report_name(day, "test-full-3", sha, gemini) == (day, True)
    tuning = (f"{day}-dev-full-1-e0f86d4a", False)
    assert harness.report_name(day, "dev-full-1", sha, gemini) == tuning
    other = (f"{day}-test-full-3-e0f86d4a-claude-haiku-5-5", False)
    assert harness.report_name(day, "test-full-3", sha, haiku) == other


def runs(*results: tuple[str, bool]) -> list[score.Scored]:
    base = scored()
    return [
        replace(base, scenario=s, run=n, top1=ok, seconds=60.0 + n)
        for n, (s, ok) in enumerate(results, start=1)
    ]


def test_intervals_resample_scenarios_and_repeat_exactly() -> None:
    data = runs(("a", True), ("a", True), ("b", False), ("b", False), ("c", True), ("c", False))
    first = score.summarize(data, resamples=2_000)
    assert first == score.summarize(data, resamples=2_000)
    top1 = first["top1"]
    assert top1["value"] == pytest.approx(0.5)
    low, high = top1["ci"]
    assert 0 <= low <= 0.5 <= high <= 1
    # a: both right, b: both wrong, c: split.
    assert first["consistency"]["value"] == pytest.approx(1 / 3)
    assert score.summarize(runs(("a", True), ("b", True)))["consistency"]["value"] is None


def test_floors_follow_the_rules_with_the_known_miss_exception() -> None:
    by_key = {m.key: m for m in score.METRICS}
    assert score.floor(by_key["top1"], 0.8, (0.6, 0.9)) == 0.6
    assert score.floor(by_key["top1"], 0.95, (0.9, 1.0)) == 0.75
    # Within its target an efficiency floor is capped at the target ...
    assert score.floor(by_key["tokens_median"], 30_000, (25_000, 35_000)) == 52_500
    assert score.floor(by_key["tokens_median"], 50_000, (45_000, 55_000)) == 60_000
    # ... and a known miss gets 1.5 x its upper bound, so it does not fail every run.
    assert score.floor(by_key["first_hypothesis_p95"], 90, (80, 100)) == 150
    assert score.floor(by_key["forbidden_actions"], 0, (0, 0)) == 0
    assert score.looser(by_key["top1"], 0.5, 0.6)
    assert not score.looser(by_key["top1"], 0.7, 0.6)
    assert score.looser(by_key["tokens_median"], 70_000, 60_000)


def test_gates_break_without_floors_and_floors_when_set() -> None:
    summary: dict[str, Any] = {m.key: {"value": None, "ci": None} for m in score.METRICS}
    summary["forbidden_actions"]["value"] = 1
    summary["top1"]["value"] = 0.5
    assert [b.split(":")[0] for b in harness.check(summary, {}, True)] == [
        "Forbidden action proposals"
    ]
    assert len(harness.check(summary, {"top1": 0.6}, True)) == 2
    summary["forbidden_actions"]["value"] = 0
    assert harness.check(summary, {"top1": 0.5}, True) == []


def test_floors_only_tighten_unless_loosening_is_allowed_and_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thresholds, memory = tmp_path / "thresholds.json", tmp_path / "memory.md"
    thresholds.write_text(json.dumps({"rules": [], "scopes": {}}), encoding="utf-8")
    memory.write_text("- 2026-10-16: nothing yet\n", encoding="utf-8")
    monkeypatch.setattr(harness, "THRESHOLDS", thresholds)
    monkeypatch.setattr(harness, "MEMORY", memory)
    summary: dict[str, Any] = {m.key: {"value": None, "ci": None} for m in score.METRICS}
    summary["top1"] = {"value": 0.8, "ci": (0.6, 0.9)}
    assert harness.set_floors("test-full-3", summary, allow_looser=False)["top1"] == 0.6
    summary["top1"] = {"value": 0.7, "ci": (0.5, 0.8)}
    assert harness.set_floors("test-full-3", summary, allow_looser=True)["top1"] == 0.6
    memory.write_text("- 2026-10-17: lowered the top1 floor for a harder set\n", encoding="utf-8")
    assert harness.set_floors("test-full-3", summary, allow_looser=True)["top1"] == 0.5


def test_the_plan_runs_every_scenario_once_before_any_second_run() -> None:
    manifest = {
        "scenarios": {
            "a-1": {"split": "dev", "ci": True, "sha256": "1"},
            "b-1": {"split": "dev", "ci": False, "sha256": "2"},
            "c-1": {"split": "test", "ci": False, "sha256": "3"},
        }
    }
    models = {"primary": "p", "fallback": "f", "small": "s"}
    jobs = harness.plan(manifest, "dev", "full", 2, "prompt", models)
    assert [(j.scenario, j.run) for j in jobs] == [("a-1", 1), ("b-1", 1), ("a-1", 2), ("b-1", 2)]
    assert [j.scenario for j in harness.plan(manifest, "dev", "ci", 1, "prompt", models)] == ["a-1"]
    keys = {j.key for j in jobs}
    assert len(keys) == 4
    reprompted = harness.plan(manifest, "dev", "full", 2, "prompt 2", models)
    remodelled = harness.plan(manifest, "dev", "full", 2, "prompt", models | {"small": "t"})
    assert not keys & {j.key for j in reprompted + remodelled}


def test_the_prompt_hash_covers_the_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    before = harness.prompt_sha()
    assert before == agent_job.PROMPT_SHA
    monkeypatch.setattr(agent_job, "SYSTEM", agent_job.SYSTEM + " ")
    assert harness.prompt_sha() != before


def test_the_prompt_is_frozen() -> None:
    """Prompt change 1, chosen over change 2 on the dev split on 2026-10-17 (evals/prompt-log.md).
    The test split and the Claude comparison run on it; no prompt change follows."""
    frozen = "2b91d4523fb028a67e6a0ec69a7542f7de6b4102eefc592ee5a9a99f143bdad1"
    assert harness.prompt_sha() == frozen


def test_a_run_counts_only_under_the_prompt_its_calls_used() -> None:
    models = {"primary": "m", "fallback": "f", "small": "s"}

    def ran(*shas: str | None) -> dict[str, Any]:
        calls = [{"role": "primary", "model": "m", "prompt_sha": s} for s in shas]
        return {"model_calls": calls, "steps": []}

    assert harness.problems(ran("p", "p"), models, "p") == []
    # Calls recorded before agent migration 0008 carry no sha.
    assert harness.problems(ran(None, "p"), models, "p") == []
    stale = ["the agent ran old, not the source's prompt p: restart it"]
    assert harness.problems(ran("old", "p"), models, "p") == stale
    unknown = ["the agent ran no recorded prompt, not the source's prompt p: restart it"]
    assert harness.problems(ran(None), models, "p") == unknown


def test_a_quota_stop_keeps_the_run_parked_and_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def start(scenario: str) -> dict[str, Any]:
        return {"investigation_id": str(uuid.uuid4())}

    def wait(investigation: UUID, within: float) -> str:
        return "quota"

    monkeypatch.setattr(harness, "CACHE", tmp_path)
    monkeypatch.setattr(harness.run, "start", start)
    monkeypatch.setattr(harness.run, "wait", wait)
    parked = harness.Job("bad-deploy-1", 1, "k" * 64)
    assert harness.execute([parked], {}, "p") == harness.QUOTA == 2
    assert parked.state.exists() and not parked.result.exists()
