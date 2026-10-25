"""Live data retention against a scratch Postgres: 30 days after a live tenant's steps, tool calls
and unsent step messages were stored they are gone; its investigation, report and model calls stay,
and the sim's investigations, or a live one that has not finished, keep everything."""

import asyncio
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine, func, select, update
from test_live_int import APPS, LIVE, VERDICT, snapshot_tool, switch
from test_workers_int import TENANT, Tools, make_job, new_request

from agent.db import Investigation, InvestigationStep, ModelCall, Outbox, ToolCall
from agent.model import ScriptedModel, ToolRequest
from agent.outbox import REPORT, STEP
from agent.partitions import ensure_partitions
from agent.retention import maintain_retention, purge

if TYPE_CHECKING:
    from conftest import ScratchDb

pytestmark = pytest.mark.integration

READ = ToolRequest(tool="read_snapshot", args={"alert": 1}, reason="Read the snapshot.")
NOTHING = {"steps": 0, "tool_calls": 0, "outbox": 0}


@pytest.fixture
def engine(scratch_db: "ScratchDb") -> Iterator[Engine]:
    scratch_db.migrate_agent("head")
    engine = create_engine(scratch_db.agent_url)
    today = datetime.now(UTC).date()
    with engine.begin() as conn:
        # Steps moved up to 40 days back need their month's partition.
        ensure_partitions(conn, today - timedelta(days=40), today)
    switch(engine, "live", True)
    yield engine
    engine.dispose()


def investigate(engine: Engine, tenant: UUID) -> UUID:
    """One investigation to its report: a live one reads its snapshot, a sim one runs SCRIPT."""
    job = make_job(
        engine,
        Tools().all(),
        live_tenants=APPS,
        live_tools=snapshot_tool([]),
        live_model=ScriptedModel([READ, VERDICT]),
    )
    request = new_request(tenant)
    assert asyncio.run(job.run(request, asyncio.Event())) == "done"
    return request.investigation_id


def age(engine: Engine, inv: UUID, days: int) -> None:
    """Moves everything the investigation stored this many days back."""
    back = timedelta(days=days)
    s, c, m, o, i = InvestigationStep, ToolCall, ModelCall, Outbox, Investigation
    with engine.begin() as conn:
        conn.execute(
            update(s).where(s.investigation_id == inv).values(created_at=s.created_at - back)
        )
        conn.execute(
            update(c).where(c.investigation_id == inv).values(created_at=c.created_at - back)
        )
        conn.execute(
            update(m).where(m.investigation_id == inv).values(created_at=m.created_at - back)
        )
        conn.execute(update(o).where(o.group_id == str(inv)).values(created_at=o.created_at - back))
        conn.execute(
            update(i)
            .where(i.id == inv)
            .values(created_at=i.created_at - back, updated_at=i.updated_at - back)
        )


def kept(engine: Engine, inv: UUID) -> dict[str, int]:
    """What the investigation still holds, by table."""
    s, c, m, o, i = InvestigationStep, ToolCall, ModelCall, Outbox, Investigation
    mine = o.group_id == str(inv)
    queries = {
        "steps": select(func.count()).where(s.investigation_id == inv),
        "tool_calls": select(func.count()).where(c.investigation_id == inv),
        "outbox": select(func.count()).where(mine, o.message_type == STEP),
        "report_message": select(func.count()).where(mine, o.message_type == REPORT),
        "model_calls": select(func.count()).where(m.investigation_id == inv),
        "report": select(func.count()).where(i.id == inv, i.final_report.is_not(None)),
    }
    with engine.connect() as conn:
        return {name: conn.execute(query).scalar_one() for name, query in queries.items()}


def test_live_steps_tool_calls_and_step_messages_go_after_30_days_and_the_report_stays(
    engine: Engine,
) -> None:
    old, recent, sim, parked = (investigate(engine, t) for t in (LIVE, LIVE, TENANT, LIVE))
    with engine.begin() as conn:
        # Waiting for its next turn, which replays its steps: it keeps them until it finishes.
        query = update(Investigation).where(Investigation.id == parked)
        conn.execute(query.values(status="queued"))
    for inv, days in ((old, 31), (recent, 29), (sim, 31), (parked, 31)):
        age(engine, inv, days)
    before = {inv: kept(engine, inv) for inv in (old, recent, sim, parked)}
    assert min(before[old]["steps"], before[old]["tool_calls"], before[old]["outbox"]) > 0

    with engine.begin() as conn:
        purged = purge(conn, set(APPS))

    assert purged == {key: before[old][key] for key in NOTHING}
    # The report, its message and the model calls stay.
    assert kept(engine, old) == before[old] | NOTHING
    assert kept(engine, old)["report"] == kept(engine, old)["report_message"] == 1
    for inv in (recent, sim, parked):
        assert kept(engine, inv) == before[inv]
    # Nothing older remains, so a second pass purges nothing.
    with engine.begin() as conn:
        assert purge(conn, set(APPS)) == NOTHING
        assert purge(conn, set()) == NOTHING


def test_every_investigation_records_its_tenant_when_it_starts(engine: Engine) -> None:
    live, sim = investigate(engine, LIVE), investigate(engine, TENANT)
    i = Investigation
    query = select(i.id, i.tenant_id).where(i.id.in_([live, sim]))
    with engine.connect() as conn:
        tenants = {row.id: row.tenant_id for row in conn.execute(query)}
    assert tenants == {live: LIVE, sim: TENANT}


def test_the_agent_purges_when_it_starts_then_waits_for_the_next_day(engine: Engine) -> None:
    old = investigate(engine, LIVE)
    age(engine, old, 40)
    stop = threading.Event()
    thread = threading.Thread(target=maintain_retention, args=(engine, set(APPS), stop))
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while kept(engine, old)["steps"] and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert kept(engine, old)["steps"] == 0
