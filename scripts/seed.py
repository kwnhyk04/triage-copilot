"""Seeds both schemas with the same fake history on every run (fixed seed).

    uv run python scripts/seed.py [--reset] [--large [STEPS]]

5 tenants, 50 services with a dependency graph, 10,000 alerts in their groups, and 2,000
investigations with steps (v1 events before CUTOVER, v2 after), tool calls, reports and decided
actions. Each schema is written by its own role. --large then adds investigations to the agent
schema only until it holds STEPS steps (2,000,000 by default), to rehearse the partitioning.
"""

import argparse
import hashlib
import json
import logging
import random
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, LiteralString, cast
from uuid import UUID

import psycopg
from faker import Faker
from sqlalchemy import Connection, create_engine, text

from agent.partitions import ensure_partitions
from agent.steps import StepKind, StepRecordedV1, StepRecordedV2, TokenUsage
from py_config import ConfigError, EnvSettings, load_settings
from py_telemetry import configure_logging

log = logging.getLogger("seed")

SEED = 2026
END = datetime(2026, 10, 1, tzinfo=UTC)
START = END - timedelta(days=365)
# Investigations created from here on stored v2 step events, earlier ones v1.
CUTOVER = datetime(2026, 6, 1, tzinfo=UTC)
TENANTS = 5
ALERTS = 10_000
INVESTIGATIONS = 2_000
LARGE_STEPS = 2_000_000
# Callers first: a service depends only on services after it, so the graph has no cycles.
SERVICES = [
    "frontend",
    "checkout",
    "cart",
    "search",
    "catalog",
    "payments",
    "inventory",
    "shipping",
    "notifications",
    "auth",
]
# Alert name, severity, and the root cause class most often behind it.
RULES = [
    ("HighErrorRate", "critical", "bad_deploy"),
    ("HighLatency", "warning", "dependency_failure"),
    ("PodCrashLooping", "critical", "config_change"),
    ("CPUThrottling", "warning", "resource_exhaustion"),
    ("TrafficSpike", "info", "traffic_surge"),
    ("DBConnectionsSaturated", "critical", "database"),
    ("NodeNotReady", "critical", "infrastructure"),
    ("CertificateExpiring", "warning", "certificate_expiry"),
]
CAUSES = [cause for _, _, cause in RULES]
TOOLS = ["query_metrics", "search_logs", "get_deploys", "get_dependencies", "search_runbooks"]
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# COPY statements in foreign key order; rows are tuples in the listed column order.
GATEWAY_COPY: dict[str, LiteralString] = {
    "tenants": "COPY gateway.tenants (id, name, created_at) FROM STDIN",
    "services": "COPY gateway.services (id, tenant_id, name, created_at) FROM STDIN",
    "service_dependencies": "COPY gateway.service_dependencies (service_id, depends_on_id)"
    " FROM STDIN",
    "alert_groups": "COPY gateway.alert_groups (id, tenant_id, group_key, window_start, severity,"
    " status, investigation_id, created_at, updated_at) FROM STDIN",
    "alerts": "COPY gateway.alerts (id, tenant_id, group_id, fingerprint, labels, starts_at,"
    " status, created_at) FROM STDIN",
    "proposed_actions": "COPY gateway.proposed_actions (id, tenant_id, investigation_id, kind,"
    " target, parameters, rationale, proposed_at, created_at) FROM STDIN",
    "approvals": "COPY gateway.approvals (action_id, approver, decision, reason, decided_at)"
    " FROM STDIN",
}
AGENT_COPY: dict[str, LiteralString] = {
    "investigations": "COPY agent.investigations (id, status, budget_tokens_used, step_count,"
    " final_report, created_at, updated_at) FROM STDIN",
    "investigation_steps": "COPY agent.investigation_steps (investigation_id, seq, created_at,"
    " schema_version, event) FROM STDIN",
    "tool_calls": "COPY agent.tool_calls (id, investigation_id, name, args, result_ref,"
    " latency_ms, error, created_at) FROM STDIN",
    "proposed_actions": "COPY agent.proposed_actions (id, investigation_id, idempotency_key, kind,"
    " target, params, rationale, status, created_at) FROM STDIN",
}

Row = tuple[object, ...]


class SeedSettings(EnvSettings):
    gateway_database_url: str
    agent_database_url: str


@dataclass(frozen=True)
class Group:
    id: UUID
    tenant_id: UUID
    service: str
    alertname: str
    severity: str
    cause: str
    window_start: datetime


@dataclass(frozen=True)
class Step:
    kind: StepKind
    summary: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    output: str | None = None
    latency_ms: int | None = None


class History:
    """Generates every row up front, from seeded random sources, before touching a database."""

    def __init__(self) -> None:
        self.rng = random.Random(SEED)  # noqa: S311 - fake data, not secrets
        self.fake = Faker()
        self.fake.seed_instance(SEED)
        self.gateway: dict[str, list[Row]] = {table: [] for table in GATEWAY_COPY}
        self.agent: dict[str, list[Row]] = {table: [] for table in AGENT_COPY}
        groups = self._alerts(self._catalog())
        investigated = {group.id for group in self.rng.sample(groups, INVESTIGATIONS)}
        for group in groups:
            self._group(group, investigated=group.id in investigated)

    def uuid(self) -> UUID:
        return UUID(int=self.rng.getrandbits(128), version=4)

    def _catalog(self) -> list[UUID]:
        tenants: list[UUID] = []
        for _ in range(TENANTS):
            tenant_id = self.uuid()
            tenants.append(tenant_id)
            self.gateway["tenants"].append((tenant_id, self.fake.unique.company(), START))
            ids = [self.uuid() for _ in SERVICES]
            for service_id, name in zip(ids, SERVICES, strict=True):
                self.gateway["services"].append((service_id, tenant_id, name, START))
            for i, caller in enumerate(ids[:-1]):
                callees = ids[i + 1 :]
                for callee in self.rng.sample(callees, min(len(callees), self.rng.randint(1, 3))):
                    self.gateway["service_dependencies"].append((caller, callee))
        return tenants

    def _alerts(self, tenants: list[UUID]) -> list[Group]:
        groups: list[Group] = []
        windows: set[tuple[UUID, str, str, datetime]] = set()
        alerts = 0
        while alerts < ALERTS:
            tenant_id, service = self.rng.choice(tenants), self.rng.choice(SERVICES)
            alertname, severity, cause = self.rng.choice(RULES)
            # Five-minute grouping windows.
            window = START + timedelta(minutes=5 * self.rng.randrange(365 * 24 * 12))
            if (tenant_id, service, alertname, window) in windows:
                continue
            windows.add((tenant_id, service, alertname, window))
            group = Group(self.uuid(), tenant_id, service, alertname, severity, cause, window)
            groups.append(group)
            for _ in range(min(self.rng.randint(1, 7), ALERTS - alerts)):
                labels = {
                    "alertname": alertname,
                    "service": service,
                    "severity": severity,
                    "pod": f"{service}-{self.rng.getrandbits(32):08x}",
                }
                fingerprint = hashlib.sha256(json.dumps(labels, sort_keys=True).encode())
                starts_at = window + timedelta(seconds=self.rng.randrange(300))
                self.gateway["alerts"].append(
                    (
                        self.uuid(),
                        tenant_id,
                        group.id,
                        fingerprint.hexdigest()[:16],
                        json.dumps(labels),
                        starts_at,
                        "resolved",
                        starts_at + timedelta(seconds=5),
                    )
                )
                alerts += 1
        return groups

    def _group(self, group: Group, *, investigated: bool) -> None:
        key = f'{{}}:{{alertname="{group.alertname}", service="{group.service}"}}'
        status, investigation_id = "queued_not_investigated", None
        updated_at = group.window_start + timedelta(minutes=1)
        if investigated:
            investigation_id = self.uuid()
            status, updated_at = self._investigation(group, investigation_id)
        self.gateway["alert_groups"].append(
            (
                group.id,
                group.tenant_id,
                key,
                group.window_start,
                group.severity,
                status,
                investigation_id,
                group.window_start,  # Before any of its alerts arrive.
                updated_at,
            )
        )

    def _action(self, cause: str) -> tuple[str, dict[str, object]] | None:
        match cause:
            case "bad_deploy":
                version = f"v1.{self.rng.randint(0, 9)}.{self.rng.randint(0, 9)}"
                return "rollback_deploy", {"to_version": version}
            case "resource_exhaustion" | "traffic_surge":
                return "scale_service", {"replicas": self.rng.randint(3, 12)}
            case "config_change":
                return "disable_feature_flag", {"flag": f"{self.fake.word()}-rollout"}
            case "dependency_failure" | "database" | "infrastructure":
                return "restart_service", {}
            case _:
                return None

    def _plan(self, group: Group, status: str, cause: str, proposes: bool) -> list[Step]:
        rng = self.rng
        steps: list[Step] = []
        for _ in range(rng.randint(1, 3) if status == "cancelled" else rng.randint(3, 15)):
            tool, call_id = rng.choice(TOOLS), "tc_" + "".join(rng.choices(CROCKFORD, k=10))
            timed_out = rng.random() < 0.05
            steps.append(
                Step(
                    "tool_call",
                    f"{tool} on {group.service}" + (" timed out" if timed_out else ""),
                    tool,
                    call_id,
                    None if timed_out else self.fake.sentence(nb_words=12),
                    10_000 if timed_out else rng.randint(80, 4_000),
                )
            )
        if status in ("resolved", "awaiting_approval"):
            steps.append(Step("hypothesis", f"{cause} in {group.service}"))
            if proposes:
                steps.append(Step("action", f"propose a remediation for {group.service}"))
            steps.append(Step("report", self.fake.sentence(nb_words=14)))
        elif status == "failed":
            steps.append(Step("hypothesis", f"no conclusion for {group.service}"))
        return steps

    def _investigation(self, group: Group, investigation_id: UUID) -> tuple[str, datetime]:
        """Adds one investigation with its steps; returns its final status and update time."""
        rng = self.rng
        created = group.window_start + timedelta(seconds=rng.randint(30, 90))
        statuses = ["resolved", "awaiting_approval", "failed", "cancelled"]
        status = rng.choices(statuses, [80, 5, 10, 5])[0]
        cause = group.cause if rng.random() < 0.7 else rng.choice(CAUSES)
        action = self._action(cause)
        if status == "awaiting_approval" and action is None:
            status = "resolved"
        proposes = action is not None and (status == "awaiting_approval" or rng.random() < 0.5)
        steps = self._plan(group, status, cause, proposes)

        at, budget = created, 0
        evidence: list[dict[str, str]] = []
        for seq, step in enumerate(steps, 1):
            at += timedelta(seconds=rng.randint(2, 20))
            usage = TokenUsage(input=rng.randint(2_000, 9_000), output=rng.randint(100, 600))
            budget += usage.input + usage.output
            fields = {
                "kind": step.kind,
                "summary": step.summary,
                "tool_name": step.tool_name,
                "tool_call_id": step.tool_call_id,
            }
            if created >= CUTOVER:
                version, event = (
                    2,
                    StepRecordedV2.model_validate(
                        {
                            **fields,
                            "result": step.output,
                            "latency_ms": step.latency_ms,
                            "tokens": usage,
                        }
                    ),
                )
            else:
                version = 1
                event = StepRecordedV1.model_validate({**fields, "observation": step.output})
            self.agent["investigation_steps"].append(
                (investigation_id, seq, at, version, event.model_dump_json())
            )
            if step.tool_call_id:
                failed = step.output is None
                self.agent["tool_calls"].append(
                    (
                        step.tool_call_id,
                        investigation_id,
                        step.tool_name,
                        json.dumps({"service": group.service}),
                        None if failed else f"sim://{step.tool_name}/{step.tool_call_id}",
                        step.latency_ms,
                        "timeout" if failed else None,
                        at,
                    )
                )
                if not failed and len(evidence) < 2:
                    excerpt = self.fake.sentence(nb_words=8)
                    evidence.append({"tool_call_id": step.tool_call_id, "excerpt": excerpt})
            if step.kind == "action" and action is not None:
                self._proposal(group, investigation_id, seq, at, action, status)

        report: dict[str, object] | None = None
        if status in ("resolved", "awaiting_approval"):
            runner_up = rng.choice([other for other in CAUSES if other != cause])
            report = {
                "summary": steps[-1].summary,
                "hypotheses": [
                    {
                        "rank": 1,
                        "root_cause_class": cause,
                        "summary": f"{cause} in {group.service}",
                        "confidence": round(rng.uniform(0.55, 0.95), 2),
                        "evidence": evidence,
                    },
                    {
                        "rank": 2,
                        "root_cause_class": runner_up,
                        "summary": f"{runner_up} in {group.service}",
                        "confidence": round(rng.uniform(0.05, 0.4), 2),
                        "evidence": [],
                    },
                ],
            }
        self.agent["investigations"].append(
            (
                investigation_id,
                status,
                budget,
                len(steps),
                None if report is None else json.dumps(report),
                created,
                at,
            )
        )
        return status, at

    def _proposal(
        self,
        group: Group,
        investigation_id: UUID,
        seq: int,
        proposed_at: datetime,
        action: tuple[str, dict[str, object]],
        status: str,
    ) -> None:
        kind, params = action
        action_id, rationale = self.uuid(), self.fake.sentence(nb_words=16)
        decision = None
        if status != "awaiting_approval":
            decision = self.rng.choices(["approved", "rejected"], [75, 25])[0]
        self.agent["proposed_actions"].append(
            (
                action_id,
                investigation_id,
                f"{investigation_id}:{seq}",
                kind,
                group.service,
                json.dumps(params),
                rationale,
                decision or "pending",
                proposed_at,
            )
        )
        self.gateway["proposed_actions"].append(
            (
                action_id,
                group.tenant_id,
                investigation_id,
                kind,
                group.service,
                json.dumps(params),
                rationale,
                proposed_at,
                proposed_at + timedelta(seconds=1),
            )
        )
        if decision:
            reason = self.fake.sentence(nb_words=6) if self.rng.random() < 0.3 else None
            decided_at = proposed_at + timedelta(minutes=self.rng.randint(1, 30))
            self.gateway["approvals"].append(
                (action_id, self.fake.email(), decision, reason, decided_at)
            )


def copy_rows(
    cursor: psycopg.Cursor[Any], statement: LiteralString, rows: Iterable[Sequence[object]]
) -> None:
    with cursor.copy(statement) as copy:
        for row in rows:
            copy.write_row(row)


def bulk_steps(agent: Connection, cursor: psycopg.Cursor[Any], target: int) -> int:
    """Adds investigations of 10 steps (the last may have fewer) until there are target steps.

    Events come from a small pre-rendered pool: volume and timestamps matter here, not content.
    """
    rng, fake = random.Random(SEED + 1), Faker()  # noqa: S311 - fake data, not secrets
    fake.seed_instance(SEED + 1)
    count = agent.execute(text("SELECT count(*) FROM agent.investigation_steps")).scalar_one()
    missing = target - count
    if missing <= 0:
        return 0
    common: dict[str, Any] = {"kind": "hypothesis", "tool_name": None, "tool_call_id": None}
    v2_extra = {"result": None, "latency_ms": None, "tokens": {"input": 4_000, "output": 300}}
    pool = {
        1: [
            StepRecordedV1.model_validate(
                {**common, "summary": fake.sentence(), "observation": None}
            ).model_dump_json()
            for _ in range(64)
        ],
        2: [
            StepRecordedV2.model_validate(
                {**common, "summary": fake.sentence(), **v2_extra}
            ).model_dump_json()
            for _ in range(64)
        ],
    }
    sizes = [10] * (missing // 10) + ([missing % 10] if missing % 10 else [])
    span = int((END - START).total_seconds()) - 3_600
    # Sorted ids keep primary key inserts sequential, which loads much faster.
    investigations = sorted(
        (
            UUID(int=rng.getrandbits(128), version=4),
            START + timedelta(seconds=rng.randrange(span)),
            size,
        )
        for size in sizes
    )
    copy_rows(
        cursor,
        AGENT_COPY["investigations"],
        (
            (
                inv_id,
                "resolved",
                4_300 * size,
                size,
                None,
                created,
                created + timedelta(seconds=10 * size),
            )
            for inv_id, created, size in investigations
        ),
    )

    def steps() -> Iterator[Row]:
        for inv_id, created, size in investigations:
            version = 2 if created >= CUTOVER else 1
            for seq in range(1, size + 1):
                at = created + timedelta(seconds=10 * seq)
                yield inv_id, seq, at, version, rng.choice(pool[version])

    copy_rows(cursor, AGENT_COPY["investigation_steps"], steps())
    return missing


def main() -> None:
    parser = argparse.ArgumentParser(description="Seeds the gateway and agent schemas.")
    parser.add_argument("--reset", action="store_true", help="delete existing rows first")
    parser.add_argument(
        "--large",
        nargs="?",
        const=LARGE_STEPS,
        type=int,
        metavar="STEPS",
        help=f"top the agent schema up to STEPS steps (default {LARGE_STEPS:,})",
    )
    args = parser.parse_args()
    try:
        settings = load_settings(SeedSettings)
    except ConfigError as error:
        sys.exit(f"seed: {error}")
    configure_logging("seed", "info")

    history = History()
    engine = create_engine(settings.agent_database_url, connect_args={"connect_timeout": 5})
    with (
        psycopg.connect(settings.gateway_database_url, connect_timeout=5) as gateway,
        engine.begin() as agent,
    ):
        row = gateway.execute("SELECT EXISTS (SELECT FROM gateway.tenants)").fetchone()
        has_data = bool(row and row[0]) or bool(
            agent.execute(text("SELECT EXISTS (SELECT FROM agent.investigations)")).scalar()
        )
        if has_data and not args.reset:
            sys.exit("seed: the database already has data; pass --reset to replace it")
        if has_data:
            gateway.execute("TRUNCATE gateway.tenants CASCADE")
            agent.execute(text("TRUNCATE agent.investigations CASCADE"))
        # Historical months need their partitions; before revision 0002 this does nothing.
        ensure_partitions(agent, START.date(), END.date())
        with gateway.cursor() as cursor:
            for table, statement in GATEWAY_COPY.items():
                copy_rows(cursor, statement, history.gateway[table])
        driver = cast(psycopg.Connection[Any], agent.connection.driver_connection)
        with driver.cursor() as cursor:
            for table, statement in AGENT_COPY.items():
                copy_rows(cursor, statement, history.agent[table])
            added = bulk_steps(agent, cursor, args.large) if args.large else 0
    engine.dispose()
    log.info(
        "seeded",
        extra={
            **{f"gateway.{table}": len(rows) for table, rows in history.gateway.items()},
            **{f"agent.{table}": len(rows) for table, rows in history.agent.items()},
            "agent.large_mode_steps": added,
        },
    )


if __name__ == "__main__":
    main()
