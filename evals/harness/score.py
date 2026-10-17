"""Deterministic scores of eval runs against their scenarios' labels, 95% bootstrap intervals, and
the floors of thresholds.json.

A run is one investigation of one scenario, as run.collect() stored it: the gateway's copy of the
report and the steps, and the agent's tool_calls and model_calls rows. Intervals resample
scenarios with replacement, so the runs of one scenario stay together, 10,000 times from a fixed
seed, and take the 2.5th and 97.5th percentiles.
"""

import math
import random
import re
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields
from typing import Any, Literal

from sim.scenario import Action, Labels

SEED = 20261016
RESAMPLES = 10_000
Json = dict[str, Any]
# The agent tool that reads each sim backend a scenario can mark down.
READERS = {
    "metrics": "query_metrics",
    "logs": "query_logs",
    "deploys": "list_deploys",
    "dependencies": "get_dependencies",
}


@dataclass(frozen=True)
class Scored:
    """One run's scores."""

    scenario: str
    family: str
    run: int
    top1: bool
    top3: bool
    grounded: bool
    action_correct: bool
    # Whether the scenario lists an acceptable action; without one, only no proposal is right.
    actionable: bool
    forbidden: int
    tool_calls: int
    invalid_calls: int
    tokens: int
    # Webhook intake to the report, both on the database's clock; None for a run that waited
    # out a quota stop, whose wall time says nothing about the agent.
    seconds: float | None
    cost: float
    billed: float
    # Whether the scenario marks a backend down, and whether missing_evidence names it.
    degraded: bool
    names_missing: bool

    def json(self) -> Json:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def same(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


def matches(proposal: Json, action: Action) -> bool:
    """Same kind and target, and every parameter the label lists, compared as text."""
    if proposal["kind"] != action.kind or not same(proposal["target"], action.target):
        return False
    given: dict[str, Any] = proposal.get("parameters") or {}
    return all(str(given.get(k, "")).strip() == str(v) for k, v in action.parameters.items())


def cost(calls: Sequence[Json], prices: dict[str, Json]) -> tuple[float, float]:
    """USD at each model's rates, and the part billed: models paid from credits, not free ones.
    Input counts cache reads and writes, which bill at their own rates; runs from before agent
    migration 0009 recorded no writes."""
    paid = billed = 0.0
    for call in calls:
        price = prices[call["model"]]
        reads = call["cached_tokens"] or 0
        writes = call.get("cache_write_tokens") or 0
        usd = (call["input_tokens"] - reads - writes) * price["input"]
        usd += reads * price["cached_input"] + writes * price.get("cache_write", price["input"])
        usd += call["output_tokens"] * price["output"]
        paid += usd
        if price["billing"] != "free":
            billed += usd
    return paid / 1_000_000, billed / 1_000_000


def free(models: Sequence[str], prices: dict[str, Json]) -> bool:
    """Whether every model bills nothing, so the $0-billed gate applies."""
    return all(prices[m]["billing"] == "free" for m in models)


def score(
    record: Json,
    labels: Labels,
    family: str,
    down: str | None,
    prices: dict[str, Json],
) -> Scored:
    report: Json = record["report"]
    hypotheses: list[Json] = report["hypotheses"]
    answers = [(labels.root_cause_class, labels.service)]
    answers += [(c.root_cause_class, c.service) for c in labels.alternatives]
    right = [
        any(same(h["root_cause_class"], c) and same(h["service"], s) for c, s in answers)
        for h in hypotheses
    ]
    results = {
        s["tool_call_id"]: (s["tool_name"], s["result"])
        for s in record["steps"]
        if s["kind"] == "tool_call" and s["result"] is not None and s["tool_call_id"]
    }
    proposals: list[Json] = report["proposed_actions"]
    cited = {i for h in hypotheses for i in h["evidence_ids"]}
    cited |= {i for p in proposals for i in p["evidence_ids"]}
    grounded = any(
        signal.tool == results[i][0] and re.search(signal.pattern, results[i][1])
        for i in cited
        if i in results
        for signal in labels.required_signals
    )
    acceptable = labels.acceptable_actions
    if acceptable:
        action_correct = bool(proposals) and all(
            any(matches(p, a) for a in acceptable) for p in proposals
        )
    else:
        action_correct = not proposals
    missing: list[str] = report["missing_evidence"]
    reader = READERS[down] if down else ""
    calls: list[Json] = record["tool_calls"]
    paid, billed = cost(record["model_calls"], prices)
    return Scored(
        scenario=record["scenario"],
        family=family,
        run=record["run"],
        top1=bool(right) and right[0],
        top3=any(right[:3]),
        grounded=grounded,
        action_correct=action_correct,
        actionable=bool(acceptable),
        forbidden=sum(any(matches(p, a) for a in labels.forbidden_actions) for p in proposals),
        tool_calls=len(calls),
        invalid_calls=sum((c["error"] or "").startswith("ToolInputError") for c in calls),
        tokens=record["tokens"],
        seconds=None if record["resumed"] else record["seconds"],
        cost=paid,
        billed=billed,
        degraded=down is not None,
        names_missing=bool(down) and any(reader in m or f"{down} backend" in m for m in missing),
    )


Clusters = list[list[Scored]]
Statistic = Callable[[Clusters], float | None]


def percentile(values: Sequence[float], q: float) -> float:
    """Linear interpolation between the closest ranks, as numpy's default."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def flat(clusters: Clusters) -> list[Scored]:
    return [run for cluster in clusters for run in cluster]


def share(
    test: Callable[[Scored], bool], among: Callable[[Scored], bool] = lambda _: True
) -> Statistic:
    def statistic(clusters: Clusters) -> float | None:
        runs = [r for r in flat(clusters) if among(r)]
        return sum(map(test, runs)) / len(runs) if runs else None

    return statistic


def of(
    value: Callable[[Scored], float | None], reduce: Callable[[list[float]], float]
) -> Statistic:
    def statistic(clusters: Clusters) -> float | None:
        values = [v for r in flat(clusters) if (v := value(r)) is not None]
        return reduce(values) if values else None

    return statistic


def consistency(clusters: Clusters) -> float | None:
    """Scenarios whose every run is top-1 correct; needs at least two runs of each."""
    if not clusters or min(map(len, clusters)) < 2:
        return None
    return sum(all(r.top1 for r in c) for c in clusters) / len(clusters)


def validity(clusters: Clusters) -> float | None:
    runs = flat(clusters)
    total = sum(r.tool_calls for r in runs)
    return 1 - sum(r.invalid_calls for r in runs) / total if total else None


def p95(values: list[float]) -> float:
    return percentile(values, 0.95)


Kind = Literal["accuracy", "efficiency", "gate"]


@dataclass(frozen=True)
class Metric:
    key: str
    name: str
    target: float
    # accuracy: higher is better; efficiency and gate: lower is better, and a gate's target is
    # the hard limit, which no run may pass whatever the floors say.
    kind: Kind
    statistic: Statistic
    unit: str = ""
    # What an agent that never proposes an action scores on the same runs, shown beside it.
    do_nothing: Statistic | None = None


METRICS = (
    Metric("top1", "Root cause top-1", 0.75, "accuracy", share(lambda r: r.top1)),
    Metric("top3", "Root cause top-3", 0.90, "accuracy", share(lambda r: r.top3)),
    Metric("consistency", "Consistency (correct in all 3 runs)", 0.65, "accuracy", consistency),
    Metric("grounding", "Evidence grounding", 0.95, "accuracy", share(lambda r: r.grounded)),
    Metric(
        "action_correctness",
        "Action correctness",
        0.80,
        "accuracy",
        share(lambda r: r.action_correct),
        do_nothing=share(lambda r: not r.actionable),
    ),
    Metric(
        "action_correctness_actionable",
        "Action correctness, scenarios with an acceptable action",
        0.80,
        "accuracy",
        share(lambda r: r.action_correct, lambda r: r.actionable),
    ),
    Metric(
        "forbidden_actions",
        "Forbidden action proposals",
        0,
        "gate",
        of(lambda r: r.forbidden, sum),
        "proposals",
    ),
    Metric("tool_call_validity", "Tool-call validity", 0.99, "accuracy", validity),
    Metric(
        "degraded_correct",
        "Degraded-tool scenarios: top-1 correct",
        0.60,
        "accuracy",
        share(lambda r: r.top1, lambda r: r.degraded),
    ),
    Metric(
        "degraded_missing_evidence",
        "Degraded-tool scenarios: down tool in missing evidence",
        1.0,
        "accuracy",
        share(lambda r: r.names_missing, lambda r: r.degraded),
    ),
    Metric(
        "tool_calls_median",
        "Tool calls per investigation (median)",
        8,
        "efficiency",
        of(lambda r: r.tool_calls, statistics.median),
        "calls",
    ),
    Metric(
        "tool_calls_max",
        "Tool calls per investigation (max, hard cap)",
        15,
        "gate",
        of(lambda r: r.tool_calls, max),
        "calls",
    ),
    Metric(
        "first_hypothesis_p95",
        "Time to first hypothesis (p95; proxy: time to the report)",
        45,
        "efficiency",
        of(lambda r: r.seconds, p95),
        "s",
    ),
    Metric(
        "investigation_time_p95",
        "Investigation time (p95)",
        180,
        "efficiency",
        of(lambda r: r.seconds, p95),
        "s",
    ),
    Metric(
        "tokens_median",
        "Tokens per investigation (median)",
        60_000,
        "efficiency",
        of(lambda r: r.tokens, statistics.median),
        "tokens",
    ),
    Metric(
        "tokens_max",
        "Tokens per investigation (max, hard cap)",
        150_000,
        "gate",
        of(lambda r: r.tokens, max),
        "tokens",
    ),
    Metric(
        "cost_paid",
        "Cost per investigation, paid-equivalent (mean)",
        0.25,
        "efficiency",
        of(lambda r: r.cost, statistics.fmean),
        "USD",
    ),
    Metric(
        "cost_billed",
        "Cost per investigation, as billed (mean)",
        0,
        "gate",
        of(lambda r: r.billed, statistics.fmean),
        "USD",
    ),
)


def clusters_of(runs: Sequence[Scored]) -> Clusters:
    """The runs grouped by scenario, in scenario and run order."""
    grouped: dict[str, list[Scored]] = {}
    for run in sorted(runs, key=lambda r: (r.scenario, r.run)):
        grouped.setdefault(run.scenario, []).append(run)
    return list(grouped.values())


def interval(
    clusters: Clusters, statistic: Statistic, resamples: int = RESAMPLES
) -> tuple[float, float] | None:
    """The 95% percentile interval of the statistic over scenario resamples."""
    if not clusters:
        return None
    r = random.Random(SEED)  # noqa: S311 - reproducible resampling, not cryptography
    n = len(clusters)
    found = [
        v
        for _ in range(resamples)
        if (v := statistic([clusters[r.randrange(n)] for _ in range(n)])) is not None
    ]
    return (percentile(found, 0.025), percentile(found, 0.975)) if found else None


def summarize(runs: Sequence[Scored], resamples: int = RESAMPLES) -> dict[str, Json]:
    clusters = clusters_of(runs)
    return {
        m.key: {
            "value": m.statistic(clusters),
            "ci": interval(clusters, m.statistic, resamples),
            "do_nothing": m.do_nothing(clusters) if m.do_nothing else None,
        }
        for m in METRICS
    }


def passes(metric: Metric, value: float | None, limit: float) -> bool | None:
    """Whether the value meets a target or floor; None when it was not measured."""
    if value is None:
        return None
    return value >= limit if metric.kind == "accuracy" else value <= limit


def floor(metric: Metric, value: float, ci: tuple[float, float]) -> float:
    """Accuracy: the lower bound, at most the target. Efficiency: 1.5 x the upper bound, at most
    the target, unless the value already misses the target, so a known miss does not fail every
    run (docs/memory.md). Gates: the target, always."""
    if metric.kind == "gate":
        return metric.target
    if metric.kind == "accuracy":
        return min(ci[0], metric.target)
    loose = ci[1] * 1.5
    return loose if value > metric.target else min(loose, metric.target)


def looser(metric: Metric, new: float, old: float) -> bool:
    return new < old if metric.kind == "accuracy" else new > old
