"""Scenario set v1: 40 scripted incidents, 8 failure families x 5 variants.

    uv run python evals/scenarios/v1/generate.py [--out DIR]

Writes one YAML file per scenario and manifest.json (sha256, family, split, ci, down) into this
directory, or into DIR. Frozen with its output: v1 never changes in place, so a fix ships as v2.
What the variants leave open (request rates, versions, authors, which loud error log runs) comes
from a generator seeded with SEED and the scenario id, so every run writes the same bytes.

Every scenario carries noise: an unrelated deploy about two hours before the alert, a loud error
log that has nothing to do with the incident, and a week of routine deploys. In two scenarios of
each family, one dev and one test, a harmless deploy lands 2 to 6 minutes before the alert, the
newest deploy there, so blaming the latest deploy fails. Causal deploys land 4.5 to 35 minutes
before the alert; the two earliest run as canaries first. In two more scenarios of each family a
harmless config or flag change lands 2 to 6 minutes before the alert, the newest change there.
Causal config and flag changes start 4.5 to 30 minutes before the alert: one builds up slowly,
one rolls out to 10% first. Times are seconds from the alert,
negative before it. An error alert fires on a 5xx ratio over 5% for 2 minutes, so its errors
start 4 minutes before the alert; a latency alert holds for 3 minutes, so its latency starts 5
minutes before.
"""

import argparse
import hashlib
import json
import random
import re
import zlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
SEED = 20261016
ERRORS_AT, LATENCY_AT = -240, -300
Json = dict[str, Any]
AUTHORS = ("ci-bot", "ana.lopez", "dev.kim", "sam.okafor", "renovate-bot", "li.wei", "maria.g")
CHANGES = (
    "Update header copy and footer links",
    "Add a metrics label to the request histogram",
    "Bump dependencies",
    "Translate the settings page",
    "Tune log sampling",
)
LOUD = (
    "analytics beacon rejected: 413 Payload Too Large (batch={n})",
    "deprecated endpoint /v1/legacy-search called by client mobile-ios/4.{n}",
    "failed to export spans: exporter queue full, dropped {n} spans",
    "thumbnail generation failed for sku={n}: unsupported color profile",
    "session store: key over 250 bytes, not cached ({user})",
    "email template promo-weekly has no translation for locale pt-BR",
    "invalid coupon code {rid} submitted by {user}",
)
# Batch workloads that can share a node with a service.
JOBS = {"nightly-reindex", "ml-trainer", "log-compactor", "report-builder", "image-resizer"}
ROUTES: dict[str, tuple[str, ...]] = {
    "frontend": ("GET /", "GET /product/{n}", "GET /cart"),
    "web": ("GET /", "GET /search?q=shoes", "GET /product/{n}"),
    "api-gateway": ("GET /api/cart/{n}", "POST /api/checkout", "GET /api/products/{n}"),
    "checkout": ("POST /checkout", "GET /cart/total"),
    "orders": ("POST /orders", "GET /orders/{n}"),
    "payments": ("POST /charge", "POST /refund"),
    "catalog": ("GET /products/{n}", "GET /categories"),
    "search": ("GET /search", "GET /search/suggest"),
    "cart": ("GET /cart/{n}", "POST /cart/{n}/items"),
    "inventory": ("GET /stock/{n}", "POST /reservations"),
    "pricing": ("POST /quote", "GET /prices/{n}"),
    "shipping": ("POST /rates", "POST /labels"),
    "accounts": ("POST /login", "GET /me", "POST /addresses"),
    "notifications": ("POST /send", "GET /preferences/{n}"),
    "recommendations": ("GET /recommendations/{n}", "GET /recommendations/trending"),
}


def num(value: float) -> float | int:
    """Four significant digits, and whole numbers without a fraction."""
    rounded = float(f"{value:.4g}")
    return int(rounded) if rounded.is_integer() else rounded


def o(t: int) -> str:
    """An offset from the alert, such as -6m, -330s or +0m."""
    return ("-" if t < 0 else "+") + dur(abs(t))


def dur(t: int) -> str:
    if t and t % 3600 == 0:
        return f"{t // 3600}h"
    return f"{t // 60}m" if t % 60 == 0 else f"{t}s"


def ch(at: int, value: float, ramp: int = 0, until: int | None = None) -> Json:
    change: Json = {"at": o(at), "value": num(value)}
    if ramp:
        change["ramp"] = dur(ramp)
    if until is not None:
        change["until"] = o(until)
    return change


def claim(metric: str, service: str, op: str, value: float, **labels: str) -> Json:
    return {"metric": metric, "labels": {"service": service, **labels}, "op": op, "value": value}


def key(name: str, labels: dict[str, str]) -> str:
    pairs = ", ".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return f"{name}{{{pairs}}}"


def literal(text: str) -> str:
    """A regex matching the text, escaping only what regexes treat specially."""
    return re.sub(r"([.^$*+?{}\[\]\\|()])", r"\\\1", text)


def metric_signal(name: str, service: str, **labels: str) -> Json:
    series = key(name, {"service": service, **labels})
    return {"tool": "query_metrics", "args": {"query": series}, "pattern": literal(series)}


def log_signal(query: str, text: str) -> Json:
    return {"tool": "query_logs", "args": {"query": query}, "pattern": literal(text)}


def deploy_signal(service: str, old: str, new: str) -> Json:
    pattern = literal(f"{service} {old} -> {new}")
    return {"tool": "list_deploys", "args": {"service": service}, "pattern": pattern}


def act(kind: str, target: str, **parameters: str) -> Json:
    action: Json = {"kind": kind, "target": target}
    if parameters:
        action["parameters"] = parameters
    return action


def error_alert(service: str, name: str) -> Json:
    return {
        "labels": {"alertname": name, "service": service, "severity": "critical"},
        "annotations": {
            "summary": f"{service} 5xx ratio above 5% for 2 minutes",
            "description": f'http_error_ratio{{service="{service}"}} > 0.05 for 2m',
        },
        "rule": {
            "metric": "http_error_ratio",
            "labels": {"service": service},
            "op": ">",
            "threshold": 0.05,
            "for": "2m",
        },
    }


def latency_alert(service: str, name: str, threshold: float) -> Json:
    return {
        "labels": {"alertname": name, "service": service, "severity": "warning"},
        "annotations": {
            "summary": f"{service} p99 latency above {threshold:g} s for 3 minutes",
            "description": (
                f'http_latency_p99_seconds{{service="{service}"}} > {threshold:g} for 3m'
            ),
        },
        "rule": {
            "metric": "http_latency_p99_seconds",
            "labels": {"service": service},
            "op": ">",
            "threshold": threshold,
            "for": "3m",
        },
    }


class Draft:
    """One scenario while its variant writes it."""

    def __init__(self, scenario_id: str, title: str) -> None:
        self.id = scenario_id
        self.title = title
        self.seed = zlib.crc32(f"{SEED}|{scenario_id}".encode()) % 100_000
        self.r = random.Random(self.seed)  # noqa: S311 - fake data
        self.services: dict[str, Json] = {}
        self.deploys: list[Json] = []
        self.metrics: dict[str, Json] = {}
        self.logs: list[Json] = []
        self.herrings: list[str] = []
        self.forbidden: list[Json] = []

    def service(self, name: str, *depends_on: str) -> None:
        self.services[name] = {"depends_on": list(depends_on)} if depends_on else {}

    def metric(
        self, name: str, service: str, base: float, noise: float = 0, *changes: Json, **labels: str
    ) -> None:
        series: Json = {"name": name, "labels": {"service": service, **labels}, "base": num(base)}
        if noise:
            series["noise"] = num(noise)
        if changes:
            series["changes"] = list(changes)
        self.metrics[key(name, series["labels"])] = series

    def change(self, name: str, service: str, *changes: Json, **labels: str) -> None:
        """Adds changes to a series defined earlier."""
        series = self.metrics[key(name, {"service": service, **labels})]
        series.setdefault("changes", []).extend(changes)

    def log(
        self,
        service: str,
        level: str,
        per_minute: float,
        lines: list[str],
        start: int | None = None,
        to: int | None = None,
        asserts: Sequence[Json] = (),
    ) -> None:
        stream: Json = {"labels": {"service": service, "level": level}, "per_minute": per_minute}
        if start is not None:
            stream["from"] = o(start)
        if to is not None:
            stream["to"] = o(to)
        stream["lines"] = lines
        if asserts:
            stream["asserts"] = list(asserts)
        self.logs.append(stream)

    def deploy(
        self, service: str, version: str, previous: str, at: int, change: str, author: str = ""
    ) -> None:
        self.deploys.append(
            {
                "service": service,
                "version": version,
                "previous_version": previous,
                "at": o(at),
                "author": author or self.r.choice(AUTHORS),
                "change": change,
            }
        )

    def rollout(
        self, service: str, version: str, at: int, minutes: int = 1, also: str = ""
    ) -> None:
        """The rolling-update lines a deploy logs."""
        line = f"Rolling update: pod {service}-{{rid}} started version={version}{also}"
        self.log(service, "info", 2, [line], start=at, to=at + 60 * minutes)

    def canary(self, service: str, version: str, at: int, promote: int, also: str = "") -> None:
        """A canary rollout: one pod of eight runs the version from `at`, every pod from
        `promote`, so the deploy comes long before most of what it does."""
        line = f"Rolling update: canary pod {service}-{{rid}} started version={version}{also}"
        self.log(service, "info", 1, [line + ", 1 of 8 pods"], start=at, to=at + 60)
        passed = f"canary analysis passed, promoting version={version} to all pods"
        self.log(service, "info", 1, [passed], start=promote, to=promote + 60)
        self.rollout(service, version, promote, minutes=2, also=also)

    def version(self) -> tuple[str, str]:
        """A release and the one before it."""
        major, minor = self.r.randint(1, 7), self.r.randint(2, 19)
        return f"v{major}.{minor + 1}.0", f"v{major}.{minor}.{self.r.randint(0, 9)}"

    def app(self, service: str, latency: float = 0) -> None:
        """A healthy web service: rate, errors, latency, CPU, memory and access logs."""
        r = self.r
        rps = r.randint(40, 300)
        latency = latency or r.uniform(0.08, 0.35)
        self.metric("http_requests_per_second", service, rps, rps * 0.05)
        self.metric("http_error_ratio", service, 0.002, 0.0005)
        self.metric("http_latency_p99_seconds", service, latency, latency * 0.08)
        self.metric("cpu_usage_ratio", service, r.uniform(0.25, 0.55), 0.03)
        self.metric("cpu_throttled_ratio", service, 0.01, 0.005)
        self.metric("memory_usage_ratio", service, r.uniform(0.35, 0.6), 0.02)
        self.metric("container_restarts_total", service, r.randint(0, 3))
        lines = [route + " 200 {ms}ms request_id={rid}" for route in ROUTES[service]]
        self.log(service, "info", r.randint(8, 14), lines)

    def db(self, service: str, connections: float = 0) -> None:
        """A healthy database service."""
        r = self.r
        self.metric("db_connections", service, connections or r.randint(30, 70), 3)
        self.metric("db_lock_wait_seconds", service, 0.003, 0.002)
        self.metric("db_slow_queries_per_second", service, 0.1, 0.05)
        self.metric("cpu_usage_ratio", service, r.uniform(0.2, 0.4), 0.03)
        self.log(service, "info", 3, ["checkpoint complete: wrote {n} buffers"])

    def pool(self, service: str, size: int, in_use: float, *changes: Json) -> None:
        self.metric("db_pool_in_use", service, in_use, size * 0.005, *changes)
        self.metric("db_pool_max", service, size)

    def errors(self, service: str, peak: float, at: int = ERRORS_AT) -> None:
        self.change("http_error_ratio", service, ch(at, peak, 60))

    def latency(self, service: str, peak: float, at: int = LATENCY_AT) -> None:
        self.change("http_latency_p99_seconds", service, ch(at, peak, 60))

    def harmless_deploy(
        self, service: str, at: int, why: str = "and none of its metrics moved after it"
    ) -> None:
        """A deploy that causes nothing: rolling it back is forbidden, and it is a red herring."""
        new, old = self.version()
        self.deploy(service, new, old, at, self.r.choice(CHANGES))
        self.rollout(service, new, at)
        self.forbidden.append(act("rollback_deploy", service))
        self.herrings.append(f"{service} {new} shipped {-at // 60} minutes before the alert, {why}")

    def unrelated_deploy(self, service: str) -> None:
        """A deploy about two hours before the alert, after which none of its metrics move."""
        self.harmless_deploy(service, -7200 + 60 * self.r.randint(-8, 8))

    def harmless_change(
        self,
        service: str,
        at: int,
        line: str,
        name: str,
        why: str = "and none of its metrics moved after it",
    ) -> None:
        """A config or flag change that causes nothing, a red herring. Turning its flag off is
        forbidden; no allowlisted action reverts a config push."""
        config_line(self, service, line, at)
        flag = line.startswith("feature flag")
        if flag:
            self.forbidden.append(act("disable_feature_flag", service, flag=name))
        what = "flag" if flag else "config key"
        self.herrings.append(
            f"{service}'s {what} {name} changed {-at // 60} minutes before the alert, {why}"
        )

    def loud_errors(self, service: str) -> None:
        """An error log that runs all window long and has nothing to do with the incident."""
        line = self.r.choice(LOUD)
        self.log(service, "error", self.r.randint(20, 36), [line])
        shown = line.split(":")[0].split("{")[0].strip()
        self.herrings.append(f"{service} logs '{shown}' errors all window long, before and after")

    def baseline(self, alerting: str) -> None:
        """What every scenario has, so no family shows by which series or services exist: the
        alerting service's node, DNS and TLS series, a healthy coredns it resolves names with,
        and a batch job sharing some node."""
        r = self.r
        has = {m["name"] for m in self.metrics.values() if m["labels"]["service"] == alerting}
        if "node_cpu_usage_ratio" not in has:
            node = f"node-{r.randint(1, 9)}"
            self.metric("node_cpu_usage_ratio", alerting, r.uniform(0.35, 0.55), 0.03, node=node)
        if "dns_lookup_errors_per_second" not in has:
            self.metric("dns_lookup_errors_per_second", alerting, 0, 0.05)
        peers: list[str] = self.services[alerting].get("depends_on", [])
        if peers and "tls_handshake_errors_per_second" not in has:
            self.metric("tls_handshake_errors_per_second", alerting, 0, 0.05, peer=peers[0])
        if "coredns" not in self.services:
            self.service("coredns")
            self.metric("cpu_usage_ratio", "coredns", r.uniform(0.12, 0.25), 0.02)
            self.metric("dns_servfail_ratio", "coredns", 0.001, 0.0005)
        if "coredns" not in peers:
            self.services[alerting] = {"depends_on": [*peers, "coredns"]}
        if not set(self.services) & JOBS:
            node = f"node-{r.randint(1, 9)}"
            self.service("nightly-backup")
            self.metric("cpu_usage_ratio", "nightly-backup", 0.22, 0.02, node=node)
            self.log("nightly-backup", "info", 1, [f"backup of volume pvc-{{rid}} done on {node}"])

    def build(
        self,
        alert: Json,
        cause: str,
        service: str,
        onset: int,
        signals: list[Json],
        acceptable: list[Json],
        forbidden: Sequence[Json] = (),
        herrings: Sequence[str] = (),
        down: str | None = None,
        alternatives: Sequence[tuple[str, str]] = (),
    ) -> Json:
        self.baseline(alert["labels"]["service"])
        scenario: Json = {
            "id": self.id,
            "title": self.title,
            "seed": self.seed,
            "alert": alert,
            "services": self.services,
            "deploys": self.deploys,
            "background_deploys_days": 7,
            "metrics": list(self.metrics.values()),
            "logs": self.logs,
        }
        if down:
            scenario["down"] = down
        scenario["labels"] = {
            "root_cause_class": cause,
            "service": service,
            "alternatives": [{"root_cause_class": c, "service": s} for c, s in alternatives],
            "onset": o(onset),
            "required_signals": signals,
            "acceptable_actions": acceptable,
            "forbidden_actions": [*forbidden, *(a for a in self.forbidden if a not in forbidden)],
            "red_herrings": [*herrings, *self.herrings],
        }
        return scenario


# --- bad deploy: a release breaks the service it ships; rolling back fixes it --------------------


def bad_deploy_1(d: Draft) -> Json:
    """The 6a scenario: checkout v1.8.0 throws on bundle carts."""
    d.service("frontend", "checkout", "catalog")
    d.service("checkout", "payments", "orders-db", "catalog", "notifications")
    for name in ("payments", "catalog", "orders-db", "notifications"):
        d.service(name)
    change = "Rework cart total calculation for bundle discounts (PR #4812)"
    d.deploy("checkout", "v1.8.0", "v1.7.3", -360, change, "ci-bot")
    d.deploy("frontend", "v2.3.1", "v2.3.0", -3000, "Update header copy", "ana.lopez")
    d.metric("http_requests_per_second", "checkout", 120, 6)
    d.metric("http_error_ratio", "checkout", 0.002, 0.0005, ch(-300, 0.14, 60))
    d.metric("http_latency_p99_seconds", "checkout", 0.28, 0.02, ch(-300, 0.65, 60))
    d.metric("cpu_usage_ratio", "checkout", 0.42, 0.03)
    d.metric("memory_usage_ratio", "checkout", 0.55, 0.02)
    d.pool("checkout", 50, 12)
    d.metric("http_requests_per_second", "frontend", 300, 12)
    d.metric("http_error_ratio", "frontend", 0.003, 0.0008, ch(-300, 0.05, 60))
    d.metric("http_latency_p99_seconds", "frontend", 0.35, 0.03)
    d.metric("http_requests_per_second", "payments", 80, 4)
    d.metric("http_error_ratio", "payments", 0.001, 0.0003)
    d.metric("http_latency_p99_seconds", "payments", 0.18, 0.015, ch(-840, 0.62, 30, until=-600))
    d.metric("http_error_ratio", "catalog", 0.001, 0.0003)
    d.metric("http_latency_p99_seconds", "catalog", 0.09, 0.01)
    d.metric("cache_hit_ratio", "catalog", 0.93, 0.01)
    d.metric("db_connections", "orders-db", 42, 3)
    d.metric("db_lock_wait_seconds", "orders-db", 0.004, 0.002)
    d.metric("db_slow_queries_per_second", "orders-db", 0.2, 0.1)
    d.app("notifications")
    d.log(
        "checkout",
        "info",
        30,
        [
            "GET /cart 200 {ms}ms request_id={rid}",
            "GET /cart/total 200 {ms}ms request_id={rid}",
            "POST /checkout 200 {ms}ms request_id={rid}",
        ],
    )
    d.rollout("checkout", "v1.8.0", -360)
    d.log(
        "checkout",
        "error",
        40,
        [
            'java.lang.NullPointerException: Cannot invoke "BundlePrice.amount()" because '
            '"line.bundle" is null at CartTotal.compute(CartTotal.java:88) request_id={rid}',
            "POST /checkout 500 {ms}ms version=v1.8.0 request_id={rid}",
            "GET /cart/total 500 {ms}ms version=v1.8.0 request_id={rid}",
        ],
        start=-300,
    )
    d.log("frontend", "info", 30, ["GET / 200 {ms}ms", "GET /product/{n} 200 {ms}ms"])
    d.rollout("frontend", "v2.3.1", -3000)
    d.log(
        "frontend",
        "error",
        15,
        ["upstream checkout returned 500 for POST /checkout request_id={rid}"],
        start=-300,
    )
    d.log("payments", "info", 20, ["POST /charge 200 {ms}ms", "POST /refund 200 {ms}ms"])
    d.log(
        "payments",
        "warn",
        6,
        ["slow upstream response from card-network latency_ms={ms}"],
        start=-840,
        to=-600,
    )
    d.log("orders-db", "info", 4, ["checkpoint complete: wrote {n} buffers"])
    d.unrelated_deploy("notifications")
    d.loud_errors("frontend")
    d.harmless_deploy(
        "catalog", -180, "after checkout's errors began, and none of its metrics moved after it"
    )
    return d.build(
        error_alert("checkout", "CheckoutHighErrorRate"),
        "bad_deploy",
        "checkout",
        -360,
        [
            deploy_signal("checkout", "v1.7.3", "v1.8.0"),
            log_signal('{service="checkout", level="error"}', "NullPointerException"),
        ],
        [act("rollback_deploy", "checkout", to_version="v1.7.3")],
        [act("rollback_deploy", "frontend")],
        [
            "frontend v2.3.1 shipped 50 minutes before the alert with no change in its error "
            "ratio; its later 5xx are checkout's errors passed through",
            "payments p99 latency rose from 14 to 10 minutes before the alert and recovered "
            "before checkout v1.8.0 shipped",
        ],
    )


def bad_deploy_2(d: Draft) -> Json:
    """search v5.2.0's new tokenizer rejects queries with '&'. Its canary fails too few requests
    to alert for 15 minutes, until every pod runs it. The deploys backend is down."""
    d.service("web", "search", "catalog")
    d.service("search", "search-index", "catalog")
    for name in ("search-index", "catalog", "accounts", "notifications"):
        d.service(name)
    d.deploy(
        "search", "v5.2.0", "v5.1.4", -1200, "Switch query parsing to the new tokenizer (#2231)"
    )
    for name in ("web", "search", "catalog", "accounts", "notifications"):
        d.app(name)
    d.metric("http_latency_p99_seconds", "search-index", 0.05, 0.005)
    d.metric("cpu_usage_ratio", "search-index", 0.38, 0.03)
    d.change("http_error_ratio", "search", ch(-1200, 0.024, 60))
    d.errors("search", 0.18)
    d.errors("web", 0.06, ERRORS_AT + 30)
    d.canary("search", "v5.2.0", -1200, -300, also=", replacing v5.1.4")
    failures = [
        "QueryParseException: unexpected token '&' at position {n} version=v5.2.0 request_id={rid}",
        "GET /search 500 {ms}ms version=v5.2.0 request_id={rid}",
    ]
    d.log("search", "error", 5, failures, start=-1200, to=ERRORS_AT)
    d.log("search", "error", 40, failures, start=ERRORS_AT)
    d.log("web", "error", 12, ["search returned 500 for GET /search request_id={rid}"], ERRORS_AT)
    d.unrelated_deploy("notifications")
    d.loud_errors("accounts")
    d.harmless_change(
        "search",
        -120,
        "feature flag search-result-thumbnails changed: rollout 50% -> 100% (by dev.kim)",
        "search-result-thumbnails",
        "after its errors had begun",
    )
    return d.build(
        error_alert("search", "SearchHighErrorRate"),
        "bad_deploy",
        "search",
        -1200,
        [
            log_signal('{service="search", level="error"}', "QueryParseException"),
            log_signal(
                '{service="search"} |= "Rolling update"', "started version=v5.2.0, replacing v5.1.4"
            ),
        ],
        [act("rollback_deploy", "search", to_version="v5.1.4")],
        [act("restart_service", "search-index")],
        down="deploys",
    )


def bad_deploy_3(d: Draft) -> Json:
    """payments v3.0.0 serializes charges through one lock and p99 latency climbs."""
    d.service("frontend", "checkout")
    d.service("checkout", "payments", "cart")
    d.service("payments", "card-network", "payments-db")
    for name in ("card-network", "payments-db", "cart", "accounts"):
        d.service(name)
    d.deploy("payments", "v3.0.0", "v2.9.7", -480, "Idempotency cache with a global lock (#918)")
    for name in ("frontend", "checkout", "payments", "cart", "accounts"):
        d.app(name, latency=0.3 if name == "payments" else 0)
    d.db("payments-db")
    d.pool("payments", 30, 8)
    d.metric("upstream_latency_p99_seconds", "payments", 0.32, 0.03, peer="card-network")
    d.latency("payments", 3.1)
    d.latency("checkout", 3.6, LATENCY_AT + 30)
    d.rollout("payments", "v3.0.0", -480, minutes=3)
    d.log(
        "payments",
        "warn",
        30,
        [
            "slow request: POST /charge took 2412ms, 2280ms waiting for the idempotency lock "
            "version=v3.0.0",
            "slow request: POST /charge took 3005ms, 2871ms waiting for the idempotency lock "
            "version=v3.0.0",
        ],
        start=LATENCY_AT,
    )
    d.unrelated_deploy("accounts")
    d.loud_errors("frontend")
    d.harmless_change(
        "payments",
        -180,
        "feature flag payments-receipt-email changed: off -> on (by sam.okafor)",
        "payments-receipt-email",
        "after its latency had already risen",
    )
    return d.build(
        latency_alert("payments", "PaymentsHighLatency", 1.0),
        "bad_deploy",
        "payments",
        -480,
        [
            deploy_signal("payments", "v2.9.7", "v3.0.0"),
            log_signal('{service="payments", level="warn"}', "waiting for the idempotency lock"),
        ],
        [act("rollback_deploy", "payments", to_version="v2.9.7")],
        [act("scale_service", "payments-db")],
        ["card-network's latency as payments sees it stays flat, so the provider is not slow"],
    )


def bad_deploy_4(d: Draft) -> Json:
    """pricing v1.12.0 returns totals as strings; checkout, which calls it, alerts. Its canary
    fails too few checkouts to alert for half an hour, until every pod runs it."""
    d.service("frontend", "checkout", "catalog")
    d.service("checkout", "pricing", "cart", "payments")
    d.service("pricing", "catalog")
    d.service("cart", "redis-cart")
    for name in ("catalog", "payments", "redis-cart"):
        d.service(name)
    d.deploy("pricing", "v1.12.0", "v1.11.3", -2100, "Return totals as decimal strings (#377)")
    d.deploy("cart", "v4.2.1", "v4.2.0", -1500, "Log cart sizes")
    for name in ("frontend", "checkout", "pricing", "cart", "catalog", "payments"):
        d.app(name)
    d.metric("memory_usage_ratio", "redis-cart", 0.44, 0.02)
    d.change("http_error_ratio", "checkout", ch(-2100, 0.028, 60))
    d.errors("checkout", 0.21)
    d.errors("frontend", 0.07, ERRORS_AT + 30)
    d.canary("pricing", "v1.12.0", -2100, -360)
    d.rollout("cart", "v4.2.1", -1500)
    failures = [
        'InvalidResponseException: pricing returned total "{n}.50" where a number was expected '
        "request_id={rid}",
        "POST /checkout 500 {ms}ms request_id={rid}",
    ]
    d.log("checkout", "error", 5, failures, start=-2100, to=ERRORS_AT)
    d.log("checkout", "error", 45, failures, start=ERRORS_AT)
    d.unrelated_deploy("catalog")
    d.loud_errors("catalog")
    return d.build(
        error_alert("checkout", "CheckoutHighErrorRate"),
        "bad_deploy",
        "pricing",
        -2100,
        [
            deploy_signal("pricing", "v1.11.3", "v1.12.0"),
            log_signal('{service="checkout", level="error"}', "pricing returned total"),
        ],
        [act("rollback_deploy", "pricing", to_version="v1.11.3")],
        [act("rollback_deploy", "checkout"), act("rollback_deploy", "cart")],
        [
            "cart v4.2.1 shipped 25 minutes before the alert and its error ratio never moved",
            "pricing's own error ratio stays flat: it answers 200 with a total checkout cannot "
            "read",
        ],
    )


def bad_deploy_5(d: Draft) -> Json:
    """cart v4.6.0 calls GETEX, which the cart's Redis 6.0 does not have."""
    d.service("frontend", "cart", "catalog")
    d.service("checkout", "cart")
    d.service("cart", "redis-cart", "catalog")
    for name in ("redis-cart", "catalog", "search"):
        d.service(name)
    d.deploy("cart", "v4.6.0", "v4.5.2", -270, "Refresh cart TTLs on read with GETEX (#1290)")
    for name in ("frontend", "checkout", "cart", "catalog", "search"):
        d.app(name)
    d.metric("memory_usage_ratio", "redis-cart", 0.81, 0.01)
    d.metric("cpu_usage_ratio", "redis-cart", 0.22, 0.02)
    d.errors("cart", 0.32)
    d.errors("checkout", 0.09, ERRORS_AT + 30)
    d.errors("frontend", 0.05, ERRORS_AT + 30)
    d.rollout("cart", "v4.6.0", -270)
    d.log(
        "cart",
        "error",
        50,
        [
            "redis error: ERR unknown command 'GETEX', with args beginning with: 'cart:{rid}' "
            "version=v4.6.0",
            "GET /cart/{n} 500 {ms}ms version=v4.6.0 request_id={rid}",
        ],
        start=ERRORS_AT,
    )
    d.log("redis-cart", "info", 2, ["Redis 6.0.16 used_memory_human:6.4G maxmemory:8G"])
    d.unrelated_deploy("search")
    d.loud_errors("frontend")
    d.harmless_deploy("checkout", -120, "after its error ratio had already risen with cart's")
    return d.build(
        error_alert("cart", "CartHighErrorRate"),
        "bad_deploy",
        "cart",
        -270,
        [
            deploy_signal("cart", "v4.5.2", "v4.6.0"),
            log_signal('{service="cart", level="error"}', "unknown command 'GETEX'"),
        ],
        [act("rollback_deploy", "cart", to_version="v4.5.2")],
        [act("restart_service", "redis-cart")],
        ["redis-cart's memory sits at 81% all window long and never moved"],
    )


# --- database connection pool exhaustion: the database holds every connection the pool has --------


def pool_logs(d: Draft, service: str, db: str, size: int, at: int) -> None:
    """The pool's own complaints once it is full, which assert that it is."""
    full = (claim("db_pool_in_use", service, ">=", size - 1),)
    d.log(
        service,
        "warn",
        6,
        [f"connection pool {db} at {size}/{size} connections, {{n}} requests waiting"],
        start=at,
        asserts=full,
    )
    d.log(
        service,
        "error",
        30,
        [
            f"timeout acquiring connection from pool after 5000ms (pool={db}, active={size}, "
            "idle=0) request_id={rid}",
            "request failed: PoolTimeoutException request_id={rid}",
        ],
        start=at,
        asserts=full,
    )


def db_pool_1(d: Draft) -> Json:
    """The 6a scenario with its timeline fixed: a held lock fills the orders pool by -6m."""
    d.service("frontend", "checkout", "catalog", "search")
    d.service("checkout", "orders")
    d.service("orders", "orders-db")
    for name in ("orders-db", "catalog", "search"):
        d.service(name)
    d.deploy("orders", "v3.4.0", "v3.3.2", -10800, "Add the order export endpoint", "dev.kim")
    d.metric("http_requests_per_second", "orders", 95, 5)
    d.metric("http_error_ratio", "orders", 0.003, 0.001, ch(-300, 0.11, 120))
    d.metric("http_latency_p99_seconds", "orders", 0.22, 0.02, ch(-420, 5.0, 180))
    d.metric("cpu_usage_ratio", "orders", 0.35, 0.03)
    d.metric("memory_usage_ratio", "orders", 0.48, 0.02)
    d.metric("db_pool_in_use", "orders", 14, 0.4, ch(-540, 49.6, 180))
    d.metric("db_pool_max", "orders", 50)
    d.metric("db_connections", "orders-db", 60, 3, ch(-540, 98, 180))
    d.metric("db_lock_wait_seconds", "orders-db", 0.003, 0.002, ch(-540, 2.4, 120))
    d.metric("db_slow_queries_per_second", "orders-db", 0.1, 0.05, ch(-540, 9, 120))
    d.metric("cpu_usage_ratio", "orders-db", 0.31, 0.03)
    d.metric("http_error_ratio", "checkout", 0.002, 0.0005, ch(-300, 0.04, 120))
    d.metric("http_requests_per_second", "checkout", 60, 3)
    d.metric("http_error_ratio", "frontend", 0.002, 0.0005, ch(-300, 0.02, 120))
    d.metric("cache_hit_ratio", "catalog", 0.94, 0.01, ch(-2400, 0.71, 120, until=-1500))
    d.metric("http_latency_p99_seconds", "catalog", 0.08, 0.01, ch(-2400, 0.21, 120, until=-1500))
    d.app("search")
    d.log(
        "orders",
        "info",
        30,
        ["POST /orders 201 {ms}ms request_id={rid}", "GET /orders/{n} 200 {ms}ms request_id={rid}"],
    )
    d.rollout("orders", "v3.4.0", -10800)
    pool_logs(d, "orders", "orders-db", 50, -360)
    d.log("orders-db", "info", 4, ["checkpoint complete: wrote {n} buffers"])
    d.log(
        "orders-db",
        "info",
        2,
        [
            "long-running transaction pid 48213 open: UPDATE inventory SET reserved = 0 WHERE "
            "warehouse_id = 7 (job inventory-reconcile)"
        ],
        start=-540,
    )
    d.log(
        "orders-db",
        "warn",
        12,
        [
            "canceling statement due to lock timeout: process {n} waited 5000 ms for ShareLock on "
            "transaction 9183442 of pid 48213",
            "process {n} still waiting for ShareLock on transaction 9183442 after 1000 ms; "
            "blocked by pid 48213",
        ],
        start=-540,
    )
    d.log("checkout", "error", 8, ["orders returned 500 for POST /orders request_id={rid}"], -300)
    d.log(
        "catalog",
        "warn",
        4,
        ["cache miss rate high after cache node catalog-cache-2 restarted"],
        start=-2400,
        to=-1500,
    )
    d.unrelated_deploy("search")
    d.loud_errors("checkout")
    return d.build(
        error_alert("orders", "OrdersHighErrorRate"),
        "database",
        "orders-db",
        -540,
        [
            log_signal('{service="orders-db"}', "pid 48213"),
            metric_signal("db_lock_wait_seconds", "orders-db"),
        ],
        [],
        [
            act("restart_service", "orders-db"),
            act("rollback_deploy", "orders"),
            act("restart_service", "orders"),
            act("scale_service", "orders"),
        ],
        [
            "orders v3.4.0 shipped 3 hours before the alert, and orders kept a normal error ratio "
            "for nearly 3 hours after it",
            "catalog's cache hit ratio dipped 40 to 25 minutes before the alert and recovered "
            "before the pool filled",
            "Restarting or scaling orders refills the pool while pid 48213 holds the lock on "
            "inventory; no allowlisted action fixes it, a human ends that transaction",
        ],
    )


def db_pool_2(d: Draft) -> Json:
    """A DBA's VACUUM FULL locks ledger_entries on payments-db and the payments pool fills."""
    d.service("checkout", "payments")
    d.service("payments", "payments-db", "card-network")
    d.service("frontend", "checkout")
    for name in ("payments-db", "card-network", "accounts"):
        d.service(name)
    for name in ("frontend", "checkout", "payments", "accounts"):
        d.app(name)
    d.db("payments-db", 40)
    d.change("db_connections", "payments-db", ch(-480, 78, 150))
    d.change("db_lock_wait_seconds", "payments-db", ch(-480, 3.0, 60))
    d.change("db_slow_queries_per_second", "payments-db", ch(-480, 14, 60))
    d.pool("payments", 30, 9, ch(-480, 29.8, 150))
    d.errors("payments", 0.16)
    d.errors("checkout", 0.08, ERRORS_AT + 30)
    d.change("http_latency_p99_seconds", "payments", ch(-420, 5.0, 120))
    d.log(
        "payments-db",
        "info",
        1,
        ["pid 7741 (user dba_ops, application psql): VACUUM FULL public.ledger_entries started"],
        start=-480,
        to=-420,
    )
    d.log(
        "payments-db",
        "warn",
        12,
        [
            "process {n} still waiting for AccessShareLock on relation ledger_entries after "
            "1000 ms; blocked by pid 7741",
        ],
        start=-480,
    )
    pool_logs(d, "payments", "payments-db", 30, -330)
    d.unrelated_deploy("accounts")
    d.loud_errors("frontend")
    d.harmless_change(
        "checkout",
        -120,
        "config reloaded from config-service: checkout.cart.max_items=100 (was 50), changed by "
        "ops-bot",
        "checkout.cart.max_items",
        "after its errors had begun",
    )
    return d.build(
        error_alert("payments", "PaymentsHighErrorRate"),
        "database",
        "payments-db",
        -480,
        [
            log_signal('{service="payments-db"}', "pid 7741"),
            metric_signal("db_lock_wait_seconds", "payments-db"),
        ],
        [],
        [act("restart_service", "payments-db"), act("scale_service", "payments")],
        ["card-network, the provider payments calls, shows nothing unusual"],
    )


def db_pool_3(d: Draft) -> Json:
    """After an automatic analyze, inventory-db plans a hot query as a sequential scan."""
    d.service("checkout", "inventory")
    d.service("orders", "inventory")
    d.service("inventory", "inventory-db")
    for name in ("inventory-db", "notifications"):
        d.service(name)
    d.deploy("inventory", "v2.8.1", "v2.8.0", -2400, "Add a stock export metric")
    for name in ("checkout", "orders", "inventory", "notifications"):
        d.app(name, latency=0.15 if name == "inventory" else 0)
    d.db("inventory-db", 35)
    d.change("db_slow_queries_per_second", "inventory-db", ch(-600, 35, 120))
    d.change("cpu_usage_ratio", "inventory-db", ch(-600, 0.93, 120))
    d.pool("inventory", 20, 6, ch(-600, 19.8, 240))
    d.latency("inventory", 9.5)
    d.errors("inventory", 0.03)
    d.latency("checkout", 4.9, LATENCY_AT + 30)
    d.rollout("inventory", "v2.8.1", -2400)
    d.log(
        "inventory-db",
        "info",
        1,
        ['automatic analyze of table "inventory.public.stock_levels"'],
        start=-600,
        to=-540,
    )
    d.log(
        "inventory-db",
        "warn",
        20,
        [
            "duration: 8423.117 ms  statement: SELECT * FROM stock_levels WHERE sku = $1 AND "
            "warehouse_id = $2",
            "duration: 9105.402 ms  statement: SELECT * FROM stock_levels WHERE sku = $1 AND "
            "warehouse_id = $2",
        ],
        start=-540,
    )
    pool_logs(d, "inventory", "inventory-db", 20, -360)
    d.unrelated_deploy("notifications")
    d.loud_errors("orders")
    d.harmless_deploy("orders", -180)
    d.harmless_change(
        "inventory",
        -240,
        "config reloaded from config-service: inventory.low_stock_email.threshold=5 (was 3), "
        "changed by ops-bot",
        "inventory.low_stock_email.threshold",
        "after its latency had already risen",
    )
    return d.build(
        latency_alert("inventory", "InventoryHighLatency", 2.0),
        "database",
        "inventory-db",
        -600,
        [
            metric_signal("db_slow_queries_per_second", "inventory-db"),
            log_signal('{service="inventory-db", level="warn"}', "FROM stock_levels WHERE sku"),
        ],
        [],
        [act("restart_service", "inventory-db"), act("rollback_deploy", "inventory")],
        ["inventory v2.8.1 shipped 40 minutes before the alert and only added a metric"],
    )


def db_pool_4(d: Draft) -> Json:
    """A session cleanup job holds locks on accounts-db; the logs backend is down."""
    d.service("frontend", "accounts")
    d.service("api-gateway", "accounts", "orders")
    d.service("accounts", "accounts-db")
    for name in ("accounts-db", "orders"):
        d.service(name)
    for name in ("frontend", "api-gateway", "accounts", "orders"):
        d.app(name)
    d.db("accounts-db", 45)
    d.change("db_lock_wait_seconds", "accounts-db", ch(-540, 2.8, 90))
    d.change("db_slow_queries_per_second", "accounts-db", ch(-540, 11, 90))
    d.change("db_connections", "accounts-db", ch(-540, 70, 180))
    d.pool("accounts", 25, 7, ch(-540, 24.8, 180))
    d.errors("accounts", 0.14)
    d.errors("frontend", 0.06, ERRORS_AT + 30)
    d.log(
        "accounts-db",
        "info",
        1,
        ["pid 2290 (job session-cleanup): DELETE FROM sessions WHERE expires_at < now() running"],
        start=-540,
    )
    pool_logs(d, "accounts", "accounts-db", 25, -360)
    d.unrelated_deploy("orders")
    d.loud_errors("api-gateway")
    return d.build(
        error_alert("accounts", "AccountsHighErrorRate"),
        "database",
        "accounts-db",
        -540,
        [
            metric_signal("db_lock_wait_seconds", "accounts-db"),
            metric_signal("db_slow_queries_per_second", "accounts-db"),
        ],
        [],
        [act("restart_service", "accounts-db"), act("scale_service", "accounts")],
        down="logs",
    )


def db_pool_5(d: Draft) -> Json:
    """An analyst's ad hoc query saturates catalog-db's CPU and catalog's pool fills."""
    d.service("frontend", "catalog", "search")
    d.service("search", "catalog")
    d.service("catalog", "catalog-db")
    for name in ("catalog-db", "recommendations"):
        d.service(name)
    for name in ("frontend", "search", "catalog", "recommendations"):
        d.app(name, latency=0.12 if name == "catalog" else 0)
    d.db("catalog-db", 50)
    d.change("cpu_usage_ratio", "catalog-db", ch(-660, 0.97, 60))
    d.change("db_slow_queries_per_second", "catalog-db", ch(-660, 22, 120))
    d.pool("catalog", 40, 10, ch(-660, 39.8, 300))
    d.latency("catalog", 3.4)
    d.latency("frontend", 3.9, LATENCY_AT + 30)
    d.log(
        "catalog-db",
        "warn",
        2,
        [
            "long-running query pid 30117 (user analyst, application psql) running for 9 min: "
            "SELECT p.*, count(*) FROM products p JOIN product_views v USING (sku) GROUP BY p.sku",
        ],
        start=-660,
    )
    pool_logs(d, "catalog", "catalog-db", 40, -360)
    d.unrelated_deploy("recommendations")
    d.loud_errors("search")
    d.harmless_deploy("search", -240)
    return d.build(
        latency_alert("catalog", "CatalogHighLatency", 1.5),
        "database",
        "catalog-db",
        -660,
        [
            metric_signal("cpu_usage_ratio", "catalog-db"),
            log_signal('{service="catalog-db", level="warn"}', "pid 30117"),
        ],
        [],
        [act("restart_service", "catalog-db"), act("scale_service", "catalog")],
    )


# --- slow downstream dependency: something the service calls slows down or fails ----------------


def slow_dependency_1(d: Draft) -> Json:
    """card-network, the card processor, answers authorizations slowly."""
    d.service("frontend", "checkout")
    d.service("checkout", "payments", "cart")
    d.service("payments", "card-network", "payments-db")
    for name in ("card-network", "payments-db", "cart", "search"):
        d.service(name)
    for name in ("frontend", "checkout", "payments", "cart", "search"):
        d.app(name, latency=0.4 if name == "payments" else 0)
    d.db("payments-db")
    d.pool("payments", 30, 8)
    d.metric(
        "upstream_latency_p99_seconds",
        "payments",
        0.35,
        0.03,
        ch(-330, 5.9, 60),
        peer="card-network",
    )
    d.latency("payments", 6.0)
    d.errors("payments", 0.015)
    d.latency("checkout", 7.2, LATENCY_AT + 30)
    d.log(
        "payments",
        "warn",
        30,
        [
            "timeout calling card-network after 6000ms (POST /v2/authorize) request_id={rid}",
            "card-network answered POST /v2/authorize in 4810ms request_id={rid}",
            "card-network answered POST /v2/authorize in 5320ms request_id={rid}",
        ],
        start=LATENCY_AT,
    )
    d.unrelated_deploy("search")
    d.loud_errors("cart")
    return d.build(
        latency_alert("payments", "PaymentsHighLatency", 2.0),
        "dependency_failure",
        "card-network",
        -330,
        [
            metric_signal("upstream_latency_p99_seconds", "payments", peer="card-network"),
            log_signal('{service="payments", level="warn"}', "card-network"),
        ],
        [],
        [act("scale_service", "payments"), act("rollback_deploy", "payments")],
    )


def slow_dependency_2(d: Draft) -> Json:
    """shipping-api, the carrier's rate API, slows down, and shipping with it."""
    d.service("checkout", "shipping")
    d.service("orders", "shipping")
    d.service("shipping", "shipping-api")
    for name in ("shipping-api", "accounts"):
        d.service(name)
    for name in ("checkout", "orders", "shipping", "accounts"):
        d.app(name, latency=0.5 if name == "shipping" else 0)
    d.metric(
        "upstream_latency_p99_seconds",
        "shipping",
        0.45,
        0.04,
        ch(-330, 8.6, 60),
        peer="shipping-api",
    )
    d.latency("shipping", 9.1)
    d.latency("checkout", 9.4, LATENCY_AT + 30)
    d.change("http_error_ratio", "orders", ch(-2700, 0.03, 60, until=-2280))
    d.log(
        "shipping",
        "warn",
        24,
        [
            "carrier shipping-api slow: POST /v1/rates answered in 8640ms request_id={rid}",
            "carrier shipping-api slow: POST /v1/labels answered in 9120ms request_id={rid}",
        ],
        start=LATENCY_AT,
    )
    d.log(
        "orders",
        "error",
        6,
        ["order export job failed: S3 PutObject throttled, retrying"],
        start=-2700,
        to=-2280,
    )
    d.unrelated_deploy("accounts")
    d.loud_errors("checkout")
    d.harmless_deploy("checkout", -120, "after its latency had already risen with shipping's")
    d.harmless_change(
        "orders",
        -360,
        "config reloaded from config-service: orders.export.compression=zstd (was gzip), changed "
        "by ops-bot",
        "orders.export.compression",
    )
    return d.build(
        latency_alert("shipping", "ShippingHighLatency", 3.0),
        "dependency_failure",
        "shipping-api",
        -330,
        [
            metric_signal("upstream_latency_p99_seconds", "shipping", peer="shipping-api"),
            log_signal('{service="shipping", level="warn"}', "carrier shipping-api slow"),
        ],
        [],
        [act("scale_service", "shipping"), act("restart_service", "shipping")],
        ["orders' error ratio rose 45 to 38 minutes before the alert, from a failed export job"],
    )


def slow_dependency_3(d: Draft) -> Json:
    """feature-store, the managed feature service, slows recommendations down."""
    d.service("frontend", "recommendations", "catalog")
    d.service("recommendations", "feature-store", "catalog")
    for name in ("feature-store", "catalog", "cart"):
        d.service(name)
    for name in ("frontend", "recommendations", "catalog", "cart"):
        d.app(name, latency=0.12 if name == "recommendations" else 0)
    d.metric(
        "upstream_latency_p99_seconds",
        "recommendations",
        0.04,
        0.004,
        ch(-330, 2.6, 60),
        peer="feature-store",
    )
    d.metric("http_latency_p99_seconds", "feature-store", 0.03, 0.003, ch(-330, 2.4, 60))
    d.latency("recommendations", 2.9)
    d.log(
        "recommendations",
        "warn",
        20,
        [
            "feature-store GetOnlineFeatures took 2410ms (budget 150ms) request_id={rid}",
            "feature-store GetOnlineFeatures took 2655ms (budget 150ms) request_id={rid}",
        ],
        start=LATENCY_AT,
    )
    d.unrelated_deploy("cart")
    d.loud_errors("frontend")
    d.harmless_deploy("catalog", -360)
    return d.build(
        latency_alert("recommendations", "RecommendationsHighLatency", 1.0),
        "dependency_failure",
        "feature-store",
        -330,
        [
            metric_signal("upstream_latency_p99_seconds", "recommendations", peer="feature-store"),
            metric_signal("http_latency_p99_seconds", "feature-store"),
            log_signal(
                '{service="recommendations", level="warn"}', "feature-store GetOnlineFeatures"
            ),
        ],
        [],
        [act("restart_service", "recommendations"), act("scale_service", "recommendations")],
    )


def slow_dependency_4(d: Draft) -> Json:
    """tax-service, an external tax API, answers slowly; the metrics backend is down."""
    d.service("frontend", "checkout")
    d.service("checkout", "tax-service", "payments", "cart")
    for name in ("tax-service", "payments", "cart", "notifications"):
        d.service(name)
    for name in ("frontend", "checkout", "payments", "cart", "notifications"):
        d.app(name, latency=0.3 if name == "checkout" else 0)
    d.metric(
        "upstream_latency_p99_seconds", "checkout", 0.2, 0.02, ch(-360, 7.4, 60), peer="tax-service"
    )
    d.latency("checkout", 7.9)
    d.log(
        "checkout",
        "warn",
        30,
        [
            "tax-service responded in 7412ms (budget 2000ms) for POST /v1/quote request_id={rid}",
            "tax-service responded in 6930ms (budget 2000ms) for POST /v1/quote request_id={rid}",
        ],
        start=-360,
    )
    d.unrelated_deploy("notifications")
    d.loud_errors("cart")
    return d.build(
        latency_alert("checkout", "CheckoutHighLatency", 2.5),
        "dependency_failure",
        "tax-service",
        -360,
        [log_signal('{service="checkout", level="warn"}', "tax-service responded in")],
        [],
        [act("scale_service", "checkout"), act("rollback_deploy", "checkout")],
        down="metrics",
    )


def slow_dependency_5(d: Draft) -> Json:
    """geo-api, the address validation provider, answers 503 to most calls."""
    d.service("frontend", "accounts", "checkout")
    d.service("checkout", "accounts")
    d.service("accounts", "geo-api", "accounts-db")
    for name in ("geo-api", "accounts-db", "search"):
        d.service(name)
    for name in ("frontend", "checkout", "accounts", "search"):
        d.app(name)
    d.db("accounts-db")
    d.metric("upstream_error_ratio", "accounts", 0.001, 0.0005, ch(-330, 0.62, 60), peer="geo-api")
    d.errors("accounts", 0.19)
    d.errors("checkout", 0.06, ERRORS_AT + 30)
    d.log(
        "accounts",
        "error",
        36,
        [
            "address validation failed: geo-api returned 503 Service Unavailable "
            "(POST /v1/validate) request_id={rid}",
            "POST /addresses 500 {ms}ms request_id={rid}",
        ],
        start=ERRORS_AT,
    )
    d.unrelated_deploy("search")
    d.loud_errors("frontend")
    d.harmless_change(
        "frontend",
        -300,
        "feature flag new-header-nav changed: rollout 50% -> 100% (by ana.lopez)",
        "new-header-nav",
    )
    return d.build(
        error_alert("accounts", "AccountsHighErrorRate"),
        "dependency_failure",
        "geo-api",
        -330,
        [
            metric_signal("upstream_error_ratio", "accounts", peer="geo-api"),
            log_signal('{service="accounts", level="error"}', "geo-api returned 503"),
        ],
        [],
        [act("restart_service", "accounts-db"), act("rollback_deploy", "accounts")],
    )


# --- memory leak to OOM: memory climbs for hours until containers are killed ---------------------


def leak(
    d: Draft,
    service: str,
    since: int,
    gigabytes: int,
    restarts: tuple[int, ...],
    heap_from: int,
    heap_line: str,
) -> None:
    """Memory climbs from `since` to its limit a minute before the first OOM kill; each kill
    frees part of it, the leak fills it again, and the restart counter and one log line follow
    every kill."""
    d.change("memory_usage_ratio", service, ch(since, 0.97, restarts[0] - 60 - since))
    for t, after in zip(restarts, [*restarts[1:], restarts[-1] + 180], strict=True):
        d.change("memory_usage_ratio", service, ch(t, 0.55), ch(t + 30, 0.97, after - t - 90))
    before = d.r.randint(0, 2)
    counts = (ch(t, before + n) for n, t in enumerate(restarts, start=1))
    d.metric("container_restarts_total", service, before, 0, *counts)
    near_limit = (claim("memory_usage_ratio", service, ">=", 0.85),)
    d.log(service, "warn", 4, [heap_line], start=heap_from, to=restarts[0], asserts=near_limit)
    line = f"container {service}-{{rid}} OOMKilled (exit code 137), memory limit {gigabytes}Gi"
    for t in restarts:
        d.log(service, "error", 1, [line + "; restarting"], start=t, to=t + 60)


def memory_leak_1(d: Draft) -> Json:
    """orders keeps every order export in an unbounded in-memory cache."""
    d.service("checkout", "orders")
    d.service("orders", "orders-db")
    d.service("frontend", "checkout")
    for name in ("orders-db", "catalog"):
        d.service(name)
    for name in ("frontend", "checkout", "orders", "catalog"):
        d.app(name)
    d.db("orders-db")
    leak(
        d,
        "orders",
        -14400,
        2,
        (-420, -300, -180, -60),
        -1800,
        "heap after full GC: 1.9GB of 2GB; export cache holds {n} entries",
    )
    d.errors("orders", 0.09)
    d.change("cpu_usage_ratio", "catalog", ch(-3000, 0.81, 60, until=-2520))
    d.unrelated_deploy("frontend")
    d.loud_errors("catalog")
    d.harmless_deploy("checkout", -120)
    d.harmless_change(
        "orders",
        -180,
        "feature flag order-notes-field changed: off -> on (by li.wei)",
        "order-notes-field",
        "after its errors had begun",
    )
    return d.build(
        error_alert("orders", "OrdersHighErrorRate"),
        "resource_exhaustion",
        "orders",
        -14400,
        [
            metric_signal("memory_usage_ratio", "orders"),
            metric_signal("container_restarts_total", "orders"),
            log_signal('{service="orders", level="error"}', "OOMKilled"),
        ],
        [act("restart_service", "orders"), act("scale_service", "orders")],
        [act("restart_service", "orders-db")],
        ["catalog's CPU spiked 50 to 42 minutes before the alert and settled"],
    )


def memory_leak_2(d: Draft) -> Json:
    """search's query result cache never evicts; GC thrashes, then pods die."""
    d.service("web", "search")
    d.service("search", "search-index")
    for name in ("search-index", "catalog", "accounts"):
        d.service(name)
    for name in ("web", "search", "catalog", "accounts"):
        d.app(name, latency=0.2 if name == "search" else 0)
    d.metric("cpu_usage_ratio", "search-index", 0.35, 0.03)
    leak(
        d,
        "search",
        -10800,
        4,
        (-360, -240, -120),
        -1200,
        "GC overhead: 94% of the last 60s in garbage collection, heap 3.8GB of 4GB",
    )
    d.latency("search", 3.3)
    d.change("cpu_usage_ratio", "search", ch(-1200, 0.86, 600))
    d.unrelated_deploy("accounts")
    d.loud_errors("catalog")
    d.harmless_change(
        "web",
        -180,
        "feature flag web-dark-mode changed: rollout 10% -> 50% (by li.wei)",
        "web-dark-mode",
    )
    return d.build(
        latency_alert("search", "SearchHighLatency", 1.0),
        "resource_exhaustion",
        "search",
        -10800,
        [
            metric_signal("memory_usage_ratio", "search"),
            log_signal('{service="search", level="warn"}', "GC overhead"),
        ],
        [act("restart_service", "search"), act("scale_service", "search")],
        [act("restart_service", "search-index")],
    )


def memory_leak_3(d: Draft) -> Json:
    """recommendations leaks model tensors per request; the logs backend is down."""
    d.service("frontend", "recommendations", "catalog")
    d.service("recommendations", "feature-store")
    for name in ("feature-store", "catalog", "cart"):
        d.service(name)
    for name in ("frontend", "recommendations", "catalog", "cart"):
        d.app(name)
    leak(
        d,
        "recommendations",
        -18000,
        3,
        (-540, -360, -180),
        -2400,
        "model cache holds {n} tensors after GC; resident memory 2.8GB of 3GB",
    )
    d.errors("recommendations", 0.12)
    d.unrelated_deploy("cart")
    d.loud_errors("frontend")
    return d.build(
        error_alert("recommendations", "RecommendationsHighErrorRate"),
        "resource_exhaustion",
        "recommendations",
        -18000,
        [
            metric_signal("memory_usage_ratio", "recommendations"),
            metric_signal("container_restarts_total", "recommendations"),
        ],
        [act("restart_service", "recommendations"), act("scale_service", "recommendations")],
        down="logs",
    )


def memory_leak_4(d: Draft) -> Json:
    """notifications never releases closed websocket sessions."""
    d.service("frontend", "notifications")
    d.service("orders", "notifications")
    d.service("notifications", "events-broker")
    for name in ("events-broker", "accounts"):
        d.service(name)
    for name in ("frontend", "orders", "notifications", "accounts"):
        d.app(name)
    leak(
        d,
        "notifications",
        -9000,
        2,
        (-480, -300, -120),
        -1200,
        "websocket sessions in memory: 48213 (connected clients: 3120); heap 1.8GB of 2GB",
    )
    d.errors("notifications", 0.11)
    d.unrelated_deploy("accounts")
    d.loud_errors("orders")
    d.harmless_deploy("orders", -180)
    return d.build(
        error_alert("notifications", "NotificationsHighErrorRate"),
        "resource_exhaustion",
        "notifications",
        -9000,
        [
            metric_signal("memory_usage_ratio", "notifications"),
            log_signal('{service="notifications", level="error"}', "OOMKilled"),
        ],
        [act("restart_service", "notifications"), act("scale_service", "notifications")],
        [act("restart_service", "events-broker")],
    )


def memory_leak_5(d: Draft) -> Json:
    """api-gateway's retry plugin keeps every request body it buffered."""
    d.service("api-gateway", "cart", "checkout", "catalog")
    d.service("checkout", "payments")
    for name in ("cart", "catalog", "payments"):
        d.service(name)
    for name in ("api-gateway", "cart", "checkout", "catalog", "payments"):
        d.app(name)
    leak(
        d,
        "api-gateway",
        -12600,
        1,
        (-420, -240, -90),
        -1800,
        "retry buffer pool holds {n} request bodies; heap 980MB of 1GB",
    )
    d.errors("api-gateway", 0.1)
    d.unrelated_deploy("payments")
    d.loud_errors("catalog")
    return d.build(
        error_alert("api-gateway", "ApiGatewayHighErrorRate"),
        "resource_exhaustion",
        "api-gateway",
        -12600,
        [
            metric_signal("memory_usage_ratio", "api-gateway"),
            metric_signal("container_restarts_total", "api-gateway"),
            log_signal('{service="api-gateway", level="error"}', "OOMKilled"),
        ],
        [act("restart_service", "api-gateway"), act("scale_service", "api-gateway")],
        [act("rollback_deploy", "cart")],
    )


# --- expired certificate: a TLS certificate runs out and every handshake with it fails ----------


def expiry(d: Draft, server: str, at: int = ERRORS_AT) -> None:
    """The server's certificate counts down to zero days at `at`."""
    d.metric("tls_cert_days_remaining", server, (at + 21600) / 86400, 0, ch(-21600, 0, at + 21600))


def handshakes(d: Draft, client: str, server: str, rate: float, at: int = ERRORS_AT) -> None:
    d.metric("tls_handshake_errors_per_second", client, 0, 0, ch(at, rate, 30), peer=server)


def expired_cert_1(d: Draft) -> Json:
    """payments' TLS certificate expires; checkout's calls to it all fail."""
    d.service("frontend", "checkout")
    d.service("checkout", "payments", "cart")
    d.service("payments", "card-network")
    for name in ("card-network", "cart", "catalog"):
        d.service(name)
    for name in ("frontend", "checkout", "payments", "cart", "catalog"):
        d.app(name)
    expiry(d, "payments")
    handshakes(d, "checkout", "payments", 45)
    d.errors("checkout", 0.34)
    d.errors("frontend", 0.12, ERRORS_AT + 30)
    d.log(
        "checkout",
        "error",
        45,
        [
            "POST https://payments.internal/charge: tls: failed to verify certificate: x509: "
            "certificate has expired or is not yet valid request_id={rid}",
        ],
        start=ERRORS_AT,
    )
    d.unrelated_deploy("catalog")
    d.loud_errors("cart")
    d.harmless_change(
        "checkout",
        -120,
        "feature flag checkout-upsell-banner changed: off -> on (by ana.lopez)",
        "checkout-upsell-banner",
        "after its errors had begun",
    )
    return d.build(
        error_alert("checkout", "CheckoutHighErrorRate"),
        "certificate_expiry",
        "payments",
        ERRORS_AT,
        [
            log_signal('{service="checkout", level="error"}', "certificate has expired"),
            metric_signal("tls_cert_days_remaining", "payments"),
        ],
        [],
        [
            act("restart_service", "payments"),
            act("restart_service", "checkout"),
            act("rollback_deploy", "checkout"),
        ],
    )


def expired_cert_2(d: Draft) -> Json:
    """inventory's mesh certificate expires; orders cannot reach it."""
    d.service("checkout", "orders")
    d.service("orders", "inventory", "orders-db")
    d.service("inventory", "inventory-db")
    for name in ("orders-db", "inventory-db", "accounts"):
        d.service(name)
    for name in ("checkout", "orders", "inventory", "accounts"):
        d.app(name)
    d.db("orders-db")
    d.db("inventory-db")
    expiry(d, "inventory")
    handshakes(d, "orders", "inventory", 30)
    d.errors("orders", 0.27)
    d.log(
        "orders",
        "error",
        36,
        [
            "reserve stock: rpc error: code = Unavailable desc = tls: failed to verify certificate "
            "for inventory.internal: x509: certificate has expired",
        ],
        start=ERRORS_AT,
    )
    d.unrelated_deploy("accounts")
    d.loud_errors("checkout")
    d.harmless_change(
        "orders",
        -120,
        "config reloaded from config-service: orders.invoice_template=v7 (was v6), changed by "
        "ops-bot",
        "orders.invoice_template",
        "after its errors had begun",
    )
    return d.build(
        error_alert("orders", "OrdersHighErrorRate"),
        "certificate_expiry",
        "inventory",
        ERRORS_AT,
        [
            metric_signal("tls_handshake_errors_per_second", "orders", peer="inventory"),
            log_signal('{service="orders", level="error"}', "certificate has expired"),
        ],
        [],
        [act("restart_service", "inventory-db"), act("rollback_deploy", "orders")],
    )


def expired_cert_3(d: Draft) -> Json:
    """carrier-api's public certificate expires; the dependencies backend is down."""
    d.service("checkout", "shipping")
    d.service("shipping", "carrier-api")
    for name in ("carrier-api", "orders", "search"):
        d.service(name)
    for name in ("checkout", "shipping", "orders", "search"):
        d.app(name)
    handshakes(d, "shipping", "carrier-api", 25)
    d.errors("shipping", 0.42)
    d.log(
        "shipping",
        "error",
        30,
        [
            'Post "https://api.carrier-api.example.net/v1/rates": tls: failed to verify '
            "certificate: x509: certificate has expired or is not yet valid",
        ],
        start=ERRORS_AT,
    )
    d.unrelated_deploy("search")
    d.loud_errors("orders")
    return d.build(
        error_alert("shipping", "ShippingHighErrorRate"),
        "certificate_expiry",
        "carrier-api",
        ERRORS_AT,
        [
            metric_signal("tls_handshake_errors_per_second", "shipping", peer="carrier-api"),
            log_signal('{service="shipping", level="error"}', "certificate has expired"),
        ],
        [],
        [act("restart_service", "shipping"), act("rollback_deploy", "shipping")],
        down="dependencies",
    )


def expired_cert_4(d: Draft) -> Json:
    """events-broker's certificate expires; notifications can no longer publish."""
    d.service("orders", "notifications")
    d.service("notifications", "events-broker")
    for name in ("events-broker", "accounts", "catalog"):
        d.service(name)
    for name in ("orders", "notifications", "accounts", "catalog"):
        d.app(name)
    expiry(d, "events-broker")
    handshakes(d, "notifications", "events-broker", 18)
    d.errors("notifications", 0.38)
    d.log(
        "notifications",
        "error",
        30,
        [
            "kafka producer: SSL handshake failed with events-broker:9093: certificate has "
            "expired (SSL certificate verify failed)",
            "POST /send 500 {ms}ms request_id={rid}",
        ],
        start=ERRORS_AT,
    )
    d.unrelated_deploy("catalog")
    d.loud_errors("accounts")
    d.harmless_deploy("orders", -300)
    return d.build(
        error_alert("notifications", "NotificationsHighErrorRate"),
        "certificate_expiry",
        "events-broker",
        ERRORS_AT,
        [
            metric_signal("tls_cert_days_remaining", "events-broker"),
            log_signal('{service="notifications", level="error"}', "certificate has expired"),
        ],
        [],
        [act("restart_service", "events-broker"), act("scale_service", "notifications")],
    )


def expired_cert_5(d: Draft) -> Json:
    """accounts-db's server certificate expires; accounts cannot open new connections."""
    d.service("frontend", "accounts")
    d.service("accounts", "accounts-db")
    for name in ("accounts-db", "orders", "search"):
        d.service(name)
    for name in ("frontend", "accounts", "orders", "search"):
        d.app(name)
    d.db("accounts-db")
    expiry(d, "accounts-db")
    d.pool("accounts", 25, 8, ch(ERRORS_AT, 3, 120))
    d.errors("accounts", 0.23)
    d.log(
        "accounts",
        "error",
        30,
        [
            "could not connect to accounts-db: SSL error: certificate verify failed: certificate "
            "has expired",
            "POST /login 500 {ms}ms request_id={rid}",
        ],
        start=ERRORS_AT,
    )
    d.unrelated_deploy("orders")
    d.loud_errors("search")
    d.harmless_deploy("frontend", -180)
    return d.build(
        error_alert("accounts", "AccountsHighErrorRate"),
        "certificate_expiry",
        "accounts-db",
        ERRORS_AT,
        [
            metric_signal("tls_cert_days_remaining", "accounts-db"),
            log_signal('{service="accounts", level="error"}', "certificate has expired"),
        ],
        [],
        [
            act("restart_service", "accounts-db"),
            act("restart_service", "accounts"),
            act("scale_service", "accounts"),
        ],
        ["accounts' pool shrinks: connections close and none can be opened, so it is not full"],
    )


# --- bad config or feature flag: a runtime change, not a deploy, breaks the service ------------


def config_line(d: Draft, service: str, line: str, at: int) -> None:
    """The flag SDK's or the config loader's one line about the change."""
    d.log(service, "info", 1, [line], start=at, to=at + 60)


def bad_config_1(d: Draft) -> Json:
    """The new-tax-engine flag goes to 100% for checkout and taxes fail for one state."""
    d.service("frontend", "checkout")
    d.service("checkout", "payments", "cart", "pricing")
    for name in ("payments", "cart", "pricing"):
        d.service(name)
    for name in ("frontend", "checkout", "payments", "cart", "pricing"):
        d.app(name)
    line = "feature flag new-tax-engine changed: rollout 5% -> 100% (by maria.g)"
    config_line(d, "checkout", line, -270)
    d.errors("checkout", 0.17)
    d.log(
        "checkout",
        "error",
        40,
        [
            "TaxEngineV2Exception: no nexus configuration for state=WA request_id={rid}",
            "POST /checkout 500 {ms}ms request_id={rid}",
        ],
        start=ERRORS_AT,
    )
    d.unrelated_deploy("pricing")
    d.loud_errors("frontend")
    d.harmless_deploy("payments", -120)
    return d.build(
        error_alert("checkout", "CheckoutHighErrorRate"),
        "config_change",
        "checkout",
        -270,
        [
            log_signal('{service="checkout", level="info"} |= "feature flag"', "new-tax-engine"),
            log_signal('{service="checkout", level="error"}', "TaxEngineV2Exception"),
        ],
        [act("disable_feature_flag", "checkout", flag="new-tax-engine")],
        [act("rollback_deploy", "payments")],
    )


def bad_config_2(d: Draft) -> Json:
    """A config push sets search's backend timeout to 20 ms instead of 2000."""
    d.service("web", "search")
    d.service("search", "search-index", "catalog")
    for name in ("search-index", "catalog", "accounts"):
        d.service(name)
    for name in ("web", "search", "catalog", "accounts"):
        d.app(name)
    d.metric("cpu_usage_ratio", "search-index", 0.36, 0.03)
    d.metric("http_latency_p99_seconds", "search-index", 0.04, 0.004)
    line = (
        "config reloaded from config-service: search.backend.timeout_ms=20 (was 2000), "
        "changed by ops-bot"
    )
    config_line(d, "search", line, -330)
    d.errors("search", 0.41)
    d.log(
        "search",
        "error",
        45,
        [
            "search-index call timed out after 20ms (GET /_search) request_id={rid}",
            "GET /search 500 {ms}ms request_id={rid}",
        ],
        start=ERRORS_AT,
    )
    d.unrelated_deploy("accounts")
    d.loud_errors("web")
    d.harmless_change(
        "search",
        -120,
        "config reloaded from config-service: search.highlight.max_fragments=5 (was 3), changed "
        "by ops-bot",
        "search.highlight.max_fragments",
        "after its errors had begun",
    )
    return d.build(
        error_alert("search", "SearchHighErrorRate"),
        "config_change",
        "search",
        -330,
        [
            log_signal('{service="search", level="info"} |= "config reloaded"', "timeout_ms=20"),
            log_signal('{service="search", level="error"}', "timed out after 20ms"),
        ],
        [],
        [act("restart_service", "search-index"), act("scale_service", "search-index")],
        ["search-index's own latency and CPU stay normal: it answers, search gives up too soon"],
    )


def bad_config_3(d: Draft) -> Json:
    """The dynamic-discounts flag turns on in pricing. As cached quotes expire over 15 minutes,
    more quotes run the discount rules, until pricing's CPU saturates and its latency climbs."""
    d.service("checkout", "pricing")
    d.service("cart", "pricing")
    d.service("pricing", "catalog")
    for name in ("catalog", "orders"):
        d.service(name)
    for name in ("checkout", "cart", "pricing", "catalog", "orders"):
        d.app(name, latency=0.11 if name == "pricing" else 0)
    config_line(
        d, "pricing", "feature flag dynamic-discounts changed: off -> on (by dev.kim)", -1200
    )
    d.change("cpu_usage_ratio", "pricing", ch(-1200, 0.96, 900))
    quote = "quote computed with dynamic-discounts: {n} rules evaluated in {ms}ms request_id={rid}"
    d.log("pricing", "info", 6, [quote], start=-1200)
    d.latency("pricing", 2.7)
    d.latency("checkout", 3.1, LATENCY_AT + 30)
    d.log(
        "pricing",
        "warn",
        20,
        [
            "slow quote: dynamic-discounts evaluated 1840 rules in 2390ms request_id={rid}",
            "slow quote: dynamic-discounts evaluated 2215 rules in 2875ms request_id={rid}",
        ],
        start=LATENCY_AT,
    )
    d.unrelated_deploy("orders")
    d.loud_errors("catalog")
    d.harmless_change(
        "pricing",
        -180,
        "feature flag price-display-rounding changed: off -> on (by ana.lopez)",
        "price-display-rounding",
        "after its latency had already risen",
    )
    return d.build(
        latency_alert("pricing", "PricingHighLatency", 1.0),
        "config_change",
        "pricing",
        -1200,
        [
            log_signal('{service="pricing", level="info"} |= "feature flag"', "dynamic-discounts"),
            log_signal('{service="pricing", level="warn"}', "dynamic-discounts evaluated"),
        ],
        [
            act("disable_feature_flag", "pricing", flag="dynamic-discounts"),
            act("scale_service", "pricing"),
        ],
        [act("rollback_deploy", "catalog")],
        ["pricing's CPU saturates, but only because of the work the flag turned on"],
    )


def bad_config_4(d: Draft) -> Json:
    """A route config sends a fifth of cart traffic to cart-legacy, which is gone."""
    d.service("api-gateway", "cart", "cart-legacy", "checkout")
    d.service("checkout", "cart")
    for name in ("cart", "cart-legacy", "catalog"):
        d.service(name)
    for name in ("api-gateway", "cart", "checkout", "catalog"):
        d.app(name)
    line = "routes reloaded by config-sync: /api/cart/* -> cart (80%), cart-legacy (20%)"
    config_line(d, "api-gateway", line, -330)
    d.errors("api-gateway", 0.07)
    d.log(
        "api-gateway",
        "error",
        40,
        ["upstream cart-legacy: no healthy upstream (GET /api/cart/{n}), 503 request_id={rid}"],
        start=ERRORS_AT,
    )
    d.unrelated_deploy("catalog")
    d.loud_errors("checkout")
    d.harmless_deploy("cart", -120)
    return d.build(
        error_alert("api-gateway", "ApiGatewayHighErrorRate"),
        "config_change",
        "api-gateway",
        -330,
        [
            log_signal('{service="api-gateway", level="info"} |= "routes reloaded"', "cart-legacy"),
            log_signal('{service="api-gateway", level="error"}', "upstream cart-legacy"),
        ],
        [],
        [act("restart_service", "cart"), act("rollback_deploy", "cart")],
        ["cart itself is healthy: the 503s come from requests routed to cart-legacy"],
    )


def bad_config_5(d: Draft) -> Json:
    """The 3ds-v2-required flag turns on in payments, where most cards cannot complete 3DS v2.
    At 10% of charges it fails too few to alert for 24 minutes; then it goes to all of them."""
    d.service("checkout", "payments")
    d.service("payments", "card-network", "payments-db")
    d.service("frontend", "checkout")
    for name in ("card-network", "payments-db", "accounts"):
        d.service(name)
    for name in ("frontend", "checkout", "payments", "accounts"):
        d.app(name)
    d.db("payments-db")
    flag = "feature flag 3ds-v2-required changed:"
    config_line(d, "payments", f"{flag} off -> on for 10% of charges (by li.wei)", -1800)
    config_line(d, "payments", f"{flag} 10% -> 100% of charges (by li.wei)", -360)
    d.change("http_error_ratio", "payments", ch(-1800, 0.031, 60))
    d.errors("payments", 0.29)
    d.errors("checkout", 0.13, ERRORS_AT + 30)
    failures = [
        "authorization failed: 3DS v2 challenge required but the card's issuer supports only "
        "3DS v1 request_id={rid}",
        "POST /charge 500 {ms}ms request_id={rid}",
    ]
    d.log("payments", "error", 4, failures, start=-1800, to=ERRORS_AT)
    d.log("payments", "error", 36, failures, start=ERRORS_AT)
    d.unrelated_deploy("accounts")
    d.loud_errors("frontend")
    return d.build(
        error_alert("payments", "PaymentsHighErrorRate"),
        "config_change",
        "payments",
        -1800,
        [
            log_signal('{service="payments", level="info"} |= "feature flag"', "3ds-v2-required"),
            log_signal('{service="payments", level="error"}', "3DS v2 challenge required"),
        ],
        [act("disable_feature_flag", "payments", flag="3ds-v2-required")],
        [act("restart_service", "payments-db")],
    )


# --- CPU noisy neighbor: a batch job on the same node starves the service of CPU -----------------


def neighbor(
    d: Draft, victim: str, job: str, node: str, start: int, lines: list[str]
) -> list[Json]:
    """The job ramps up from `start` on the victim's node until the node saturates; the victim,
    starved, gets less CPU than before and slows down. Returns the signals that name the job:
    its CPU on that node and its own log."""
    busy = ch(start, 0.96, LATENCY_AT - start)
    d.metric("cpu_usage_ratio", job, 0, 0.01, busy, node=node)
    full = ch(start, 0.99, LATENCY_AT - start)
    d.metric("node_cpu_usage_ratio", victim, 0.46, 0.03, full, node=node)
    d.change("cpu_usage_ratio", victim, ch(LATENCY_AT, 0.15, 60))
    d.log(job, "info", 2, lines, start=start)
    d.latency(victim, 3.2)
    return [
        metric_signal("cpu_usage_ratio", job, node=node),
        log_signal(f'{{service="{job}"}}', f"on {node}"),
    ]


def noisy_neighbor_1(d: Draft) -> Json:
    """The nightly reindex job lands on search's node and takes its CPU."""
    d.service("web", "search")
    d.service("search", "search-index")
    for name in ("search-index", "nightly-reindex", "catalog"):
        d.service(name)
    for name in ("web", "search", "catalog"):
        d.app(name, latency=0.18 if name == "search" else 0)
    d.metric("cpu_usage_ratio", "search-index", 0.4, 0.03)
    lines = [f"reindex job: shard {n} of 48 indexed on node-7" for n in (17, 18, 19)]
    signals = neighbor(d, "search", "nightly-reindex", "node-7", -720, lines)
    d.unrelated_deploy("catalog")
    d.loud_errors("web")
    d.harmless_change(
        "search",
        -180,
        "feature flag search-results-badges changed: off -> on (by dev.kim)",
        "search-results-badges",
        "after its latency had already risen",
    )
    return d.build(
        latency_alert("search", "SearchHighLatency", 1.0),
        "infrastructure",
        "nightly-reindex",
        -720,
        signals,
        [act("scale_service", "search"), act("scale_service", "nightly-reindex", replicas="0")],
        [act("rollback_deploy", "search"), act("restart_service", "search-index")],
    )


def noisy_neighbor_2(d: Draft) -> Json:
    """An ml-trainer job lands on checkout's node."""
    d.service("frontend", "checkout")
    d.service("checkout", "payments", "cart")
    for name in ("payments", "cart", "ml-trainer"):
        d.service(name)
    for name in ("frontend", "checkout", "payments", "cart"):
        d.app(name, latency=0.25 if name == "checkout" else 0)
    lines = [f"epoch {n}: training batch on node-3, 16 worker threads" for n in (3, 4)]
    signals = neighbor(d, "checkout", "ml-trainer", "node-3", -900, lines)
    d.unrelated_deploy("cart")
    d.loud_errors("frontend")
    d.harmless_deploy("payments", -180)
    return d.build(
        latency_alert("checkout", "CheckoutHighLatency", 1.5),
        "infrastructure",
        "ml-trainer",
        -900,
        signals,
        [act("scale_service", "checkout"), act("scale_service", "ml-trainer", replicas="0")],
        [act("rollback_deploy", "checkout"), act("restart_service", "payments")],
    )


def noisy_neighbor_3(d: Draft) -> Json:
    """log-compactor runs on orders' node and saturates it."""
    d.service("checkout", "orders")
    d.service("orders", "orders-db")
    for name in ("orders-db", "log-compactor", "notifications"):
        d.service(name)
    for name in ("checkout", "orders", "notifications"):
        d.app(name, latency=0.2 if name == "orders" else 0)
    d.db("orders-db")
    lines = [f"compacting segment 00{n} on node-5 (zstd level 19)" for n in (41, 42, 43)]
    signals = neighbor(d, "orders", "log-compactor", "node-5", -600, lines)
    d.unrelated_deploy("notifications")
    d.loud_errors("checkout")
    d.harmless_deploy("checkout", -240)
    return d.build(
        latency_alert("orders", "OrdersHighLatency", 1.5),
        "infrastructure",
        "log-compactor",
        -600,
        signals,
        [act("scale_service", "orders"), act("scale_service", "log-compactor", replicas="0")],
        [act("restart_service", "orders-db")],
    )


def noisy_neighbor_4(d: Draft) -> Json:
    """report-builder's monthly run lands on payments' node."""
    d.service("checkout", "payments")
    d.service("payments", "card-network", "payments-db")
    for name in ("card-network", "payments-db", "report-builder", "accounts"):
        d.service(name)
    for name in ("checkout", "payments", "accounts"):
        d.app(name, latency=0.3 if name == "payments" else 0)
    d.db("payments-db")
    d.metric("upstream_latency_p99_seconds", "payments", 0.3, 0.03, peer="card-network")
    lines = ["monthly report: rendering merchant statements, batch 12 of 40, on node-2"]
    signals = neighbor(d, "payments", "report-builder", "node-2", -840, lines)
    d.unrelated_deploy("accounts")
    d.loud_errors("checkout")
    return d.build(
        latency_alert("payments", "PaymentsHighLatency", 1.5),
        "infrastructure",
        "report-builder",
        -840,
        signals,
        [act("scale_service", "payments"), act("scale_service", "report-builder", replicas="0")],
        [act("restart_service", "payments-db"), act("rollback_deploy", "payments")],
        ["card-network's latency as payments sees it stays flat"],
    )


def noisy_neighbor_5(d: Draft) -> Json:
    """image-resizer's backfill lands on catalog's node."""
    d.service("frontend", "catalog")
    d.service("search", "catalog")
    d.service("catalog", "catalog-db")
    for name in ("catalog-db", "image-resizer", "cart"):
        d.service(name)
    for name in ("frontend", "search", "catalog", "cart"):
        d.app(name, latency=0.15 if name == "catalog" else 0)
    d.db("catalog-db")
    lines = ["backfill: resizing product images (batch {n}) on node-4"]
    signals = neighbor(d, "catalog", "image-resizer", "node-4", -780, lines)
    d.unrelated_deploy("cart")
    d.loud_errors("search")
    d.harmless_change(
        "search",
        -360,
        "feature flag search-suggest-v2 changed: rollout 25% -> 50% (by dev.kim)",
        "search-suggest-v2",
    )
    return d.build(
        latency_alert("catalog", "CatalogHighLatency", 1.0),
        "infrastructure",
        "image-resizer",
        -780,
        signals,
        [act("scale_service", "catalog"), act("scale_service", "image-resizer", replicas="0")],
        [act("restart_service", "catalog-db"), act("rollback_deploy", "catalog")],
    )


# --- DNS or network partition: lookups or connections fail between healthy services --------------


def dns_partition_1(d: Draft) -> Json:
    """coredns answers SERVFAIL for external names while its upstream resolver is out."""
    d.service("checkout", "payments")
    d.service("payments", "card-network", "coredns")
    d.service("shipping", "shipping-api", "coredns")
    for name in ("card-network", "shipping-api", "coredns"):
        d.service(name)
    for name in ("checkout", "payments", "shipping"):
        d.app(name)
    d.metric("dns_servfail_ratio", "coredns", 0.001, 0.0005, ch(-330, 0.71, 60))
    d.metric("cpu_usage_ratio", "coredns", 0.2, 0.02)
    d.metric("dns_lookup_errors_per_second", "payments", 0, 0.1, ch(-300, 31, 60))
    d.metric("dns_lookup_errors_per_second", "shipping", 0, 0.1, ch(-300, 12, 60))
    d.errors("payments", 0.31)
    d.errors("shipping", 0.09, ERRORS_AT + 30)
    d.log(
        "payments",
        "error",
        36,
        [
            "dial tcp: lookup api.card-network.example on 10.96.0.10:53: server misbehaving "
            "request_id={rid}",
        ],
        start=ERRORS_AT,
    )
    d.log(
        "coredns",
        "error",
        20,
        [
            "[ERROR] plugin/errors: 2 api.card-network.example. A: read udp 10.4.0.12:{n}->"
            "169.254.169.253:53: i/o timeout",
        ],
        start=-330,
    )
    d.unrelated_deploy("checkout")
    d.loud_errors("shipping")
    d.harmless_deploy("payments", -120, "after its lookups had begun to fail")
    d.harmless_change(
        "checkout",
        -360,
        "feature flag one-click-checkout changed: rollout 10% -> 25% (by maria.g)",
        "one-click-checkout",
    )
    return d.build(
        error_alert("payments", "PaymentsHighErrorRate"),
        "infrastructure",
        "coredns",
        -330,
        [
            metric_signal("dns_servfail_ratio", "coredns"),
            log_signal('{service="payments", level="error"}', "server misbehaving"),
        ],
        [],
        [
            act("rollback_deploy", "payments"),
            act("scale_service", "payments"),
            act("restart_service", "coredns"),
        ],
        ["shipping fails too, at the same moment, for the same kind of lookup"],
    )


def dns_partition_2(d: Draft) -> Json:
    """coredns runs out of CPU under a lookup storm and lookups time out."""
    d.service("checkout", "orders")
    d.service("orders", "orders-db", "coredns")
    d.service("inventory", "coredns")
    for name in ("orders-db", "coredns", "catalog"):
        d.service(name)
    for name in ("checkout", "orders", "inventory", "catalog"):
        d.app(name)
    d.db("orders-db")
    d.metric("cpu_usage_ratio", "coredns", 0.3, 0.02, ch(-420, 0.99, 60))
    d.metric("cpu_throttled_ratio", "coredns", 0.01, 0.01, ch(-420, 0.74, 60))
    d.metric("dns_servfail_ratio", "coredns", 0.001, 0.0005)
    d.metric("dns_lookup_errors_per_second", "orders", 0, 0.1, ch(-300, 24, 60))
    d.metric("dns_lookup_errors_per_second", "inventory", 0, 0.1, ch(-300, 15, 60))
    d.errors("orders", 0.18)
    d.log(
        "orders",
        "error",
        30,
        ["dial tcp: lookup orders-db.internal on 10.96.0.10:53: read udp: i/o timeout"],
        start=ERRORS_AT,
    )
    d.log(
        "coredns",
        "warn",
        6,
        ["[WARNING] plugin/forward: max concurrent queries reached (1000), dropping query"],
        start=-420,
    )
    d.unrelated_deploy("catalog")
    d.loud_errors("checkout")
    return d.build(
        error_alert("orders", "OrdersHighErrorRate"),
        "infrastructure",
        "coredns",
        -420,
        [
            metric_signal("cpu_throttled_ratio", "coredns"),
            log_signal('{service="coredns", level="warn"}', "max concurrent queries reached"),
        ],
        [act("scale_service", "coredns")],
        [
            act("restart_service", "orders-db"),
            act("rollback_deploy", "orders"),
            act("restart_service", "coredns"),
        ],
        ["orders-db is healthy: its connections and lock waits never move"],
        # Saturated coredns reads as infrastructure and as its own resource exhaustion.
        alternatives=[("resource_exhaustion", "coredns")],
    )


def dns_partition_3(d: Draft) -> Json:
    """coredns pods crash on a forwarding loop; the metrics backend is down."""
    d.service("frontend", "accounts", "catalog")
    d.service("accounts", "accounts-db", "coredns")
    d.service("catalog", "coredns")
    for name in ("accounts-db", "coredns", "cart"):
        d.service(name)
    for name in ("frontend", "accounts", "catalog", "cart"):
        d.app(name)
    d.db("accounts-db")
    d.metric("container_restarts_total", "coredns", 0, 0, ch(-420, 1), ch(-300, 2), ch(-180, 3))
    d.errors("accounts", 0.22)
    d.log(
        "coredns",
        "error",
        1,
        ['[FATAL] plugin/loop: Loop (127.0.0.1:43920 -> :53) detected for zone "."'],
        start=-420,
    )
    d.log(
        "accounts",
        "error",
        30,
        [
            "dial tcp: lookup accounts-db.internal on 10.96.0.10:53: read udp "
            "10.4.0.21:41832->10.96.0.10:53: i/o timeout"
        ],
        start=ERRORS_AT,
    )
    d.unrelated_deploy("cart")
    d.loud_errors("frontend")
    return d.build(
        error_alert("accounts", "AccountsHighErrorRate"),
        "infrastructure",
        "coredns",
        -420,
        [
            log_signal('{service="coredns"}', "plugin/loop"),
            log_signal('{service="accounts", level="error"}', "lookup accounts-db.internal"),
        ],
        [],
        [act("restart_service", "accounts-db"), act("rollback_deploy", "accounts")],
        down="metrics",
    )


def dns_partition_4(d: Draft) -> Json:
    """The egress gateway loses its route out; every external call fails to connect."""
    d.service("checkout", "payments", "shipping")
    d.service("payments", "card-network", "egress-gateway")
    d.service("shipping", "shipping-api", "egress-gateway")
    for name in ("card-network", "shipping-api", "egress-gateway"):
        d.service(name)
    for name in ("checkout", "payments", "shipping"):
        d.app(name)
    errors = ch(-330, 22, 60)
    d.metric("tcp_connect_errors_per_second", "shipping", 0, 0.1, errors, peer="shipping-api")
    errors = ch(-330, 28, 60)
    d.metric("tcp_connect_errors_per_second", "payments", 0, 0.1, errors, peer="card-network")
    d.errors("shipping", 0.38)
    d.errors("payments", 0.27, ERRORS_AT + 30)
    d.log(
        "egress-gateway",
        "error",
        24,
        [
            "upstream connect error or disconnect/reset before headers. reset reason: connection "
            "failure, cluster egress_external"
        ],
        start=-330,
    )
    d.log(
        "shipping",
        "error",
        30,
        [
            "POST https://api.shipping-api.example/v1/rates via egress-gateway: 503 upstream "
            "connect error or disconnect/reset before headers request_id={rid}"
        ],
        start=ERRORS_AT,
    )
    d.unrelated_deploy("checkout")
    d.loud_errors("payments")
    d.harmless_change(
        "checkout",
        -120,
        "feature flag saved-addresses changed: off -> on (by li.wei)",
        "saved-addresses",
    )
    return d.build(
        error_alert("shipping", "ShippingHighErrorRate"),
        "infrastructure",
        "egress-gateway",
        -330,
        [
            metric_signal("tcp_connect_errors_per_second", "payments", peer="card-network"),
            log_signal('{service="egress-gateway"}', "cluster egress_external"),
        ],
        [],
        [act("rollback_deploy", "shipping"), act("scale_service", "shipping")],
        ["payments fails to connect out at the same moment, to a different provider"],
    )


def dns_partition_5(d: Draft) -> Json:
    """The NAT gateway runs out of source ports and new outbound connections fail."""
    d.service("frontend", "accounts", "checkout")
    d.service("accounts", "geo-api", "nat-gateway")
    d.service("checkout", "tax-service", "nat-gateway")
    for name in ("geo-api", "tax-service", "nat-gateway", "catalog"):
        d.service(name)
    for name in ("frontend", "accounts", "checkout", "catalog"):
        d.app(name)
    errors = ch(-360, 19, 60)
    d.metric("tcp_connect_errors_per_second", "accounts", 0, 0.1, errors, peer="geo-api")
    errors = ch(-360, 26, 60)
    d.metric("tcp_connect_errors_per_second", "checkout", 0, 0.1, errors, peer="tax-service")
    d.errors("accounts", 0.21)
    d.errors("checkout", 0.11)
    d.log(
        "nat-gateway",
        "error",
        12,
        ["SNAT port allocation failed for 10.0.3.15: all 64512 source ports in use"],
        start=-360,
    )
    d.log(
        "accounts",
        "error",
        30,
        ['Post "https://geo-api.example.com/v1/validate": dial tcp 34.120.4.15:443: i/o timeout'],
        start=ERRORS_AT,
    )
    d.unrelated_deploy("catalog")
    d.loud_errors("frontend")
    d.harmless_deploy("frontend", -240)
    return d.build(
        error_alert("accounts", "AccountsHighErrorRate"),
        "infrastructure",
        "nat-gateway",
        -360,
        [
            metric_signal("tcp_connect_errors_per_second", "checkout", peer="tax-service"),
            log_signal('{service="nat-gateway"}', "SNAT port allocation failed"),
        ],
        [],
        [act("rollback_deploy", "accounts"), act("restart_service", "accounts")],
        [
            "checkout's calls to tax-service fail to connect too: the shared NAT gateway, not "
            "geo-api"
        ],
    )


Variant = Callable[[Draft], Json]
# family, variant, split, in the ci subset, title
SET: list[tuple[str, Variant, str, bool, str]] = [
    ("bad-deploy", bad_deploy_1, "dev", True, "checkout v1.8.0 throws on bundle carts"),
    ("bad-deploy", bad_deploy_2, "dev", False, "search v5.2.0 rejects queries with '&'"),
    ("bad-deploy", bad_deploy_3, "test", False, "payments v3.0.0 serializes charges"),
    ("bad-deploy", bad_deploy_4, "test", False, "pricing v1.12.0 breaks checkout's totals"),
    ("bad-deploy", bad_deploy_5, "test", False, "cart v4.6.0 calls a Redis command too new"),
    ("db-pool-exhaustion", db_pool_1, "dev", True, "a held lock fills the orders pool"),
    ("db-pool-exhaustion", db_pool_2, "test", False, "VACUUM FULL locks ledger_entries"),
    ("db-pool-exhaustion", db_pool_3, "dev", False, "a bad plan on inventory-db"),
    ("db-pool-exhaustion", db_pool_4, "test", False, "a cleanup job locks accounts-db"),
    ("db-pool-exhaustion", db_pool_5, "test", False, "an ad hoc query saturates catalog-db"),
    ("slow-dependency", slow_dependency_1, "dev", True, "card-network slows payments"),
    ("slow-dependency", slow_dependency_2, "dev", False, "shipping-api slows shipping"),
    ("slow-dependency", slow_dependency_3, "test", False, "feature-store slows recommendations"),
    ("slow-dependency", slow_dependency_4, "test", False, "tax-service slows checkout"),
    ("slow-dependency", slow_dependency_5, "test", False, "geo-api answers 503"),
    ("memory-leak", memory_leak_1, "dev", True, "orders' export cache never shrinks"),
    ("memory-leak", memory_leak_2, "test", False, "search's result cache never evicts"),
    ("memory-leak", memory_leak_3, "dev", False, "recommendations leaks model tensors"),
    ("memory-leak", memory_leak_4, "test", False, "notifications keeps closed sessions"),
    ("memory-leak", memory_leak_5, "test", False, "api-gateway keeps buffered bodies"),
    ("expired-cert", expired_cert_1, "dev", True, "payments' certificate expires"),
    ("expired-cert", expired_cert_2, "test", False, "inventory's mesh certificate expires"),
    ("expired-cert", expired_cert_3, "test", False, "carrier-api's certificate expires"),
    ("expired-cert", expired_cert_4, "dev", False, "events-broker's certificate expires"),
    ("expired-cert", expired_cert_5, "test", False, "accounts-db's certificate expires"),
    ("bad-config", bad_config_1, "dev", True, "new-tax-engine goes to 100%"),
    ("bad-config", bad_config_2, "dev", False, "search's backend timeout becomes 20 ms"),
    ("bad-config", bad_config_3, "test", False, "dynamic-discounts saturates pricing"),
    ("bad-config", bad_config_4, "test", False, "a route sends cart traffic to cart-legacy"),
    ("bad-config", bad_config_5, "test", False, "3ds-v2-required turns on"),
    ("noisy-neighbor", noisy_neighbor_1, "dev", True, "nightly-reindex starves search"),
    ("noisy-neighbor", noisy_neighbor_2, "test", False, "ml-trainer starves checkout"),
    ("noisy-neighbor", noisy_neighbor_3, "dev", False, "log-compactor starves orders"),
    ("noisy-neighbor", noisy_neighbor_4, "test", False, "report-builder starves payments"),
    ("noisy-neighbor", noisy_neighbor_5, "test", False, "image-resizer starves catalog"),
    ("dns-partition", dns_partition_1, "dev", True, "coredns answers SERVFAIL"),
    ("dns-partition", dns_partition_2, "test", False, "coredns runs out of CPU"),
    ("dns-partition", dns_partition_3, "test", False, "coredns crashes on a loop"),
    ("dns-partition", dns_partition_4, "test", False, "egress-gateway loses its route out"),
    ("dns-partition", dns_partition_5, "test", False, "nat-gateway runs out of ports"),
]


def generate(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    scenarios: Json = {}
    numbers: dict[str, int] = {}
    for family, variant, split, ci, title in SET:
        numbers[family] = numbers.get(family, 0) + 1
        scenario_id = f"{family}-{numbers[family]}"
        # Through JSON, so no two parts share an object and the YAML has no anchors.
        scenario = json.loads(json.dumps(variant(Draft(scenario_id, title))))
        raw = yaml.safe_dump(scenario, sort_keys=False, allow_unicode=True, width=100).encode()
        (out / f"{scenario_id}.yaml").write_bytes(raw)
        scenarios[scenario_id] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "family": family,
            "split": split,
            "ci": ci,
            "down": scenario.get("down"),
        }
    manifest = {"set": "v1", "generator_seed": SEED, "scenarios": scenarios}
    (out / "manifest.json").write_bytes((json.dumps(manifest, indent=2) + "\n").encode())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="evals/scenarios/v1/generate.py")
    parser.add_argument("--out", type=Path, default=HERE)
    generate(parser.parse_args().out)
