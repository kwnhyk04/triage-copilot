from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from py_config import ConfigError, load_settings
from sim.main import Settings, create_apps

SHIPPED = Path(__file__).resolve().parents[2] / "evals" / "scenarios" / "v1"
ANCHOR = "2026-10-15T12:00:00Z"
ROUTES: dict[str, list[tuple[str, dict[str, Any]]]] = {
    "metrics": [
        ("/api/v1/query", {"query": "http_error_ratio"}),
        ("/api/v1/query_range", {"query": "http_error_ratio", "start": 0, "end": 60, "step": 30}),
        ("/api/v1/label/__name__/values", {}),
    ],
    "logs": [("/loki/api/v1/query_range", {"query": '{service="checkout"}'})],
    "deploys": [("/api/v1/deploys", {})],
    "dependencies": [("/api/v1/dependencies/checkout", {})],
}
# Ways a data request might try to reach an admin route: encoded, traversing, doubled.
ADMIN_PATHS = [
    "/admin/state",
    "/admin/scenarios",
    "/admin/scenarios/bad-deploy-1/activate",
    "/admin/down",
    "/%61dmin/state",
    "/admin%2Fstate",
    "//admin/state",
    "/api/v1/../../admin/state",
    "/loki/%2E%2E/admin/state",
    "/api/v1/dependencies/..%2F..%2F..%2Fadmin%2Fstate",
    "/api/v1/dependencies/%2E%2E%2F%2E%2E%2F%2E%2E%2Fadmin%2Fstate",
]


def statuses(client: TestClient) -> dict[str, list[int]]:
    return {
        tool: [client.get(path, params=params).status_code for path, params in routes]
        for tool, routes in ROUTES.items()
    }


def test_data_routes_answer_503_until_a_scenario_is_active(shipped: TestClient) -> None:
    for routes in ROUTES.values():
        for path, params in routes:
            response = shipped.get(path, params=params)
            assert response.status_code == 503
            assert response.json() == {
                "status": "error",
                "errorType": "unavailable",
                "error": "no scenario is active",
            }


@pytest.mark.parametrize("tool", list(ROUTES))
def test_a_tool_marked_down_answers_503_until_cleared(shipped: TestClient, tool: str) -> None:
    shipped.post("/admin/scenarios/bad-deploy-1/activate", json={"anchor": ANCHOR})
    assert shipped.put("/admin/down", json={"tool": tool}).json()["down"] == tool
    for path, params in ROUTES[tool]:
        response = shipped.get(path, params=params)
        assert response.status_code == 503
        assert response.json()["error"] == f"{tool} backend is down (simulated)"
    assert {t: s for t, s in statuses(shipped).items() if t != tool} == {
        t: [200] * len(r) for t, r in ROUTES.items() if t != tool
    }
    shipped.put("/admin/down", json={"tool": None})
    assert statuses(shipped) == {t: [200] * len(r) for t, r in ROUTES.items()}


def test_activation_sets_the_state_and_the_scenarios_own_down_tool(shipped: TestClient) -> None:
    shipped.put("/admin/down", json={"tool": "logs"})
    body = shipped.post("/admin/scenarios/db-pool-exhaustion-1/activate", json={"seed": 5}).json()
    state = shipped.get("/admin/state").json()
    assert state == {
        "scenario": "db-pool-exhaustion-1",
        "sha256": body["sha256"],
        "seed": 5,
        "anchor": body["anchor"],
        "down": None,
    }
    assert len(state["sha256"]) == 64
    # The anchor defaults to now, truncated to the second.
    assert state["anchor"].endswith("Z") and "." not in state["anchor"]
    degraded = shipped.post("/admin/scenarios/bad-deploy-2/activate", json={"anchor": ANCHOR})
    assert degraded.json()["down"] == "deploys"
    assert shipped.get("/api/v1/deploys").status_code == 503


def test_admin_routes_refuse_unknown_scenarios_and_tools(shipped: TestClient) -> None:
    missing = shipped.post("/admin/scenarios/ghost/activate")
    assert missing.status_code == 404
    assert len(missing.json()["scenarios"]) == 40
    assert shipped.put("/admin/down", json={"tool": "everything"}).status_code == 400
    bad_anchor = shipped.post("/admin/scenarios/bad-deploy-1/activate", json={"anchor": "noon"})
    assert bad_anchor.status_code == 400


def test_the_data_app_serves_no_admin_route_by_any_path() -> None:
    apps = create_apps(Settings(sim_scenarios_dir=SHIPPED, log_level="warn"))
    paths = [route.path for route in apps.data.routes if isinstance(route, APIRoute)]
    assert paths and not [p for p in paths if "admin" in p]
    TestClient(apps.admin).post(
        "/admin/scenarios/db-pool-exhaustion-1/activate", json={"anchor": ANCHOR}
    )
    data = TestClient(apps.data)
    for path in ADMIN_PATHS:
        for method in ("GET", "POST", "PUT"):
            response = data.request(method, path, json={"tool": "logs"})
            assert response.status_code in (404, 405), (method, path)
            assert "db-pool-exhaustion" not in response.text, (method, path)
    # Nothing above reached the admin app: the down switch is still off.
    assert data.get("/loki/api/v1/query_range", params={"query": '{service="orders"}'}).is_success


def test_the_admin_port_must_differ_from_the_data_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_PORT", "4230")
    monkeypatch.setenv("SIM_ADMIN_PORT", "4230")
    with pytest.raises(ConfigError, match="SIM_ADMIN_PORT must differ from SIM_PORT"):
        load_settings(Settings)
