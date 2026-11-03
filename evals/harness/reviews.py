"""Part B: the plan reviewer (agent.reviewer) on kept plans, before it goes live (docs/todo.md).

    make eval-reviews [ESTIMATE=1] [MAX_USD=n] [REVIEW_MODEL=claude-opus-5-5] [FRESH=1]

The dev set is the 44 plans the planner measurement kept on the dev split (Opus 5.5 at effort
medium on 2026-11-02, high and max on 2026-11-03), where the rules are tuned. FRESH=1 takes the
fresh set instead: the max plans `make eval-plans FRESH=1` made for 16 test incidents, which must
be judged in evals/plan-judgments.json before their review runs (the user's honest check). Plans
are kept in evals/reports/plans/ (git-ignored).

Each plan gets what a live one will: the code checks (agent.planner.plan_refusal: the target's
kind, the restart rules, each file's shape and the source allowlist; the sim has no repo, so no
source index), then, if they pass, one call to the arm's Claude at effort high: a structured
output with no tools, whose prompt is the planner's view of the incident, the plan and TC's
rules. Its issues decide the verdict (agent.reviewer.verdict_of). It never sees the labels or the
judgments, which the report shows beside each review with what each judgment expects: a refusal
for a "to rule it out" step or a pattern as a file, at least a flag for any other bad step. Each
call may answer CAP output tokens, so none is cut off; production's allowance comes from what
these reviews used.

A review the model answered is kept in evals/reports/reviews/ under a key of the reviewer's
prompt sha, model, effort and allowance, the plan and its triage run, so a stopped pass goes on
where it stopped. The finished pass writes evals/reports/<date>-<dev|fresh>-reviews-<reviewer sha,
first 8>-<model>-<effort>.json and .md.

Exit codes: 0 the bar is met (no trusted plan refused, every expectation met); 1 it is not; 2 the
pass stopped on a failed call or the spend limit, so run it again to go on; 3 the setup is wrong.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import eval as harness
import plans

from agent.model import PROMPT_OVERHEAD, ModelError, ToolSpec, Usage
from agent.planner import PlanDraft, plan_refusal
from agent.pricing import RATES, cost, worst
from agent.providers import DEFAULT_BASE_URLS, AnthropicProvider
from agent.reviewer import (
    REVIEWER_PROMPT_SHA,
    ReviewDraft,
    review_problems,
    review_request,
    verdict_of,
)
from agent.settings import Effort, Settings
from py_config import ConfigError, load_settings
from sim.scenario import Scenario

Json = dict[str, Any]
# A kept plan to review: its set (the planner's effort, or fresh), scenario, triage run and plan.
Item = tuple[str, Scenario, Json, Json]
REVIEWS = harness.REPORTS / "reviews"
JUDGMENTS = harness.REPO / "evals" / "plan-judgments.json"
EFFORTS = ("medium", "high", "max")
FRESH = ("fresh",)
PLANNER = "claude-opus-5-5"
# The two arms the user asked for (2026-11-03), both at effort high.
REVIEWERS = ("claude-sonnet-5-5", "claude-opus-5-5")
EFFORT: Effort = "high"
CAP = 32_000
# A guess at a review's output, thinking included, for the estimate's range.
OUTPUT_GUESS = (1_000, 6_000)
BROKE, STOPPED, SETUP = 1, 2, 3


class Reviewer(Protocol):
    async def structured(
        self, system: str, text: str, shape: ToolSpec, max_output_tokens: int, effort: Effort | None
    ) -> tuple[dict[str, Any], Usage]: ...


@dataclass(frozen=True)
class Arm:
    """A reviewer's Claude: its model, by which it is priced, and its provider."""

    model: str
    provider: Reviewer


def judgments(sections: Sequence[str]) -> dict[tuple[str, str], Json]:
    """The kept plans' judgments by (set, scenario), each with what it expects of the review:
    refuse, flag, or None for nothing in particular. The report alone reads them."""
    data = harness.load(JUDGMENTS)
    return {
        (section, sid): {"expect": None, **found}
        for section in sections
        for sid, found in data.get(section, {}).items()
    }


def saved(runs: Sequence[plans.Run]) -> list[Item]:
    """Every dev plan make eval-plans kept on Opus, by effort, then scenario."""
    found: list[Item] = []
    for effort in EFFORTS:
        for scenario, triage in runs:
            path = plans.PLANS / plans.kept_name(PLANNER, effort, scenario, triage)
            row = harness.load(path) if path.exists() else None
            if row is not None and row["plan"] is not None:
                found.append((effort, scenario, triage, row["plan"]))
    return found


def saved_fresh(runs: Sequence[plans.Run]) -> list[Item]:
    """Every fresh plan make eval-plans FRESH=1 kept at effort max; the set needs all of them."""
    found: list[Item] = []
    for scenario, triage in runs:
        path = plans.PLANS / plans.kept_name(PLANNER, "max", scenario, triage)
        row = harness.load(path) if path.exists() else None
        if row is None or row["plan"] is None:
            raise harness.Setup(f"no kept max plan of {scenario.id}: run make eval-plans FRESH=1")
        found.append(("fresh", scenario, triage, row["plan"]))
    return found


def check_judged(items: Sequence[Item], judged: Mapping[tuple[str, str], Json]) -> None:
    """Refuses a pass while a fresh plan has no judgment: each is judged before its review."""
    for section, scenario, _, _ in items:
        if section == "fresh" and (section, scenario.id) not in judged:
            raise harness.Setup(
                f"judge {scenario.id} in evals/plan-judgments.json before its review runs"
            )


def kept_name(
    model: str, effort: str, scenario: Scenario, triage: Json, plan: Json, repeat: int = 1
) -> str:
    """A kept review's name; a repeat past the first is another sample of the same review."""
    key = {
        "prompt": REVIEWER_PROMPT_SHA,
        "model": model,
        "effort": EFFORT,
        "cap": CAP,
        "plan": plan,
        "triage": triage["key"],
    } | ({"repeat": repeat} if repeat > 1 else {})
    return f"{scenario.id}-{effort}-{harness.sha(json.dumps(key, sort_keys=True))[:16]}.json"


def price(model: str, usage: Usage) -> float:
    return cost(
        RATES[model],
        input=usage.input,
        cached=usage.cached,
        cache_writes=usage.cache_writes,
        output=usage.output,
    )


def asked(scenario: Scenario, triage: Json, plan: Json) -> tuple[str | None, int]:
    """The code checks' refusal, or None, and the review prompt's bytes."""
    found, draft = plans.catalog(scenario), PlanDraft.model_validate(plan)
    refused = plan_refusal(draft, found, None)
    return refused, review_request(plans.request(found, triage).text, draft).bound


async def review_one(arm: Arm, scenario: Scenario, triage: Json, effort: str, plan: Json) -> Json:
    """One plan's review as the agent makes it: the code checks, then, if they pass, one call."""
    found, draft = plans.catalog(scenario), PlanDraft.model_validate(plan)
    row: Json = {
        "effort": effort,
        "scenario": scenario.id,
        "plan": plan,
        "verdict": None,
        "model_verdict": None,
        "by": None,
        "issues": [],
        "problem": None,
        "call": None,
        "cost": 0.0,
    }
    refused = plan_refusal(draft, found, None)
    if refused is not None:
        return {**row, "verdict": "refuse", "by": "check", "problem": refused}
    request = review_request(plans.request(found, triage).text, draft)
    started = time.perf_counter()
    answer: dict[str, Any] | None = None
    error: str | None = None
    try:
        answer, usage = await arm.provider.structured(
            request.system, request.text, request.shape, CAP, EFFORT
        )
    except Exception as failed:  # every failure is recorded, as Router.plan records its own
        usage = failed.usage if isinstance(failed, ModelError) else Usage()
        error = f"{type(failed).__name__}: {failed}"[:500]
    call = {
        "outcome": "error" if error else "ok",
        "error": error,
        "bound": request.bound,
        "usage": {"input": usage.input, "output": usage.output, "cached": usage.cached},
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }
    row = {**row, "call": call, "cost": price(arm.model, usage)}
    if answer is None:
        return {**row, "problem": error}
    problem = review_problems(answer)
    if problem is not None:
        return {**row, "problem": problem, "answer": answer}
    review = ReviewDraft.model_validate(answer)
    verdict = verdict_of(review.issues)
    return {
        **row,
        "verdict": verdict,
        "model_verdict": None if review.verdict == verdict else review.verdict,
        "by": "model",
        "issues": [issue.model_dump() for issue in review.issues],
    }


def answered(row: Json) -> bool:
    """Whether the row is kept: a code refusal, or a call the model answered, even invalidly; a
    call that failed before any output is asked again on the next pass."""
    call = row["call"]
    return call is None or call["outcome"] == "ok" or call["usage"]["output"] > 0


def acceptance(rows: Sequence[Json]) -> list[str]:
    """Why the pass misses the bar, or nothing: a trusted plan refused, a plan whose step must be
    refused that was not, or a plan with any other bad step that got neither flag nor refusal."""
    found: list[str] = []
    for row in rows:
        at, verdict, expect = f"{row['effort']} {row['scenario']}", row["verdict"], row["expect"]
        if row["judgment"] == "trusted" and verdict == "refuse":
            found.append(f"{at}: trusted, but refused")
        elif expect == "refuse" and verdict != "refuse":
            found.append(f"{at}: {verdict}, but its step must be refused")
        elif expect == "flag" and verdict not in ("flag", "refuse"):
            found.append(f"{at}: {verdict}, but it must be flagged at least")
    return found


def inconsistent(rows: Sequence[Json]) -> list[str]:
    """Near-identical plans, the same scenario's with the same expectation, that got different
    verdicts."""
    groups: dict[tuple[str, str | None], list[Json]] = {}
    for row in rows:
        groups.setdefault((row["scenario"], row["expect"]), []).append(row)
    return [
        f"{sid} ({expect or 'none'}): "
        + ", ".join(f"{row['effort']} {row['verdict']}" for row in group)
        for (sid, expect), group in groups.items()
        if len(group) > 1 and len({row["verdict"] for row in group}) > 1
    ]


def estimate(model: str, items: Sequence[Item]) -> None:
    """The pass's most and likely cost, from each prompt's bytes; nothing is called."""
    bounds: list[int] = []
    refused = 0
    for effort, scenario, triage, plan in items:
        if (REVIEWS / kept_name(model, effort, scenario, triage, plan)).exists():
            continue
        refusal, bound = asked(scenario, triage, plan)
        if refusal is None:
            bounds.append(bound)
        else:
            refused += 1
    harness.say(
        f"{len(items)} kept plans; {refused} refused by the code checks with no call; "
        f"{len(bounds)} to review on {model}, effort {EFFORT}."
    )
    if not bounds:
        return
    # The planner's bytes a token on the dev incidents (Opus 5.5's tokenizer), as the guess.
    rows = [harness.load(path) for path in plans.PLANS.glob("*.json")]
    calls = [(r["bound"], r["calls"][0]["usage"]["input"]) for r in rows if r["calls"]]
    per_token = sum(b - PROMPT_OVERHEAD for b, _ in calls) / sum(t for _, t in calls)
    rates = RATES[model]

    def likely(output: int) -> float:
        return sum(
            cost(rates, input=round(b / per_token), cached=0, cache_writes=0, output=output)
            for b in bounds
        )

    most = sum(worst(rates, b, CAP, caches=False) for b in bounds)
    harness.say(
        f"Prompts {min(bounds):,} to {max(bounds):,} bytes. The most the pass can cost: "
        f"${most:.2f} (each prompt's bytes as tokens and {CAP:,} output tokens)."
    )
    harness.say(
        f"Likely: ${likely(OUTPUT_GUESS[0]):.2f} to ${likely(OUTPUT_GUESS[1]):.2f}, at "
        f"{per_token:.2f} bytes a token (the planner's on the dev incidents) and "
        f"{OUTPUT_GUESS[0]:,} to {OUTPUT_GUESS[1]:,} output tokens a review, thinking included."
    )


async def make(
    arm: Arm,
    items: Sequence[Item],
    judged: Mapping[tuple[str, str], Json],
    rows: list[Json],
    limit: float | None,
    repeat: int = 1,
) -> bool:
    """Reviews every plan not kept yet, one at a time; False when a call or the spend limit stopped
    the pass. The limit holds each next review's worst case, so the pass never spends past it."""
    spent = 0.0
    for effort, scenario, triage, plan in items:
        judgment = judged.get(
            (effort, scenario.id), {"judgment": "not judged", "expect": None, "note": ""}
        )
        path = REVIEWS / kept_name(arm.model, effort, scenario, triage, plan, repeat)
        if path.exists():
            rows.append({**harness.load(path), **judgment})
            continue
        refusal, bound = asked(scenario, triage, plan)
        most = 0.0 if refusal else worst(RATES[arm.model], bound, CAP, caches=False)
        if not plans.fits(limit, spent, most):
            harness.say(
                f"eval-reviews: stopped before {effort} {scenario.id}: ${spent:.2f} spent, and its "
                f"worst case ${most:.2f} would pass the limit of ${limit or 0:.2f}"
            )
            return False
        row = await review_one(arm, scenario, triage, effort, plan)
        spent += row["cost"]
        verdict = row["verdict"] or f"no review ({row['problem']})"
        harness.say(f"{effort} {scenario.id}: {verdict}, ${row['cost']:.4f}")
        if not answered(row):
            harness.say(f"eval-reviews: stopped, {row['problem']}")
            return False
        if row["by"] != "check":
            harness.save(path, row)
        rows.append({**row, **judgment})
    return True


def summary(rows: Sequence[Json]) -> Json:
    calls = [row["call"] for row in rows if row["call"] is not None]
    costs = [row["cost"] for row in rows if row["call"] is not None]
    outputs = [call["usage"]["output"] for call in calls]
    latencies = [call["latency_ms"] for call in calls]
    table = Counter(f"{row['judgment']}: {row['verdict'] or 'no review'}" for row in rows)
    return {
        "plans": len(rows),
        "by_judgment": dict(sorted(table.items())),
        "acceptance": acceptance(rows),
        "inconsistent": inconsistent(rows),
        "model_verdict_differs": sum(row["model_verdict"] is not None for row in rows),
        "cost": {
            "total": sum(costs),
            "mean": statistics.fmean(costs) if costs else 0.0,
            "max": max(costs, default=0.0),
        },
        "output_tokens": {
            "mean": statistics.fmean(outputs) if outputs else 0.0,
            "max": max(outputs, default=0),
        },
        "latency_ms": {
            "median": statistics.median(latencies) if latencies else 0,
            "max": max(latencies, default=0),
        },
    }


def markdown(report: Json) -> str:
    total = report["summary"]
    spend, output, latency = total["cost"], total["output_tokens"], total["latency_ms"]
    bar = "; ".join(total["acceptance"])
    plans_of = (
        "the fresh test incidents' max plans"
        if report["set"] == "fresh"
        else "the dev plans make eval-plans kept (claude-opus-5-5 at medium, high and max)"
    )
    lines = [
        f"# Plan reviewer on {report['set']} plans ({report['date']})",
        "",
        f"Reviewer {report['reviewer']['model']} at effort {report['reviewer']['effort']}, prompt "
        f"sha {report['reviewer']['prompt_sha'][:16]}, up to {CAP:,} output tokens, on "
        f"{plans_of}.",
        "",
        f"- Plans: {total['plans']}.",
        "- By judgment and verdict: "
        + "; ".join(f"{k} {n}" for k, n in total["by_judgment"].items())
        + ".",
        f"- Cost: ${spend['total']:.4f} in all; per review mean ${spend['mean']:.4f}, max "
        f"${spend['max']:.4f}.",
        f"- Output tokens, thinking included: mean {output['mean']:,.0f}, max {output['max']:,}. "
        f"Latency: median {latency['median'] / 1000:.1f} s, max {latency['max'] / 1000:.1f} s.",
        f"- The bar: {'met' if not bar else 'missed: ' + bar}.",
        "- Near-identical plans with different verdicts: "
        + ("; ".join(total["inconsistent"]) or "none")
        + ".",
        f"- Reviews whose model verdict differed from its issues': "
        f"{total['model_verdict_differs']}.",
        "",
        "| Set | Scenario | Judgment | Expects | Plan | Verdict | By | Tokens in / out | Cost |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["reviews"]:
        plan, call = row["plan"], row["call"]
        tokens = "" if call is None else f"{call['usage']['input']:,} / {call['usage']['output']:,}"
        lines.append(
            f"| {row['effort']} | {row['scenario']} | {row['judgment']} | "
            f"{row['expect'] or ''} | {plan['kind']} {plan['target']} | "
            f"{row['verdict'] or 'no review'} | {row['by'] or ''} | {tokens} | ${row['cost']:.4f} |"
        )
    lines += ["", "## Reviews", ""]
    for row in report["reviews"]:
        plan = row["plan"]
        files = f" Files: {', '.join(plan['files'])}." if plan["files"] else ""
        expects = f" Expects {row['expect']}." if row["expect"] else ""
        lines += [
            f"### {row['effort']} {row['scenario']}: {row['verdict'] or 'no review'}",
            "",
            f"Judged {row['judgment']}: {row['note']}{expects}",
            "",
            f"Plan: {plan['kind']} of {plan['target']}.{files}",
            "",
            plan["rationale"],
            "",
        ]
        if row["problem"]:
            why = "Refused by the code checks" if row["by"] == "check" else "No review"
            lines += [f"{why}: {row['problem']}", ""]
        lines += [f'- {i["kind"]}: "{i["quote"]}" {i["reason"]}' for i in row["issues"]]
        lines += [""] if row["issues"] else []
    lines += [
        "## Notes",
        "",
        "- The sim has no repo, so the source index check (each file at origin/main, or new in a "
        "folder that is) does not run here; tests on temporary indexes cover it.",
        "- The sim keeps no watch snapshot, so the code checks refuse every restart for that "
        "reason, as make eval-plans showed.",
        "- One review a plan, at the reviewer's rates (evals/pricing.json); no cap in either "
        "database counts this pass.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Part B: the plan reviewer on the kept plans.")
    parser.add_argument("--estimate", action="store_true", help="print the cost; call nothing")
    parser.add_argument("--max-usd", type=float, help="stop before a review could pass this spend")
    parser.add_argument("--model", choices=REVIEWERS, default=REVIEWERS[0], help="the arm")
    parser.add_argument("--fresh", action="store_true", help="review the fresh set")
    parser.add_argument("--repeat", type=int, default=1, help="another sample of each review")
    args = parser.parse_args()
    scope = "fresh" if args.fresh else "dev"
    again = f"-r{args.repeat}" if args.repeat > 1 else ""
    try:
        settings = load_settings(Settings)
        if args.fresh:
            items, judged = saved_fresh(plans.fresh_runs()), judgments(FRESH)
            check_judged(items, judged)
        else:
            items, judged = saved(plans.triage_runs()), judgments(EFFORTS)
        if args.estimate:
            estimate(args.model, items)
            return 0
        key = settings.anthropic_api_key
        if key is None:
            raise harness.Setup("ANTHROPIC_API_KEY is not set; run it as make eval-reviews")
        if datetime.now(UTC).date() >= settings.llm_claude_until:
            raise harness.Setup(f"Claude's credits ended on {settings.llm_claude_until}")
        provider = AnthropicProvider(
            args.model, key.get_secret_value(), DEFAULT_BASE_URLS["anthropic"]
        )
        rows: list[Json] = []
        arm = Arm(args.model, provider)
        if not asyncio.run(make(arm, items, judged, rows, args.max_usd, args.repeat)):
            harness.say(f"{len(rows)} of {len(items)} reviews kept; run the same command to go on.")
            return STOPPED
    except (harness.Setup, ConfigError) as error:
        harness.say(f"eval-reviews: {error}")
        return SETUP
    today = datetime.now(UTC).date().isoformat()
    report: Json = {
        "date": today,
        "set": scope,
        "reviewer": {"model": args.model, "effort": EFFORT, "prompt_sha": REVIEWER_PROMPT_SHA},
        "summary": summary(rows),
        "reviews": rows,
    }
    name = f"{today}-{scope}-reviews-{REVIEWER_PROMPT_SHA[:8]}-{args.model}-{EFFORT}{again}"
    harness.save(harness.REPORTS / f"{name}.json", report)
    (harness.REPORTS / f"{name}.md").write_text(markdown(report), encoding="utf-8")
    harness.say(f"report: evals/reports/{name}.md")
    return BROKE if report["summary"]["acceptance"] else 0


if __name__ == "__main__":
    sys.exit(main())
