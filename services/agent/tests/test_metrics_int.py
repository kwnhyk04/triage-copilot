"""The agent's GET /metrics against a scratch Postgres, which Prometheus reads for the investigation
and Claude rules (infra/prometheus-alerts.yml): the investigation messages that failed in the last
hour, and, while live triage's Claude takes no call, why.
"""

import re
import secrets
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, delete, insert
from test_live_int import claude_spend, switch

from agent.db import DeadLetter, Investigation, ModelCall
from agent.llm import Limiter
from agent.main import create_app
from agent.metrics import render
from agent.settings import Role, Settings

if TYPE_CHECKING:
    from conftest import ScratchDb

pytestmark = pytest.mark.integration

LATER = datetime.now(UTC).date() + timedelta(days=60)
# Live triage's Claude as production runs it: $0.40 a UTC day, $15 a month.
TRIAGE = Role(
    "primary",
    "anthropic",
    "claude-haiku-5-5",
    None,
    0,
    0,
    until=LATER,
    daily_usd=0.40,
    monthly_usd=15.0,
)
SAMPLE = re.compile(r"^(\w+(?:\{[^}]*\})?) (\S+)$")
DLQ = 'tc_investigations_failed_recent{kind="dlq"}'
UNREADABLE = 'tc_investigations_failed_recent{kind="unreadable"}'


@pytest.fixture
def engine(scratch_db: "ScratchDb") -> Iterator[Engine]:
    scratch_db.migrate_agent("head")
    engine = create_engine(scratch_db.agent_url)
    yield engine
    engine.dispose()


def samples(text: str) -> dict[str, float]:
    """Each sample line of Prometheus's text format, by its name and labels."""
    found = [SAMPLE.match(line) for line in text.splitlines() if not line.startswith("#")]
    assert all(found), text
    return {match[1]: float(match[2]) for match in found if match}


def metrics(
    engine: Engine, claude: Role | None = TRIAGE, limiter: Limiter | None = None
) -> dict[str, float]:
    with engine.connect() as conn:
        return samples(render(conn, claude, limiter or Limiter(None)))


def stopped(
    engine: Engine, claude: Role | None = TRIAGE, limiter: Limiter | None = None
) -> dict[str, float]:
    found = metrics(engine, claude, limiter)
    return {name: value for name, value in found.items() if name.startswith("tc_claude_stopped")}


def failed(engine: Engine, age: timedelta, status: str = "failed") -> None:
    with engine.begin() as conn:
        updated = datetime.now(UTC) - age
        conn.execute(insert(Investigation).values(id=uuid4(), status=status, updated_at=updated))


def unreadable(engine: Engine, age: timedelta) -> None:
    with engine.begin() as conn:
        row = {"message_id": "m", "group_id": "g", "body": "{}", "error": "invalid"}
        conn.execute(insert(DeadLetter).values(**row, created_at=datetime.now(UTC) - age))


def test_counts_the_investigation_messages_that_failed_in_the_last_hour(engine: Engine) -> None:
    assert {name: metrics(engine)[name] for name in (DLQ, UNREADABLE)} == {DLQ: 0, UNREADABLE: 0}

    failed(engine, timedelta(minutes=5))
    failed(engine, timedelta(minutes=50))
    failed(engine, timedelta(hours=2))
    failed(engine, timedelta(minutes=5), status="resolved")
    unreadable(engine, timedelta(minutes=1))
    unreadable(engine, timedelta(hours=3))

    found = metrics(engine)
    assert {name: found[name] for name in (DLQ, UNREADABLE)} == {DLQ: 2, UNREADABLE: 1}


def test_says_nothing_of_claude_while_it_takes_calls_or_is_not_set_up(engine: Engine) -> None:
    assert stopped(engine) == {}
    # No ANTHROPIC_API_KEY (or AI off): live triage has no Claude to report on.
    switch(engine, "claude", False)
    assert stopped(engine, claude=None) == {}


def test_names_why_claude_takes_no_call(engine: Engine) -> None:
    switch(engine, "claude", False)
    assert stopped(engine) == {'tc_claude_stopped{reason="switched_off"}': 1}
    switch(engine, "claude", True)

    # $0.39 of today's $0.40: the largest call's worst case no longer fits.
    claude_spend(engine, "claude-haiku-5-5", 780_000)
    assert stopped(engine) == {'tc_claude_stopped{reason="daily_cap"}': 1}
    with engine.begin() as conn:
        conn.execute(delete(ModelCall))
    assert stopped(engine) == {}

    limiter = Limiter(None)
    limiter.reject("anthropic", "401 invalid x-api-key")
    assert stopped(engine, limiter=limiter) == {'tc_claude_stopped{reason="key_rejected"}': 1}

    # The credits' planned end, which the rules leave alone.
    ended = replace(TRIAGE, until=datetime.now(UTC).date())
    assert stopped(engine, claude=ended) == {'tc_claude_stopped{reason="credits_ended"}': 1}


def test_the_route_serves_them_in_prometheus_s_text_format(scratch_db: "ScratchDb") -> None:
    scratch_db.migrate_agent("head")
    settings = Settings(
        agent_database_url=scratch_db.agent_url,
        sqs_endpoint_url="http://127.0.0.1:1",
        agent_service_token=SecretStr(secrets.token_urlsafe(32)),
    )
    # No lifespan: the consumer never starts, and the route reads Postgres alone.
    response = TestClient(create_app(settings)).get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert samples(response.text) == {DLQ: 0, UNREADABLE: 0}
