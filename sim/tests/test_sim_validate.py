"""The scenario validator: every shipped scenario tells one timeline, and each check catches the
inconsistency it is for. CI runs this file, so a scenario that contradicts itself fails the build.
"""

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from sim.scenario import Scenario, load_scenarios
from sim.validate import main, problems

SCENARIOS = Path(__file__).resolve().parents[2] / "evals" / "scenarios"
SHIPPED = {
    f"{directory.name}/{scenario_id}": loaded.scenario
    for directory in sorted(p for p in SCENARIOS.glob("v*") if p.is_dir())
    for scenario_id, loaded in load_scenarios(directory).items()
}
POOL = yaml.safe_load((SCENARIOS / "v1" / "db-pool-exhaustion-1.yaml").read_text("utf-8"))
Mutation = Callable[[dict[str, Any]], None]


def series(data: dict[str, Any], name: str, service: str) -> dict[str, Any]:
    return next(
        m for m in data["metrics"] if m["name"] == name and m["labels"] == {"service": service}
    )


def stream(data: dict[str, Any], text: str) -> dict[str, Any]:
    return next(s for s in data["logs"] if any(text in line for line in s["lines"]))


def checked(mutate: Mutation) -> list[str]:
    data = deepcopy(POOL)
    mutate(data)
    return problems(Scenario.model_validate(data))


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_every_shipped_scenario_tells_one_timeline(name: str) -> None:
    assert problems(SHIPPED[name]) == []


def test_the_6a_pool_timeline_fails() -> None:
    """Warn lines claimed a full pool from -8m while the pool filled from -8m to -4m."""

    def old(data: dict[str, Any]) -> None:
        series(data, "db_pool_in_use", "orders")["changes"] = [
            {"at": "-8m", "value": 49.6, "ramp": "4m"}
        ]
        for s in data["logs"]:
            if s.get("asserts"):
                s["from"] = "-8m"

    found = checked(old)
    assert len(found) == 2
    assert all('claims db_pool_in_use{service="orders"} >= 49 from -8m' in p for p in found)


MUTATIONS: list[tuple[str, Mutation, str]] = [
    (
        "an alert that would have fired 25 minutes earlier",
        lambda d: series(d, "http_error_ratio", "orders").update(
            changes=[{"at": "-30m", "value": 0.11, "ramp": "2m"}]
        ),
        "the alert would have fired earlier",
    ),
    (
        "an alert whose condition holds less than its for",
        lambda d: series(d, "http_error_ratio", "orders").update(
            changes=[{"at": "-90s", "value": 0.11, "ramp": "30s"}]
        ),
        "short of for 2m",
    ),
    (
        "an alert whose condition never holds",
        lambda d: series(d, "http_error_ratio", "orders").update(changes=[]),
        "does not surely hold at the alert",
    ),
    (
        "a log line naming a version the service does not run",
        lambda d: stream(d, "Rolling update: pod orders").update(
            lines=["Rolling update: pod orders-{rid} started version=v3.5.0"]
        ),
        "logs version=v3.5.0 while orders runs v3.4.0",
    ),
    (
        "a deploy that does not replace the version before it",
        lambda d: d["deploys"].append(
            d["deploys"][0] | {"version": "v3.5.0", "previous_version": "v3.0.0", "at": "-1h"}
        ),
        "replaces v3.0.0, but v3.4.0 ran before it",
    ),
    (
        "evidence that predates the cause",
        lambda d: stream(d, "due to lock timeout").update({"from": "-20m"}),
        "before onset",
    ),
    (
        "a symptom that predates the cause",
        lambda d: d["labels"].update(onset="-2m"),
        "before onset -2m",
    ),
    (
        "a required signal from the backend that is down",
        lambda d: d.update(down="logs"),
        "its backend is down",
    ),
    (
        "a required signal no data shows",
        lambda d: d["labels"]["required_signals"].append(
            {"tool": "query_logs", "args": {"query": '{service="orders"}'}, "pattern": "nowhere"}
        ),
        "no stream its query selects",
    ),
    (
        "an action both acceptable and forbidden",
        lambda d: d["labels"].update(
            acceptable_actions=[{"kind": "restart_service", "target": "orders-db"}]
        ),
        "both acceptable and forbidden",
    ),
    (
        "a rollback to a version that never ran",
        lambda d: d["labels"].update(
            acceptable_actions=[
                {"kind": "rollback_deploy", "target": "orders", "parameters": {"to_version": "v1"}}
            ],
            forbidden_actions=[],
        ),
        "must go to_version v3.3.2",
    ),
    (
        "a series defined twice",
        lambda d: d["metrics"].append(series(d, "db_pool_max", "orders")),
        "defined twice",
    ),
]


@pytest.mark.parametrize(("case", "mutate", "problem"), MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_each_inconsistency_is_caught(case: str, mutate: Mutation, problem: str) -> None:
    assert any(problem in p for p in checked(mutate)), case


def test_the_cli_fails_on_any_problem(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(SCENARIOS / "v1")]) == 0
    data = deepcopy(POOL)
    data["labels"]["onset"] = "-2m"
    (tmp_path / "db-pool-exhaustion-1.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    assert main([str(tmp_path)]) == 1
    assert "before onset" in capsys.readouterr().out
    assert main([str(tmp_path / "empty")]) == 1
