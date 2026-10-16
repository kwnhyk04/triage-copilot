"""Sim apps over the shipped scenarios or over scenarios written for one test.

Tests talk to both apps through one client that sends /admin/ paths to the admin app and the
rest to the data app; test_sim_admin.py checks the data app alone has no admin route.
"""

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.types import Receive, Scope, Send

from sim.main import Apps, Settings, create_apps

SHIPPED = Path(__file__).resolve().parents[2] / "evals" / "scenarios" / "v1"
MINIMAL: dict[str, Any] = {
    "id": "minimal",
    "title": "One service, one metric, one log stream",
    "seed": 7,
    "alert": {
        "labels": {"alertname": "ApiDown", "service": "api"},
        "annotations": {},
        "rule": {
            "metric": "http_requests_per_second",
            "labels": {"service": "api"},
            "op": ">",
            "threshold": 5,
            "for": "2m",
        },
    },
    "services": {"api": {"depends_on": ["db"]}, "db": {}},
    "metrics": [{"name": "http_requests_per_second", "labels": {"service": "api"}, "base": 10}],
    "logs": [{"labels": {"service": "api"}, "per_minute": 1, "lines": ["GET / 200 {ms}ms"]}],
    "labels": {
        "root_cause_class": "unknown",
        "service": "api",
        "onset": "-6h",
        "required_signals": [
            {"tool": "query_logs", "args": {"query": '{service="api"}'}, "pattern": "GET /"}
        ],
        "acceptable_actions": [],
        "forbidden_actions": [],
        "red_herrings": [],
    },
}


def both(apps: Apps) -> TestClient:
    """One client over both apps, routing by path as the two ports do by address."""

    async def route(scope: Scope, receive: Receive, send: Send) -> None:
        admin = scope["type"] == "http" and str(scope["path"]).startswith("/admin/")
        await (apps.admin if admin else apps.data)(scope, receive, send)

    return TestClient(route)


def sim(directory: Path, **settings: Any) -> TestClient:
    return both(create_apps(Settings(sim_scenarios_dir=directory, log_level="warn", **settings)))


@pytest.fixture
def shipped() -> TestClient:
    """The shipped scenarios, none active yet."""
    return sim(SHIPPED)


@pytest.fixture
def serve() -> Callable[..., TestClient]:
    """Serves a directory of scenarios with other settings: serve(directory, **settings)."""
    return sim


@pytest.fixture
def minimal() -> dict[str, Any]:
    """A valid scenario to change per test."""
    return deepcopy(MINIMAL)


def write(directory: Path, scenario: dict[str, Any]) -> None:
    text = yaml.safe_dump(scenario, sort_keys=False)
    (directory / f"{scenario['id']}.yaml").write_text(text, encoding="utf-8")


@pytest.fixture
def make_client(tmp_path: Path) -> Callable[[dict[str, Any]], TestClient]:
    """Writes the scenario to its own file and serves it."""

    def make(scenario: dict[str, Any]) -> TestClient:
        write(tmp_path, scenario)
        return sim(tmp_path)

    return make
