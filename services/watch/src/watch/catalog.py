"""What watch may read of each app: its services, containers, dependencies and log signals.

Committed in TC and holding no secret: URLs, tokens and repo paths come from the environment.
Taken read-only from each app's compose files, logging setup and alert rules on 2026-10-25.
"""

from dataclasses import dataclass
from typing import Literal

AppName = Literal["freight-audit", "filing-lens"]
Stack = Literal["prod", "dev"]
STACKS: tuple[Stack, ...] = ("prod", "dev")
# A database, queue, cache, storage or proxy is never restarted (P2.3); apps and workers may be.
Kind = Literal["app", "worker", "database", "queue", "cache", "storage", "proxy"]
Severity = Literal["critical", "warning", "info"]


@dataclass(frozen=True)
class Service:
    name: str
    kind: Kind
    depends_on: tuple[str, ...] = ()
    # The stacks that run it as a container; FreightAudit's dev api and worker run on the host.
    stacks: tuple[Stack, ...] = STACKS
    # A missing container is normal: FilingLens's ingest workers run only for an ingest.
    optional: bool = False


@dataclass(frozen=True)
class Signal:
    """A log line that is an alert by itself: its JSON `field` equals `value`."""

    alertname: str
    field: str
    value: str
    severity: Severity


@dataclass(frozen=True)
class Rule:
    """One of the app's own Prometheus rules: the service it is about and its severity label."""

    service: str
    severity: Severity


@dataclass(frozen=True)
class Catalog:
    app: AppName
    # The compose project of each stack; `docker ps` reads only containers labelled with it.
    projects: dict[Stack, str]
    services: tuple[Service, ...]
    # The JSON log fields the scrubber keeps, dotted when nested; every other field is dropped.
    log_fields: frozenset[str]
    level_field: str
    error_levels: frozenset[str | int]
    signals: tuple[Signal, ...]
    # The app's own Prometheus rules (dev stacks only), by alertname.
    rules: dict[str, Rule]
    # Instant queries for the snapshot while that Prometheus is up.
    queries: tuple[str, ...]

    def service(self, name: str) -> Service:
        for service in self.services:
            if service.name == name:
                return service
        raise KeyError(f"{self.app} has no service {name}")

    def container(self, service: Service, stack: Stack) -> str | None:
        """Compose's name for the service's one container, or None where it runs no container."""
        return f"{self.projects[stack]}-{service.name}-1" if stack in service.stacks else None


_FA_STORES = ("postgres", "redis", "minio")
_FL_STORES = ("postgres", "redis", "rabbitmq")

CATALOGS: dict[AppName, Catalog] = {
    "freight-audit": Catalog(
        app="freight-audit",
        projects={"prod": "freightaudit-prod", "dev": "freightaudit"},
        services=(
            Service("api", "app", _FA_STORES, stacks=("prod",)),
            Service("worker", "worker", (*_FA_STORES, "egress-proxy"), stacks=("prod",)),
            Service("postgres", "database"),
            # BullMQ's queues live in Redis.
            Service("redis", "queue"),
            Service("minio", "storage"),
            Service("egress-proxy", "proxy", stacks=("prod",)),
            Service("tailscale", "proxy", ("api", "egress-proxy"), stacks=("prod",)),
        ),
        # pino lines: a numeric level, epoch milliseconds, and its request, response and error
        # serializers. Headers, hostnames and addresses are left out, and so is err.stack.
        log_fields=frozenset(
            {
                "level",
                "time",
                "msg",
                "alert",
                "reason",
                "status",
                "check",
                "jobId",
                "reqId",
                "signal",
                "req.method",
                "req.url",
                "res.statusCode",
                "responseTime",
                "err.type",
                "err.message",
                "err.code",
            }
        ),
        level_field="level",
        error_levels=frozenset({50, 60}),
        signals=(
            Signal("ExtractPaused", "alert", "extract_paused", "critical"),
            Signal("ClaudeSwitchedOff", "alert", "claude_switched_off", "warning"),
            Signal("ReadinessCheckFailed", "msg", "readiness check failed", "warning"),
        ),
        rules={
            "DeadLettersWaiting": Rule("worker", "warning"),
            "ExtractWaitHigh": Rule("worker", "warning"),
            "CostPerInvoiceHigh": Rule("worker", "warning"),
            "ExtractCircuitOpen": Rule("worker", "critical"),
        },
        queries=(
            "max by (queue) (freightaudit_queue_dead_letters)",
            "max(freightaudit_extract_paused)",
        ),
    ),
    "filing-lens": Catalog(
        app="filing-lens",
        projects={"prod": "filinglens-prod", "dev": "filinglens"},
        services=(
            Service("api", "app", _FL_STORES),
            Service("ingest", "worker", _FL_STORES, optional=True),
            Service("embed", "worker", _FL_STORES, optional=True),
            Service("postgres", "database"),
            Service("redis", "cache"),
            Service("rabbitmq", "queue"),
            Service("tailscale", "proxy", ("api",), stacks=("prod",)),
        ),
        # structlog lines: `event` is the message, `exception` the formatted traceback. Request
        # text, documents and filing contents are left out.
        log_fields=frozenset(
            {
                "timestamp",
                "level",
                "event",
                "logger",
                "service",
                "request_id",
                "trace_id",
                "check",
                "error",
                "exception",
                "reason",
                "model",
                "credits_expire",
                "queue",
                "task",
                "task_id",
                "filing_id",
                "code",
                "count",
                "timeout_s",
            }
        ),
        level_field="level",
        error_levels=frozenset({"error", "critical"}),
        signals=(
            Signal(
                "ClaudeSwitchedOff",
                "event",
                "claude is off for the rest of the credit period",
                "warning",
            ),
            Signal("ClaudeKeyRejected", "event", "claude rejected the key or model", "warning"),
            Signal("ModelProviderUnavailable", "event", "model provider unavailable", "warning"),
            Signal("ReadinessCheckFailed", "event", "readiness check failed", "warning"),
            Signal("IngestTaskFailed", "event", "task failed for good", "warning"),
        ),
        # MonitoringTargetDown and TelemetryMissing are about the monitoring stack itself.
        rules={
            "ApiNotReady": Rule("api", "critical"),
            "WorkerNotConsuming": Rule("ingest", "critical"),
            "SecRefused": Rule("ingest", "critical"),
            "AnswersFailing": Rule("api", "critical"),
            "AiBudgetExhausted": Rule("api", "critical"),
            "AiBudgetNearlyUsed": Rule("api", "warning"),
            "LlmProviderErrors": Rule("api", "warning"),
            "ClaudeKeyOrModelRejected": Rule("api", "critical"),
            "CitationValidationDegraded": Rule("api", "warning"),
            "SearchDegraded": Rule("api", "warning"),
            "DeadLetters": Rule("ingest", "warning"),
            "IngestStalled": Rule("ingest", "warning"),
        },
        queries=(
            "rabbitmq_detailed_queue_messages_ready",
            "max by (kind) (filinglens_claude_off)",
            "up",
        ),
    ),
}
