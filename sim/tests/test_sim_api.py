import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from py_config import load_settings
from sim.main import Settings, create_app
from sim.scenario import ScenarioError


def test_health_endpoints(shipped: TestClient) -> None:
    assert shipped.get("/healthz").json() == {"status": "ok"}
    assert shipped.get("/readyz").json() == {"status": "ok"}


def test_not_ready_without_scenarios(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(sim_scenarios_dir=tmp_path)))
    assert client.get("/readyz").status_code == 503


EARLY_DEPLOY = {
    "service": "api",
    "version": "v2",
    "previous_version": "v1",
    "at": "-7h",
    "author": "a",
    "change": "c",
}
FLOOD = {"labels": {"service": "api"}, "per_minute": 10_000, "lines": ["GET / 200"]}
BAD_SCENARIOS: list[tuple[dict[str, Any], str]] = [
    ({"logs": [FLOOD]}, "per_minute: .*6000"),
    ({"metrics": [{"name": "made_up", "labels": {"service": "api"}, "base": 1}]}, "catalog"),
    ({"metrics": [{"name": "cpu_usage_ratio", "labels": {}, "base": 1}]}, "service label"),
    ({"deploys": [EARLY_DEPLOY]}, "outside"),
    ({"services": {"api": {"depends_on": ["ghost"]}}}, "unknown dependency ghost"),
    ({"alert": {"labels": {"alertname": "X"}, "annotations": {}}}, "alertname and service"),
]


@pytest.mark.parametrize(("change", "problem"), BAD_SCENARIOS)
def test_a_bad_scenario_stops_startup_naming_the_file(
    make_client: Callable[[dict[str, Any]], TestClient],
    minimal: dict[str, Any],
    change: dict[str, Any],
    problem: str,
) -> None:
    with pytest.raises(ScenarioError, match=rf"minimal\.json: .*{problem}"):
        make_client(minimal | change)


def test_a_scenario_id_must_match_its_file_name(tmp_path: Path, minimal: dict[str, Any]) -> None:
    (tmp_path / "other.json").write_text(json.dumps(minimal), encoding="utf-8")
    with pytest.raises(ScenarioError, match=r"other\.json: id minimal"):
        create_app(Settings(sim_scenarios_dir=tmp_path))


def test_settings_hold_no_approver_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPROVER_API_KEY", "approver-key-the-sim-must-not-load-000")
    settings = load_settings(Settings)
    assert not [name for name in Settings.model_fields if "approver" in name]
    assert "approver-key-the-sim-must-not-load" not in settings.model_dump_json()
