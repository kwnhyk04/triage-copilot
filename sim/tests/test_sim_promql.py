from typing import Any

import pytest
from fastapi.testclient import TestClient

from sim.promql import QueryError, parse_selector

ANCHOR = 1_792_065_600  # 2026-10-15T12:00:00Z
RANGE = "/api/v1/query_range"
ERRORS = 'http_error_ratio{service="checkout"}'


@pytest.fixture
def client(shipped: TestClient) -> TestClient:
    shipped.post("/admin/scenarios/bad-deploy-1/activate", json={"anchor": "2026-10-15T12:00:00Z"})
    return shipped


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("up", [("__name__", "=", "up")]),
        ("up{}", [("__name__", "=", "up")]),
        ('{__name__="up"}', [("__name__", "=", "up")]),
        (
            ' up { a="1", b!="2", c=~"x|y", d!~"z", } ',
            [
                ("__name__", "=", "up"),
                ("a", "=", "1"),
                ("b", "!=", "2"),
                ("c", "=~", "x|y"),
                ("d", "!~", "z"),
            ],
        ),
        ('up{a="q\\"uote\\\\ \\n"}', [("__name__", "=", "up"), ("a", "=", 'q"uote\\ \n')]),
        ("up{a='single'}", [("__name__", "=", "up"), ("a", "=", "single")]),
        ("up{a=~`raw\\d+`}", [("__name__", "=", "up"), ("a", "=~", "raw\\d+")]),
    ],
)
def test_selectors_parse(query: str, expected: list[tuple[str, str, str]]) -> None:
    assert [(m.label, m.op, m.value) for m in parse_selector(query)] == expected


@pytest.mark.parametrize(
    "query",
    [
        "",
        "rate(up[5m])",
        "sum(up)",
        "sum by (service) (up)",
        "up[5m]",
        "up + 1",
        "{}",
        '{a=~".*"}',
        'up{a="1"',
        "up{a=1}",
        'up{a="\\q"}',
        'up{a=~"("}',
        'up{a="1" b="2"}',
    ],
)
def test_anything_but_a_selector_is_rejected(query: str) -> None:
    with pytest.raises(QueryError):
        parse_selector(query)


def test_matchers_follow_prometheus_semantics() -> None:
    def matches(query: str, labels: dict[str, str]) -> bool:
        return all(m.matches(labels) for m in parse_selector(query))

    assert matches('{service=~"check.*"}', {"service": "checkout"})
    # Regexes are anchored at both ends, so a substring does not match.
    assert not matches('{service=~"heck"}', {"service": "checkout"})
    assert matches('{service!~"pay.*", a="x"}', {"service": "checkout", "a": "x"})
    # A missing label counts as empty.
    assert matches('up{zone=""}', {"__name__": "up"})
    assert not matches('up{zone!=""}', {"__name__": "up"})


def values(client: TestClient, **params: Any) -> list[list[Any]]:
    body = client.get(RANGE, params={"query": ERRORS} | params).json()
    [series] = body["data"]["result"]
    return series["values"]


def test_query_range_serves_samples_on_the_requested_steps(client: TestClient) -> None:
    found = values(client, start=ANCHOR - 600, end=ANCHOR, step="60s")
    assert [t for t, _ in found] == [float(ANCHOR - 600 + 60 * i) for i in range(11)]
    assert all(isinstance(v, str) for _, v in found)
    before, after = float(found[0][1]), float(found[-1][1])
    assert before < 0.01 < 0.1 < after
    assert found == values(client, start="2026-10-15T11:50:00Z", end=str(ANCHOR), step="1m")
    assert found == values(client, start=ANCHOR - 600, end=ANCHOR, step="60")


def test_samples_sit_on_a_30_second_grid_from_the_anchor(client: TestClient) -> None:
    fine = values(client, start=ANCHOR, end=ANCHOR + 29, step="1s")
    assert len({v for _, v in fine}) == 1
    assert values(client, start=ANCHOR + 30, end=ANCHOR + 30, step="1")[0][1] != fine[0][1]


def test_nothing_exists_outside_the_window(client: TestClient) -> None:
    body = client.get(
        RANGE,
        params={
            "query": ERRORS,
            "start": ANCHOR - 7 * 3600,
            "end": ANCHOR - 6 * 3600 - 1,
            "step": "60",
        },
    ).json()
    assert body["data"]["result"] == []


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"start": ANCHOR, "end": ANCHOR + 11_000, "step": "1"}, "11,000 points"),
        ({"start": ANCHOR, "end": ANCHOR - 1, "step": "1"}, "before start"),
        ({"start": ANCHOR, "end": ANCHOR, "step": "0"}, "step"),
        ({"start": "yesterday", "end": ANCHOR, "step": "1"}, "timestamp"),
        ({"start": ANCHOR, "end": ANCHOR, "step": "1x"}, "duration"),
        (
            {"query": "rate(http_error_ratio[5m])", "start": ANCHOR, "end": ANCHOR, "step": "1"},
            "Only selectors are supported",
        ),
        ({"start": ANCHOR, "end": ANCHOR}, "step"),
    ],
)
def test_bad_range_queries_get_prometheus_errors(
    client: TestClient, params: dict[str, Any], message: str
) -> None:
    response = client.get(RANGE, params={"query": ERRORS} | params)
    assert response.status_code == 400
    body = response.json()
    assert (body["status"], body["errorType"]) == ("error", "bad_data")
    assert message in body["error"]


def test_a_rejected_query_lists_the_scenarios_metrics(client: TestClient) -> None:
    body = client.get("/api/v1/query", params={"query": "sum(http_error_ratio)"}).json()
    assert "http_error_ratio" in body["error"]
    assert "db_lock_wait_seconds" in body["error"]


def test_instant_queries_default_to_the_anchor(client: TestClient) -> None:
    body = client.get("/api/v1/query", params={"query": ERRORS}).json()
    [sample] = body["data"]["result"]
    assert body["data"]["resultType"] == "vector"
    assert sample["metric"] == {"__name__": "http_error_ratio", "service": "checkout"}
    assert sample["value"] == [
        float(ANCHOR),
        values(client, start=ANCHOR, end=ANCHOR, step="1")[0][1],
    ]
    early = client.get("/api/v1/query", params={"query": ERRORS, "time": ANCHOR - 3600}).json()
    assert float(early["data"]["result"][0]["value"][1]) < 0.01


def test_label_values_list_the_active_scenarios_labels(client: TestClient) -> None:
    names = client.get("/api/v1/label/__name__/values").json()["data"]
    assert "http_error_ratio" in names
    assert names == sorted(set(names))
    services = client.get("/api/v1/label/service/values").json()["data"]
    assert services == [
        "catalog",
        "checkout",
        "coredns",
        "frontend",
        "nightly-backup",
        "notifications",
        "orders-db",
        "payments",
    ]
