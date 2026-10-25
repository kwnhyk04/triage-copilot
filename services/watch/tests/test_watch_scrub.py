import json

import pytest
from watch_fixtures import CANARIES, app_lines, log_lines

from watch.catalog import CATALOGS, AppName
from watch.scrub import FIELD_CHARS, LINE_CHARS, mask, scrub_lines

APPS = sorted(CATALOGS)


@pytest.mark.parametrize("app", APPS)
def test_no_canary_survives_the_app_logs(app: AppName) -> None:
    lines = app_lines(app)
    raw = "\n".join(lines)
    assert [name for name, canary in CANARIES.items() if canary not in raw] == []
    scrubbed = "\n".join(scrub_lines(CATALOGS[app], lines))
    assert [name for name, canary in CANARIES.items() if canary in scrubbed] == []


@pytest.mark.parametrize(
    ("app", "kept"),
    [
        (
            "freight-audit",
            [
                '"alert":"extract_paused"',
                '"alert":"claude_switched_off"',
                '"msg":"readiness check failed"',
                "connect ECONNREFUSED postgres://[credentials]@postgres:5432/freightaudit",
                '"type":"HTTPError"',
                '"statusCode":200',
                "Error: unexpected token [key] in the config",
            ],
        ),
        (
            "filing-lens",
            [
                '"event":"claude is off for the rest of the credit period"',
                '"event":"readiness check failed"',
                '"event":"task failed for good"',
                '"exception":"RuntimeError: Anthropic refused [key]"',
                "ImportError: cannot import name 'answer' from 'filinglens_api'"
                " with Bearer [token]",
                "GET /v1/search?[query] HTTP/1.1",
            ],
        ),
    ],
)
def test_what_triage_needs_is_kept(app: AppName, kept: list[str]) -> None:
    scrubbed = "\n".join(scrub_lines(CATALOGS[app], app_lines(app)))
    assert [text for text in kept if text not in scrubbed] == []


def test_fields_off_the_allowlist_are_dropped() -> None:
    request = log_lines("freight-audit-api.log")[1]
    [line] = scrub_lines(CATALOGS["freight-audit"], [request])
    entry = json.loads(line)
    assert sorted(entry) == ["level", "msg", "req", "reqId", "time"]
    assert entry["req"] == {"method": "GET", "url": "/v1/invoices?[query]"}


def test_a_traceback_keeps_the_exception_type_and_its_last_line() -> None:
    scrubbed = scrub_lines(CATALOGS["filing-lens"], log_lines("filing-lens-api.log"))
    assert json.loads(scrubbed[2])["exception"] == "RuntimeError: Anthropic refused [key]"
    # The plain-text traceback at the end loses its header and frames, not its last line.
    assert scrubbed[-1].startswith("ImportError: cannot import name 'answer'")
    assert not [line for line in scrubbed if line.startswith(("Traceback", " "))]


def test_a_node_stack_keeps_only_its_message() -> None:
    scrubbed = scrub_lines(CATALOGS["freight-audit"], log_lines("freight-audit-api.log"))
    assert "Error: unexpected token [key] in the config" in scrubbed
    assert not [line for line in scrubbed if line.lstrip().startswith("at ")]


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("key sk-ant-api99-canarycanarycanary01 end", "key [key] end"),
        ("AIzaCanarycanarycanarycanary02", "[key]"),
        ("nvapi-canarycanarycanarycanary03", "[key]"),
        ("gsk_canarycanarycanarycanary04", "[key]"),
        ("ghp_canarycanarycanary05", "[key]"),
        ("github_pat_canarycanary06", "[key]"),
        ("t eyJjYW5hcnkiOiIx.eyJjYW5hcnkiOiIy.Y2FuYXJ5MDcw", "t [jwt]"),
        ("Authorization: Bearer canarycanarybearer08", "Authorization: Bearer [token]"),
        ("postgres://u:canarycanarypass09@db:5432/x", "postgres://[credentials]@db:5432/x"),
        ("GET /v1/x?key=canarycanaryquery10 HTTP/1.1", "GET /v1/x?[query] HTTP/1.1"),
        ("https://h.example/p?a=1&b=2", "https://h.example/p?[query]"),
        ("mail canary.person@example.com now", "mail [email] now"),
        ("https://filinglens.canary-tailnet.ts.net/hook", "https://[tailnet]/hook"),
        ("-----BEGIN PRIVATE KEY-----\nY2Fu\n-----END PRIVATE KEY----- x", "[pem] x"),
        ("password=canarycanarycanary16", "password=[redacted]"),
        ("secret: canarycanarycanary16", "secret: [redacted]"),
        ("is it ready? yes", "is it ready? yes"),
    ],
)
def test_mask(text: str, masked: str) -> None:
    assert mask(text) == masked


def test_fields_and_lines_are_truncated() -> None:
    catalog = CATALOGS["filing-lens"]
    [line] = scrub_lines(catalog, [json.dumps({"event": "x" * 5000, "level": "error"})])
    assert len(json.loads(line)["event"]) <= FIELD_CHARS
    [plain] = scrub_lines(catalog, ["y" * 5000])
    assert len(plain) <= LINE_CHARS


def test_a_line_that_is_not_an_object_is_masked_as_text() -> None:
    assert scrub_lines(CATALOGS["filing-lens"], ['["Bearer canarycanarybearer08"]']) == [
        '["Bearer [token]"]'
    ]
