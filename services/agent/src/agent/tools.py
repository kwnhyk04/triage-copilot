"""The investigation's tools: argument models, the definitions the model sees, and the read-only
tools themselves.

The tools only read: the sim (metrics, logs, deploys, dependencies), the gateway's alert context,
and runbook search. propose_action and submit_report are defined here but handled by the job,
which records them in its own transaction. Time windows count from the moment the alert fired.
"""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, cast
from urllib.parse import quote
from uuid import UUID

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agent.model import ModelCall, ReportDraft, ToolSpec
from agent.prompt import PROPOSE_TOOL, REPORT_TOOL
from agent.runbooks import RunbookSearch
from py_clients.clients import AlertContextClient, ApiError

RAW_PAGE_LINES = 100
SUMMARY_PAGE_LINES = 200
LINE_CHARS = 160
SERIES_SHOWN = 10
SAMPLES_SHOWN = 24
UNSUMMARIZED_LINES = 40
SERVICE_NAME = re.compile(r"[A-Za-z0-9._-]+")
# Alertmanager's standard annotations, which get_alert_context shows when its tools ask for them;
# anything larger, such as a watch snapshot, gets its own tool.
SHOWN_ANNOTATIONS = ("summary", "description", "runbook_url")
ANNOTATION_CHARS = 500


class ToolInputError(Exception):
    """The arguments or the query are wrong: retrying the same call cannot help."""


class ToolUnavailable(Exception):
    """The backend answered with a server error: the evidence is missing for now."""


@dataclass(frozen=True)
class ToolContext:
    investigation_id: UUID
    fired_at: datetime
    # Tokens the tool may spend on model calls without touching the final report's reserve.
    token_allowance: int
    # The investigation's prompt sha, which the tool's own model calls record.
    prompt_sha: str
    # A live tenant's app (settings.LiveApp), whose catalog the live tools read; None for the sim.
    app: str | None = None


@dataclass(frozen=True)
class ToolResult:
    text: str
    calls: Sequence[ModelCall] = ()


Tool = Callable[[ToolContext, dict[str, Any]], Awaitable[str | ToolResult]]
# A log page, the tokens it may spend and the prompt sha its calls record.
Summarize = Callable[[str, int, str], Awaitable[tuple[str | None, list[ModelCall]]]]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AlertContextArgs(_Args):
    pass


class MetricsArgs(_Args):
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            description='A Prometheus selector, such as http_error_ratio{service="checkout"}.',
        ),
    ]
    minutes_before: Annotated[
        int, Field(ge=5, le=360, description="Minutes before the alert fired; 60 if unset.")
    ] = 60
    minutes_after: Annotated[
        int, Field(ge=0, le=60, description="Minutes after the alert fired; 15 if unset.")
    ] = 15


class LogsArgs(_Args):
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            description='LogQL, such as {service="checkout", level="error"} |= "timeout".',
        ),
    ]
    minutes_before: Annotated[
        int, Field(ge=1, le=360, description="Minutes before the alert fired; 30 if unset.")
    ] = 30
    minutes_after: Annotated[
        int, Field(ge=0, le=60, description="Minutes after the alert fired; 15 if unset.")
    ] = 15
    cursor: Annotated[
        str,
        Field(max_length=1000, description="next_cursor of the previous page; empty for page 1."),
    ] = ""
    direction: Annotated[
        Literal["backward", "forward"],
        Field(description="backward (newest first, if unset) or forward."),
    ] = "backward"


class DeploysArgs(_Args):
    service: Annotated[
        str, Field(max_length=200, description="Only this service's deploys; empty for all.")
    ] = ""
    limit: Annotated[int, Field(ge=1, le=20, description="Deploys per page; 10 if unset.")] = 10
    cursor: Annotated[
        str, Field(max_length=1000, description="next_cursor of the previous page; empty first.")
    ] = ""


class DependenciesArgs(_Args):
    service: Annotated[str, Field(min_length=1, max_length=200, description="A service name.")]

    @field_validator("service")
    @classmethod
    def _service_name(cls, service: str) -> str:
        # The name becomes a URL path segment; dots and slashes could reach other routes.
        if not SERVICE_NAME.fullmatch(service) or service.strip(".") == "":
            raise ValueError("a service name has only letters, digits, '.', '_' and '-'")
        return service


class RunbooksArgs(_Args):
    query: Annotated[
        str, Field(min_length=3, max_length=500, description="A symptom or an error message.")
    ]
    k: Annotated[int, Field(ge=1, le=5, description="Sections to return; 3 if unset.")] = 3


class ProposeActionArgs(_Args):
    kind: Literal["rollback_deploy", "restart_service", "scale_service", "disable_feature_flag"]
    target: Annotated[
        str, Field(min_length=1, max_length=200, description="The service the action applies to.")
    ]
    params: Annotated[
        dict[str, Annotated[str, Field(max_length=200)]],
        Field(
            max_length=10,
            description=(
                "Arguments as strings: to_version for rollback_deploy, replicas for "
                "scale_service, flag for disable_feature_flag; empty for restart_service."
            ),
        ),
    ]
    rationale: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence_ids: Annotated[
        list[str],
        Field(min_length=1, max_length=20, description="Ids of the results it rests on."),
    ]


def parse[T: BaseModel](model: type[T], args: dict[str, Any]) -> T:
    """The model's arguments, or a ToolInputError that tells it what to fix."""
    try:
        return model.model_validate(args)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}"
            for e in error.errors(include_url=False, include_input=False)
        )
        raise ToolInputError(f"invalid arguments: {problems}") from None


def _plain(schema: dict[str, Any]) -> dict[str, Any]:
    """The JSON schema with $refs inlined and titles and defaults left out, which some
    providers' function declarations reject; descriptions name the defaults instead."""
    defs: dict[str, Any] = schema.get("$defs", {})

    def walk(node: object) -> object:
        if isinstance(node, dict):
            fields = cast("dict[str, object]", node)
            if "$ref" in fields:
                return walk(defs[str(fields["$ref"]).rsplit("/", 1)[1]])
            skip = {"$defs", "title", "default"}
            return {k: walk(v) for k, v in fields.items() if k not in skip}
        if isinstance(node, list):
            return [walk(v) for v in cast("list[object]", node)]
        return node

    return cast("dict[str, Any]", walk(schema))


def spec(name: str, description: str, args: type[BaseModel], *, reason: bool = True) -> ToolSpec:
    schema = _plain(args.model_json_schema())
    if reason:
        why = {"type": "string", "description": "Why you call this tool now, in one sentence."}
        schema["properties"] = {"reason": why, **schema.get("properties", {})}
        schema["required"] = ["reason", *schema.get("required", [])]
    return ToolSpec(name, description, schema)


TOOL_SPECS: tuple[ToolSpec, ...] = (
    spec(
        "get_alert_context",
        "The alert group under investigation: its alerts with their labels and start times.",
        AlertContextArgs,
    ),
    spec(
        "query_metrics",
        "Metric series matching a Prometheus selector around the time the alert fired: first, "
        "min, max and last value, and samples timed relative to the alert.",
        MetricsArgs,
    ),
    spec(
        "query_logs",
        "Log lines matching a LogQL query around the time the alert fired, newest first unless "
        "direction is forward. The first page holds up to 100 lines; pass next_cursor for the "
        "next page, which holds up to 200 lines and comes back summarized.",
        LogsArgs,
    ),
    spec(
        "list_deploys",
        "Deploys, newest first, with version, author and change; next_cursor reads older ones.",
        DeploysArgs,
    ),
    spec(
        "get_dependencies",
        "The services a service depends on and the services that depend on it.",
        DependenciesArgs,
    ),
    spec(
        "search_runbooks",
        "The runbook sections closest to a symptom or an error message.",
        RunbooksArgs,
    ),
    spec(
        PROPOSE_TOOL,
        "Proposes one remediation for a human to approve. It runs nothing. Cite the tool results "
        "it rests on.",
        ProposeActionArgs,
    ),
    spec(
        REPORT_TOOL,
        "Ends the investigation with the final report: hypotheses best first, each with the ids "
        "of the results that support it, and what could not be checked.",
        ReportDraft,
        reason=False,
    ),
)


class Tools:
    def __init__(
        self,
        # None for live triage's tools, which never query the sim.
        sim: httpx2.AsyncClient | None,
        alerts: AlertContextClient,
        runbooks: RunbookSearch | None,
        summarize: Summarize | None,
        *,
        annotations: bool = False,
    ) -> None:
        self.sim = sim
        self.alerts = alerts
        self.runbooks = runbooks
        self.summarize = summarize
        # The sim's tools leave annotations out, so eval runs stay comparable with the cached ones.
        self.annotations = annotations

    def all(self) -> dict[str, Tool]:
        return {
            "get_alert_context": self.get_alert_context,
            "query_metrics": self.query_metrics,
            "query_logs": self.query_logs,
            "list_deploys": self.list_deploys,
            "get_dependencies": self.get_dependencies,
            "search_runbooks": self.search_runbooks,
        }

    async def get_alert_context(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        parse(AlertContextArgs, args)
        try:
            group = await asyncio.to_thread(self.alerts.get, ctx.investigation_id)
        except ApiError as error:
            if error.status >= 500:
                raise ToolUnavailable(f"the gateway answered {error}") from None
            raise ToolInputError(f"the gateway answered {error}") from None
        lines = [
            f"Alert group {group.group_key} ({group.severity}), window opened "
            f"{_at(group.window_start)}, {group.alert_count} alerts:"
        ]
        for alert in group.alerts[:20]:
            labels = ", ".join(f"{k}={v}" for k, v in sorted(alert.labels.root.items()))
            started = relative(alert.starts_at, ctx.fired_at)
            lines.append(f"- {alert.status} since {started}: {labels}")
            notes = alert.annotations.root if self.annotations and alert.annotations else {}
            lines.extend(
                f"  {name}: {notes[name][:ANNOTATION_CHARS]}"
                for name in SHOWN_ANNOTATIONS
                if name in notes
            )
        if group.alert_count > 20:
            lines.append(f"(+{group.alert_count - 20} more alerts)")
        return "\n".join(lines)

    async def query_metrics(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        query = parse(MetricsArgs, args)
        start, end = _window(ctx, query.minutes_before, query.minutes_after)
        # About 120 samples at most, on the sim's 30 s grid.
        step = max(30, -(-int((end - start).total_seconds()) // (120 * 30)) * 30)
        params = {"query": query.query, "start": _at(start), "end": _at(end), "step": f"{step}s"}
        body = await self._get("/api/v1/query_range", params)
        series: list[dict[str, Any]] = body["data"]["result"]
        if not series:
            window = f"{relative(start, ctx.fired_at)} to {relative(end, ctx.fired_at)}"
            return f"No series match {query.query} from {window} around the alert."
        lines: list[str] = []
        fired = ctx.fired_at.timestamp()
        for item in series[:SERIES_SHOWN]:
            metric: dict[str, str] = item["metric"]
            labels = ", ".join(f'{k}="{v}"' for k, v in sorted(metric.items()) if k != "__name__")
            points = [(float(t), float(v)) for t, v in item["values"]]
            values = [v for _, v in points]
            lines.append(
                f"{metric.get('__name__', '')}{{{labels}}}: first {_num(values[0])}, min "
                f"{_num(min(values))}, max {_num(max(values))}, last {_num(values[-1])}"
            )
            every = max(1, -(-len(points) // SAMPLES_SHOWN))
            shown = points[::every]
            if shown[-1] != points[-1]:
                shown.append(points[-1])
            lines.append("  " + ", ".join(f"{_offset(t - fired)} {_num(v)}" for t, v in shown))
        if len(series) > SERIES_SHOWN:
            lines.append(f"(+{len(series) - SERIES_SHOWN} more series; narrow the selector)")
        return "\n".join(lines)

    async def query_logs(self, ctx: ToolContext, args: dict[str, Any]) -> str | ToolResult:
        query = parse(LogsArgs, args)
        start, end = _window(ctx, query.minutes_before, query.minutes_after)
        limit = SUMMARY_PAGE_LINES if query.cursor else RAW_PAGE_LINES
        params: dict[str, Any] = {
            "query": query.query,
            "start": _at(start),
            "end": _at(end),
            "limit": limit,
            "direction": query.direction,
        }
        if query.cursor:
            params["cursor"] = query.cursor
        body = await self._get("/loki/api/v1/query_range", params)
        found = [
            (int(ns), stream.get("service", "?"), stream.get("level", ""), line)
            for result in body["data"]["result"]
            for stream in [result["stream"]]
            for ns, line in result["values"]
        ]
        found.sort(key=lambda item: item[0], reverse=query.direction == "backward")
        fired = ctx.fired_at.timestamp()
        rendered = [
            f"{_offset(ns / 1e9 - fired)} {service}{'/' + level if level else ''} "
            f"{line[:LINE_CHARS]}"
            for ns, service, level, line in found
        ]
        footer = f"next_cursor: {body.get('next_cursor') or 'none (last page)'}"
        if not rendered:
            return f"No lines match {query.query}.\n{footer}"
        page = "\n".join(rendered)
        if not query.cursor or self.summarize is None:
            return f"{len(rendered)} lines:\n{page}\n{footer}"
        summary, calls = await self.summarize(page, ctx.token_allowance, ctx.prompt_sha)
        if summary is None:
            head = "\n".join(rendered[:UNSUMMARIZED_LINES])
            text = f"{len(rendered)} lines, no summary available; the first {UNSUMMARIZED_LINES}:"
            return ToolResult(f"{text}\n{head}\n{footer}", calls)
        return ToolResult(f"Summary of {len(rendered)} lines:\n{summary}\n{footer}", calls)

    async def list_deploys(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        query = parse(DeploysArgs, args)
        params: dict[str, Any] = {"limit": query.limit}
        if query.service:
            params["service"] = query.service
        if query.cursor:
            params["cursor"] = query.cursor
        body = await self._get("/api/v1/deploys", params)
        lines = [
            f"{d['at']} ({relative(datetime.fromisoformat(d['at']), ctx.fired_at)}) "
            f"{d['service']} {d['previous_version']} -> {d['version']} by {d['author']}: "
            f"{d['change']}"
            for d in body["deploys"]
        ]
        footer = f"next_cursor: {body.get('next_cursor') or 'none (last page)'}"
        return "\n".join([*(lines or ["No deploys."]), footer])

    async def get_dependencies(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        query = parse(DependenciesArgs, args)
        body = await self._get(f"/api/v1/dependencies/{quote(query.service, safe='')}", {})
        depends_on = ", ".join(body["depends_on"]) or "nothing"
        dependents = ", ".join(body["dependents"]) or "nothing"
        return f"{query.service} depends on: {depends_on}. Depended on by: {dependents}."

    async def search_runbooks(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        query = parse(RunbooksArgs, args)
        if self.runbooks is None:
            raise ToolUnavailable("runbook search is not configured (FASTEMBED_CACHE_PATH)")
        found = await self.runbooks.search(query.query, query.k)
        if not found:
            return "No runbooks are indexed."
        return "\n\n".join(
            f"[{runbook} #{chunk}] (distance {distance:.3f})\n{content[:1500]}"
            for runbook, chunk, content, distance in found
        )

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """A sim response; 4xx is the model's to fix, 5xx is missing evidence."""
        if self.sim is None:
            raise ToolUnavailable("this tool set has no sim")
        response = await self.sim.get(path, params=params)
        try:
            data: object = response.json()
        except json.JSONDecodeError:
            data = None
        body = cast("dict[str, Any]", data) if isinstance(data, dict) else {}
        error = str(body.get("error") or f"HTTP {response.status_code}")
        if response.status_code >= 500:
            raise ToolUnavailable(error)
        if response.status_code >= 400:
            known = body.get("services")
            names = (
                ", ".join(map(str, cast("list[object]", known))) if isinstance(known, list) else ""
            )
            raise ToolInputError(f"{error}. Known services: {names}." if names else f"{error}.")
        return body


def _window(ctx: ToolContext, before: int, after: int) -> tuple[datetime, datetime]:
    """Minutes around the moment the alert fired, never past now: metrics have no future."""
    end = min(ctx.fired_at + timedelta(minutes=after), datetime.now(UTC))
    return ctx.fired_at - timedelta(minutes=before), end


def _at(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def relative(moment: datetime, fired_at: datetime) -> str:
    return _offset((moment - fired_at).total_seconds())


def _offset(seconds: float) -> str:
    """Seconds from the moment the alert fired, as "-4m12s" or "+30s"."""
    sign = "-" if seconds < 0 else "+"
    minutes, rest = divmod(round(abs(seconds)), 60)
    return f"{sign}{minutes}m{rest:02d}s" if minutes else f"{sign}{rest}s"


def _num(value: float) -> str:
    return f"{value:.4g}"
