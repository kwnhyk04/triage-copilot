"""Live tenants and the kill switches against a scratch Postgres: a live tenant's investigation
never reaches the sim tools and parks while the live switch is off, and every switch takes effect
at the next model turn."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine, select
from test_workers_int import SCRIPT, TENANT, TOOLS, Counting, Tools, make_job, new_request, recorded

from agent.db import Investigation
from agent.llm import spent
from agent.model import ScriptedModel, is_model_turn
from agent.settings import Role
from agent.switches import Switch, is_on, run, set_switch

if TYPE_CHECKING:
    from conftest import ScratchDb

pytestmark = pytest.mark.integration

LIVE = UUID("0b6f2d1e-3c4a-4f5e-8a9b-1c2d3e4f5a6b")
APPS = {LIVE: "freight-audit"}


@pytest.fixture
def engine(scratch_db: "ScratchDb") -> Iterator[Engine]:
    scratch_db.migrate_agent("head")
    engine = create_engine(scratch_db.agent_url)
    yield engine
    engine.dispose()


def switch(engine: Engine, name: Switch, on: bool) -> None:
    with engine.begin() as conn:
        set_switch(conn, name, on)


def test_live_starts_off_claude_on_and_the_cli_flips_them(engine: Engine) -> None:
    with engine.connect() as conn:
        assert (is_on(conn, "live"), is_on(conn, "claude")) == (False, True)

    assert run(engine, ["set", "live", "on"]) == "live: on"
    assert run(engine, ["show"]) == "live: on\nclaude: on"
    with pytest.raises(SystemExit):
        run(engine, ["set", "planner", "on"])
    with engine.connect() as conn:
        assert is_on(conn, "live")


def test_a_live_tenant_parks_while_live_is_off_and_no_model_is_asked(engine: Engine) -> None:
    model = Counting(ScriptedModel(SCRIPT))
    tools = Tools()
    job = make_job(engine, tools.all(), model, live_tenants=APPS)
    request = new_request(LIVE)

    assert asyncio.run(job.run(request, asyncio.Event())) == "parked"

    assert (model.calls, tools.calls) == (0, [])
    inv = request.investigation_id
    with engine.connect() as conn:
        waiting = conn.scalar(select(Investigation.waiting_reason).where(Investigation.id == inv))
    assert waiting == "budget"
    with engine.connect() as conn:
        assert not job.resumable(conn, inv, "budget", LIVE, new=True)
    # A sim tenant is not held back by the live switch.
    assert asyncio.run(job.run(new_request(TENANT), asyncio.Event())) == "done"
    switch(engine, "live", True)
    with engine.connect() as conn:
        assert job.resumable(conn, inv, "budget", LIVE, new=True)


def test_a_live_tenant_never_reaches_the_sim_tools(engine: Engine) -> None:
    switch(engine, "live", True)
    sim = Tools()
    job = make_job(engine, sim.all(), live_tenants=APPS)
    request = new_request(LIVE)

    assert asyncio.run(job.run(request, asyncio.Event())) == "done"

    assert sim.calls == []
    steps = [s for _, s in recorded(engine, request.investigation_id)]
    failed = [s.summary for s in steps if s.result is None and not is_model_turn(s)]
    assert failed[:3] == [f"{name} failed: ToolInputError: unknown tool {name}" for name in TOOLS]


def test_turning_live_off_parks_a_live_investigation_at_its_next_turn(engine: Engine) -> None:
    switch(engine, "live", True)

    async def turn_live_off() -> None:
        switch(engine, "live", False)

    live, sim = Tools(), Tools()
    tools = live.all(query_metrics=turn_live_off)
    job = make_job(engine, sim.all(), live_tenants=APPS, live_tools=tools)

    assert asyncio.run(job.run(new_request(LIVE), asyncio.Event())) == "parked"

    assert (live.calls, sim.calls) == (["query_metrics"], [])


def test_turning_claude_off_skips_every_claude_model_with_the_reason(engine: Engine) -> None:
    later = datetime.now(UTC).date() + timedelta(days=60)
    haiku = Role("fallback", "anthropic", "claude-haiku-5-5", None, 0, 0, until=later)
    gemini = Role("primary", "gemini", "gemini-3.5-flash-lite", None, 0, 0)

    switch(engine, "claude", False)
    with engine.connect() as conn:
        assert "switched off" in (spent(conn, haiku) or "")
        assert spent(conn, gemini) is None
    switch(engine, "claude", True)
    with engine.connect() as conn:
        assert spent(conn, haiku) is None
