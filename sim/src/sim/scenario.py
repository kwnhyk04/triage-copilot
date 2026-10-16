"""Scenario files: one incident's simulated world, and the labels an investigation is scored on.

Times are offsets from the anchor, which is the alert's startsAt: "-6m", "+15m", "-2h". The data
window runs from anchor-6h to anchor+1h, and nothing exists outside it. sim.validate checks that a
scenario's metrics, logs and deploys tell one timeline.
"""

import hashlib
import re
from pathlib import Path
from typing import Annotated, Literal, NamedTuple, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from sim.catalog import CATALOG

# libyaml's loader when the wheel has it, about ten times faster than the pure-Python one.
LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
UNITS = {"s": 1, "m": 60, "h": 3600}
WINDOW = (-6 * 3600, 3600)
Offset = Annotated[str, Field(pattern=r"^[+-]\d+[smh]$")]
Duration = Annotated[str, Field(pattern=r"^\d+[smh]$")]
Scalar = str | int | float | bool
# The sim's backends a scenario can mark down; each answers 503 while it is.
Tool = Literal["metrics", "logs", "deploys", "dependencies"]
RootCause = Literal[
    "bad_deploy",
    "config_change",
    "dependency_failure",
    "resource_exhaustion",
    "traffic_surge",
    "database",
    "infrastructure",
    "certificate_expiry",
    "unknown",
]


def seconds(span: str) -> int:
    """Signed seconds of an offset such as -6m, or of a duration such as 1m."""
    sign = -1 if span.startswith("-") else 1
    return sign * int(span.lstrip("+-")[:-1]) * UNITS[span[-1]]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Rule(Model):
    """The alert's condition on one series, which held for `for` up to the anchor."""

    metric: str
    labels: dict[str, str]
    op: Literal[">", "<"]
    threshold: float
    hold: Annotated[Duration, Field(alias="for")]


class Alert(Model):
    labels: dict[str, str]
    annotations: dict[str, str]
    rule: Rule


class Service(Model):
    depends_on: list[str] = Field(default_factory=list[str])


class Deploy(Model):
    service: str
    version: str
    previous_version: str
    at: Offset
    author: str
    change: str


class Change(Model):
    """Moves a metric to `value`, linearly over `ramp`; `until` returns it to its base."""

    at: Offset
    value: Annotated[float, Field(ge=0)]
    ramp: Duration = "0s"
    until: Offset | None = None


class Metric(Model):
    name: str
    labels: dict[str, str]
    base: Annotated[float, Field(ge=0)]
    noise: Annotated[float, Field(ge=0)] = 0
    changes: list[Change] = Field(default_factory=list[Change])


class Assert(Model):
    """What a log stream's lines claim about a metric, true whenever the stream logs."""

    metric: str
    labels: dict[str, str]
    op: Literal[">", ">=", "<", "<="]
    value: float


class LogStream(Model):
    """Lines spread evenly at per_minute, each a template filled from the seeded generator."""

    labels: dict[str, str]
    per_minute: Annotated[float, Field(gt=0, le=6000)]
    start: Annotated[Offset | None, Field(alias="from")] = None
    to: Offset | None = None
    lines: Annotated[list[str], Field(min_length=1)]
    asserts: list[Assert] = Field(default_factory=list[Assert])


class Action(Model):
    kind: Literal["rollback_deploy", "restart_service", "scale_service", "disable_feature_flag"]
    target: str
    # A proposal matches when kind and target are equal and it carries every parameter listed.
    parameters: dict[str, Scalar] = Field(default_factory=dict[str, Scalar])


class Signal(Model):
    """Evidence of the root cause: the agent tool's result for `args` matches `pattern`."""

    tool: Literal["query_metrics", "query_logs", "list_deploys", "get_dependencies"]
    args: dict[str, Scalar]
    pattern: str

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, pattern: str) -> str:
        try:
            re.compile(pattern)
        except re.error as error:
            raise ValueError(f"not a regex: {error}") from None
        return pattern


class Culprit(Model):
    root_cause_class: RootCause
    service: str


class Labels(Model):
    """What a correct investigation finds and proposes; evals score reports against it."""

    root_cause_class: RootCause
    service: str
    # Other (class, service) answers that are as right, where the classes overlap; kept rare.
    alternatives: list[Culprit] = Field(default_factory=list[Culprit])
    # When the cause began: every symptom and every required signal starts at or after it.
    onset: Offset
    required_signals: Annotated[list[Signal], Field(min_length=1)]
    acceptable_actions: list[Action]
    forbidden_actions: list[Action]
    red_herrings: list[str]


class Scenario(Model):
    id: str
    title: str
    seed: int
    alert: Alert
    services: dict[str, Service]
    deploys: list[Deploy] = Field(default_factory=list[Deploy])
    # Routine deploys of every service over this many days before the scenario's own.
    background_deploys_days: Annotated[int, Field(ge=0, le=30)] = 0
    metrics: list[Metric]
    logs: list[LogStream]
    # A backend that answers 503 from activation on.
    down: Tool | None = None
    labels: Labels

    @model_validator(mode="after")
    def check_consistent(self) -> Self:
        known = set(self.services)
        problems = [
            f"unknown dependency {name}"
            for service in self.services.values()
            for name in service.depends_on
            if name not in known
        ]
        offsets = [d.at for d in self.deploys]
        for deploy in self.deploys:
            if deploy.service not in known:
                problems.append(f"deploy of unknown service {deploy.service}")
        for metric in self.metrics:
            if metric.name not in CATALOG:
                problems.append(f"metric {metric.name} is not in the catalog")
            if metric.labels.get("service") not in known:
                problems.append(f"metric {metric.name} needs a known service label")
            offsets += [c.at for c in metric.changes] + [c.until for c in metric.changes if c.until]
        for stream in self.logs:
            if stream.labels.get("service") not in known:
                problems.append("every log stream needs a known service label")
            offsets += [o for o in (stream.start, stream.to) if o]
        problems += [f"offset {o} is outside -6h..+1h" for o in offsets if not in_window(o)]
        if not {"alertname", "service"} <= set(self.alert.labels):
            problems.append("the alert needs alertname and service labels")
        labels = self.labels
        actions = [*labels.acceptable_actions, *labels.forbidden_actions]
        culprits = [labels.service, *(c.service for c in labels.alternatives)]
        targets = [*culprits, *(a.target for a in actions)]
        problems += [f"labelled service {t} is unknown" for t in targets if t not in known]
        if problems:
            raise ValueError("; ".join(problems))
        return self


def in_window(offset: str) -> bool:
    return WINDOW[0] <= seconds(offset) <= WINDOW[1]


class ScenarioError(Exception):
    """A scenario file that does not load; the message names the file."""


class Loaded(NamedTuple):
    scenario: Scenario
    # Of the file's bytes, so a run can name the exact version it ran against.
    sha256: str


def load_scenarios(directory: Path) -> dict[str, Loaded]:
    """Every *.yaml scenario in the directory, by id; a missing directory holds none."""
    scenarios: dict[str, Loaded] = {}
    for path in sorted(directory.glob("*.yaml")):
        raw = path.read_bytes()
        try:
            scenario = Scenario.model_validate(yaml.load(raw, Loader=LOADER))  # noqa: S506 - safe
        except yaml.YAMLError as error:
            raise ScenarioError(f"{path.name}: {error}") from None
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'scenario'}: {e['msg']}"
                for e in error.errors(include_url=False, include_input=False)
            )
            raise ScenarioError(f"{path.name}: {problems}") from None
        if scenario.id != path.stem:
            raise ScenarioError(f"{path.name}: id {scenario.id} differs from the file name")
        scenarios[scenario.id] = Loaded(scenario, hashlib.sha256(raw).hexdigest())
    return scenarios
