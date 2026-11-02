"""P2.5: the planner (agent.planner) on the dev split's triage reports, before the planner switch
goes on in production (docs/todo.md).

    make eval-plans [ESTIMATE=1]

The reports come from P0's Claude-first pass of 2026-11-01 (Haiku 5.5 first, 350,000-token cap),
one cached run per dev scenario in evals/reports/runs/. Each plan gets what a live one gets: the
report, the results it cites, and a catalog, here the scenario's services with the kinds in KINDS.
The sim keeps no watch snapshot, so the planner is told none was read, as for a live alert
without one, and the check then refuses every restart. One call a plan to the planner's Claude
(Settings.planner(): claude-opus-5-5 at effort medium) through Router.plan, with no model behind
it; nothing reaches a database, the gateway or the sim. No call can cost more than its worst case
(the prompt's bytes and 16,000 output tokens), which ESTIMATE=1 adds up without calling anything.

A plan the model answered is kept in evals/reports/plans/ under a key of the planner's prompt
sha, model, effort and its triage run's key, so a stopped pass goes on where it stopped and buys
no plan twice. The finished pass writes evals/reports/<date>-dev-plans-<planner sha, first 8>-
<model>.json and .md.

Exit codes: 0 every plan asked; 1 the check kept a plan that restarts or changes a database,
queue, cache, storage or proxy; 2 the pass stopped on a failed call or Claude's credits, so run
it again to go on; 3 the setup is wrong.
"""

import argparse
import asyncio
import dataclasses
import json
import statistics
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

import eval as harness

from agent.job import prompt_sha
from agent.llm import Binding, Limiter, Router
from agent.model import PROMPT_OVERHEAD, ModelCall, PlanRequest
from agent.planner import (
    PLANNER_PROMPT_SHA,
    PLANNER_SYSTEM,
    RESTARTABLE,
    SOURCES,
    PlanModel,
    parse_plan,
    plan_problems,
    plan_refusal,
    plan_shape,
    planner_text,
)
from agent.pricing import RATES, cost, worst
from agent.providers import make_provider, plan_limits
from agent.settings import Role, Settings
from py_config import ConfigError, load_settings
from sim.scenario import Scenario, load_scenarios
from watch.catalog import AppName, Catalog, Kind, Service

Json = dict[str, Any]
Run = tuple[Scenario, Json]
PLANS = harness.REPORTS / "plans"
BROKE, STOPPED, SETUP = 1, 2, 3
# P0's Claude-first pass (docs/todo.md, P0): Haiku also triages live alerts until 2026-11-10.
TRIAGE = harness.DEFAULTS | {"claude": "claude-haiku-5-5"}
SIM = cast("AppName", "sim")
# The sim's worlds have no repo, so a code fix's files are judged against FilingLens's shape.
SOURCES[SIM] = ("services/**/*.py",)
# What newest_snapshot answers for the sim's alerts, which carry no snapshot.
NO_SNAPSHOT = "no alert of the group has a snapshot"
# Opus 5.5's tokenizer may count up to this many times the tokens of older ones (claude-api skill).
TOKENIZER = 1.35
# A guess at a plan's output, thinking included, for the estimate's range.
OUTPUT_GUESS = (1_000, 6_000)
# Every service of the dev split's worlds as a catalog would kind it. External providers (None)
# are left out, as FreightAudit's and FilingLens's catalogs list no model provider.
_GROUPS: tuple[tuple[Kind | None, str], ...] = (
    (
        "app",
        "accounts cart catalog checkout frontend inventory notifications orders payments pricing "
        "recommendations search shipping web",
    ),
    ("worker", "log-compactor nightly-backup nightly-reindex"),
    ("database", "inventory-db orders-db payments-db"),
    ("queue", "events-broker"),
    ("storage", "feature-store search-index"),
    ("proxy", "coredns"),
    (None, "card-network shipping-api"),
)
KINDS: dict[str, Kind | None] = {name: kind for kind, names in _GROUPS for name in names.split()}


def catalog(scenario: Scenario) -> Catalog:
    """The scenario's services as the planner's catalog, external providers left out."""
    kinds: dict[str, Kind] = {
        name: kind for name in scenario.services if (kind := KINDS[name]) is not None
    }
    services = tuple(
        Service(name, kind, tuple(d for d in scenario.services[name].depends_on if d in kinds))
        for name, kind in kinds.items()
    )
    return Catalog(SIM, {}, services, frozenset(), "level", frozenset(), (), {}, ())


def request(found: Catalog, triage: Json) -> PlanRequest:
    """The request Planner.plan builds, from the triage run's report and its kept results."""
    results = {
        step["tool_call_id"]: step["result"]
        for step in triage["steps"]
        if step["tool_call_id"] and step["result"] is not None
    }
    fired = datetime.fromisoformat(triage["anchor"])
    text = planner_text(found, triage["report"], results, None, NO_SNAPSHOT, fired)
    shape, problems = plan_shape(found), plan_problems(found)
    return PlanRequest(PLANNER_SYSTEM, text, shape, problems, PLANNER_PROMPT_SHA)


def price(call: ModelCall) -> float:
    usage = call.usage
    return cost(
        RATES[call.model],
        input=usage.input,
        cached=usage.cached,
        cache_writes=usage.cache_writes,
        output=usage.output,
    )


async def plan_one(model: PlanModel, scenario: Scenario, triage: Json) -> Json:
    """One plan as Planner.plan makes it, keeping a draft the check refused, with its calls."""
    found = catalog(scenario)
    asked = request(found, triage)
    answer, calls = await model.plan(asked)
    draft = None if answer is None else parse_plan(answer, found)
    return {
        "scenario": scenario.id,
        "triage": triage["key"],
        "bound": asked.bound,
        "plan": None if draft is None else draft.model_dump(),
        "target_kind": None if draft is None else found.service(draft.target).kind,
        "refused": None if draft is None else plan_refusal(draft, found, None),
        "calls": [dataclasses.asdict(call) for call in calls],
        "cost": sum(price(call) for call in calls),
    }


def answered(row: Json) -> bool:
    """Whether the model answered every call (a plan, a refusal, a cut-off or invalid one), so the
    row is kept; a call that failed before any output, or on credits, is asked again later."""
    return all(
        call["outcome"] == "ok" or (call["outcome"] == "error" and call["usage"]["output"] > 0)
        for call in row["calls"]
    )


def acts_on_storage(row: Json) -> bool:
    """A restart or a code fix of anything but an app or a worker: never kept (P2.3)."""
    plan = row["plan"]
    return plan is not None and plan["kind"] != "none" and row["target_kind"] not in RESTARTABLE


def summary(rows: Sequence[Json]) -> Json:
    costs = [row["cost"] for row in rows]
    drafts = [row for row in rows if row["plan"] is not None]
    return {
        "plans": len(rows),
        "answered": len(drafts),
        "kept": sum(row["refused"] is None for row in drafts),
        "refused": [
            {"scenario": row["scenario"], "plan": row["plan"], "reason": row["refused"]}
            for row in drafts
            if row["refused"] is not None
        ],
        "kinds": dict(Counter(row["plan"]["kind"] for row in drafts)),
        "acts_on_storage": [row["scenario"] for row in drafts if acts_on_storage(row)],
        "kept_on_storage": [
            row["scenario"] for row in drafts if acts_on_storage(row) and row["refused"] is None
        ],
        "cost": {
            "total": sum(costs),
            "mean": statistics.fmean(costs) if costs else 0.0,
            "median": statistics.median(costs) if costs else 0.0,
            "max": max(costs, default=0.0),
        },
    }


def triage_runs() -> list[Run]:
    """Each dev scenario with its run of P0's Claude-first pass."""
    manifest = harness.load(harness.SET / "manifest.json")
    scenarios = load_scenarios(harness.SET)
    jobs = harness.plan(manifest, "dev", "full", 1, prompt_sha(), TRIAGE, harness.CLAUDE_CAP)
    missing = [job.scenario for job in jobs if not job.result.exists()]
    if missing:
        names = ", ".join(missing)
        raise harness.Setup(f"no cached Claude-first run of {names} in {harness.CACHE}")
    return [(scenarios[job.scenario].scenario, harness.load(job.result)) for job in jobs]


def kept_name(model: str, effort: str | None, scenario: Scenario, triage: Json) -> str:
    key = {"prompt": PLANNER_PROMPT_SHA, "model": model, "effort": effort, "triage": triage["key"]}
    return f"{scenario.id}-{harness.sha(json.dumps(key, sort_keys=True))[:16]}.json"


def estimate(settings: Settings, runs: Sequence[Run]) -> None:
    """The pass's most and likely cost, from each prompt's bytes; nothing is called."""
    model, effort = settings.llm_planner_model, settings.llm_planner_effort
    pending = [(s, t) for s, t in runs if not (PLANS / kept_name(model, effort, s, t)).exists()]
    bounds = [request(catalog(s), t).bound for s, t in pending]
    rates = RATES[model]
    # Haiku's bytes a token on these runs' first prompts, whose bound is their exact bytes.
    firsts = [t["model_calls"][0] for _, t in runs]
    per_token = sum(c["input_bound"] - PROMPT_OVERHEAD for c in firsts) / sum(
        c["input_tokens"] for c in firsts
    )

    def likely(output: int, factor: float) -> float:
        tokens = [round(b / per_token * factor) for b in bounds]
        return sum(cost(rates, input=t, cached=0, cache_writes=0, output=output) for t in tokens)

    cap = plan_limits(effort)[0]
    most = sum(worst(rates, b, cap, caches=False) for b in bounds)
    harness.say(f"{len(runs)} plans planned, {len(pending)} to make, on {model}, effort {effort}.")
    if not bounds:
        return
    harness.say(
        f"Prompts {min(bounds):,} to {max(bounds):,} bytes. The most the pass can cost: "
        f"${most:.2f} (each prompt's bytes as tokens and {cap:,} output tokens)."
    )
    harness.say(
        f"Likely: ${likely(OUTPUT_GUESS[0], 1.0):.2f} to ${likely(OUTPUT_GUESS[1], TOKENIZER):.2f}"
        f", at Haiku's {per_token:.2f} bytes a token on these runs (Opus up to {TOKENIZER} times "
        f"that) and {OUTPUT_GUESS[0]:,} to {OUTPUT_GUESS[1]:,} output tokens a plan, thinking "
        "included."
    )


def fits(limit: float | None, spent: float, worst_usd: float) -> bool:
    """Whether the next plan's worst case stays within the pass's spend limit, when it has one."""
    return limit is None or spent + worst_usd <= limit


def report_name(today: str, model: str, effort: str | None) -> str:
    """The report's name; the effort keeps two passes of one day apart."""
    return f"{today}-dev-plans-{PLANNER_PROMPT_SHA[:8]}-{model}-{effort}"


async def make(
    role: Role, runs: Sequence[Run], rows: list[Json], limit: float | None = None
) -> bool:
    """Makes every plan not kept yet, one at a time; False when a call or the spend limit stopped
    the pass. The limit holds each next plan's worst case, so the pass never spends past it."""
    binding = Binding(role, make_provider(role))
    spent, cap = 0.0, plan_limits(role.effort)[0]
    # Only the planner is asked: with fallback_on_rate_limit off a plan's chain is its first model.
    router = Router(
        binding, None, binding, fallback_on_rate_limit=False, limiter=Limiter(None), planner=binding
    )
    for scenario, triage in runs:
        path = PLANS / kept_name(role.model, role.effort, scenario, triage)
        if path.exists():
            rows.append(harness.load(path))
            continue
        most = worst(RATES[role.model], request(catalog(scenario), triage).bound, cap, caches=False)
        if not fits(limit, spent, most):
            harness.say(
                f"eval-plans: stopped before {scenario.id}: ${spent:.2f} spent, and its worst case "
                f"${most:.2f} would pass the limit of ${limit:.2f}"
            )
            return False
        row = await plan_one(router, scenario, triage)
        spent += row["cost"]
        plan = row["plan"]
        what = "no plan" if plan is None else f"{plan['kind']} of {plan['target']}"
        verdict = "kept" if plan is not None and row["refused"] is None else "not kept"
        harness.say(f"{scenario.id}: {what}, {verdict}, ${row['cost']:.4f}")
        if not answered(row):
            failed = next(call for call in row["calls"] if call["outcome"] != "ok")
            harness.say(f"eval-plans: stopped, {failed['outcome']}: {failed['error']}")
            return False
        harness.save(path, row)
        rows.append(row)
    return True


def markdown(report: Json, runs: Sequence[Run]) -> str:
    total = report["summary"]
    worlds = {scenario.id: scenario for scenario, _ in runs}
    reports = {scenario.id: triage["report"] for scenario, triage in runs}
    lines = [
        f"# Planner on the dev split ({report['date']})",
        "",
        f"Planner {report['planner']['model']} at effort {report['planner']['effort']}, prompt sha "
        f"{report['planner']['prompt_sha'][:16]}, on P0's Claude-first triage runs of 2026-11-01 "
        "(claude-haiku-5-5 first, 350,000-token cap), one per dev scenario.",
        "",
        f"- Plans: {total['plans']}; answered {total['answered']}; kept {total['kept']}; refused "
        f"by the check {len(total['refused'])}.",
        "- Kinds: " + ", ".join(f"{k} {n}" for k, n in sorted(total["kinds"].items())) + ".",
        "- Restarts or code fixes of a database, queue, cache, storage or proxy: "
        f"{len(total['acts_on_storage'])} proposed, {len(total['kept_on_storage'])} kept.",
        f"- Cost: ${total['cost']['total']:.4f} in all; per plan mean "
        f"${total['cost']['mean']:.4f}, median ${total['cost']['median']:.4f}, max "
        f"${total['cost']['max']:.4f}.",
        "",
        "| Scenario | Label | Triage's cause | Plan | Check | Tokens in / out | Cost |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["plans"]:
        labels = worlds[row["scenario"]].labels
        hypotheses = reports[row["scenario"]]["hypotheses"]
        top = f"{hypotheses[0]['root_cause_class']} in {hypotheses[0]['service']}"
        plan = row["plan"]
        what = "no plan" if plan is None else f"{plan['kind']} {plan['target']}"
        check = "no answer" if plan is None else "refused" if row["refused"] else "kept"
        tokens = " + ".join(
            f"{c['usage']['input']:,} / {c['usage']['output']:,}" for c in row["calls"]
        )
        lines.append(
            f"| {row['scenario']} | {labels.root_cause_class} in {labels.service} | "
            f"{top if hypotheses else 'none'} | {what} | {check} | {tokens} | ${row['cost']:.4f} |"
        )
    lines += ["", "## Refusals", ""]
    lines += [
        f"- {r['scenario']}, {r['plan']['kind']} of {r['plan']['target']}: {r['reason']}"
        for r in total["refused"]
    ] or ["None."]
    lines += ["", "## Plans", ""]
    for row in report["plans"]:
        labels = worlds[row["scenario"]].labels
        good = ", ".join(f"{a.kind} {a.target}" for a in labels.acceptable_actions) or "none"
        bad = ", ".join(f"{a.kind} {a.target}" for a in labels.forbidden_actions) or "none"
        plan = row["plan"]
        lines += [f"### {row['scenario']}", "", f"Labels: acceptable {good}; forbidden {bad}.", ""]
        if plan is None:
            errors = [c["error"] for c in row["calls"] if c["error"]]
            lines += [f"No plan: {'; '.join(errors) or 'no model was asked'}", ""]
            continue
        files = f" Files: {', '.join(plan['files'])}." if plan["files"] else ""
        lines += [f"{plan['kind']} of {plan['target']} ({row['target_kind']}).{files}", ""]
        lines += [plan["rationale"], ""]
        if row["refused"]:
            lines += [f"Refused: {row['refused']}", ""]
    lines += [
        "## Notes",
        "",
        "- The catalog is each scenario's services, kinded by `KINDS` in `evals/harness/plans.py`; "
        "the external providers (card-network, shipping-api) are left out, as the real apps' "
        "catalogs list no provider. Code fixes are judged against `services/**/*.py`.",
        "- The sim keeps no watch snapshot, so the planner read none and the check refuses every "
        "restart for that reason; a live alert carries one. The labels show which restarts the "
        "scenario accepts.",
        "- The cost is each call's tokens at the planner's rates (evals/pricing.json); no cap in "
        "either database counts this pass.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="P2.5: the planner on the dev split's reports.")
    parser.add_argument("--estimate", action="store_true", help="print the cost; call nothing")
    parser.add_argument("--max-usd", type=float, help="stop before a plan could pass this spend")
    args = parser.parse_args()
    try:
        settings = load_settings(Settings)
        runs = triage_runs()
        if args.estimate:
            estimate(settings, runs)
            return 0
        role = settings.planner()
        if role is None:
            raise harness.Setup("ANTHROPIC_API_KEY is not set; run it as make eval-plans")
        if role.until is not None and datetime.now(UTC).date() >= role.until:
            raise harness.Setup(f"Claude's credits ended on {role.until} (LLM_CLAUDE_UNTIL)")
        rows: list[Json] = []
        if not asyncio.run(make(role, runs, rows, args.max_usd)):
            harness.say(f"{len(rows)} of {len(runs)} plans kept; run the same command to go on.")
            return STOPPED
    except (harness.Setup, ConfigError) as error:
        harness.say(f"eval-plans: {error}")
        return SETUP
    today = datetime.now(UTC).date().isoformat()
    report: Json = {
        "date": today,
        "planner": {"model": role.model, "effort": role.effort, "prompt_sha": PLANNER_PROMPT_SHA},
        "triage": {"models": TRIAGE, "token_cap": harness.CLAUDE_CAP},
        "summary": summary(rows),
        "plans": rows,
    }
    name = report_name(today, role.model, role.effort)
    harness.save(harness.REPORTS / f"{name}.json", report)
    (harness.REPORTS / f"{name}.md").write_text(markdown(report, runs), encoding="utf-8")
    harness.say(f"report: evals/reports/{name}.md")
    if report["summary"]["kept_on_storage"]:
        harness.say(f"broke: kept plans on storage: {report['summary']['kept_on_storage']}")
        return BROKE
    return 0


if __name__ == "__main__":
    sys.exit(main())
