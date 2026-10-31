"""The router, the limiter, the token budget math, and what the model sees."""

import asyncio
import json
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, cast

import httpx2
import pytest
from anthropic_errors import (
    ANSWERS,
    INVALID_KEY,
    OVERLOADED,
    RATE_LIMITED,
    REFUSAL,
    Answer,
    Stop,
)
from sqlalchemy import Connection

from agent.job import REPORT_OUTPUT_CAP
from agent.llm import LARGEST_CALL, PLAN_OUTPUT_CAP, Binding, Limiter, Router, spent
from agent.model import (
    PROMPT_LIMIT,
    BudgetRefused,
    CreditsExhausted,
    KeyRejected,
    Message,
    ModelError,
    PlanRequest,
    QuotaExhausted,
    RateLimited,
    Refused,
    ReportDraft,
    ToolRequest,
    ToolSpec,
    Truncated,
    TurnBudget,
    TurnRequest,
    Usage,
)
from agent.pricing import RATES, worst
from agent.prompt import METRICS, SYSTEM
from agent.providers import AnthropicProvider, ProviderTurn
from agent.settings import Role, RoleName
from agent.tools import TOOL_SPECS, DependenciesArgs, ToolContext, ToolInputError, Tools, parse
from py_clients.clients import AlertContextClient
from sim.catalog import CATALOG

PLAN_ANSWER: dict[str, Any] = {
    "kind": "none",
    "target": "postgres",
    "files": [],
    "rationale": "Postgres refuses connections; a human must look at its disk.",
}
BUDGET = TurnBudget(
    remaining=150_000,
    prompt_bytes=10_000,
    delta_bytes=0,
    messages=1,
    new_messages=1,
    output_cap=2_048,
    min_output=256,
)
# The sha the job sends with its prompt: every call of the turn records it.
SHA = "sha256-of-the-prompt"
REQUEST = TurnRequest([], "system", [Message("user", text="go")], [], BUDGET, SHA)


class Fake:
    """A provider that fails with each queued error, then answers."""

    def __init__(self, name: str, *errors: ModelError) -> None:
        self.name = name
        self.model = f"{name}-model"
        self.errors = list(errors)
        self.max_outputs: list[int] = []
        # What structured() answers, and the effort each of its calls asked for.
        self.answer: dict[str, Any] = PLAN_ANSWER
        self.efforts: list[str | None] = []

    async def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        allowed: tuple[str, ...] | None,
        max_output_tokens: int,
    ) -> ProviderTurn:
        self.max_outputs.append(max_output_tokens)
        if self.errors:
            raise self.errors.pop(0)
        args: dict[str, Any] = {"query": "up", "reason": "Check the error rate."}
        return ProviderTurn("query_metrics", args, None, Usage(input=900, output=30))

    async def complete(self, system: str, text: str, max_output_tokens: int) -> tuple[str, Usage]:
        if self.errors:
            raise self.errors.pop(0)
        return "- 40 timeouts", Usage(input=500, output=10)

    async def structured(
        self, system: str, text: str, shape: ToolSpec, max_output_tokens: int, effort: str | None
    ) -> tuple[dict[str, Any], Usage]:
        self.max_outputs.append(max_output_tokens)
        self.efforts.append(effort)
        if self.errors:
            raise self.errors.pop(0)
        return dict(self.answer), Usage(input=12_000, output=900)


def role(name: RoleName, model: str, rpm: int = 0, rpd: int = 0) -> Role:
    return Role(name, "gemini", model, None, rpm, rpd)


def router(primary: Fake, fallback: Fake, *, fallback_on: bool = True) -> Router:
    return Router(
        Binding(role("primary", primary.model), primary),
        Binding(role("fallback", fallback.model), fallback),
        Binding(role("small", "small-model"), Fake("small")),
        fallback_on_rate_limit=fallback_on,
        limiter=Limiter(None),
    )


def test_a_429_moves_the_turn_to_the_fallback_and_both_are_recorded() -> None:
    primary, fallback = Fake("primary", RateLimited("429")), Fake("fallback")

    turn = asyncio.run(router(primary, fallback).turn(REQUEST))

    outcomes = [(c.role, c.outcome) for c in turn.calls]
    assert outcomes == [("primary", "rate_limited"), ("fallback", "ok")]
    assert turn.model == "fallback-model"
    reason = "Check the error rate."
    assert turn.action == ToolRequest(tool="query_metrics", args={"query": "up"}, reason=reason)


def test_evals_never_fall_back() -> None:
    primary, fallback = Fake("primary", RateLimited("429", retry_after=5)), Fake("fallback")

    with pytest.raises(RateLimited) as raised:
        asyncio.run(router(primary, fallback, fallback_on=False).turn(REQUEST))

    assert [c.outcome for c in raised.value.calls] == ["rate_limited"]
    assert fallback.max_outputs == []


def test_a_turn_whose_input_leaves_too_little_is_refused_before_any_request() -> None:
    primary = Fake("primary")
    tight = TurnRequest([], "s", [], [], TurnBudget(10_200, 10_000, 0, 0, 0, 2_048, 256), SHA)

    with pytest.raises(BudgetRefused):
        asyncio.run(router(primary, Fake("fallback")).turn(tight))

    assert primary.max_outputs == []


def test_output_is_capped_to_what_the_input_leaves() -> None:
    primary = Fake("primary")
    request = TurnRequest([], "s", [], [], TurnBudget(11_000, 10_000, 0, 0, 0, 2_048, 256), SHA)

    asyncio.run(router(primary, Fake("fallback")).turn(request))

    assert primary.max_outputs == [11_000 - 10_000 - 256]


def test_the_bound_builds_on_the_same_models_exact_count() -> None:
    budget = TurnBudget(
        remaining=100_000,
        prompt_bytes=50_000,
        delta_bytes=3_000,
        messages=10,
        new_messages=2,
        output_cap=2_048,
        min_output=256,
        last_model="m",
        last_input=12_000,
        last_output=500,
    )
    assert budget.bound("m") == 12_000 + 500 + 3_000 + 2 * 16
    assert budget.bound("other") == 50_000 + 10 * 16 + 256


def test_a_prompt_past_the_limit_is_refused_whatever_tokens_are_left() -> None:
    at_limit = TurnBudget(10**6, PROMPT_LIMIT - 256, 0, 0, 0, 2_048, 256)
    assert at_limit.limits("m") == (PROMPT_LIMIT, 2_048)
    over = TurnBudget(10**6, PROMPT_LIMIT - 255, 0, 0, 0, 2_048, 256)
    assert over.limits("m") is None
    exact = TurnBudget(10**6, 0, 600, 1, 1, 2_048, 256, "m", PROMPT_LIMIT - 1_000, 500)
    assert exact.limits("m") is None
    primary = Fake("primary")

    with pytest.raises(BudgetRefused):
        asyncio.run(router(primary, Fake("fallback")).turn(TurnRequest([], "s", [], [], over, SHA)))

    assert primary.max_outputs == []


def test_an_invalid_report_comes_back_as_a_tool_call_to_fix() -> None:
    class Reporter(Fake):
        async def generate(self, *args: Any, **kwargs: Any) -> ProviderTurn:
            return ProviderTurn("submit_report", {"summary": ""}, None, Usage(input=1, output=1))

    turn = asyncio.run(router(Reporter("primary"), Fake("fallback")).turn(REQUEST))

    assert isinstance(turn.action, ToolRequest)
    assert turn.action.tool == "submit_report"


def test_the_limiter_waits_out_the_minute_and_refuses_a_spent_day() -> None:
    now = [0.0]
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    limiter = Limiter(lambda role, worst: None, clock=lambda: now[0], sleep=sleep)
    twice_a_minute = role("primary", "m", rpm=2)

    async def three() -> None:
        for _ in range(3):
            await limiter.acquire(twice_a_minute)

    asyncio.run(three())
    assert waits == [60.0]

    spent_day = Limiter(lambda role, worst: "it made its 500 requests for today (Pacific)")
    with pytest.raises(QuotaExhausted) as raised:
        asyncio.run(spent_day.acquire(role("primary", "m", rpd=500)))
    assert str(raised.value) == "m skipped: it made its 500 requests for today (Pacific)"


def test_summaries_skip_when_the_page_would_not_fit() -> None:
    small = router(Fake("primary"), Fake("fallback"))

    assert asyncio.run(small.summarize("x" * 5_000, 1_000, SHA)) == (None, [])

    summary, calls = asyncio.run(small.summarize("x" * 500, 10_000, SHA))
    assert summary == "- 40 timeouts"
    assert [(c.role, c.outcome, c.usage.input) for c in calls] == [("small", "ok", 500)]


def test_summaries_skip_a_page_past_the_prompt_limit() -> None:
    small = router(Fake("primary"), Fake("fallback"))

    assert asyncio.run(small.summarize("x" * PROMPT_LIMIT, 10**6, SHA)) == (None, [])


# Claude, which every chain asks first: Haiku under the triage cap of $0.40 a UTC day.
TRIAGE = Role("primary", "anthropic", "claude-haiku-5-5", None, 0, 0, daily_usd=0.40)


def chain(
    claude: Fake | AnthropicProvider,
    primary: Fake,
    fallback: Fake,
    *,
    small: Fake | None = None,
    limiter: Limiter | None = None,
    fallback_on: bool = True,
) -> Router:
    """Claude first, then the free chain: Gemini, then the free fallback; for summaries, Claude
    and then the small model."""
    return Router(
        Binding(role("primary", primary.model), primary),
        Binding(role("fallback", fallback.model), fallback),
        Binding(role("small", "small-model"), small or Fake("small")),
        fallback_on_rate_limit=fallback_on,
        limiter=limiter or Limiter(None),
        claude=Binding(TRIAGE, claude),
    )


Reply = Answer | dict[str, Any] | type[httpx2.TransportError]


def anthropic(answer: Reply) -> tuple[list[httpx2.Request], AnthropicProvider]:
    """Claude on a transport that gives every request the same answer (one of Anthropic's error
    answers, a 200 body, or a failure before any answer), and the requests it saw."""
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if isinstance(answer, Answer):
            return answer.response()
        if isinstance(answer, dict):
            return httpx2.Response(200, json=answer)
        raise answer("no answer", request=request)

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    return seen, AnthropicProvider("claude-haiku-5-5", "sk-ant-test", "https://claude.test", http)


# Every answer Claude can give a turn instead of a tool call: the outcome recorded for its call,
# and how long Claude stays off after it.
FAILURES: list[tuple[str, Reply, str, Stop]] = [
    *((answer.name, answer, outcome, stop) for answer, outcome, stop in ANSWERS),
    ("refusal", REFUSAL, "error", "call"),
    ("connection refused", httpx2.ConnectError, "error", "call"),
    ("read timeout", httpx2.ReadTimeout, "error", "call"),
]


@pytest.mark.parametrize(
    ("name", "answer", "outcome", "stop"), FAILURES, ids=[failure[0] for failure in FAILURES]
)
def test_each_anthropic_answer_falls_through_for_its_call_or_stops_claude(
    name: str, answer: Reply, outcome: str, stop: Stop
) -> None:
    seen, claude = anthropic(answer)
    router = chain(claude, Fake("primary"), Fake("fallback"))

    turn = asyncio.run(router.turn(REQUEST))

    # Gemini answers the call Claude could not, whatever Claude answered.
    assert [(c.model, c.outcome) for c in turn.calls] == [
        ("claude-haiku-5-5", outcome),
        ("primary-model", "ok"),
    ]
    assert turn.model == "primary-model"
    again = asyncio.run(router.turn(REQUEST))
    if stop == "call":
        # Nothing stops on it: the next call asks Claude again.
        assert len(seen) == 2
        assert again.calls[0].outcome == outcome
    if stop == "restart":
        # A rejected key keeps Claude off in this process, with no request and the reason.
        assert len(seen) == 1
        assert again.calls[0].outcome == "skipped"
        assert "until the agent restarts" in (again.calls[0].error or "")
        # A restart, with its new limiter, asks Claude again.
        asyncio.run(chain(claude, Fake("primary"), Fake("fallback")).turn(REQUEST))
        assert len(seen) == 2
    # "month": spent() reads the recorded credits_exhausted until the month ends
    # (test_workers_int.py, test_a_credits_stop_holds_every_anthropic_model_until_the_month_ends).


def test_a_rejected_key_stops_every_claude_model_until_the_agent_restarts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, claude = anthropic(INVALID_KEY)
    shared = Limiter(None)
    opus = Fake("opus")

    with caplog.at_level(logging.WARNING, logger="agent.llm"):
        asyncio.run(chain(claude, Fake("primary"), Fake("fallback"), limiter=shared).turn(REQUEST))
    plans = planners(opus, Fake("primary"), Fake("fallback"), limiter=shared)
    answer, calls = asyncio.run(plans.plan(PLAN_REQUEST))

    # The planner's Opus has the same key: skipped with the reason, and the free chain plans.
    assert [(c.model, c.outcome) for c in calls] == [
        ("claude-opus-5-5", "skipped"),
        ("primary-model", "ok"),
    ]
    assert "until the agent restarts" in (calls[0].error or "")
    assert (answer, opus.max_outputs) == (PLAN_ANSWER, [])
    assert any("API key" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


def test_evals_run_claude_then_gemini_and_never_the_free_fallback() -> None:
    _, claude = anthropic(RATE_LIMITED)
    fallback = Fake("fallback")

    turn = asyncio.run(chain(claude, Fake("primary"), fallback, fallback_on=False).turn(REQUEST))

    assert [c.model for c in turn.calls] == ["claude-haiku-5-5", "primary-model"]
    # With Gemini busy too, the eval's turn waits: the free fallback never answers an eval.
    busy = Fake("primary", RateLimited("gemini 429", retry_after=5))
    with pytest.raises(RateLimited):
        asyncio.run(chain(claude, busy, fallback, fallback_on=False).turn(REQUEST))
    assert fallback.max_outputs == []


SUMMARY_FAILURES: list[Reply] = [
    RATE_LIMITED,
    OVERLOADED,
    INVALID_KEY,
    REFUSAL,
    httpx2.ConnectError,
]


@pytest.mark.parametrize(
    "answer",
    SUMMARY_FAILURES,
    ids=["rate limited", "overloaded", "invalid key", "refusal", "connection refused"],
)
def test_summaries_go_to_the_small_model_while_claude_cannot_answer(answer: Reply) -> None:
    _, claude = anthropic(answer)
    router = chain(claude, Fake("primary"), Fake("fallback"))

    summary, calls = asyncio.run(router.summarize("x" * 500, 10_000, SHA))

    assert summary == "- 40 timeouts"
    # A summary is the small role's work whichever model writes it, so the job's LAST_TURN, which
    # skips role small, never takes one for the last turn after a resume.
    assert [(c.role, c.model) for c in calls] == [
        ("small", "claude-haiku-5-5"),
        ("small", "small-model"),
    ]
    assert calls[0].outcome != "ok"


def test_credits_gone_moves_the_turn_down_the_chain_and_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gone = CreditsExhausted("anthropic 402: credits or spend limit used up")
    claude = Fake("claude", gone)

    with caplog.at_level(logging.WARNING, logger="agent.llm"):
        turn = asyncio.run(chain(claude, Fake("primary"), Fake("fallback")).turn(REQUEST))

    assert [(c.model, c.outcome) for c in turn.calls] == [
        ("claude-haiku-5-5", "credits_exhausted"),
        ("primary-model", "ok"),
    ]
    assert any("credits" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)
    # Once spent() reads that call, the limiter keeps Claude off without asking it.
    claude_spent = Limiter(lambda r, w: "credits used up" if r.provider == "anthropic" else None)
    again = chain(claude, Fake("primary"), Fake("fallback"), limiter=claude_spent)
    turn = asyncio.run(again.turn(REQUEST))
    assert [c.outcome for c in turn.calls] == ["skipped", "ok"]
    assert len(claude.max_outputs) == 1


def test_a_skipped_model_records_why_it_was_skipped() -> None:
    expired = "its credits ended on 2026-10-01 (UTC)"
    limiter = Limiter(lambda r, w: expired if r.provider == "anthropic" else None)
    claude = Fake("claude")

    turn = asyncio.run(
        chain(claude, Fake("primary"), Fake("fallback"), limiter=limiter).turn(REQUEST)
    )

    assert [(c.model, c.outcome, c.error) for c in turn.calls] == [
        ("claude-haiku-5-5", "skipped", f"claude-haiku-5-5 skipped: {expired}"),
        ("primary-model", "ok", None),
    ]
    assert claude.max_outputs == []


def test_every_call_records_the_prompt_sha_it_was_made_with() -> None:
    skip_claude = Limiter(lambda r, w: "its day is spent" if r.provider == "anthropic" else None)
    busy = Fake("primary", RateLimited("busy minute", retry_after=3))
    router = chain(Fake("claude"), busy, Fake("fallback"), limiter=skip_claude)

    turn = asyncio.run(router.turn(REQUEST))
    summary, calls = asyncio.run(router.summarize("x" * 500, 10_000, "sha-of-the-log-prompt"))

    assert [(c.outcome, c.prompt_sha) for c in turn.calls] == [
        ("skipped", SHA),
        ("rate_limited", SHA),
        ("ok", SHA),
    ]
    assert summary is not None
    assert [c.prompt_sha for c in calls] == ["sha-of-the-log-prompt"] * 2


def test_claude_is_spent_from_the_day_its_credits_expire() -> None:
    # The date alone decides, before any query: no connection is needed.
    no_db = cast("Connection", object())
    expired = Role("fallback", "anthropic", "claude-haiku-5-5", None, 0, 0, until=date(2026, 10, 1))
    assert spent(no_db, expired) == "its credits ended on 2026-10-01 (UTC)"


def test_the_system_prompt_lists_the_sims_metric_catalog() -> None:
    assert METRICS == CATALOG
    assert all(name in SYSTEM for name in CATALOG)


def test_tool_definitions_are_plain_json_schema_with_a_reason() -> None:
    for spec in TOOL_SPECS:
        text = json.dumps(spec.parameters)
        assert '"$ref"' not in text
        assert '"title"' not in text
        assert '"default"' not in text
        if spec.name != "submit_report":
            assert spec.parameters["required"][0] == "reason"
    report = next(s for s in TOOL_SPECS if s.name == "submit_report")
    assert set(report.parameters["properties"]) == set(ReportDraft.model_fields)


def test_alert_context_shows_annotations_only_where_asked() -> None:
    annotations = {
        "summary": "checkout 5xx ratio above 5% for 2 minutes",
        "runbook_url": "https://runbooks.example/5xx",
        "snapshot": "x" * 5_000,
    }
    alert = {"fingerprint": "f", "labels": {"alertname": "CheckoutHighErrorRate"}}
    context = {
        "investigation_id": str(uuid.uuid4()),
        "group_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "group_key": '{}:{alertname="CheckoutHighErrorRate"}',
        "window_start": "2026-10-14T12:00:05Z",
        "severity": "critical",
        "alert_count": 1,
        "alerts": [
            alert
            | {"starts_at": "2026-10-14T12:00:05Z", "status": "firing", "annotations": annotations}
        ],
    }
    transport = httpx2.MockTransport(lambda _: httpx2.Response(200, json=context))
    alerts = AlertContextClient("http://gateway.test", "token", transport=transport)
    ctx = ToolContext(uuid.uuid4(), datetime(2026, 10, 14, 12, tzinfo=UTC), 0, SHA)
    sim = cast("httpx2.AsyncClient", None)

    shown = asyncio.run(Tools(sim, alerts, None, None, annotations=True).get_alert_context(ctx, {}))
    hidden = asyncio.run(Tools(sim, alerts, None, None).get_alert_context(ctx, {}))

    assert "  summary: checkout 5xx ratio above 5% for 2 minutes" in shown.splitlines()
    assert "  runbook_url: https://runbooks.example/5xx" in shown.splitlines()
    # Only the standard annotations: a large one, such as a snapshot, has its own tool.
    assert "snapshot" not in shown
    # The sim's tools keep their results, so eval runs stay comparable.
    assert "summary" not in hidden


def test_claude_answers_first_and_the_free_chain_after_it() -> None:
    primary = Fake("primary")
    turn = asyncio.run(chain(Fake("claude"), primary, Fake("fallback")).turn(REQUEST))
    assert (turn.model, primary.max_outputs) == ("claude-haiku-5-5", [])

    for error in (
        Refused("anthropic declined the request (category cyber)"),
        KeyRejected("anthropic 401: the API key was rejected"),
        CreditsExhausted("anthropic 402: credits or spend limit used up"),
    ):
        turn = asyncio.run(
            chain(Fake("claude", error), Fake("primary"), Fake("fallback")).turn(REQUEST)
        )
        assert [c.model for c in turn.calls] == ["claude-haiku-5-5", "primary-model"]
        assert turn.model == "primary-model"
    # With Gemini's day spent as well, the free fallback answers.
    gemini_spent = Fake("primary", QuotaExhausted("gemini 429: daily quota spent"))
    turn = asyncio.run(
        chain(Fake("claude", Refused("no")), gemini_spent, Fake("fallback")).turn(REQUEST)
    )
    assert turn.model == "fallback-model"


def test_summaries_ask_claude_first() -> None:
    router = chain(Fake("claude"), Fake("primary"), Fake("fallback"))
    summary, calls = asyncio.run(router.summarize("x" * 500, 10_000, SHA))
    assert summary == "- 40 timeouts"
    assert [(c.role, c.model) for c in calls] == [("small", "claude-haiku-5-5")]


def test_the_triage_cap_is_checked_against_each_call_s_worst_case() -> None:
    asked: list[tuple[str, float]] = []

    def is_spent(role: Role, worst_usd: float) -> str | None:
        asked.append((role.model, worst_usd))
        over = role.daily_usd is not None and worst_usd > 0.002
        return "its worst case passes the triage cap" if over else None

    claude = Fake("claude")
    router = chain(claude, Fake("primary"), Fake("fallback"), limiter=Limiter(is_spent))
    turn = asyncio.run(router.turn(REQUEST))

    limits = REQUEST.budget.limits("claude-haiku-5-5")
    assert limits is not None
    # Every input token at the cache-write rate, and the whole output allowance.
    expected = worst(RATES["claude-haiku-5-5"], *limits)
    assert asked == [("claude-haiku-5-5", pytest.approx(expected)), ("primary-model", 0.0)]
    assert [(c.model, c.outcome) for c in turn.calls] == [
        ("claude-haiku-5-5", "skipped"),
        ("primary-model", "ok"),
    ]
    assert claude.max_outputs == []


def test_every_claude_role_is_held_to_tc_s_monthly_cap_by_each_call_s_worst_case() -> None:
    asked: list[tuple[str, float]] = []

    def is_spent(role: Role, worst_usd: float) -> str | None:
        asked.append((role.model, worst_usd))
        return None

    # A Claude pinned as an eval's primary role, here with TC's monthly cap alone.
    claude = Role("primary", "anthropic", "claude-haiku-5-5", None, 0, 0, monthly_usd=15.0)
    pinned = Router(
        Binding(claude, Fake("claude")),
        None,
        Binding(role("small", "small-model"), Fake("small")),
        fallback_on_rate_limit=False,
        limiter=Limiter(is_spent),
    )
    asyncio.run(pinned.turn(REQUEST))

    limits = REQUEST.budget.limits("claude-haiku-5-5")
    assert limits is not None
    expected = worst(RATES["claude-haiku-5-5"], *limits)
    assert asked == [("claude-haiku-5-5", pytest.approx(expected))]


def test_availability_is_judged_on_the_largest_call_a_turn_can_make() -> None:
    # A prompt at the limit and the forced report's output allowance.
    assert LARGEST_CALL == (PROMPT_LIMIT, REPORT_OUTPUT_CAP)


# The planner's Claude: Opus at effort medium, held to its own $1.00 a UTC day and TC's $15 a month.
PLANNER = Role(
    "planner",
    "anthropic",
    "claude-opus-5-5",
    None,
    0,
    0,
    daily_usd=1.00,
    monthly_usd=15.0,
    effort="medium",
)
PLAN_REQUEST = PlanRequest(
    "Plan the fix.",
    "The report.",
    ToolSpec("submit_plan", "Submit the plan.", {"type": "object"}),
    lambda answer: None if answer.get("kind") else "it names no kind",
    SHA,
)


def planners(
    claude: Fake | None, primary: Fake, fallback: Fake, *, limiter: Limiter | None = None
) -> Router:
    """The live router's plan chain: the planner's Claude when there is one, then the free chain."""
    return Router(
        Binding(role("primary", primary.model), primary),
        Binding(role("fallback", fallback.model), fallback),
        Binding(role("small", "small-model"), Fake("small")),
        fallback_on_rate_limit=True,
        limiter=limiter or Limiter(None),
        planner=None if claude is None else Binding(PLANNER, claude),
    )


def test_a_refused_plan_falls_to_the_free_chain_and_every_call_is_the_planner_s() -> None:
    claude = Fake("claude", Refused("anthropic declined the request (category cyber)"))
    primary = Fake("primary")

    answer, calls = asyncio.run(planners(claude, primary, Fake("fallback")).plan(PLAN_REQUEST))

    assert answer == PLAN_ANSWER
    assert [(c.role, c.model, c.outcome) for c in calls] == [
        ("planner", "claude-opus-5-5", "error"),
        ("planner", "primary-model", "ok"),
    ]
    assert (calls[0].error or "").startswith("Refused: anthropic declined the request")
    # Opus at effort medium, the free model at its own; both get the plan's 16,000 output tokens.
    assert PLAN_OUTPUT_CAP == 16_000
    assert (claude.efforts, primary.efforts) == (["medium"], [None])
    assert claude.max_outputs == primary.max_outputs == [PLAN_OUTPUT_CAP]
    assert {(c.prompt_sha, c.input_bound) for c in calls} == {(SHA, PLAN_REQUEST.bound)}


def test_a_cut_off_failed_or_invalid_plan_moves_on_and_none_is_left_when_all_fail() -> None:
    cut = Truncated("anthropic stopped at 16,000 output tokens, before the answer ended")
    invalid = Fake("primary")
    invalid.answer = {"rationale": "No kind."}

    answer, calls = asyncio.run(
        planners(Fake("claude", cut), invalid, Fake("fallback")).plan(PLAN_REQUEST)
    )

    assert answer == PLAN_ANSWER
    assert [(c.model, c.outcome) for c in calls] == [
        ("claude-opus-5-5", "error"),
        ("primary-model", "error"),
        ("fallback-model", "ok"),
    ]
    assert (calls[0].error or "").startswith("Truncated: ")
    assert calls[1].error == "InvalidPlan: it names no kind"
    # The invalid answer was billed, so its usage is recorded.
    assert calls[1].usage.input == 12_000

    failing = [Fake(name, ModelError(f"{name} 500")) for name in ("claude", "primary", "fallback")]
    answer, calls = asyncio.run(planners(*failing).plan(PLAN_REQUEST))
    assert (answer, [c.outcome for c in calls]) == (None, ["error", "error", "error"])


def test_without_a_planner_claude_the_free_chain_plans() -> None:
    free = planners(None, Fake("primary"), Fake("fallback"))
    answer, calls = asyncio.run(free.plan(PLAN_REQUEST))
    assert (answer, [c.model for c in calls]) == (PLAN_ANSWER, ["primary-model"])


def test_the_planner_cap_is_checked_against_the_plan_s_worst_case_with_nothing_cached() -> None:
    asked: list[tuple[str, float]] = []

    def is_spent(role: Role, worst_usd: float) -> str | None:
        asked.append((role.model, worst_usd))
        over = role.daily_usd is not None
        return "today's plans plus this call's worst case pass the planner cap" if over else None

    claude = Fake("claude")
    router = planners(claude, Fake("primary"), Fake("fallback"), limiter=Limiter(is_spent))

    answer, calls = asyncio.run(router.plan(PLAN_REQUEST))

    # A plan writes nothing to the cache: every input token at the input rate, and the whole output.
    expected = worst(RATES["claude-opus-5-5"], PLAN_REQUEST.bound, PLAN_OUTPUT_CAP, caches=False)
    assert asked == [("claude-opus-5-5", pytest.approx(expected)), ("primary-model", 0.0)]
    assert [(c.model, c.outcome) for c in calls] == [
        ("claude-opus-5-5", "skipped"),
        ("primary-model", "ok"),
    ]
    assert "planner cap" in (calls[0].error or "")
    assert (answer, claude.max_outputs) == (PLAN_ANSWER, [])


def test_provider_text_is_masked_before_it_is_recorded_or_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    planted = "sk-ant-canarycanarycanary01"
    gone = CreditsExhausted(f"anthropic 402: credits or spend limit used up: {planted}")
    leaky = ModelError(f"nvidia 400: bad request for https://nim.example/v1?key={planted}")
    with caplog.at_level(logging.WARNING, logger="agent.llm"):
        turn = asyncio.run(
            chain(Fake("claude", gone), Fake("primary"), Fake("fallback")).turn(REQUEST)
        )
        with pytest.raises(ModelError) as raised:
            asyncio.run(router(Fake("primary", leaky), Fake("fallback")).turn(REQUEST))
    recorded = [c.error or "" for c in [*turn.calls, *raised.value.calls]]
    assert "[key]" in recorded[0]
    assert not [
        text for text in [*recorded, *map(str, map(vars, caplog.records))] if planted in text
    ]


@pytest.mark.parametrize("service", ["../../../admin/state", "..", "a/b", "orders db"])
def test_get_dependencies_refuses_names_that_leave_its_route(service: str) -> None:
    with pytest.raises(ToolInputError):
        parse(DependenciesArgs, {"service": service})
    assert parse(DependenciesArgs, {"service": "orders-db"}).service == "orders-db"
