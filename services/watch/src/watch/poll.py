"""One poll of one app: what it observes, which alerts change, and what is posted.

Each poll reads the app's health URLs, its containers, their new log lines and, on a dev stack
that opted in, its Prometheus alerts. An alert fires after FIRE_AFTER bad observations in a row
and resolves after RESOLVE_AFTER good ones, and only those transitions are posted to the
gateway's webhook. Its labels are always the same five, so fingerprints and dedupe hold; what
varies goes in its annotations, with a scrubbed snapshot of the app of at most SNAPSHOT_BYTES.
"""

import json
import logging
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import quote

import httpx2

from py_clients.clients import AlertsClient, ApiError
from py_clients.generated import gateway
from watch.catalog import Catalog, Service, Severity, Stack
from watch.readers import Answer, Container, Docker, Git, Http, ReadError, stamp
from watch.scrub import FIELD_CHARS, mask, scrub_lines

log = logging.getLogger(__name__)

Source = Literal["health", "docker", "logs", "prometheus"]
Status = Literal["firing", "resolved"]
FIRE_AFTER: dict[Source, int] = {"health": 3, "docker": 2, "logs": 1, "prometheus": 1}
RESOLVE_AFTER = 2
# watch's own rate limit: an alert that fired FLAP_FIRINGS times within FLAP_WINDOW flaps, and
# stays firing until the first of those firings is FLAP_WINDOW old, so one alert posts at most
# that many firings an hour and its flapping shows as one long firing.
FLAP_FIRINGS, FLAP_WINDOW = 3, timedelta(hours=1)
# A log signal stays bad this long after its last line.
LOG_HOLD = timedelta(minutes=10)
# This many error lines within the window make an ErrorBurst.
BURST, BURST_WINDOW = 10, timedelta(minutes=5)
LOG_TAIL = 200  # lines read per container and poll
KEPT_LINES = 100  # scrubbed lines kept per service for the snapshot
CHANGES, COMMITS = timedelta(hours=24), 10
SNAPSHOT_BYTES = 48 * 1024
SUMMARY_CHARS = 200
# Alertmanager's endsAt while an alert fires.
NEVER = datetime(1, 1, 1, tzinfo=UTC)

Observed = dict["Key", tuple[bool, str]]


@dataclass(frozen=True)
class Target:
    """One watched app: its catalog and stack, its tenant's webhook token, health URLs by
    service, its repo (for origin/main's commits) and, on a dev stack, its Prometheus."""

    catalog: Catalog
    stack: Stack
    token: str = field(repr=False)
    health: Mapping[str, str] = field(default_factory=dict[str, str])
    repo: Path | None = None
    prometheus: str | None = None


@dataclass(frozen=True)
class Key:
    """An alert's labels, apart from its app."""

    alertname: str
    service: str
    severity: Severity
    source: Source


@dataclass
class Track:
    bad: int = 0
    good: int = 0
    detail: str = ""
    # The startsAt of the alert as posted, while it fires.
    firing: datetime | None = None
    # When it fired, within the last FLAP_WINDOW.
    fired: deque[datetime] = field(default_factory=deque[datetime])


def fingerprint(labels: Mapping[str, str]) -> str:
    """Prometheus's label-set fingerprint (FNV-1a 64, names in byte order), as the gateway's."""
    value = 14695981039346656037
    for name, label in sorted((k.encode(), v.encode()) for k, v in labels.items()):
        for part in (name, b"\xff", label, b"\xff"):
            for byte in part:
                value = ((value ^ byte) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return f"{value:016x}"


def _down(service: Service, container: Container | None) -> tuple[bool, str]:
    """Whether the service's container is down, and how it looks."""
    if container is None:
        return not service.optional, "no container"
    state, status = container.state, container.status
    if state == "running":
        unhealthy = "(unhealthy)" in status
        return unhealthy, f"container {'unhealthy' if unhealthy else 'running'} ({status})"
    if state == "exited":
        clean = status.startswith("Exited (0)")
        killed = "; exit 137 is a kill, often out of memory" if "(137)" in status else ""
        return not (service.optional and clean), f"container exited ({status}){killed}"
    return True, f"container {state} ({status})"


def _mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _entry(text: str) -> dict[str, Any]:
    try:
        return _mapping(json.loads(text))
    except ValueError:
        return {}


def _data(answer: Answer) -> dict[str, Any] | None:
    """The `data` of a successful Prometheus API answer."""
    body = _entry(answer.body) if answer.status == 200 else {}
    data = body.get("data")
    if body.get("status") != "success" or not isinstance(data, dict):
        return None
    return cast("dict[str, Any]", data)


def fit(snapshot: dict[str, Any], limit: int) -> str:
    """The snapshot as JSON of at most `limit` bytes. The oldest log lines go first, from the
    service with the most, then the oldest commits, and at worst all but the alert itself."""

    def dumps(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    text = dumps(snapshot)
    excess = len(text.encode()) - limit
    logs: dict[str, list[str]] = snapshot["logs"]
    if excess > 0 and logs:
        # Each line's exact cost, its comma included, so one serialization settles it.
        costs = {
            name: deque(len(dumps(line).encode()) + 1 for line in lines)
            for name, lines in logs.items()
        }
        totals = {name: sum(each) for name, each in costs.items()}
        while excess > 0 and any(totals.values()):
            name = max(totals, key=totals.__getitem__)
            cost = costs[name].popleft()
            logs[name].pop(0)
            totals[name] -= cost
            excess -= cost
        for name in [name for name, lines in logs.items() if not lines]:
            del logs[name]
        text = dumps(snapshot)
    commits: list[object] | None = snapshot["changes"]["commits"]
    while len(text.encode()) > limit and commits:
        commits.pop()
        text = dumps(snapshot)
    if len(text.encode()) > limit:
        short = {name: snapshot[name] for name in ("app", "stack", "taken_at", "alert")}
        text = dumps({**short, "truncated": True})
    return text


class Watcher:
    """Polls one app and posts its alerts' transitions to the gateway's webhook."""

    def __init__(self, target: Target, gateway_url: str) -> None:
        self.target = target
        base = target.prometheus
        self._query_urls = {
            query: f"{base}/api/v1/query?query={quote(query, safe='')}"
            for query in target.catalog.queries
            if base
        }
        alerts_url = [f"{base}/api/v1/alerts"] if base else []
        self.http = Http([*target.health.values(), *alerts_url, *self._query_urls.values()])
        self.docker = Docker()
        self.git = Git(target.repo) if target.repo else None
        self.client = AlertsClient(gateway_url, target.token)
        self.tracks: dict[Key, Track] = {}
        # What kept the last poll from seeing or posting everything; empty when nothing did.
        self.problems: list[str] = []
        self.ok = False
        self._health: dict[str, tuple[str, Answer]] = {}
        self._containers: dict[str, Container] = {}
        self._read_until: dict[str, datetime] = {}
        self._lines: dict[str, deque[str]] = {}
        self._seen: dict[tuple[str, str], datetime] = {}
        self._errors: dict[str, deque[datetime]] = {}
        self._alerts: list[dict[str, str]] = []
        # Commits and query results, read once per poll for all of its snapshots.
        self._changes: tuple[object, object] | None = None

    @property
    def app(self) -> str:
        return self.target.catalog.app

    def close(self) -> None:
        self.http.close()
        self.client.close()

    def poll(self, now: datetime) -> list[tuple[Status, Key]]:
        """Observes the app once and posts every alert whose state changed."""
        self.problems = []
        self._changes = None
        observed: Observed = {}
        self._probe(observed)
        self._read_docker(now, observed)
        self._read_prometheus(observed)
        posted = self._step(now, observed)
        self.ok = not self.problems
        return posted

    def _probe(self, observed: Observed) -> None:
        for service, url in self.target.health.items():
            answer = self.http.get(url)
            self._health[service] = (url, answer)
            ok = answer.status is not None and 200 <= answer.status < 300
            how = f"answered {answer.status}" if answer.status else f"failed: {answer.error}"
            key = Key("HealthCheckFailed", service, "critical", "health")
            observed[key] = (not ok, f"GET {mask(url)} {how}")

    def _read_docker(self, now: datetime, observed: Observed) -> None:
        catalog, stack = self.target.catalog, self.target.stack
        services = {
            name: service
            for service in catalog.services
            if (name := catalog.container(service, stack))
        }
        try:
            listed = {c.name: c for c in self.docker.ps(catalog.projects[stack], services)}
        except ReadError as error:
            self.problems.append(f"docker ps: {error}")
            return
        self._containers = {services[name].name: c for name, c in listed.items()}
        for name, service in services.items():
            key = Key("ContainerDown", service.name, "critical", "docker")
            observed[key] = _down(service, listed.get(name))
        for service, container in self._containers.items():
            self._read_logs(service, container, now, observed)

    def _read_logs(
        self, service: str, container: Container, now: datetime, observed: Observed
    ) -> None:
        catalog = self.target.catalog
        since = self._read_until.get(service, now - LOG_HOLD)
        try:
            lines = self.docker.logs(container, since, now, LOG_TAIL)
        except ReadError as error:
            self.problems.append(f"docker logs {service}: {error}")
            return
        self._read_until[service] = now
        errors = self._errors.setdefault(service, deque())
        for at, text in lines:
            entry = _entry(text)
            for signal in catalog.signals:
                if entry.get(signal.field) == signal.value:
                    self._seen[(service, signal.alertname)] = at
            level = entry.get(catalog.level_field)
            if isinstance(level, str | int) and level in catalog.error_levels:
                errors.append(at)
        while errors and now - errors[0] > BURST_WINDOW:
            errors.popleft()
        kept = self._lines.setdefault(service, deque(maxlen=KEPT_LINES))
        kept.extend(scrub_lines(catalog, [text for _, text in lines]))
        for signal in catalog.signals:
            seen = self._seen.get((service, signal.alertname))
            bad = seen is not None and now - seen <= LOG_HOLD
            when = f" at {stamp(seen)}" if seen else ""
            key = Key(signal.alertname, service, signal.severity, "logs")
            observed[key] = (bad, f"logged {signal.value}{when}")
        burst = Key("ErrorBurst", service, "warning", "logs")
        observed[burst] = (len(errors) >= BURST, f"{len(errors)} error lines in 5 minutes")

    def _read_prometheus(self, observed: Observed) -> None:
        if not self.target.prometheus:
            return
        rules = self.target.catalog.rules
        answer = self.http.get(f"{self.target.prometheus}/api/v1/alerts")
        data = _data(answer)
        if data is None:
            self.problems.append(f"prometheus alerts: {answer.error or answer.status}")
            return
        firing: dict[str, dict[str, str]] = {}
        for alert in cast("list[dict[str, Any]]", data.get("alerts") or []):
            labels, notes = _mapping(alert.get("labels")), _mapping(alert.get("annotations"))
            name = labels.get("alertname")
            if isinstance(name, str) and name in rules and alert.get("state") == "firing":
                firing[name] = {
                    "alertname": name,
                    "state": "firing",
                    "active_at": mask(str(alert.get("activeAt", ""))),
                    "value": mask(str(alert.get("value", ""))),
                    "summary": mask(str(notes.get("summary", "")))[:FIELD_CHARS],
                }
        self._alerts = list(firing.values())
        for name, rule in rules.items():
            key = Key(name, rule.service, rule.severity, "prometheus")
            observed[key] = (name in firing, f"Prometheus alert {name} is firing")

    def _queries(self) -> dict[str, list[dict[str, object]]]:
        results: dict[str, list[dict[str, object]]] = {}
        for query, url in self._query_urls.items():
            answer = self.http.get(url)
            data = _data(answer)
            if data is None:
                self.problems.append(f"prometheus query: {answer.error or answer.status}")
                continue
            rows = cast("list[dict[str, Any]]", data.get("result") or [])
            results[query] = [
                {
                    "metric": {k: mask(str(v)) for k, v in _mapping(row.get("metric")).items()},
                    "value": mask(str((row.get("value") or [""])[-1])),
                }
                for row in rows[:20]
            ]
        return results

    def _commits(self, now: datetime) -> list[dict[str, object]] | None:
        if self.git is None:
            return None
        try:
            commits = self.git.commits(now - CHANGES, COMMITS)
        except ReadError as error:
            self.problems.append(f"git log: {error}")
            return None
        return [
            {
                "sha": commit.sha,
                "at": stamp(commit.at),
                "subject": mask(commit.subject)[:SUMMARY_CHARS],
                "paths": [mask(path)[:SUMMARY_CHARS] for path in commit.paths[:20]],
            }
            for commit in commits
        ]

    def _snapshot(self, now: datetime, key: Key, detail: str) -> str:
        catalog = self.target.catalog
        involved = (key.service, *catalog.service(key.service).depends_on)
        if self._changes is None:
            self._changes = (
                self._commits(now),
                self._queries() if self.target.prometheus else None,
            )
        commits, queries = self._changes
        snapshot: dict[str, Any] = {
            "app": catalog.app,
            "stack": self.target.stack,
            "taken_at": stamp(now),
            "alert": {
                "alertname": key.alertname,
                "service": key.service,
                "source": key.source,
                "detail": mask(detail)[:FIELD_CHARS],
            },
            "health": [
                {
                    "service": service,
                    "url": mask(url),
                    "status": answer.status,
                    "ms": answer.ms,
                    "error": answer.error,
                    "body": mask(answer.body)[:FIELD_CHARS],
                }
                for service, (url, answer) in self._health.items()
            ],
            "containers": [
                {
                    "service": service,
                    "name": c.name,
                    "image": mask(c.image),
                    "state": c.state,
                    "status": mask(c.status),
                    "created": stamp(c.created) if c.created else None,
                }
                for service, c in self._containers.items()
            ],
            "logs": {name: list(self._lines[name]) for name in involved if self._lines.get(name)},
            "changes": {
                "recreated": [
                    {"service": service, "created": stamp(c.created), "image": mask(c.image)}
                    for service, c in self._containers.items()
                    if c.created and now - c.created <= CHANGES
                ],
                "commits": commits,
            },
        }
        if queries is not None:
            snapshot["prometheus"] = {"alerts": self._alerts, "queries": queries}
        snapshot["problems"] = list(self.problems)
        return fit(snapshot, SNAPSHOT_BYTES)

    def _step(self, now: datetime, observed: Observed) -> list[tuple[Status, Key]]:
        posted: list[tuple[Status, Key]] = []
        for key, (bad, detail) in observed.items():
            track = self.tracks.setdefault(key, Track())
            track.bad, track.good = (track.bad + 1, 0) if bad else (0, track.good + 1)
            if bad:
                track.detail = detail
            while track.fired and now - track.fired[0] >= FLAP_WINDOW:
                track.fired.popleft()
            due: Status | None = None
            if track.firing is None and track.bad >= FIRE_AFTER[key.source]:
                due = "firing"
            elif track.firing is not None and track.good >= RESOLVE_AFTER:
                # A flapping alert's resolve waits, which keeps it one firing.
                due = "resolved" if len(track.fired) < FLAP_FIRINGS else None
            if due and self._post(key, due, track.firing or now, now, track.detail):
                track.firing = now if due == "firing" else None
                if due == "firing":
                    track.fired.append(now)
                posted.append((due, key))
        return posted

    def _post(self, key: Key, status: Status, starts: datetime, now: datetime, detail: str) -> bool:
        """Posts one transition; a refused or failed post is tried again at the next poll."""
        catalog = self.target.catalog
        labels = {
            "alertname": key.alertname,
            "app": catalog.app,
            "service": key.service,
            "severity": key.severity,
            "source": key.source,
        }
        head = f"{catalog.app} {key.service}: "
        if status == "firing":
            annotations = {
                "summary": (head + detail)[:SUMMARY_CHARS],
                "description": (
                    f"Seen by watch ({key.source}) on the {self.target.stack} stack. The snapshot"
                    " holds the health checks, containers, recent log lines and changes."
                ),
                "snapshot": self._snapshot(now, key, detail),
            }
        else:
            annotations = {"summary": f"{head}recovered from: {detail}"[:SUMMARY_CHARS]}
        alert = gateway.Alert(
            status=gateway.AlertStatus(status),
            labels=gateway.Labels(labels),
            annotations=gateway.Labels(annotations),
            startsAt=starts,
            endsAt=NEVER if status == "firing" else now,
            generatorURL="",
            fingerprint=fingerprint(labels),
        )
        # Alertmanager's common annotations would repeat the snapshot; the gateway reads neither.
        webhook = gateway.AlertmanagerWebhook(
            version="4",
            groupKey=f'{{}}:{{app="{catalog.app}"}}',
            status=gateway.AlertStatus(status),
            receiver="watch",
            groupLabels=gateway.Labels({"app": catalog.app}),
            commonLabels=gateway.Labels(labels),
            commonAnnotations=gateway.Labels({}),
            externalURL="",
            alerts=[alert],
        )
        # Not `service`, which the JSON log line already uses for watch itself.
        extra = {"app": catalog.app, "alertname": key.alertname, "app_service": key.service}
        try:
            self.client.post(webhook)
        except (ApiError, httpx2.HTTPError) as error:
            self.problems.append(f"post {key.alertname} {key.service}: {mask(str(error))}")
            log.warning("alert not posted", extra={**extra, "error": mask(str(error))})
            return False
        log.info("alert %s", status, extra=extra)
        return True
