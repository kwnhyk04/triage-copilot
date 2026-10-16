from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from py_config import load_settings
from sim.main import Settings, create_apps
from sim.scenario import ScenarioError


def test_health_endpoints(shipped: TestClient) -> None:
    assert shipped.get("/healthz").json() == {"status": "ok"}
    assert shipped.get("/readyz").json() == {"status": "ok"}


def test_not_ready_without_scenarios(tmp_path: Path) -> None:
    apps = create_apps(Settings(sim_scenarios_dir=tmp_path))
    assert TestClient(apps.data).get("/readyz").status_code == 503
    assert TestClient(apps.admin).get("/healthz").json() == {"status": "ok"}


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
    ({"down": "everything"}, "down"),
]


@pytest.mark.parametrize(("change", "problem"), BAD_SCENARIOS)
def test_a_bad_scenario_stops_startup_naming_the_file(
    make_client: Callable[[dict[str, Any]], TestClient],
    minimal: dict[str, Any],
    change: dict[str, Any],
    problem: str,
) -> None:
    with pytest.raises(ScenarioError, match=rf"minimal\.yaml: .*{problem}"):
        make_client(minimal | change)


def test_alert_and_label_problems_stop_startup_too(
    make_client: Callable[[dict[str, Any]], TestClient], minimal: dict[str, Any]
) -> None:
    minimal["alert"] = minimal["alert"] | {"labels": {"alertname": "X"}}
    with pytest.raises(ScenarioError, match="alertname and service"):
        make_client(minimal)
    minimal["alert"]["labels"]["service"] = "api"
    minimal["labels"]["forbidden_actions"] = [{"kind": "restart_service", "target": "ghost"}]
    with pytest.raises(ScenarioError, match="labelled service ghost is unknown"):
        make_client(minimal)
    minimal["labels"]["forbidden_actions"] = []
    minimal["labels"]["required_signals"][0]["pattern"] = "(unclosed"
    with pytest.raises(ScenarioError, match="required_signals"):
        make_client(minimal)


def test_a_scenario_id_must_match_its_file_name(tmp_path: Path, minimal: dict[str, Any]) -> None:
    (tmp_path / "other.yaml").write_text(yaml.safe_dump(minimal), encoding="utf-8")
    with pytest.raises(ScenarioError, match=r"other\.yaml: id minimal"):
        create_apps(Settings(sim_scenarios_dir=tmp_path))


def test_a_file_that_is_not_yaml_names_itself(tmp_path: Path) -> None:
    (tmp_path / "broken.yaml").write_text("id: [unclosed", encoding="utf-8")
    with pytest.raises(ScenarioError, match=r"broken\.yaml: "):
        create_apps(Settings(sim_scenarios_dir=tmp_path))


def test_settings_hold_no_approver_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPROVER_API_KEY", "approver-key-the-sim-must-not-load-000")
    settings = load_settings(Settings)
    assert not [name for name in Settings.model_fields if "approver" in name]
    assert "approver-key-the-sim-must-not-load" not in settings.model_dump_json()
