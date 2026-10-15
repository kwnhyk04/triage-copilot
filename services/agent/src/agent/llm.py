"""The Model the job uses in production: the primary role, the fallback on a 429 outside evals,
the small role for log-page summaries, and a limiter that keeps each role under its free-tier
requests per minute and per day.

Every request is returned as a ModelCall, with its outcome and usage, for the job to record.
"""

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Connection, text

from agent.model import (
    NO_USAGE,
    PROMPT_OVERHEAD,
    BudgetRefused,
    ModelCall,
    ModelError,
    ModelTurn,
    Outcome,
    QuotaExhausted,
    RateLimited,
    ReportDraft,
    ToolRequest,
    TurnRequest,
    Usage,
)
from agent.prompt import REPORT_TOOL
from agent.providers import REQUEST_TIMEOUT, Provider
from agent.settings import Role
from agent.steps import TokenUsage

SUMMARY_OUTPUT_CAP = 400
SUMMARY_MIN_OUTPUT = 64
SUMMARY_SYSTEM = (
    "Summarize these log lines for an incident investigation in at most 8 short bullet points: "
    "the error messages quoted exactly with their counts, the first and last time of each, the "
    "services, versions and endpoints involved, and anything that stops or starts in the page. "
    "Report only what the lines show."
)
# Requests to a model since midnight Pacific, when Google resets daily quotas. A 429 or a request
# the limiter refused never reached the model, so it does not count.
CALLS_TODAY = text("""
    SELECT count(*) FROM agent.model_calls
    WHERE model = :model AND outcome IN ('ok', 'error')
      AND created_at >= date_trunc('day', now() AT TIME ZONE 'America/Los_Angeles')
                        AT TIME ZONE 'America/Los_Angeles'
""")


def calls_today(conn: Connection, model: str) -> int:
    with conn.begin():
        return conn.execute(CALLS_TODAY, {"model": model}).scalar_one()


class Limiter:
    """Keeps each role under its RPM (a sliding minute) and RPD (recorded calls today).

    ponytail: the minute window lives in this process and the day count sees only recorded calls,
    so several agent processes, or calls still in flight, can pass a limit by a few requests;
    move the window into Postgres if several agents share one key.
    """

    def __init__(
        self,
        count_today: Callable[[str], int] | None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._count_today = count_today
        self._clock = clock
        self._sleep = sleep
        self._windows: defaultdict[str, deque[float]] = defaultdict(deque)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def spent(self, conn: Connection, role: Role) -> bool:
        return role.rpd > 0 and calls_today(conn, role.model) >= role.rpd

    async def acquire(self, role: Role) -> None:
        """Waits for a free request this minute; raises QuotaExhausted once today's are spent."""
        if role.rpd and self._count_today is not None:
            used = await asyncio.to_thread(self._count_today, role.model)
            if used >= role.rpd:
                raise QuotaExhausted(f"{role.model}: all {role.rpd} requests for today are spent")
        if not role.rpm:
            return
        async with self._locks[role.model]:
            window = self._windows[role.model]
            while True:
                now = self._clock()
                while window and window[0] <= now - 60:
                    window.popleft()
                if len(window) < role.rpm:
                    window.append(now)
                    return
                await self._sleep(window[0] + 60 - now)


@dataclass(frozen=True)
class Binding:
    role: Role
    provider: Provider


class Router:
    def __init__(
        self,
        primary: Binding,
        fallback: Binding | None,
        small: Binding,
        *,
        fallback_on_rate_limit: bool,
        limiter: Limiter,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.small = small
        self.fallback_on_rate_limit = fallback_on_rate_limit
        self.limiter = limiter

    async def turn(self, request: TurnRequest) -> ModelTurn:
        """The primary's turn; on a 429 or a spent day, the fallback's, unless evals forbid it."""
        calls: list[ModelCall] = []
        try:
            try:
                return await self._turn(self.primary, request, calls)
            except (RateLimited, QuotaExhausted):
                if self.fallback is None or not self.fallback_on_rate_limit:
                    raise
                return await self._turn(self.fallback, request, calls)
        except (ModelError, BudgetRefused) as error:
            error.calls = calls
            raise

    def available(self, conn: Connection) -> bool:
        if not self.limiter.spent(conn, self.primary.role):
            return True
        fallback = self.fallback
        return (
            fallback is not None
            and self.fallback_on_rate_limit
            and not self.limiter.spent(conn, fallback.role)
        )

    async def summarize(self, page: str, remaining: int) -> tuple[str | None, list[ModelCall]]:
        """A summary of one log page by the small role; None when it cannot fit or fails."""
        role, provider = self.small.role, self.small.provider
        bound = len(SUMMARY_SYSTEM.encode()) + len(page.encode()) + PROMPT_OVERHEAD
        max_output = min(SUMMARY_OUTPUT_CAP, remaining - bound)
        if max_output < SUMMARY_MIN_OUTPUT:
            return None, []
        try:
            await self.limiter.acquire(role)
        except QuotaExhausted as error:
            return None, [_call(role, "quota_exhausted", bound, max_output, error=str(error))]
        started = time.perf_counter()
        try:
            summary, usage = await asyncio.wait_for(
                provider.complete(SUMMARY_SYSTEM, page, max_output), REQUEST_TIMEOUT + 5
            )
        except Exception as error:
            usage = error.usage if isinstance(error, ModelError) else Usage()
            failed = _call(
                role, _outcome(error), bound, max_output, usage, _ms(started), _err(error)
            )
            return None, [failed]
        ok = _call(role, "ok", bound, max_output, usage, _ms(started))
        return summary.strip() or None, [ok]

    async def _turn(
        self, binding: Binding, request: TurnRequest, calls: list[ModelCall]
    ) -> ModelTurn:
        role, provider = binding.role, binding.provider
        limits = request.budget.limits(role.model)
        if limits is None:
            raise BudgetRefused
        bound, max_output = limits
        try:
            await self.limiter.acquire(role)
        except QuotaExhausted as error:
            calls.append(_call(role, "quota_exhausted", bound, max_output, error=str(error)))
            raise
        started = time.perf_counter()
        try:
            out = await asyncio.wait_for(
                provider.generate(
                    request.system, request.messages, request.tools, request.allowed, max_output
                ),
                REQUEST_TIMEOUT + 5,
            )
        except ModelError as error:
            outcome = _outcome(error)
            calls.append(
                _call(role, outcome, bound, max_output, error.usage, _ms(started), _err(error))
            )
            raise
        except Exception as error:
            calls.append(
                _call(role, "error", bound, max_output, Usage(), _ms(started), _err(error))
            )
            raise ModelError(f"{role.provider} call failed: {type(error).__name__}") from error
        calls.append(_call(role, "ok", bound, max_output, out.usage, _ms(started)))
        return ModelTurn(
            action=_action(out.tool, out.args),
            tokens=TokenUsage(input=out.usage.input, output=out.usage.output),
            calls=calls,
            provider_state=out.provider_state,
            model=role.model,
        )


def _action(name: str, args: dict[str, Any]) -> ToolRequest | ReportDraft:
    """A valid report, or a tool call: an invalid report goes back to the model as a failed call."""
    if name == REPORT_TOOL:
        try:
            return ReportDraft.model_validate(args)
        except ValidationError:
            pass
    reason = str(args.pop("reason", "") or "").strip()[:2000] or f"Call {name}."
    return ToolRequest(tool=name, args=args, reason=reason)


def _call(
    role: Role,
    outcome: Outcome,
    bound: int,
    max_output: int,
    usage: Usage = NO_USAGE,
    latency_ms: int = 0,
    error: str | None = None,
) -> ModelCall:
    return ModelCall(
        role.name, role.provider, role.model, outcome, usage, bound, max_output, latency_ms, error
    )


def _outcome(error: Exception) -> Outcome:
    if isinstance(error, RateLimited):
        return "rate_limited"
    return "quota_exhausted" if isinstance(error, QuotaExhausted) else "error"


def _err(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:500]


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
