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
from sqlalchemy import Connection

from agent.llm import Binding, Limiter, Router, spent
from agent.model import (
    PROMPT_LIMIT,
    BudgetRefused,
    CreditsExhausted,
    KeyRejected,
    Message,
    ModelError,
    QuotaExhausted,
    RateLimited,
    Refused,
    ReportDraft,
    ToolRequest,
    ToolSpec,
    TurnBudget,
    TurnRequest,
    Usage,
)
from agent.pricing import RATES, worst
from agent.prompt import METRICS, SYSTEM
from agent.providers import ProviderTurn
from agent.settings import Role, RoleName
from agent.tools import TOOL_SPECS, DependenciesArgs, ToolContext, ToolInputError, Tools, parse
from py_clients.clients import AlertContextClient
from sim.catalog import CATALOG

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


def chain(
    primary: Fake,
    claude: Fake,
    fallback: Fake,
    *,
    small: Fake | None = None,
    limiter: Limiter | None = None,
    fallback_on: bool = True,
) -> Router:
    """Gemini, then Claude, then the free fallback; the small role with Claude after it."""
    return Router(
        Binding(role("primary", primary.model), primary),
        Binding(role("fallback", fallback.model), fallback),
        Binding(role("small", "small-model"), small or Fake("small")),
        fallback_on_rate_limit=fallback_on,
        limiter=limiter or Limiter(None),
        claude=Binding(Role("fallback", "anthropic", claude.model, None, 0, 0), claude),
    )


def test_claude_answers_after_gemini_and_before_the_free_fallback() -> None:
    for error in (QuotaExhausted("day spent"), RateLimited("busy minute")):
        claude, fallback = Fake("claude"), Fake("fallback")

        turn = asyncio.run(chain(Fake("primary", error), claude, fallback).turn(REQUEST))

        models = [(c.model, c.outcome) for c in turn.calls]
        assert models == [("primary-model", _outcome(error)), ("claude-model", "ok")]
        assert turn.model == "claude-model"
        assert fallback.max_outputs == []


def _outcome(error: Exception) -> str:
    return "rate_limited" if isinstance(error, RateLimited) else "quota_exhausted"


def test_credits_gone_moves_the_turn_down_the_chain_and_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gone = CreditsExhausted("anthropic 402: credits or spend limit used up")
    claude, fallback = Fake("claude", gone), Fake("fallback")
    primary = Fake("primary", QuotaExhausted("day spent"))

    with caplog.at_level(logging.WARNING, logger="agent.llm"):
        turn = asyncio.run(chain(primary, claude, fallback).turn(REQUEST))

    outcomes = [(c.model, c.outcome) for c in turn.calls]
    assert outcomes == [
        ("primary-model", "quota_exhausted"),
        ("claude-model", "credits_exhausted"),
        ("fallback-model", "ok"),
    ]
    assert any("credits" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)
    # Once spent() reads that call, the limiter keeps Claude off without asking it.
    claude_spent = Limiter(lambda r, w: "credits used up" if r.provider == "anthropic" else None)
    primary = Fake("primary", QuotaExhausted("day spent"))
    again = chain(primary, claude, Fake("fallback"), limiter=claude_spent)
    turn = asyncio.run(again.turn(REQUEST))
    # The provider's own "spent" stops its model; a call the agent skipped stops nothing.
    assert [c.outcome for c in turn.calls] == ["quota_exhausted", "skipped", "ok"]
    assert len(claude.max_outputs) == 1


def test_a_claude_429_with_a_retry_after_is_retried_not_skipped() -> None:
    claude = Fake("claude", RateLimited("anthropic 429: rate limited", retry_after=7))
    fallback = Fake("fallback")
    router = chain(
        Fake("primary", QuotaExhausted("spent"), QuotaExhausted("spent")), claude, fallback
    )

    with pytest.raises(RateLimited) as raised:
        asyncio.run(router.turn(REQUEST))
    # The job waits the Retry-After and asks again, and Claude answers.
    turn = asyncio.run(router.turn(REQUEST))

    assert raised.value.retry_after == 7
    assert turn.model == "claude-model"
    assert fallback.max_outputs == []


def test_evals_pin_one_model_and_never_reach_claude() -> None:
    claude, fallback = Fake("claude"), Fake("fallback")
    router = chain(Fake("primary", QuotaExhausted("spent")), claude, fallback, fallback_on=False)

    with pytest.raises(QuotaExhausted):
        asyncio.run(router.turn(REQUEST))

    assert claude.max_outputs == fallback.max_outputs == []


@pytest.mark.parametrize(
    "error",
    [
        RateLimited("busy minute"),
        KeyRejected("gemini 401: the API key was rejected"),
        Refused("the model declined the request"),
    ],
)
def test_summaries_go_to_claude_while_the_small_model_cannot_answer(error: ModelError) -> None:
    router = chain(Fake("primary"), Fake("claude"), Fake("fallback"), small=Fake("small", error))

    summary, calls = asyncio.run(router.summarize("x" * 500, 10_000, SHA))

    assert summary == "- 40 timeouts"
    # A summary is the small role's work whichever model writes it, so the job's LAST_TURN, which
    # skips role small, never takes one for the last turn after a resume.
    assert [(c.role, c.model, c.outcome) for c in calls] == [
        ("small", "small-model", "rate_limited" if isinstance(error, RateLimited) else "error"),
        ("small", "claude-model", "ok"),
    ]


def test_a_rejected_key_or_a_refusal_moves_the_turn_down_the_chain_with_its_reason() -> None:
    for error in (
        KeyRejected("anthropic 401: the API key was rejected"),
        Refused("anthropic declined the request (category cyber)"),
    ):
        claude, fallback = Fake("claude", error), Fake("fallback")
        primary = Fake("primary", QuotaExhausted("day spent"))

        turn = asyncio.run(chain(primary, claude, fallback).turn(REQUEST))

        assert [(c.model, c.outcome) for c in turn.calls] == [
            ("primary-model", "quota_exhausted"),
            ("claude-model", "error"),
            ("fallback-model", "ok"),
        ]
        assert turn.calls[1].error == f"{type(error).__name__}: {error}"
        assert turn.model == "fallback-model"


def test_a_skipped_model_records_why_it_was_skipped() -> None:
    expired = "its credits ended on 2026-10-01 (UTC)"
    limiter = Limiter(lambda r, w: expired if r.provider == "anthropic" else None)
    claude = Fake("claude")
    router = chain(
        Fake("primary", QuotaExhausted("day spent")), claude, Fake("fallback"), limiter=limiter
    )

    turn = asyncio.run(router.turn(REQUEST))

    assert [(c.model, c.outcome, c.error) for c in turn.calls[1:]] == [
        ("claude-model", "skipped", f"claude-model skipped: {expired}"),
        ("fallback-model", "ok", None),
    ]
    assert claude.max_outputs == []


def test_every_call_records_the_prompt_sha_it_was_made_with() -> None:
    skip_primary = Limiter(lambda r, w: "its day is spent" if r.name == "primary" else None)
    claude = Fake("claude", RateLimited("busy minute", retry_after=3))
    router = chain(Fake("primary"), claude, Fake("fallback"), limiter=skip_primary)

    with pytest.raises(RateLimited) as raised:
        asyncio.run(router.turn(REQUEST))
    turn = asyncio.run(router.turn(REQUEST))
    summary, calls = asyncio.run(router.summarize("x" * 500, 10_000, "sha-of-the-log-prompt"))

    assert [(c.outcome, c.prompt_sha) for c in raised.value.calls] == [
        ("skipped", SHA),
        ("rate_limited", SHA),
    ]
    assert {c.prompt_sha for c in turn.calls} == {SHA}
    assert summary is not None
    assert [c.prompt_sha for c in calls] == ["sha-of-the-log-prompt"]


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


# Live triage's Claude role, with the triage cap of $0.40 a UTC day.
TRIAGE = Role("primary", "anthropic", "claude-haiku-5-5", None, 0, 0, daily_usd=0.40)


def live(claude: Fake, primary: Fake, fallback: Fake, *, limiter: Limiter | None = None) -> Router:
    """Live triage's chain: Claude first, then the free chain (Gemini, then the free fallback)."""
    return Router(
        Binding(role("primary", primary.model), primary),
        Binding(role("fallback", fallback.model), fallback),
        Binding(role("small", "small-model"), Fake("small")),
        fallback_on_rate_limit=True,
        limiter=limiter or Limiter(None),
        claude=Binding(TRIAGE, claude),
        claude_first=True,
    )


def test_live_triage_asks_claude_first_and_the_free_chain_after_it() -> None:
    primary = Fake("primary")
    turn = asyncio.run(live(Fake("claude"), primary, Fake("fallback")).turn(REQUEST))
    assert (turn.model, primary.max_outputs) == ("claude-haiku-5-5", [])

    for error in (
        Refused("anthropic declined the request (category cyber)"),
        KeyRejected("anthropic 401: the API key was rejected"),
        CreditsExhausted("anthropic 402: credits or spend limit used up"),
    ):
        turn = asyncio.run(
            live(Fake("claude", error), Fake("primary"), Fake("fallback")).turn(REQUEST)
        )
        assert [c.model for c in turn.calls] == ["claude-haiku-5-5", "primary-model"]
        assert turn.model == "primary-model"
    # With Gemini's day spent as well, the free fallback answers.
    gemini_spent = Fake("primary", QuotaExhausted("gemini 429: daily quota spent"))
    turn = asyncio.run(
        live(Fake("claude", Refused("no")), gemini_spent, Fake("fallback")).turn(REQUEST)
    )
    assert turn.model == "fallback-model"


def test_claude_s_429_is_waited_out_only_up_to_a_minute() -> None:
    minute = Fake("claude", RateLimited("anthropic 429: rate limited", retry_after=60))
    with pytest.raises(RateLimited):
        asyncio.run(live(minute, Fake("primary"), Fake("fallback")).turn(REQUEST))
    for retry_after in (61, None):
        busy = Fake("claude", RateLimited("anthropic 429: rate limited", retry_after=retry_after))
        turn = asyncio.run(live(busy, Fake("primary"), Fake("fallback")).turn(REQUEST))
        assert [(c.model, c.outcome) for c in turn.calls] == [
            ("claude-haiku-5-5", "rate_limited"),
            ("primary-model", "ok"),
        ]


def test_live_summaries_ask_claude_first() -> None:
    router = live(Fake("claude"), Fake("primary"), Fake("fallback"))
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
    router = live(claude, Fake("primary"), Fake("fallback"), limiter=Limiter(is_spent))
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


def test_provider_text_is_masked_before_it_is_recorded_or_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    planted = "sk-ant-canarycanarycanary01"
    gone = CreditsExhausted(f"anthropic 402: credits or spend limit used up: {planted}")
    leaky = ModelError(f"nvidia 400: bad request for https://nim.example/v1?key={planted}")
    with caplog.at_level(logging.WARNING, logger="agent.llm"):
        turn = asyncio.run(
            live(Fake("claude", gone), Fake("primary"), Fake("fallback")).turn(REQUEST)
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
