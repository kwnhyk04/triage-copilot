from collections.abc import Callable
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from py_clients.generated.gateway import AlertmanagerWebhook
from sim.catalog import CATALOG
from sim.scenario import load_scenarios, seconds

SHIPPED = Path(__file__).resolve().parents[2] / "evals" / "scenarios" / "v1"
ANCHOR = "2026-10-15T12:00:00Z"
ANCHOR_S = 1_792_065_600
LOADED = load_scenarios(SHIPPED)


def activate(client: TestClient, scenario: str, seed: int | None = None) -> dict[str, Any]:
    response = client.post(
        f"/admin/scenarios/{scenario}/activate", json={"anchor": ANCHOR, "seed": seed}
    )
    assert response.status_code == 200
    return response.json()


def instant(client: TestClient, query: str, at: int = ANCHOR_S) -> float:
    body = client.get("/api/v1/query", params={"query": query, "time": at}).json()
    [sample] = body["data"]["result"]
    return float(sample["value"][1])


def every_deploy(
    client: TestClient, limit: int, service: str | None = None
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": limit} | ({"cursor": cursor} if cursor else {})
        body = client.get(
            "/api/v1/deploys", params=params | ({"service": service} if service else {})
        )
        assert body.status_code == 200
        found += body.json()["deploys"]
        cursor = body.json()["next_cursor"]
        if cursor is None:
            return found


def test_the_shipped_scenarios_load_and_use_only_catalog_metrics(shipped: TestClient) -> None:
    assert shipped.get("/admin/scenarios").json() == sorted(LOADED)
    assert len(LOADED) == 40
    for scenario, _ in LOADED.values():
        assert {m.name for m in scenario.metrics} <= set(CATALOG)
        assert all("service" in m.labels for m in scenario.metrics)


@pytest.mark.parametrize("scenario", sorted(LOADED))
def test_activation_returns_the_webhook_the_gateway_accepts(
    shipped: TestClient, scenario: str
) -> None:
    body = activate(shipped, scenario)
    webhook = AlertmanagerWebhook.model_validate(body["alertmanager"])
    [alert] = webhook.alerts
    assert body["anchor"] == ANCHOR
    assert body["sha256"] == LOADED[scenario].sha256
    assert alert.startsAt == datetime(2026, 10, 15, 12, tzinfo=UTC)
    assert (
        webhook.groupKey
        == "{}:{" + ", ".join(f'{k}="{v}"' for k, v in webhook.groupLabels.root.items()) + "}"
    )
    assert set(webhook.groupLabels.root) == {"alertname", "service"}


def test_bad_deploy_breaks_checkout_while_payments_stays_healthy(shipped: TestClient) -> None:
    activate(shipped, "bad-deploy-1")
    assert instant(shipped, 'http_error_ratio{service="checkout"}') > 0.1
    assert instant(shipped, 'http_error_ratio{service="checkout"}', ANCHOR_S - 600) < 0.01
    assert instant(shipped, 'http_error_ratio{service="payments"}') < 0.005
    latency = 'http_latency_p99_seconds{service="payments"}'
    # The red herring: a payments latency blip that is over before checkout v1.8.0 ships.
    assert instant(shipped, latency, ANCHOR_S - 12 * 60) > 0.4
    assert instant(shipped, latency) < 0.25
    [deploy] = every_deploy(shipped, 1, "checkout")[:1]
    assert (deploy["version"], deploy["previous_version"]) == ("v1.8.0", "v1.7.3")
    assert deploy["at"] == "2026-10-15T11:54:00Z"


def test_db_pool_exhaustion_fills_the_pool_before_it_says_so(shipped: TestClient) -> None:
    activate(shipped, "db-pool-exhaustion-1")
    pool = 'db_pool_in_use{service="orders"}'
    assert 49 <= instant(shipped, pool) <= 50
    assert instant(shipped, 'db_pool_max{service="orders"}') == 50
    assert instant(shipped, pool, ANCHOR_S - 1800) < 20
    assert instant(shipped, 'db_lock_wait_seconds{service="orders-db"}') > 1
    # The pool reports itself full only once it is: no warn line before -6m, and 49+ from then.
    params = {"query": '{service="orders", level="warn"}', "direction": "forward", "limit": 1}
    [stream] = shipped.get("/loki/api/v1/query_range", params=params).json()["data"]["result"]
    first = int(stream["values"][0][0]) // 10**9
    assert ANCHOR_S - 360 <= first < ANCHOR_S - 300
    assert instant(shipped, pool, ANCHOR_S - 360) >= 49
    recent = [d for d in every_deploy(shipped, 50, "orders") if d["at"] >= "2026-10-15T10:00:00Z"]
    assert recent == []


def test_background_deploys_stay_out_of_the_incident_and_chain_versions(
    shipped: TestClient,
) -> None:
    activate(shipped, "bad-deploy-1")
    deploys = every_deploy(shipped, 50)
    near = [d for d in deploys if d["at"] >= "2026-10-15T10:00:00Z"]
    scenario = LOADED["bad-deploy-1"].scenario
    own = {(d.service, d.version) for d in scenario.deploys if seconds(d.at) >= -7200}
    assert ("checkout", "v1.8.0") in own
    assert {(d["service"], d["version"]) for d in near} == own
    checkout = [d for d in deploys if d["service"] == "checkout"]
    assert len(checkout) > 3
    for newer, older in pairwise(checkout):
        assert newer["previous_version"] == older["version"]
    assert min(d["at"] for d in deploys) >= "2026-10-08T12:00:00Z"


def test_deploy_pages_list_every_deploy_once_newest_first(shipped: TestClient) -> None:
    activate(shipped, "bad-deploy-1")
    whole = every_deploy(shipped, 50)
    assert every_deploy(shipped, 5) == whole
    assert len({d["id"] for d in whole}) == len(whole)
    assert [d["at"] for d in whole] == sorted((d["at"] for d in whole), reverse=True)
    payments = every_deploy(shipped, 3, "payments")
    assert payments and {d["service"] for d in payments} == {"payments"}
    assert shipped.get("/api/v1/deploys", params={"limit": 51}).status_code == 400
    assert shipped.get("/api/v1/deploys", params={"cursor": "nope"}).status_code == 400


def test_dependencies_name_both_directions(shipped: TestClient) -> None:
    activate(shipped, "bad-deploy-1")
    assert shipped.get("/api/v1/dependencies/checkout").json() == {
        "service": "checkout",
        "depends_on": ["catalog", "coredns", "notifications", "orders-db", "payments"],
        "dependents": ["frontend"],
    }
    missing = shipped.get("/api/v1/dependencies/ghost")
    assert missing.status_code == 404
    assert missing.json()["services"] == sorted(LOADED["bad-deploy-1"].scenario.services)


def snapshot(client: TestClient) -> list[Any]:
    """Responses that must repeat byte for byte for one scenario, seed and anchor."""
    window = {"start": ANCHOR_S - 3600, "end": ANCHOR_S + 600, "step": "30s"}
    return [
        client.get("/api/v1/query_range", params={"query": "http_error_ratio"} | window).content,
        client.get(
            "/loki/api/v1/query_range", params={"query": '{service=~".+"}', "limit": 200}
        ).content,
        client.get("/api/v1/deploys", params={"limit": 50}).content,
    ]


def test_the_same_seed_and_anchor_give_the_same_world(
    serve: Callable[..., TestClient],
) -> None:
    first, second = serve(SHIPPED), serve(SHIPPED)
    activate(first, "bad-deploy-1")
    activate(second, "bad-deploy-1")
    assert snapshot(first) == snapshot(second)
    activate(second, "bad-deploy-1", seed=99)
    assert [a != b for a, b in zip(snapshot(first), snapshot(second), strict=True)] == [True] * 3
