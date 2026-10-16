import base64
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sim.logql import parse_log_query
from sim.promql import QueryError

LOKI = "/loki/api/v1/query_range"
ANCHOR = "2026-10-15T12:00:00Z"
ANCHOR_NS = 1_792_065_600 * 10**9
Line = tuple[str, str]


@pytest.fixture
def flood(
    make_client: Callable[[dict[str, Any]], TestClient], minimal: dict[str, Any]
) -> TestClient:
    """5,000 lines: 100 a minute for the 50 minutes before the anchor."""
    stream = {
        "labels": {"service": "api", "level": "info"},
        "per_minute": 100,
        "from": "-50m",
        "to": "+0m",
        "lines": ["GET /item/{n} 200 {ms}ms", "GET /item/{n} 500 {ms}ms upstream timeout"],
    }
    client = make_client(minimal | {"logs": [stream]})
    client.post("/admin/scenarios/minimal/activate", json={"anchor": ANCHOR})
    return client


def read_all(
    client: TestClient, direction: str, query: str = '{service="api"}'
) -> list[list[Line]]:
    pages: list[list[Line]] = []
    cursor: str | None = None
    while True:
        params = {"query": query, "limit": 200, "direction": direction}
        body = client.get(LOKI, params=params | ({"cursor": cursor} if cursor else {})).json()
        pages.append(
            [(ns, line) for stream in body["data"]["result"] for ns, line in stream["values"]]
        )
        cursor = body["next_cursor"]
        if cursor is None:
            return pages


@pytest.mark.parametrize(
    ("query", "matchers", "filters"),
    [
        ('{service="api"}', [("service", "=", "api")], []),
        (
            '{service=~"a.*", level!="debug", zone!~"eu-.*"}',
            [("service", "=~", "a.*"), ("level", "!=", "debug"), ("zone", "!~", "eu-.*")],
            [],
        ),
        (
            '{service="api"} |= "timeout" != "health" |~ "5\\\\d\\\\d" !~ `GET /ping`',
            [("service", "=", "api")],
            [("|=", "timeout"), ("!=", "health"), ("|~", "5\\d\\d"), ("!~", "GET /ping")],
        ),
        ('{service="api"}|="say \\"hi\\""', [("service", "=", "api")], [("|=", 'say "hi"')]),
    ],
)
def test_log_queries_parse(
    query: str, matchers: list[tuple[str, str, str]], filters: list[tuple[str, str]]
) -> None:
    parsed = parse_log_query(query)
    assert [(m.label, m.op, m.value) for m in parsed.matchers] == matchers
    assert [(f.op, f.value) for f in parsed.filters] == filters


@pytest.mark.parametrize(
    "query",
    [
        '"timeout"',
        "{}",
        '{service=~".*"}',
        '{service="api"} |> "x"',
        '{service="api"} |= "open',
        '{service="api"} |~ "("',
        'service="api"',
        '{service="api"} "x"',
    ],
)
def test_other_log_queries_are_rejected(query: str) -> None:
    with pytest.raises(QueryError):
        parse_log_query(query)


def test_line_filters_test_text_and_search_regexes() -> None:
    query = parse_log_query('{a="b"} |= "500" !~ "health"')
    assert all(f.keeps("GET /x 500 3ms") for f in query.filters)
    assert not all(f.keeps("GET /health 500 3ms") for f in query.filters)
    # |~ searches anywhere in the line, unlike label regexes.
    assert parse_log_query('{a="b"} |~ "time.ut"').filters[0].keeps("upstream timeout")


@pytest.mark.parametrize("direction", ["forward", "backward"])
def test_pages_cover_5000_lines_once_in_either_direction(flood: TestClient, direction: str) -> None:
    pages = read_all(flood, direction)
    lines = [line for page in pages for line in page]
    assert [len(page) for page in pages] == [200] * 25
    assert len(lines) == len(set(lines)) == 5000
    times = [int(ns) for ns, _ in lines]
    assert times == sorted(times, reverse=direction == "backward")
    assert len(set(times)) == 5000
    assert ANCHOR_NS - 50 * 60 * 10**9 <= min(times) < max(times) < ANCHOR_NS


def test_filters_apply_before_paging(flood: TestClient) -> None:
    every = {line for page in read_all(flood, "forward") for line in page}
    pages = read_all(flood, "forward", '{service="api"} |= " 500 "')
    errors = [line for page in pages for line in page]
    assert {line for line in every if " 500 " in line[1]} == set(errors)
    assert all(len(page) == 200 for page in pages[:-1])
    assert 0 < len(pages[-1]) <= 200


def test_start_and_end_bound_the_lines(flood: TestClient) -> None:
    def count(start: str, end: str) -> int:
        params = {"query": '{service="api"}', "limit": 200, "start": start, "end": end}
        body = flood.get(LOKI, params=params).json()
        return sum(len(s["values"]) for s in body["data"]["result"])

    minute_ns = 60 * 10**9
    first = str(ANCHOR_NS - minute_ns)
    # One minute holds 100 lines, whether the bounds are nanoseconds, seconds or RFC 3339.
    assert count(first, str(ANCHOR_NS)) == 100
    assert count(str(ANCHOR_NS // 10**9 - 60), str(ANCHOR_NS // 10**9)) == 100
    assert count("2026-10-15T11:59:00Z", ANCHOR) == 100


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"limit": 201}, "limit must be at most 200"),
        ({"limit": 0}, "at least 1"),
        ({"direction": "sideways"}, "direction"),
        ({"cursor": "garbage!"}, "invalid cursor"),
        ({"cursor": base64.urlsafe_b64encode(b'{"a": 1}').decode()}, "invalid cursor"),
        ({"query": "nope"}, "stream selector"),
        ({"start": "2026-10-15T12:00:00Z", "end": "2026-10-15T11:00:00Z"}, "before start"),
    ],
)
def test_bad_log_requests_get_400(flood: TestClient, params: dict[str, Any], message: str) -> None:
    response = flood.get(LOKI, params={"query": '{service="api"}'} | params)
    assert response.status_code == 400
    assert message in response.json()["error"]


def test_a_cursor_reads_on_only_in_its_own_direction(flood: TestClient) -> None:
    query = {"query": '{service="api"}', "limit": 10, "direction": "forward"}
    cursor = flood.get(LOKI, params=query).json()["next_cursor"]
    backward = flood.get(LOKI, params=query | {"direction": "backward", "cursor": cursor})
    assert backward.status_code == 400
    assert "other direction" in backward.json()["error"]


def test_pages_group_lines_by_stream_in_page_order(shipped: TestClient) -> None:
    shipped.post("/admin/scenarios/bad-deploy-1/activate", json={"anchor": ANCHOR})
    params = {"query": '{service="checkout"}', "limit": 50, "start": "2026-10-15T11:58:00Z"}
    body = shipped.get(LOKI, params=params | {"end": ANCHOR}).json()
    result = body["data"]["result"]
    assert {s["stream"]["level"] for s in result} == {"info", "error"}
    for stream in result:
        times = [int(ns) for ns, _ in stream["values"]]
        assert times == sorted(times, reverse=True)
