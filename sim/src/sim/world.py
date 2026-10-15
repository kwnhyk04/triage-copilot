"""The active scenario's telemetry, generated on demand and fixed by its seed and anchor."""

import itertools
import json
import math
import random
import re
import time
import zlib
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cached_property
from typing import Any
from urllib.parse import quote

from sim.logql import LogQuery
from sim.promql import Matcher, QueryError
from sim.scenario import WINDOW, Metric, Scenario, seconds

STEP = 30
NS = 1_000_000_000
HOUR = 3600
PLACEHOLDER = re.compile(r"\{(rid|ms|n|ip|user)\}")
VERSION = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
AUTHORS = ("ci-bot", "ana.lopez", "dev.kim", "sam.okafor", "renovate-bot")
CHANGES = (
    "Bump dependencies",
    "Tune request timeouts",
    "Fix log formatting",
    "Add a metrics label",
    "Update the base image",
)
Version = tuple[int, int, int]
LineKey = tuple[int, int, int]


def rng(seed: int, key: str, index: int) -> random.Random:
    """The generator for one value; crc32 because hash() of a str is salted per process."""
    return random.Random(zlib.crc32(f"{seed}|{key}|{index}".encode()))  # noqa: S311 - fake data


def rfc3339(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


def level(metric: Metric, t: int) -> float:
    """The metric's level t seconds after the anchor, before noise."""
    current = metric.base
    for change in sorted(metric.changes, key=lambda c: seconds(c.at)):
        start = seconds(change.at)
        if t < start:
            break
        if change.until is not None and t >= seconds(change.until):
            current = metric.base
            continue
        ramp = seconds(change.ramp)
        if t >= start + ramp:
            current = change.value
        else:
            current += (change.value - current) * (t - start) / ramp
    return current


def fill(template: str, r: random.Random) -> str:
    def value(match: re.Match[str]) -> str:
        match match[1]:
            case "rid":
                return f"{r.getrandbits(32):08x}"
            case "ms":
                return str(r.randint(5, 900))
            case "n":
                return str(r.randint(1, 9999))
            case "ip":
                return f"10.{r.randint(0, 255)}.{r.randint(0, 255)}.{r.randint(1, 254)}"
            case _:
                return f"user-{r.randint(1000, 9999)}"

    return PLACEHOLDER.sub(value, template)


def older(version: Version) -> Version:
    major, minor, patch = version
    if patch:
        return major, minor, patch - 1
    return (major, minor - 1, 9) if minor else (max(major - 1, 0), 9, 9)


def tag(version: Version) -> str:
    return "v" + ".".join(map(str, version))


class Deadline:
    """One request's time limit, which long loops check cooperatively."""

    def __init__(self, limit: float) -> None:
        self.limit = limit
        self.at = time.perf_counter() + limit

    def watch[T](self, items: Iterable[T]) -> Iterator[T]:
        """The items, raising QueryError at every 32nd once the time limit has passed."""
        # Every 32nd, because one log line under an RE2 pattern near its size limit takes ~2 ms.
        for count, item in enumerate(items):
            if not count % 32 and time.perf_counter() > self.at:
                raise QueryError(
                    f"query exceeded the {self.limit} s time limit; "
                    "narrow the time range or the selector"
                )
            yield item


# eq=False hashes a series by identity, so World can memoize its values per series.
@dataclass(frozen=True, eq=False)
class Series:
    metric: Metric
    key: str
    labels: dict[str, str]


@dataclass(frozen=True)
class LogLine:
    ns: int
    stream: int
    index: int
    text: str

    @property
    def key(self) -> LineKey:
        """Position in the total order of a log query: time, then stream, then line."""
        return self.ns, self.stream, self.index


@dataclass(frozen=True)
class DeployRecord:
    id: str
    service: str
    version: str
    previous_version: str
    at: int
    author: str
    change: str

    def json(self) -> dict[str, str]:
        return {
            "id": self.id,
            "service": self.service,
            "version": self.version,
            "previous_version": self.previous_version,
            "at": rfc3339(self.at),
            "author": self.author,
            "change": self.change,
        }


def paginate(
    lines: list[LogLine], forward: bool, after: LineKey | None, limit: int
) -> tuple[list[LogLine], bool]:
    """The next `limit` lines past the cursor key in the direction, and whether more remain."""
    if forward:
        begin = 0 if after is None else bisect_right(lines, after, key=lambda line: line.key)
        return lines[begin : begin + limit], begin + limit < len(lines)
    stop = len(lines) if after is None else bisect_left(lines, after, key=lambda line: line.key)
    begin = max(stop - limit, 0)
    return lines[begin:stop][::-1], begin > 0


class World:
    """One activation of a scenario; `anchor` is the alert's startsAt in Unix seconds."""

    def __init__(self, scenario: Scenario, seed: int, anchor: int) -> None:
        self.scenario = scenario
        self.seed = seed
        self.anchor = anchor
        self.start = anchor + WINDOW[0]
        self.end = anchor + WINDOW[1]
        self._values: dict[tuple[Series, int], float] = {}
        self._lines: dict[int, list[LogLine]] = {}

    @cached_property
    def series(self) -> list[Series]:
        found: list[Series] = []
        for metric in self.scenario.metrics:
            pairs = ",".join(f'{k}="{v}"' for k, v in sorted(metric.labels.items()))
            found.append(
                Series(
                    metric, f"{metric.name}{{{pairs}}}", {"__name__": metric.name, **metric.labels}
                )
            )
        return found

    def select(self, matchers: list[Matcher]) -> list[Series]:
        return [s for s in self.series if all(m.matches(s.labels) for m in matchers)]

    def value(self, series: Series, t: float) -> float | None:
        """The sample at or before t on a 30 s grid from the anchor; None outside the window."""
        offset = t - self.anchor
        if not WINDOW[0] <= offset <= WINDOW[1]:
            return None
        key = series, math.floor(offset / STEP)
        if key not in self._values:
            self._values[key] = self._bucket(*key)
        return self._values[key]

    def _bucket(self, series: Series, index: int) -> float:
        """The value of one 30 s bucket: its level plus seeded noise, clamped for ratios."""
        metric = series.metric
        found = level(metric, index * STEP)
        if metric.noise:
            found += rng(self.seed, series.key, index).uniform(-metric.noise, metric.noise)
        found = max(found, 0.0)
        return min(found, 1.0) if metric.name.endswith("_ratio") else found

    def lines(self, number: int, deadline: Deadline) -> list[LogLine]:
        """Every line of one stream, oldest first, made on first use; jitter stays below spacing."""
        if number not in self._lines:
            spec = self.scenario.logs[number]
            first = (self.anchor + (seconds(spec.start) if spec.start else WINDOW[0])) * NS
            last = (self.anchor + (seconds(spec.to) if spec.to else WINDOW[1])) * NS
            spacing = round(60 * NS / spec.per_minute)
            lines: list[LogLine] = []
            for index in deadline.watch(itertools.count()):
                r = rng(self.seed, f"log|{number}", index)
                ns = first + index * spacing + int(r.random() * spacing)
                if ns >= last:
                    break
                lines.append(LogLine(ns, number, index, fill(r.choice(spec.lines), r)))
            self._lines[number] = lines
        return self._lines[number]

    def logs(
        self, query: LogQuery, start_ns: int, end_ns: int, deadline: Deadline
    ) -> list[LogLine]:
        """Matching lines with start <= time < end, in the total order of LogLine.key."""
        found: list[LogLine] = []
        for number, spec in enumerate(self.scenario.logs):
            if all(m.matches(spec.labels) for m in query.matchers):
                found += [
                    line
                    for line in deadline.watch(self.lines(number, deadline))
                    if start_ns <= line.ns < end_ns
                    and all(f.keeps(line.text) for f in query.filters)
                ]
        return sorted(found, key=lambda line: line.key)

    @cached_property
    def deploys(self) -> list[DeployRecord]:
        """The scenario's deploys and routine background ones, newest first by (time, id)."""
        records = [
            self._record(
                d.service,
                d.version,
                d.previous_version,
                self.anchor + seconds(d.at),
                d.author,
                d.change,
            )
            for d in self.scenario.deploys
        ]
        return sorted(records + self._background(), key=lambda d: (d.at, d.id), reverse=True)

    def _record(
        self, service: str, version: str, previous: str, at: int, author: str, change: str
    ) -> DeployRecord:
        digest = zlib.crc32(f"{self.seed}|{service}|{version}|{at}".encode())
        return DeployRecord(f"dep-{digest:08x}", service, version, previous, at, author, change)

    def _background(self) -> list[DeployRecord]:
        """Patch releases of each service 8 to 30 hours apart, ending at its first own deploy.

        They stay out of the last two hours before the anchor, so they never compete with the
        scenario's deploys, and each service's last one is the version its first own deploy
        replaced.
        """
        oldest = self.anchor - self.scenario.background_deploys_days * 24 * HOUR
        records: list[DeployRecord] = []
        for service in sorted(self.scenario.services):
            own = sorted(
                (d for d in self.scenario.deploys if d.service == service),
                key=lambda d: seconds(d.at),
            )
            at = min([self.anchor - 2 * HOUR, *(self.anchor + seconds(d.at) for d in own)])
            parsed = VERSION.match(own[0].previous_version) if own else None
            r = rng(self.seed, f"version|{service}", 0)
            version: Version = (
                (int(parsed[1]), int(parsed[2]), int(parsed[3]))
                if parsed
                else (1, r.randint(0, 9), r.randint(1, 9))
            )
            for index in itertools.count():
                r = rng(self.seed, f"deploy|{service}", index)
                at -= r.randint(8 * HOUR, 30 * HOUR)
                if at < oldest:
                    break
                previous = older(version)
                author, change = r.choice(AUTHORS), r.choice(CHANGES)
                records.append(
                    self._record(service, tag(version), tag(previous), at, author, change)
                )
                version = previous
        return records

    def alertmanager(self) -> dict[str, Any]:
        """The Alertmanager v4 webhook that opens the incident, as the gateway receives it."""
        labels = self.scenario.alert.labels
        group = {name: labels[name] for name in ("alertname", "service")}
        key = ", ".join(f"{name}={json.dumps(value)}" for name, value in group.items())
        fingerprint = zlib.crc32(json.dumps(labels, sort_keys=True).encode())
        annotations = self.scenario.alert.annotations
        return {
            "version": "4",
            "groupKey": f"{{}}:{{{key}}}",
            "truncatedAlerts": 0,
            "status": "firing",
            "receiver": "triage-copilot",
            "groupLabels": group,
            "commonLabels": labels,
            "commonAnnotations": annotations,
            "externalURL": "http://sim.local",
            "alerts": [
                {
                    "status": "firing",
                    "labels": labels,
                    "annotations": annotations,
                    "startsAt": rfc3339(self.anchor),
                    "endsAt": "0001-01-01T00:00:00Z",
                    "generatorURL": f"http://sim.local/graph?g0.expr={quote(labels['alertname'])}",
                    "fingerprint": f"{fingerprint:016x}",
                }
            ],
        }
