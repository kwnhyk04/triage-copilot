from typing import Any

import pytest
from fastapi.testclient import TestClient

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
    shipped.post("/admin/scenarios/bad-deploy/activate", json={"anchor": ANCHOR})
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


def test_activation_clears_the_down_switch_and_sets_the_state(shipped: TestClient) -> None:
    shipped.put("/admin/down", json={"tool": "logs"})
    body = shipped.post("/admin/scenarios/db-pool-exhaustion/activate", json={"seed": 5})
    state = shipped.get("/admin/state").json()
    assert state == {
        "scenario": "db-pool-exhaustion",
        "seed": 5,
        "anchor": body.json()["anchor"],
        "down": None,
    }
    # The anchor defaults to now, truncated to the second.
    assert state["anchor"].endswith("Z") and "." not in state["anchor"]


def test_admin_routes_refuse_unknown_scenarios_and_tools(shipped: TestClient) -> None:
    missing = shipped.post("/admin/scenarios/ghost/activate")
    assert missing.status_code == 404
    assert missing.json()["scenarios"] == ["bad-deploy", "db-pool-exhaustion"]
    assert shipped.put("/admin/down", json={"tool": "everything"}).status_code == 400
    assert (
        shipped.post("/admin/scenarios/bad-deploy/activate", json={"anchor": "noon"}).status_code
        == 400
    )
