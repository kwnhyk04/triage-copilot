"""Checks that a scenario's metrics, logs and deploys tell one timeline, and that its labels can
be met.

    uv run python -m sim.validate evals/scenarios/v1 [more directories]

Bounds are worst case: a sample can be its level plus or minus the metric's noise, clamped as
the sim clamps it, so a scenario that passes is consistent for every seed. Times are checked on
the sim's 30 s grid.
"""

import re
import sys
from collections.abc import Callable, Iterator
from itertools import pairwise
from pathlib import Path

from sim.logql import parse_log_query
from sim.promql import QueryError, parse_selector
from sim.scenario import (
    WINDOW,
    Action,
    Change,
    Deploy,
    LogStream,
    Metric,
    Scenario,
    ScenarioError,
    Signal,
    load_scenarios,
    seconds,
)
from sim.world import STEP, fill, level, rng

# How long before its `for` window an alert's condition may first hold: rule evaluation
# intervals and a ramp through the samples' noise.
SLACK = 180
# The agent's tools and the sim backend each reads.
BACKENDS = {
    "query_metrics": "metrics",
    "query_logs": "logs",
    "list_deploys": "deploys",
    "get_dependencies": "dependencies",
}
# The agent shows the first 160 characters of a log line (agent.tools.LINE_CHARS).
LINE_CHARS = 160
FILLS = 3
VERSION = re.compile(r"version=(v\d+\.\d+\.\d+)")
COMPARE: dict[str, Callable[[float, float], bool]] = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
}


def at(t: int) -> str:
    sign = "-" if t < 0 else "+"
    minutes, rest = divmod(abs(t), 60)
    return f"{sign}{minutes}m{rest:02d}s" if rest else f"{sign}{minutes}m"


def key(name: str, labels: dict[str, str]) -> str:
    """A series as query_metrics shows it: name{a="1", b="2"}."""
    pairs = ", ".join(f'{k}="{v}"' for k, v in sorted(labels.items()) if k != "__name__")
    return f"{name}{{{pairs}}}"


def bounds(metric: Metric, t: int) -> tuple[float, float]:
    """The lowest and highest sample the metric can take at t."""
    mid = level(metric, t)
    low, high = max(mid - metric.noise, 0.0), max(mid + metric.noise, 0.0)
    if metric.name.endswith("_ratio"):
        low, high = min(low, 1.0), min(high, 1.0)
    return low, high


def grid(first: int, last: int) -> range:
    """The 30 s buckets that overlap [first, last)."""
    return range(first // STEP * STEP, last, STEP)


def span(change: Change) -> tuple[int, int]:
    """When a change starts and when its ramp ends."""
    start = seconds(change.at)
    return start, start + seconds(change.ramp)


def lifetime(stream: LogStream) -> tuple[int, int]:
    first = seconds(stream.start) if stream.start else WINDOW[0]
    return first, seconds(stream.to) if stream.to else WINDOW[1]


def find(scenario: Scenario, name: str, labels: dict[str, str]) -> Metric | None:
    found = [m for m in scenario.metrics if m.name == name and m.labels == labels]
    return found[0] if found else None


def own_deploys(scenario: Scenario, service: str) -> list[Deploy]:
    return sorted(
        (d for d in scenario.deploys if d.service == service), key=lambda d: seconds(d.at)
    )


def filled(scenario: Scenario, stream: LogStream) -> Iterator[list[str]]:
    """Each of the stream's line templates, filled FILLS ways."""
    for n, template in enumerate(stream.lines):
        yield [fill(template, rng(scenario.seed, f"validate|{n}", i)) for i in range(FILLS)]


def shown(stream: LogStream, line: str) -> str:
    """A log line as query_logs shows it, after its time."""
    return f"{stream.labels['service']}/{stream.labels.get('level', '')} {line[:LINE_CHARS]}"


def logs_match(scenario: Scenario, stream: LogStream, pattern: str) -> bool:
    """Whether one of the stream's templates matches the pattern however it is filled."""
    return any(
        all(re.search(pattern, shown(stream, line)) for line in lines)
        for lines in filled(scenario, stream)
    )


def alert_start(scenario: Scenario) -> tuple[int | None, list[str]]:
    """Since when the alert's condition surely held, and what is wrong with it."""
    rule = scenario.alert.rule
    metric = find(scenario, rule.metric, rule.labels)
    if metric is None:
        return None, [f"alert rule: no series {key(rule.metric, rule.labels)}"]
    problems: list[str] = []
    if rule.labels.get("service") != scenario.alert.labels["service"]:
        problems.append("alert rule: its series belongs to another service than the alert")
    hold, threshold = seconds(rule.hold), rule.threshold

    def surely(t: int) -> bool:
        low, high = bounds(metric, t)
        return low > threshold if rule.op == ">" else high < threshold

    def never(t: int) -> bool:
        low, high = bounds(metric, t)
        return high <= threshold if rule.op == ">" else low >= threshold

    if not surely(0):
        return None, [*problems, "alert rule: the condition does not surely hold at the alert"]
    start = 0
    while start - STEP >= WINDOW[0] and surely(start - STEP):
        start -= STEP
    if start > -hold:
        problems.append(
            f"alert rule: the condition holds from {at(start)}, short of for {rule.hold}"
        )
    false = next((t for t in range(start - STEP, WINDOW[0] - 1, -STEP) if never(t)), None)
    if false is None or false < -hold - SLACK:
        since = at(WINDOW[0] if false is None else false + STEP)
        problems.append(
            f"alert rule: the condition may hold from {since}, more than {SLACK // 60} minutes "
            "before its for window, so the alert would have fired earlier"
        )
    return start, problems


def check_asserts(scenario: Scenario) -> Iterator[str]:
    for n, stream in enumerate(scenario.logs):
        first, last = lifetime(stream)
        for claim in stream.asserts:
            metric = find(scenario, claim.metric, claim.labels)
            name = key(claim.metric, claim.labels)
            if metric is None:
                yield f"logs[{n}]: asserts on {name}, which is not a series"
                continue
            for t in grid(first, last):
                low, high = bounds(metric, t)
                seen = low if claim.op.startswith(">") else high
                if not COMPARE[claim.op](seen, claim.value):
                    yield (
                        f"logs[{n}] ({stream.labels['service']}) claims {name} {claim.op} "
                        f"{claim.value:g} from {at(first)}, but it can be {seen:.4g} at {at(t)}"
                    )
                    break


def check_versions(scenario: Scenario) -> Iterator[str]:
    for service in scenario.services:
        for older, newer in pairwise(own_deploys(scenario, service)):
            if newer.previous_version != older.version:
                yield (
                    f"deploys of {service}: {newer.version} replaces {newer.previous_version}, "
                    f"but {older.version} ran before it"
                )
    for n, stream in enumerate(scenario.logs):
        service = stream.labels["service"]
        named = {v for line in stream.lines for v in VERSION.findall(line)}
        if not named:
            continue
        own = own_deploys(scenario, service)
        if len(named) > 1 or not own:
            yield f"logs[{n}] ({service}) names {sorted(named)}, which no deploy pins"
            continue
        [version] = named
        first, last = lifetime(stream)
        running = own[0].previous_version
        for deploy in own:
            if seconds(deploy.at) <= first:
                running = deploy.version
        changed = [d for d in own if first < seconds(d.at) < last]
        if running != version or changed:
            yield f"logs[{n}] ({service}) logs version={version} while {service} runs {running}"


def query(signal: Signal) -> str:
    return str(signal.args.get("query", ""))


def check_signal(scenario: Scenario, signal: Signal, onset: int) -> Iterator[str]:
    """The signal shows before the alert in what its args fetch, and nothing it matches
    anywhere in the scenario begins before onset."""
    pattern = signal.pattern
    if signal.tool == "query_metrics":
        matchers = parse_selector(query(signal))
        named = [m for m in scenario.metrics if re.search(pattern, key(m.name, m.labels))]
        fetched = [
            m for m in named if all(x.matches({"__name__": m.name, **m.labels}) for x in matchers)
        ]
        # A change counts from its start to the end of its ramp: a countdown or a leak may
        # begin before onset, as long as it is still moving then.
        if not any(a < 0 and b >= onset for m in fetched for a, b in map(span, m.changes)):
            yield "no series its query selects moves between onset and the alert"
        early = [a for m in named for a, b in map(span, m.changes) if b < onset]
        if early:
            yield f"a series it matches moves at {at(min(early))} and settles before onset"
    elif signal.tool == "query_logs":
        parsed = parse_log_query(query(signal))
        named = [s for s in scenario.logs if logs_match(scenario, s, pattern)]
        fetched = [
            s
            for s in named
            if all(m.matches(s.labels) for m in parsed.matchers)
            and any(
                all(all(f.keeps(line) for f in parsed.filters) for line in lines)
                for lines in filled(scenario, s)
            )
        ]
        if not any(lifetime(s)[0] < 0 for s in fetched):
            yield "no stream its query selects logs a matching line before the alert"
        early = [lifetime(s)[0] for s in named if lifetime(s)[0] < onset]
        if early:
            yield f"a stream it matches logs from {at(min(early))}, before onset"
    elif signal.tool == "list_deploys":
        service = signal.args.get("service") or None
        named = [
            d
            for d in scenario.deploys
            if re.search(
                pattern,
                f"{d.service} {d.previous_version} -> {d.version} by {d.author}: {d.change}",
            )
        ]
        if not any(seconds(d.at) < 0 for d in named if service in (None, d.service)):
            yield "no deploy before the alert matches"
        if any(seconds(d.at) < onset for d in named):
            yield "a deploy it matches comes before onset"
    else:
        service = str(signal.args.get("service", ""))
        found = scenario.services.get(service)
        users = sorted(s for s, v in scenario.services.items() if service in v.depends_on)
        uses = ", ".join(sorted(found.depends_on)) if found else ""
        used_by = ", ".join(users) or "nothing"
        text = f"{service} depends on: {uses or 'nothing'}. Depended on by: {used_by}."
        if found is None or not re.search(pattern, text):
            yield f"the dependencies of {service!r} do not match"


def check_signals(scenario: Scenario, onset: int) -> Iterator[str]:
    for n, signal in enumerate(scenario.labels.required_signals):
        name = f"required_signals[{n}] ({signal.tool} {signal.pattern!r})"
        if BACKENDS[signal.tool] == scenario.down:
            yield f"{name}: its backend is down in this scenario"
        try:
            yield from (f"{name}: {problem}" for problem in check_signal(scenario, signal, onset))
        except QueryError as error:
            yield f"{name}: query {query(signal)!r}: {error}"


def overlaps(a: Action, b: Action) -> bool:
    if (a.kind, a.target) != (b.kind, b.target):
        return False
    shared = a.parameters.keys() & b.parameters.keys()
    return all(str(a.parameters[k]) == str(b.parameters[k]) for k in shared)


def check_actions(scenario: Scenario) -> Iterator[str]:
    labels = scenario.labels
    for good in labels.acceptable_actions:
        for bad in labels.forbidden_actions:
            if overlaps(good, bad):
                yield f"{good.kind} {good.target} is both acceptable and forbidden"
        if good.kind == "rollback_deploy":
            before = [d for d in own_deploys(scenario, good.target) if seconds(d.at) < 0]
            wanted = before[-1].previous_version if before else None
            if wanted is None or str(good.parameters.get("to_version")) != wanted:
                yield f"rollback of {good.target} must go to_version {wanted}, its last deploy's"
        if good.kind == "disable_feature_flag" and "flag" not in good.parameters:
            yield f"disable_feature_flag on {good.target} must name its flag"


def problems(scenario: Scenario) -> list[str]:
    """Everything that makes the scenario tell two stories, or its labels impossible to meet."""
    found: list[str] = []
    seen: set[str] = set()
    for metric in scenario.metrics:
        name = key(metric.name, metric.labels)
        if name in seen:
            found.append(f"series {name} is defined twice")
        seen.add(name)
    start, alert = alert_start(scenario)
    found += alert
    onset = seconds(scenario.labels.onset)
    if start is not None and start < onset:
        found.append(f"the alert condition holds from {at(start)}, before onset {at(onset)}")
    found += check_asserts(scenario)
    found += check_versions(scenario)
    found += check_signals(scenario, onset)
    found += check_actions(scenario)
    return found


def main(directories: list[str]) -> int:
    failed = checked = 0
    for directory in directories:
        try:
            loaded = load_scenarios(Path(directory))
        except ScenarioError as error:
            print(f"{directory}: {error}")  # noqa: T201 - CLI output
            failed += 1
            continue
        for scenario_id, (scenario, _) in loaded.items():
            checked += 1
            for problem in problems(scenario):
                print(f"{directory}/{scenario_id}.yaml: {problem}")  # noqa: T201
                failed += 1
    print(f"{checked} scenarios checked, {failed} problems")  # noqa: T201
    return 1 if failed or not checked else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
