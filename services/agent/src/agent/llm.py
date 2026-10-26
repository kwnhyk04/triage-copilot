"""The Model the job uses in production: the primary role, then outside evals Claude while its
credits last and the free fallback, on a 429, a spent quota, a rejected key or a refusal; the
small role for log-page summaries, with Claude after it too; and a limiter that keeps each role
under its requests per minute and per day, and off any model whose provider said it is spent.
Live triage's router asks Claude first, under its daily dollar cap, then the same free chain; its
plan for a real incident asks the planner's Claude, under the planner's own daily cap, then the
free chain. Every Claude call also stays within TC's monthly dollar cap, evals included.

Every request is returned as a ModelCall, with its outcome, usage and prompt sha, for the job to
record; a skipped one says why. Provider text is masked (watch.scrub) before it is kept or logged.
"""

import asyncio
import logging
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import Connection, text

from agent.model import (
    NO_USAGE,
    PROMPT_LIMIT,
    PROMPT_OVERHEAD,
    BudgetRefused,
    CreditsExhausted,
    KeyRejected,
    ModelCall,
    ModelError,
    ModelExhausted,
    ModelTurn,
    Outcome,
    PlanRequest,
    QuotaExhausted,
    RateLimited,
    Refused,
    ReportDraft,
    ToolRequest,
    TurnRequest,
    Usage,
)
from agent.pricing import RATES, cost, worst
from agent.prompt import REPORT_TOOL
from agent.providers import PLAN_TIMEOUT, REQUEST_TIMEOUT, Provider
from agent.settings import Role, RoleName
from agent.steps import TokenUsage
from agent.switches import is_on
from watch.scrub import mask

log = logging.getLogger(__name__)

# Claude's 429 with a retry-after up to this many seconds is waited out by the job; a longer one,
# or none, moves the turn down the chain.
MAX_WAIT = 60
# The largest call a turn makes: a prompt at the limit, and the forced report's output allowance
# (job.REPORT_OUTPUT_CAP; a test keeps the two equal).
LARGEST_CALL = (PROMPT_LIMIT, 4_096)
# A plan's output allowance, thinking included.
PLAN_OUTPUT_CAP = 16_000
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


# The provider has said no request gets through, the longest stop first: credits or a spend limit
# for every model of the provider, and a 429 without retry-after for that model, until the month
# ends (UTC), when Anthropic resets spend limits; a daily quota until midnight Pacific.
# ponytail: the provider clause scans model_calls; index (provider, created_at) WHERE outcome =
# 'credits_exhausted' if the table grows large.
STOPPED = text("""
    SELECT outcome FROM agent.model_calls
    WHERE (provider = :provider AND outcome = 'credits_exhausted'
           AND created_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
       OR (model = :model AND outcome = 'model_exhausted'
           AND created_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
       OR (model = :model AND outcome = 'quota_exhausted'
           AND created_at >= date_trunc('day', now() AT TIME ZONE 'America/Los_Angeles')
                             AT TIME ZONE 'America/Los_Angeles')
    ORDER BY CASE outcome WHEN 'credits_exhausted' THEN 0 WHEN 'model_exhausted' THEN 1 ELSE 2 END
    LIMIT 1
""")
# Claude's tokens per model since this UTC day or month began (:period), of the calls made for the
# roles a cap counts (:roles), for the dollar caps.
SPEND_SINCE = text("""
    SELECT model, sum(input_tokens)::bigint AS input, sum(cached_tokens)::bigint AS cached,
           sum(cache_write_tokens)::bigint AS writes, sum(output_tokens)::bigint AS output
    FROM agent.model_calls
    WHERE provider = 'anthropic' AND role::text = ANY(:roles)
      AND created_at >= date_trunc(:period, now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
    GROUP BY model
""")
# Whose calls each cap counts: the planner's daily cap its plans, the triage cap every other
# Claude call of the day (evals included), and TC's monthly cap all of them.
PLANS: tuple[RoleName, ...] = ("planner",)
TURNS: tuple[RoleName, ...] = ("primary", "fallback", "small")
# A dollar cap: its period, how a reason names it, the cap, whose calls it counts, its name, and
# what those calls spent.
Cap = tuple[Literal["day", "month"], str, float | None, tuple[RoleName, ...], str, str]
STOP_REASONS = {
    "credits_exhausted": "its provider's credits or spend limit are used up, for every model until "
    "the month ends (UTC)",
    "model_exhausted": "its provider answered it 429 without retry-after, so it is off until the "
    "month ends (UTC)",
    "quota_exhausted": "its provider said its daily quota is spent, until midnight Pacific",
}


def calls_today(conn: Connection, model: str) -> int:
    with conn.begin():
        return conn.execute(CALLS_TODAY, {"model": model}).scalar_one()


def claude_spend(
    conn: Connection, period: Literal["day", "month"], roles: tuple[RoleName, ...] = PLANS + TURNS
) -> float | str:
    """What Claude cost since this UTC day or month began for the roles' calls, in US dollars,
    every model priced by its own rates; or the name of a model with no rates, whose cost cannot
    be known."""
    with conn.begin():
        rows = conn.execute(SPEND_SINCE, {"period": period, "roles": list(roles)}).all()
    total = 0.0
    for row in rows:
        rates = RATES.get(row.model)
        if rates is None:
            return row.model
        total += cost(
            rates, input=row.input, cached=row.cached, cache_writes=row.writes, output=row.output
        )
    return total


def spent(conn: Connection, role: Role, worst_usd: float = 0.0) -> str | None:
    """Why no request to the role's model can get through now, or None: Claude's credits have
    expired, it is switched off, today's or this month's Claude spend plus the call's worst case
    (worst_usd) passes the role's dollar cap, today's requests are used up, or the provider said
    so. Parked investigations then stay parked instead of trying again every sweep, and the
    skipped call records the reason."""
    if role.until is not None and datetime.now(UTC).date() >= role.until:
        return f"its credits ended on {role.until.isoformat()} (UTC)"
    if role.provider == "anthropic" and not is_on(conn, "claude"):
        return "Claude is switched off (agent.switches)"
    day: Cap = (
        ("day", "today's", role.daily_usd, PLANS, "the planner cap", "spend on plans")
        if role.name == "planner"
        else ("day", "today's", role.daily_usd, TURNS, "the triage cap", "spend")
    )
    month: Cap = (
        "month",
        "this month's",
        role.monthly_usd,
        PLANS + TURNS,
        "TC's monthly Claude cap",
        "spend",
    )
    for period, when, cap, roles, name, what in (day, month):
        if cap is None:
            continue
        spend = claude_spend(conn, period, roles)
        if isinstance(spend, str):
            return f"{spend} has no price in agent/pricing.py, so {name} cannot be checked"
        if spend + worst_usd > cap:
            return (
                f"{when} Claude {what} of ${spend:.4f} plus this call's worst case of "
                f"${worst_usd:.4f} passes {name} of ${cap:.2f} (UTC)"
            )
    if role.rpd > 0 and calls_today(conn, role.model) >= role.rpd:
        return f"it made its {role.rpd} requests for today (Pacific)"
    with conn.begin():
        stop = conn.execute(STOPPED, {"model": role.model, "provider": role.provider}).scalar()
    return STOP_REASONS[stop] if stop else None


class Limiter:
    """Keeps each role under its RPM (a sliding minute), and refuses a spent one before any request.

    ponytail: the minute window lives in this process and the day count sees only recorded calls,
    so several agent processes, or calls still in flight, can pass a limit by a few requests;
    move the window into Postgres if several agents share one key.
    """

    def __init__(
        self,
        is_spent: Callable[[Role, float], str | None] | None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._is_spent = is_spent
        self._clock = clock
        self._sleep = sleep
        self._windows: defaultdict[str, deque[float]] = defaultdict(deque)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, role: Role, worst_usd: float = 0.0) -> None:
        """Waits for a free request this minute; raises QuotaExhausted, with spent()'s reason,
        while the role is spent or, for a role with a dollar cap, the call's worst case
        (worst_usd) would pass it."""
        is_spent = self._is_spent
        reason = None if is_spent is None else await asyncio.to_thread(is_spent, role, worst_usd)
        if reason is not None:
            raise QuotaExhausted(f"{role.model} skipped: {reason}")
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
        claude_first: bool = False,
        planner: Binding | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.small = small
        self.fallback_on_rate_limit = fallback_on_rate_limit
        self.limiter = limiter
        self.claude = claude
        # Live triage: Claude before the free chain, for turns and summaries alike.
        self.claude_first = claude_first
        # The planner's Claude, before the free chain of a plan.
        self.planner = planner

    def _chain(self, *bindings: Binding | None) -> list[Binding]:
        """The bindings that exist, in order; evals pin the first alone."""
        chain = [binding for binding in bindings if binding is not None]
        return chain if self.fallback_on_rate_limit else chain[:1]

    def _turns(self) -> list[Binding]:
        if self.claude_first:
            return self._chain(self.claude, self.primary, self.fallback)
        return self._chain(self.primary, self.claude, self.fallback)

    async def turn(self, request: TurnRequest) -> ModelTurn:
        """The first model of the chain that answers: a 429, a spent quota, a rejected key or a
        refusal moves the turn down the chain, except Claude's 429 with a retry-after of at most
        MAX_WAIT seconds, which the job waits out and asks again."""
        calls: list[ModelCall] = []
        chain = self._turns()
        try:
            for binding in chain[:-1]:
                try:
                    return await self._turn(binding, request, calls)
                except (QuotaExhausted, KeyRejected, Refused):
                    continue
                except RateLimited as error:
                    wait = error.retry_after
                    if binding is self.claude and wait is not None and wait <= MAX_WAIT:
                        raise
            return await self._turn(chain[-1], request, calls)
        except (ModelError, BudgetRefused) as error:
            error.calls = calls
            raise

    def available(self, conn: Connection) -> bool:
        """Whether some model of the turn's chain can answer now, judged on the largest call: a
        Claude with less of its dollar cap left waits for the cap to reset, not for every sweep."""
        return any(
            spent(conn, binding.role, _worst(binding.role, *LARGEST_CALL)) is None
            for binding in self._turns()
        )

    async def summarize(
        self, page: str, remaining: int, prompt_sha: str
    ) -> tuple[str | None, list[ModelCall]]:
        """A summary of one log page by the small role, or by Claude while the small role is rate
        limited or spent, its key is rejected or it refuses (Claude first for live triage); None
        when it cannot fit or fails."""
        bound = len(SUMMARY_SYSTEM.encode()) + len(page.encode()) + PROMPT_OVERHEAD
        max_output = min(SUMMARY_OUTPUT_CAP, remaining - bound)
        if max_output < SUMMARY_MIN_OUTPUT or bound > PROMPT_LIMIT:
            return None, []
        calls: list[ModelCall] = []
        order = (self.claude, self.small) if self.claude_first else (self.small, self.claude)
        for binding in self._chain(*order):
            summary, call, moves_on = await self._summary(
                binding, page, bound, max_output, prompt_sha
            )
            # The small role's work whichever model writes it, so LAST_TURN (job.py) skips it.
            calls.append(replace(call, role="small"))
            if not moves_on:
                return summary, calls
        return None, calls

    async def plan(self, request: PlanRequest) -> tuple[dict[str, Any] | None, list[ModelCall]]:
        """The first answer of the plan's chain (the planner's Claude while there is one, then the
        free chain) that request.problems accepts, and every call made, each recorded as the
        planner's; None when no model gave one. A skipped, failed, refused, cut-off or invalid
        answer moves the plan down the chain: a plan is one call, so nothing is retried."""
        calls: list[ModelCall] = []
        bound, sha = request.bound, request.prompt_sha
        for binding in self._chain(self.planner, self.primary, self.fallback):
            role, provider = binding.role, binding.provider
            try:
                # A plan marks nothing for the cache, so its input bills at the input rate.
                worst_usd = _worst(role, bound, PLAN_OUTPUT_CAP, caches=False)
                await self.limiter.acquire(role, worst_usd)
            except QuotaExhausted as error:
                calls.append(_plan_call(role, "skipped", bound, sha, error=str(error)))
                continue
            started = time.perf_counter()
            try:
                answer, usage = await asyncio.wait_for(
                    provider.structured(
                        request.system, request.text, request.shape, PLAN_OUTPUT_CAP, role.effort
                    ),
                    PLAN_TIMEOUT + 5,
                )
            except Exception as error:
                _say_if_credits_gone(role, error)
                usage = error.usage if isinstance(error, ModelError) else Usage()
                calls.append(_plan_call(role, _outcome(error), bound, sha, usage, started, error))
                continue
            problem = request.problems(answer)
            if problem is not None:
                invalid = f"InvalidPlan: {problem}"
                calls.append(_plan_call(role, "error", bound, sha, usage, started, invalid))
                continue
            calls.append(_plan_call(role, "ok", bound, sha, usage, started))
            return answer, calls
        return None, calls

    async def _summary(
        self, binding: Binding, page: str, bound: int, max_output: int, sha: str
    ) -> tuple[str | None, ModelCall, bool]:
        """The summary, its call, and whether the next model of the chain should try."""
        role, provider = binding.role, binding.provider
        try:
            await self.limiter.acquire(role, _worst(role, bound, max_output))
        except QuotaExhausted as error:
            skipped = _call(role, "skipped", bound, max_output, error=str(error), sha=sha)
            return None, skipped, True
        started = time.perf_counter()
        try:
            summary, usage = await asyncio.wait_for(
                provider.complete(SUMMARY_SYSTEM, page, max_output), REQUEST_TIMEOUT + 5
            )
        except Exception as error:
            _say_if_credits_gone(role, error)
            usage = error.usage if isinstance(error, ModelError) else Usage()
            failed = _call(
                role, _outcome(error), bound, max_output, usage, _ms(started), _err(error), sha=sha
            )
            moves_on = isinstance(error, (RateLimited, QuotaExhausted, KeyRejected, Refused))
            return None, failed, moves_on
        done = _call(role, "ok", bound, max_output, usage, _ms(started), sha=sha)
        return summary.strip() or None, done, False

    async def _turn(
        self, binding: Binding, request: TurnRequest, calls: list[ModelCall]
    ) -> ModelTurn:
        role, provider, sha = binding.role, binding.provider, request.prompt_sha
        limits = request.budget.limits(role.model)
        if limits is None:
            raise BudgetRefused
        bound, max_output = limits
        try:
            await self.limiter.acquire(role, _worst(role, bound, max_output))
        except QuotaExhausted as error:
            calls.append(_call(role, "skipped", bound, max_output, error=str(error), sha=sha))
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
            usage, elapsed = error.usage, _ms(started)
            calls.append(
                _call(role, outcome, bound, max_output, usage, elapsed, _err(error), sha=sha)
            )
            raise
        except Exception as error:
            elapsed = _ms(started)
            calls.append(
                _call(role, "error", bound, max_output, Usage(), elapsed, _err(error), sha=sha)
            )
            raise ModelError(f"{role.provider} call failed: {type(error).__name__}") from error
        calls.append(_call(role, "ok", bound, max_output, out.usage, _ms(started), sha=sha))
        return ModelTurn(
            action=_action(out.tool, out.args, request.report),
            tokens=TokenUsage(input=out.usage.input, output=out.usage.output),
            calls=calls,
            provider_state=out.provider_state,
            model=role.model,
        )


def _worst(role: Role, bound: int, max_output: int, *, caches: bool = True) -> float:
    """The call's worst-case cost in US dollars, for a role with a dollar cap; 0 for the others."""
    if role.daily_usd is None and role.monthly_usd is None:
        return 0.0
    return worst(RATES[role.model], bound, max_output, caches=caches)


def _action(
    name: str, args: dict[str, Any], report: type[ReportDraft]
) -> ToolRequest | ReportDraft:
    """A valid report, or a tool call: an invalid report goes back to the model as a failed call."""
    if name == REPORT_TOOL:
        try:
            return report.model_validate(args)
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
    *,
    sha: str,
) -> ModelCall:
    return ModelCall(
        role.name,
        role.provider,
        role.model,
        outcome,
        usage,
        bound,
        max_output,
        latency_ms,
        None if error is None else mask(error)[:500],
        sha,
    )


def _plan_call(
    role: Role,
    outcome: Outcome,
    bound: int,
    sha: str,
    usage: Usage = NO_USAGE,
    started: float | None = None,
    error: Exception | str | None = None,
) -> ModelCall:
    """A plan's call, recorded as the planner's whichever model of the chain it went to."""
    ms = 0 if started is None else _ms(started)
    why = _err(error) if isinstance(error, Exception) else error
    call = _call(role, outcome, bound, PLAN_OUTPUT_CAP, usage, ms, why, sha=sha)
    return replace(call, role="planner")


def _outcome(error: Exception) -> Outcome:
    if isinstance(error, RateLimited):
        return "rate_limited"
    if isinstance(error, CreditsExhausted):
        return "credits_exhausted"
    if isinstance(error, ModelExhausted):
        return "model_exhausted"
    return "quota_exhausted" if isinstance(error, QuotaExhausted) else "error"


def _say_if_credits_gone(role: Role, error: Exception) -> None:
    if not isinstance(error, (CreditsExhausted, ModelExhausted)):
        return
    today = datetime.now(UTC).date()
    back = date(today.year + today.month // 12, today.month % 12 + 1, 1)
    message = (
        "Claude's credits or spend limit are used up: no call to any Anthropic model until the "
        "month ends, and turns go on down the chain"
        if isinstance(error, CreditsExhausted)
        else "Anthropic answered 429 without retry-after: no call to this model until the month "
        "ends, and turns go on down the chain"
    )
    extra = {"model": role.model, "until": back.isoformat(), "detail": mask(str(error))}
    log.warning(message, extra=extra)


def _err(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:500]


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
