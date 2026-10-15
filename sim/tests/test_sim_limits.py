"""Limits that keep one request from holding the sim: RE2 patterns, bounded inputs and work."""

import base64
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sim.main import Settings, create_app

SHIPPED = Path(__file__).resolve().parents[2] / "evals" / "scenarios"
ANCHOR = "2026-10-15T12:00:00Z"
ANCHOR_S = 1_792_065_600
LOKI = "/loki/api/v1/query_range"
RANGE = "/api/v1/query_range"
ORDERS = '{service="orders"}'
REJECTED = ["(?=x)", r"(a)\1"]
DEEP_CURSOR = base64.urlsafe_b64encode(b"[" * 3000).decode()


def activate(client: TestClient) -> TestClient:
    response = client.post("/admin/scenarios/db-pool-exhaustion/activate", json={"anchor": ANCHOR})
    assert response.status_code == 200
    return client


@pytest.fixture
def pool(shipped: TestClient) -> TestClient:
    """The db-pool-exhaustion scenario, active at the anchor."""
    return activate(shipped)


@pytest.mark.parametrize(
    ("path", "query"),
    [
        (LOKI, ORDERS + r' |~ "(\\w+\\s*)+timeout"'),
        ("/api/v1/query", r'{__name__=~"(\\w+_?)+Z"}'),
        # 3000 capture groups took 64 ms a matching line until RE2 stopped capturing.
        (LOKI, f"{ORDERS} |~ `{'(' * 3000}e{')' * 3000}`"),
    ],
)
def test_backtracking_patterns_run_in_linear_time(pool: TestClient, path: str, query: str) -> None:
    began = time.perf_counter()
    response = pool.get(path, params={"query": query})
    assert time.perf_counter() - began < 1
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("path", "query"),
    [("/api/v1/query", f"{{service=~`{p}`}}") for p in REJECTED]
    + [(LOKI, f"{ORDERS} |~ `{p}`") for p in REJECTED],
)
def test_patterns_re2_rejects_answer_400(pool: TestClient, path: str, query: str) -> None:
    response = pool.get(path, params={"query": query})
    body = response.json()
    assert (response.status_code, body["errorType"]) == (400, "bad_data")
    assert "RE2" in body["error"]


@pytest.mark.parametrize(
    ("path", "params"),
    [
        (LOKI, {"query": ORDERS, "start": "1e308"}),
        (LOKI, {"query": ORDERS, "start": "-1e308", "end": "-1e308"}),
        (LOKI, {"query": ORDERS, "start": "1" * 5000}),
        (LOKI, {"query": ORDERS, "cursor": DEEP_CURSOR}),
        (LOKI, {"query": f"{ORDERS} |~ `a{{4294967296}}`"}),
        (RANGE, {"query": "db_pool_max", "start": "-1e308", "end": "1e308", "step": "1"}),
        (RANGE, {"query": "db_pool_max", "start": 0, "end": ANCHOR_S, "step": "1e-300"}),
        (RANGE, {"query": "db_pool_max", "start": 0, "end": 0, "step": "1" * 5000 + "s"}),
        ("/api/v1/query", {"query": "{service=~`a{4294967296}`}"}),
        ("/api/v1/deploys", {"cursor": DEEP_CURSOR}),
    ],
)
def test_out_of_range_input_answers_400(
    pool: TestClient, path: str, params: dict[str, Any]
) -> None:
    response = pool.get(path, params=params)
    assert (response.status_code, response.json()["errorType"]) == (400, "bad_data")


def test_a_request_over_the_time_limit_answers_400() -> None:
    settings = Settings(
        sim_scenarios_dir=SHIPPED, log_level="warn", sim_request_time_limit_seconds=0.000001
    )
    client = activate(TestClient(create_app(settings)))
    response = client.get(LOKI, params={"query": '{service=~".+"}'})
    assert response.status_code == 400
    assert "time limit" in response.json()["error"]


def test_the_default_time_limit_serves_the_whole_window(pool: TestClient) -> None:
    assert pool.get(LOKI, params={"query": '{service=~".+"}'}).status_code == 200


@pytest.mark.parametrize(
    ("tool", "path", "params"),
    [
        ("logs", LOKI, {"query": ORDERS, "direction": "FORWARD"}),
        ("logs", LOKI, {"query": ORDERS, "limit": "abc"}),
        ("metrics", RANGE, {"query": "db_pool_max", "start": ANCHOR_S, "end": ANCHOR_S}),
        ("deploys", "/api/v1/deploys", {"limit": "abc"}),
    ],
)
def test_a_tool_marked_down_answers_503_before_validating_parameters(
    pool: TestClient, tool: str, path: str, params: dict[str, Any]
) -> None:
    assert pool.get(path, params=params).status_code == 400
    pool.put("/admin/down", json={"tool": tool})
    response = pool.get(path, params=params)
    assert response.status_code == 503
    assert response.json()["error"] == f"{tool} backend is down (simulated)"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_are_not_served(shipped: TestClient, path: str) -> None:
    assert shipped.get(path).status_code == 404


def test_a_range_query_over_250000_points_answers_400(
    make_client: Callable[[dict[str, Any]], TestClient], minimal: dict[str, Any]
) -> None:
    metric = minimal["metrics"][0]
    minimal["metrics"] = [metric | {"labels": {"service": "api", "pod": str(n)}} for n in range(23)]
    client = make_client(minimal)
    client.post("/admin/scenarios/minimal/activate", json={"anchor": ANCHOR})
    params = {"query": "http_requests_per_second", "start": ANCHOR_S - 10_999, "end": ANCHOR_S}
    # 23 series of 11,000 points pass the per-series limit but not the total one.
    response = client.get(RANGE, params=params | {"step": "1"})
    assert response.status_code == 400
    assert "narrow the selector or raise the step" in response.json()["error"]
    assert client.get(RANGE, params=params | {"step": "2"}).status_code == 200


# Prints a digest of responses that must not change from one process to the next.
SCRIPT = """
import hashlib, sys
from pathlib import Path
from fastapi.testclient import TestClient
from sim.main import Settings, create_app

client = TestClient(create_app(Settings(sim_scenarios_dir=Path(sys.argv[1]), log_level="warn")))
activation = {"seed": 3, "anchor": "2026-10-15T12:00:00Z"}
client.post("/admin/scenarios/bad-deploy/activate", json=activation)
window = {"start": 1792062000, "end": 1792066200, "step": "30s"}
digest = hashlib.sha256()
for path, params in [
    ("/api/v1/query_range", {"query": '{__name__=~".+"}', **window}),
    ("/loki/api/v1/query_range", {"query": '{service=~".+"}', "limit": 200}),
    ("/api/v1/deploys", {"limit": 50}),
]:
    digest.update(client.get(path, params=params).content)
print(digest.hexdigest())
"""


def test_responses_do_not_depend_on_the_hash_seed() -> None:
    def digest(hash_seed: str) -> str:
        done = subprocess.run(  # noqa: S603 - this interpreter and a fixed script
            [sys.executable, "-c", SCRIPT, str(SHIPPED)],
            env=os.environ | {"PYTHONHASHSEED": hash_seed},
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert done.returncode == 0, done.stderr
        return done.stdout.split()[-1]

    first, second = digest("1"), digest("2")
    assert len(first) == 64
    assert first == second
