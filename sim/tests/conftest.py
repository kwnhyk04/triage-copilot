"""Sim apps over the shipped scenarios or over scenarios written for one test."""

import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sim.main import Settings, create_app

SHIPPED = Path(__file__).resolve().parents[2] / "evals" / "scenarios"
MINIMAL: dict[str, Any] = {
    "id": "minimal",
    "title": "One service, one metric, one log stream",
    "seed": 7,
    "alert": {"labels": {"alertname": "ApiDown", "service": "api"}, "annotations": {}},
    "services": {"api": {"depends_on": ["db"]}, "db": {}},
    "metrics": [{"name": "http_requests_per_second", "labels": {"service": "api"}, "base": 10}],
    "logs": [{"labels": {"service": "api"}, "per_minute": 1, "lines": ["GET / 200 {ms}ms"]}],
    "expected": {
        "root_cause_class": "unknown",
        "service": "api",
        "allowed_actions": [],
        "red_herrings": [],
    },
}


@pytest.fixture
def shipped() -> TestClient:
    """The shipped scenarios, none active yet."""
    return TestClient(create_app(Settings(sim_scenarios_dir=SHIPPED, log_level="warn")))


@pytest.fixture
def minimal() -> dict[str, Any]:
    """A valid scenario to change per test."""
    return deepcopy(MINIMAL)


@pytest.fixture
def make_client(tmp_path: Path) -> Callable[[dict[str, Any]], TestClient]:
    """Writes the scenario to its own file and serves it."""

    def make(scenario: dict[str, Any]) -> TestClient:
        (tmp_path / f"{scenario['id']}.json").write_text(json.dumps(scenario), encoding="utf-8")
        return TestClient(create_app(Settings(sim_scenarios_dir=tmp_path, log_level="warn")))

    return make
