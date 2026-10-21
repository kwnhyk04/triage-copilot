"""The Model the job uses in production: the primary role, then outside evals Claude while its
credits last and the free fallback, on a 429 or a spent quota; the small role for log-page
summaries, with Claude after it too; and a limiter that keeps each role under its requests per
minute and per day, and off any model whose provider said it is spent.

Every request is returned as a ModelCall, with its outcome and usage, for the job to record.
"""

import asyncio
import logging
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Connection, text

from agent.model import (
    NO_USAGE,
    PROMPT_LIMIT,
    PROMPT_OVERHEAD,
    BudgetRefused,
    CreditsExhausted,
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

log = logging.getLogger(__name__)

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


# The provider has said no request gets through: a daily quota until midnight Pacific, Claude's
# credits or spend limit until the month ends (UTC), when Anthropic resets spend limits.
EXHAUSTED = text("""
    SELECT EXISTS (
        SELECT 1 FROM agent.model_calls
        WHERE model = :model AND (
            (outcome = 'quota_exhausted'
             AND created_at >= date_trunc('day', now() AT TIME ZONE 'America/Los_Angeles')
                               AT TIME ZONE 'America/Los_Angeles')
            OR (outcome = 'credits_exhausted'
                AND created_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
        )
    )
""")


def calls_today(conn: Connection, model: str) -> int:
    with conn.begin():
        return conn.execute(CALLS_TODAY, {"model": model}).scalar_one()


def spent(conn: Connection, role: Role) -> bool:
    """No request to the role's model can get through now: Claude's credits have expired, today's
    requests are used up, or the provider said so (quota today, credits this month). Parked
    investigations then stay parked instead of trying again every sweep."""
    if role.until is not None and datetime.now(UTC).date() >= role.until:
        return True
    if role.rpd > 0 and calls_today(conn, role.model) >= role.rpd:
        return True
    with conn.begin():
        return bool(conn.execute(EXHAUSTED, {"model": role.model}).scalar_one())


class Limiter:
    """Keeps each role under its RPM (a sliding minute), and refuses a spent one before any request.

    ponytail: the minute window lives in this process and the day count sees only recorded calls,
    so several agent processes, or calls still in flight, can pass a limit by a few requests;
    move the window into Postgres if several agents share one key.
    """

    def __init__(
        self,
        is_spent: Callable[[Role], bool] | None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._is_spent = is_spent
        self._clock = clock
        self._sleep = sleep
        self._windows: defaultdict[str, deque[float]] = defaultdict(deque)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, role: Role) -> None:
        """Waits for a free request this minute; raises QuotaExhausted while the role is spent."""
        if self._is_spent is not None and await asyncio.to_thread(self._is_spent, role):
            raise QuotaExhausted(f"{role.model}: spent (daily requests, credits or their end date)")
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
        claude: Binding | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.small = small
        self.fallback_on_rate_limit = fallback_on_rate_limit
        self.limiter = limiter
        self.claude = claude

    def _chain(self, first: Binding, *fallbacks: Binding | None) -> list[Binding]:
        """The role's own model, then, unless evals pin it alone, its fallbacks in order."""
        if not self.fallback_on_rate_limit:
            return [first]
        return [first, *(binding for binding in fallbacks if binding is not None)]

    async def turn(self, request: TurnRequest) -> ModelTurn:
        """The first of the primary, Claude and the free fallback that answers: a 429 or a spent
        quota moves the turn down the chain, except Claude's 429 with a retry-after, which the job
        waits out and asks again."""
        calls: list[ModelCall] = []
        chain = self._chain(self.primary, self.claude, self.fallback)
        try:
            for binding in chain[:-1]:
                try:
                    return await self._turn(binding, request, calls)
                except QuotaExhausted:
                    continue
                except RateLimited:
                    if binding is self.claude:
                        raise
            return await self._turn(chain[-1], request, calls)
        except (ModelError, BudgetRefused) as error:
            error.calls = calls
            raise

    def available(self, conn: Connection) -> bool:
        """Whether some model of the turn's chain can answer now."""
        chain = self._chain(self.primary, self.claude, self.fallback)
        return any(not spent(conn, binding.role) for binding in chain)

    async def summarize(self, page: str, remaining: int) -> tuple[str | None, list[ModelCall]]:
        """A summary of one log page by the small role, or by Claude while the small role is rate
        limited or spent; None when it cannot fit or fails."""
        bound = len(SUMMARY_SYSTEM.encode()) + len(page.encode()) + PROMPT_OVERHEAD
        max_output = min(SUMMARY_OUTPUT_CAP, remaining - bound)
        if max_output < SUMMARY_MIN_OUTPUT or bound > PROMPT_LIMIT:
            return None, []
        calls: list[ModelCall] = []
        for binding in self._chain(self.small, self.claude):
            summary, call = await self._summary(binding, page, bound, max_output)
            calls.append(call)
            if call.outcome not in ("rate_limited", "quota_exhausted", "credits_exhausted"):
                return summary, calls
        return None, calls

    async def _summary(
        self, binding: Binding, page: str, bound: int, max_output: int
    ) -> tuple[str | None, ModelCall]:
        role, provider = binding.role, binding.provider
        try:
            await self.limiter.acquire(role)
        except QuotaExhausted as error:
            return None, _call(role, "quota_exhausted", bound, max_output, error=str(error))
        started = time.perf_counter()
        try:
            summary, usage = await asyncio.wait_for(
                provider.complete(SUMMARY_SYSTEM, page, max_output), REQUEST_TIMEOUT + 5
            )
        except Exception as error:
            _say_if_credits_gone(role, error)
            usage = error.usage if isinstance(error, ModelError) else Usage()
            failed = _call(
                role, _outcome(error), bound, max_output, usage, _ms(started), _err(error)
            )
            return None, failed
        return summary.strip() or None, _call(role, "ok", bound, max_output, usage, _ms(started))

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
            _say_if_credits_gone(role, error)
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
    if isinstance(error, CreditsExhausted):
        return "credits_exhausted"
    return "quota_exhausted" if isinstance(error, QuotaExhausted) else "error"


def _say_if_credits_gone(role: Role, error: Exception) -> None:
    if not isinstance(error, CreditsExhausted):
        return
    today = datetime.now(UTC).date()
    back = date(today.year + today.month // 12, today.month % 12 + 1, 1)
    log.warning(
        "Claude's credits or spend limit are used up: no Claude call until the month ends, and "
        "turns go on down the chain",
        extra={"model": role.model, "until": back.isoformat(), "detail": str(error)},
    )


def _err(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:500]


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
