"""The router, the limiter, the token budget math, and what the model sees."""

import asyncio
import json
from collections.abc import Sequence
from typing import Any

import pytest

from agent.llm import Binding, Limiter, Router
from agent.model import (
    BudgetRefused,
    Message,
    ModelError,
    QuotaExhausted,
    RateLimited,
    ReportDraft,
    ToolRequest,
    ToolSpec,
    TurnBudget,
    TurnRequest,
    Usage,
)
from agent.prompt import METRICS, SYSTEM
from agent.providers import ProviderTurn
from agent.settings import Role, RoleName
from agent.tools import TOOL_SPECS, DependenciesArgs, ToolInputError, parse
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
REQUEST = TurnRequest([], "system", [Message("user", text="go")], [], BUDGET)


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
    tight = TurnRequest([], "s", [], [], TurnBudget(10_200, 10_000, 0, 0, 0, 2_048, 256))

    with pytest.raises(BudgetRefused):
        asyncio.run(router(primary, Fake("fallback")).turn(tight))

    assert primary.max_outputs == []


def test_output_is_capped_to_what_the_input_leaves() -> None:
    primary = Fake("primary")
    request = TurnRequest([], "s", [], [], TurnBudget(11_000, 10_000, 0, 0, 0, 2_048, 256))

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

    limiter = Limiter(lambda model: 0, clock=lambda: now[0], sleep=sleep)
    twice_a_minute = role("primary", "m", rpm=2)

    async def three() -> None:
        for _ in range(3):
            await limiter.acquire(twice_a_minute)

    asyncio.run(three())
    assert waits == [60.0]

    spent = Limiter(lambda model: 500)
    with pytest.raises(QuotaExhausted):
        asyncio.run(spent.acquire(role("primary", "m", rpd=500)))


def test_summaries_skip_when_the_page_would_not_fit() -> None:
    small = router(Fake("primary"), Fake("fallback"))

    assert asyncio.run(small.summarize("x" * 5_000, remaining=1_000)) == (None, [])

    summary, calls = asyncio.run(small.summarize("x" * 500, remaining=10_000))
    assert summary == "- 40 timeouts"
    assert [(c.role, c.outcome, c.usage.input) for c in calls] == [("small", "ok", 500)]


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


@pytest.mark.parametrize("service", ["../../../admin/state", "..", "a/b", "orders db"])
def test_get_dependencies_refuses_names_that_leave_its_route(service: str) -> None:
    with pytest.raises(ToolInputError):
        parse(DependenciesArgs, {"service": service})
    assert parse(DependenciesArgs, {"service": "orders-db"}).service == "orders-db"
