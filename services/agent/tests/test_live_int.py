"""Live tenants and the kill switches against a scratch Postgres: a live tenant's investigation
never reaches the sim tools and parks while the live switch is off, and every switch takes effect
at the next model turn."""

import asyncio
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

import pytest
from anthropic_errors import INVALID_KEY
from sqlalchemy import Connection, Engine, create_engine, insert, select, update
from test_llm import REQUEST, anthropic
from test_workers_int import SCRIPT, TENANT, TOOLS, Counting, Tools, make_job, new_request, recorded

from agent.db import AiUsage, Investigation, ModelCall, Outbox
from agent.job import PROMPT_SHA, UTC_TODAY
from agent.live import LIVE_PROMPT_SHA, LIVE_SPECS, LIVE_SYSTEM, LiveReportDraft
from agent.llm import Binding, Limiter, Router, spent
from agent.model import (
    HypothesisDraft,
    KeyRejected,
    Message,
    ModelTurn,
    ReportDraft,
    ScriptedModel,
    ToolRequest,
    ToolSpec,
    TurnRequest,
    Usage,
    is_model_turn,
)
from agent.providers import Provider, ProviderTurn
from agent.settings import Role
from agent.switches import Switch, is_on, run, set_switch
from agent.tools import Tool, ToolContext
from py_clients.generated.messages.report_recorded_v2_schema import ReportRecordedV2

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


def test_live_and_the_planner_start_off_claude_on_and_the_cli_flips_them(engine: Engine) -> None:
    with engine.connect() as conn:
        assert [is_on(conn, name) for name in ("live", "claude", "planner")] == [False, True, False]

    assert run(engine, ["set", "live", "on"]) == "live: on"
    assert run(engine, ["show"]) == "live: on\nclaude: on\nplanner: off"
    assert run(engine, ["set", "planner", "on"]) == "planner: on"
    with pytest.raises(SystemExit):
        run(engine, ["set", "coder", "on"])
    with engine.connect() as conn:
        assert is_on(conn, "live") and is_on(conn, "planner")


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


LATER = datetime.now(UTC).date() + timedelta(days=60)
# Live triage's Claude, capped at $0.40 a UTC day, and the free chain after it.
TRIAGE = Role("primary", "anthropic", "claude-haiku-5-5", None, 0, 0, until=LATER, daily_usd=0.40)
GEMINI = Role("primary", "gemini", "gemini-3.5-flash-lite", None, 0, 0)
NIM = Role("fallback", "nvidia", "deepseek-ai/deepseek-v4.1-flash", None, 0, 0)
SMALL = Role("small", "gemini", "gemini-3.1-flash-lite", None, 0, 0)
VERDICT = LiveReportDraft(
    summary="freight-audit's api fails its readiness check: postgres restarts.",
    hypotheses=[
        HypothesisDraft(
            root_cause_class="database",
            service="postgres",
            confidence=0.7,
            summary="The api's readiness check fails on postgres, whose container restarts.",
            evidence_ids=[],
        )
    ],
    verdict="real",
)


class Recording:
    """Plays a script and keeps every request the job made."""

    def __init__(self, actions: Sequence[ToolRequest | ReportDraft]) -> None:
        self.model = ScriptedModel(actions)
        self.requests: list[TurnRequest] = []

    async def turn(self, request: TurnRequest) -> ModelTurn:
        self.requests.append(request)
        return await self.model.turn(request)

    def available(self, conn: Connection) -> bool:
        return True


class Reporting:
    """A provider that answers every turn with live triage's report."""

    name = "gemini"
    model = "gemini-3.5-flash-lite"

    async def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        allowed: tuple[str, ...] | None,
        max_output_tokens: int,
    ) -> ProviderTurn:
        return ProviderTurn(
            "submit_report", VERDICT.model_dump(), None, Usage(input=900, output=60)
        )

    async def complete(self, system: str, text: str, max_output_tokens: int) -> tuple[str, Usage]:
        raise AssertionError("live triage summarizes nothing")


def snapshot_tool(seen: list[ToolContext]) -> dict[str, Tool]:
    async def read_snapshot(ctx: ToolContext, args: dict[str, Any]) -> str:
        seen.append(ctx)
        return "Snapshot of freight-audit on the prod stack: postgres restarting."

    return {"read_snapshot": read_snapshot}


def report_message(engine: Engine, inv: UUID) -> ReportRecordedV2:
    query = select(Outbox.body).where(
        Outbox.message_type == "report-recorded", Outbox.group_id == str(inv)
    )
    with engine.connect() as conn:
        return ReportRecordedV2.model_validate(conn.execute(query).scalar_one())


def prompt_shas(engine: Engine, inv: UUID) -> set[str | None]:
    query = select(ModelCall.prompt_sha).where(ModelCall.investigation_id == inv)
    with engine.connect() as conn:
        return set(conn.execute(query).scalars())


def test_a_live_triage_runs_its_own_prompt_and_tools_and_reports_a_verdict(
    engine: Engine,
) -> None:
    switch(engine, "live", True)
    read = ToolRequest(tool="read_snapshot", args={"alert": 1}, reason="Read the snapshot.")
    model, sim = Recording([read, VERDICT]), Recording(SCRIPT)
    seen: list[ToolContext] = []
    job = make_job(
        engine,
        Tools().all(),
        sim,
        live_tenants=APPS,
        live_tools=snapshot_tool(seen),
        live_model=model,
        live_token_cap=350_000,
    )
    live, other = new_request(LIVE), new_request(TENANT)

    assert asyncio.run(job.run(live, asyncio.Event())) == "done"
    assert asyncio.run(job.run(other, asyncio.Event())) == "done"

    first = model.requests[0]
    assert (first.system, first.prompt_sha, first.report) == (
        LIVE_SYSTEM,
        LIVE_PROMPT_SHA,
        LiveReportDraft,
    )
    assert [spec.name for spec in first.tools] == [spec.name for spec in LIVE_SPECS]
    # Live triage runs under its own token cap, the sim under the eval's.
    assert (first.budget.remaining, sim.requests[0].budget.remaining) == (350_000, 150_000)
    assert [(ctx.app, ctx.prompt_sha) for ctx in seen] == [("freight-audit", LIVE_PROMPT_SHA)]
    assert prompt_shas(engine, live.investigation_id) == {LIVE_PROMPT_SHA}
    assert prompt_shas(engine, other.investigation_id) == {PROMPT_SHA}
    message = report_message(engine, live.investigation_id)
    assert (message.schema_version, message.verdict) == (2, "real")
    assert [m.model_dump(mode="json") for m in message.models] == [
        {
            "part": "triage",
            "provider": "scripted",
            "model": "scripted",
            "calls": 2,
            "failed": 0,
            "reason": None,
        }
    ]
    assert message.report.hypotheses[0].service == "postgres"
    # A simulated incident has no verdict, and its models are listed too.
    sim_message = report_message(engine, other.investigation_id)
    assert (sim_message.verdict, [m.calls for m in sim_message.models]) == (None, [4])


def test_live_triage_runs_on_its_own_daily_budget(engine: Engine) -> None:
    switch(engine, "live", True)
    with engine.begin() as conn:
        # Past the sim's budget of 1,000,000 tokens, far under live triage's.
        conn.execute(insert(AiUsage).values(day=UTC_TODAY, tokens=2_000_000))
    job = make_job(
        engine,
        Tools().all(),
        live_tenants=APPS,
        live_tools=snapshot_tool([]),
        live_model=ScriptedModel([VERDICT]),
        live_daily_token_budget=25_000_000,
    )

    assert asyncio.run(job.run(new_request(TENANT), asyncio.Event())) == "parked"
    assert asyncio.run(job.run(new_request(LIVE), asyncio.Event())) == "done"


def live_router(engine: Engine) -> Router:
    """Live triage's chain on the real limiter and spent(): Claude, then Gemini, then NIM."""

    def is_spent(role: Role, worst_usd: float) -> str | None:
        with engine.connect() as conn:
            return spent(conn, role, worst_usd)

    unused = cast("Provider", object())
    return Router(
        Binding(GEMINI, cast("Provider", Reporting())),
        Binding(NIM, unused),
        Binding(SMALL, unused),
        fallback_on_rate_limit=True,
        limiter=Limiter(is_spent),
        claude=Binding(TRIAGE, unused),
    )


def test_a_skipped_claude_is_named_in_the_report_and_back_once_its_reason_ends(
    engine: Engine,
) -> None:
    switch(engine, "live", True)
    switch(engine, "claude", False)
    job = make_job(
        engine,
        Tools().all(),
        live_tenants=APPS,
        live_tools=snapshot_tool([]),
        live_model=live_router(engine),
    )
    request = new_request(LIVE)

    assert asyncio.run(job.run(request, asyncio.Event())) == "done"

    message = report_message(engine, request.investigation_id)
    skipped = "claude-haiku-5-5 skipped: Claude is switched off (agent.switches)"
    assert [m.model_dump(mode="json") for m in message.models] == [
        {
            "part": "triage",
            "provider": "anthropic",
            "model": "claude-haiku-5-5",
            "calls": 0,
            "failed": 1,
            "reason": skipped,
        },
        {
            "part": "triage",
            "provider": "gemini",
            "model": "gemini-3.5-flash-lite",
            "calls": 1,
            "failed": 0,
            "reason": None,
        },
    ]
    # A call the agent skipped stops nothing once its reason ends: Claude answers at once again.
    switch(engine, "claude", True)
    with engine.connect() as conn:
        assert spent(conn, TRIAGE) is None


def claude_spend(engine: Engine, model: str, output_tokens: int, role: str = "primary") -> None:
    """An Anthropic call today that produced this many output tokens and read no input."""
    inv = uuid4()
    with engine.begin() as conn:
        conn.execute(insert(Investigation).values(id=inv))
        conn.execute(
            insert(ModelCall).values(
                investigation_id=inv,
                role=role,
                provider="anthropic",
                model=model,
                outcome="ok",
                input_tokens=0,
                output_tokens=output_tokens,
                cached_tokens=0,
                thought_tokens=0,
                latency_ms=900,
            )
        )


def test_the_triage_cap_counts_today_s_claude_spend_and_each_call_s_worst_case(
    engine: Engine,
) -> None:
    claude_spend(engine, "claude-haiku-5-5", 780_000)  # $0.39 at $0.50 per million out
    with engine.connect() as conn:
        assert "triage cap of $0.40" in (spent(conn, TRIAGE, 0.02) or "")
        assert spent(conn, TRIAGE, 0.005) is None
        # Only roles with a cap are held to it.
        assert spent(conn, replace(TRIAGE, daily_usd=None), 1.0) is None
    with engine.begin() as conn:
        conn.execute(update(ModelCall).values(created_at=ModelCall.created_at - timedelta(days=1)))
    with engine.connect() as conn:
        # Yesterday's spend (UTC) no longer counts.
        assert spent(conn, TRIAGE, 0.02) is None
    claude_spend(engine, "claude-sonnet-5-5", 1)
    with engine.connect() as conn:
        # A model without a price cannot be checked against the cap, so Claude waits.
        assert "claude-sonnet-5-5" in (spent(conn, TRIAGE, 0.0) or "")


# A Claude role with TC's monthly cap and no daily one, as an eval's Claude has.
MONTHLY = replace(TRIAGE, daily_usd=None, monthly_usd=15.0)


def test_tc_s_monthly_claude_cap_counts_this_month_s_claude_spend(engine: Engine) -> None:
    claude_spend(engine, "claude-haiku-5-5", 29_980_000)  # $14.99 at $0.50 per million out
    with engine.connect() as conn:
        assert "TC's monthly Claude cap of $15.00" in (spent(conn, MONTHLY, 0.02) or "")
        assert spent(conn, MONTHLY, 0.005) is None
        assert spent(conn, replace(MONTHLY, monthly_usd=None), 1.0) is None
    with engine.begin() as conn:
        # 32 days back is always before this month began (UTC).
        conn.execute(update(ModelCall).values(created_at=ModelCall.created_at - timedelta(days=32)))
    with engine.connect() as conn:
        assert spent(conn, MONTHLY, 0.02) is None


PLANNER = Role(
    "planner",
    "anthropic",
    "claude-opus-5-5",
    None,
    0,
    0,
    until=LATER,
    daily_usd=1.00,
    monthly_usd=15.0,
    effort="medium",
)


def test_the_planner_cap_counts_today_s_plans_only_and_the_monthly_cap_counts_both(
    engine: Engine,
) -> None:
    claude_spend(engine, "claude-opus-5-5", 30_000, role="planner")  # $0.60 at $20 per million out
    with engine.connect() as conn:
        assert "planner cap of $1.00" in (spent(conn, PLANNER, 0.48) or "")
        assert spent(conn, PLANNER, 0.39) is None
        # Plans never use up the triage cap.
        assert spent(conn, TRIAGE, 0.39) is None
    claude_spend(engine, "claude-haiku-5-5", 780_000)  # $0.39 of triage
    with engine.connect() as conn:
        # Nor does triage use up the planner's.
        assert spent(conn, PLANNER, 0.39) is None
        assert "triage cap of $0.40" in (spent(conn, TRIAGE, 0.02) or "")
    claude_spend(engine, "claude-opus-5-5", 699_500, role="planner")  # $13.99 more of plans
    with engine.connect() as conn:
        # TC's $15 a month counts plans and triage alike: $14.98 spent.
        assert "TC's monthly Claude cap" in (spent(conn, MONTHLY, 0.03) or "")


def test_a_claude_only_chain_waits_once_no_call_can_fit_this_month_s_cap(engine: Engine) -> None:
    """An eval pinned to Claude parks until the month ends instead of waking at every sweep."""

    def is_spent(role: Role, worst_usd: float) -> str | None:
        with engine.connect() as conn:
            return spent(conn, role, worst_usd)

    unused = cast("Provider", object())
    pinned = Router(
        Binding(MONTHLY, unused),
        None,
        Binding(SMALL, unused),
        fallback_on_rate_limit=False,
        limiter=Limiter(is_spent),
    )
    # $14.99: a prompt at the limit with the forced report's output would pass $15.
    claude_spend(engine, "claude-haiku-5-5", 29_980_000)
    with engine.connect() as conn:
        assert not pinned.available(conn)
    with engine.begin() as conn:
        conn.execute(update(ModelCall).values(output_tokens=29_900_000))  # $14.95
    with engine.connect() as conn:
        assert pinned.available(conn)


def test_a_rejected_key_keeps_a_claude_only_chain_waiting_until_the_agent_restarts(
    engine: Engine,
) -> None:
    """With its key rejected no Claude call can get through, so investigations parked on a
    Claude-only chain stay parked instead of trying again every sweep; a restart asks again."""

    def is_spent(role: Role, worst_usd: float) -> str | None:
        with engine.connect() as conn:
            return spent(conn, role, worst_usd)

    seen, claude = anthropic(INVALID_KEY)
    unused = cast("Provider", object())

    def pinned() -> Router:
        return Router(
            Binding(MONTHLY, claude),
            None,
            Binding(SMALL, unused),
            fallback_on_rate_limit=False,
            limiter=Limiter(is_spent),
        )

    router = pinned()
    with pytest.raises(KeyRejected):
        asyncio.run(router.turn(REQUEST))
    with engine.connect() as conn:
        assert not router.available(conn)
        # A restart builds a new limiter, which asks Claude again.
        assert pinned().available(conn)
    assert len(seen) == 1
