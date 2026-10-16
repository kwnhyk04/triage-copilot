import asyncio
import base64
import contextlib
import json
import logging
import math
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple, Self

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from py_config import ConfigError, EnvSettings, load_settings
from py_telemetry import LogLevel, configure_logging, instrument
from sim.logql import parse_log_query
from sim.promql import Matcher, QueryError, parse_selector
from sim.scenario import Tool, load_scenarios
from sim.world import NS, Deadline, LineKey, World, paginate, rfc3339

log = logging.getLogger(__name__)

# Route prefixes of each tool, so a tool marked down answers before parameter validation.
TOOL_ROUTES: dict[Tool, tuple[str, ...]] = {
    "metrics": ("/api/v1/query", "/api/v1/label/"),
    "logs": ("/loki/",),
    "deploys": ("/api/v1/deploys",),
    "dependencies": ("/api/v1/dependencies/",),
}
MAX_POINTS = 11_000
MAX_TOTAL_POINTS = 250_000
MAX_LOG_LINES = 200
MAX_DIGITS = 20
MAX_EPOCH = 253_402_300_799  # 9999-12-31T23:59:59Z
STEP_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}
STEP = re.compile(r"(\d+)(ms|s|m|h|d)")


class Settings(EnvSettings):
    sim_host: str = "127.0.0.1"
    sim_port: int = 4230
    # The admin routes (activation, state, the down switch) listen on their own port, which only
    # the eval harness is given: no request to the data port can reach them, whatever its path.
    sim_admin_host: str = "127.0.0.1"
    sim_admin_port: int = 4231
    sim_scenarios_dir: Path = Path("evals/scenarios/v1")
    sim_request_time_limit_seconds: Annotated[float, Field(gt=0)] = 2.0
    log_level: LogLevel = "info"
    otel_exporter_otlp_endpoint: str | None = None

    @model_validator(mode="after")
    def _apart(self) -> Self:
        if self.sim_admin_port == self.sim_port:
            raise ValueError("SIM_ADMIN_PORT must differ from SIM_PORT")
        return self


class Unavailable(Exception):
    """The data a route needs is not there: no scenario is active."""


class Activation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int | None = None
    anchor: AwareDatetime | None = None


class Down(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Tool | None


@dataclass
class State:
    # ponytail: one active scenario per process; key worlds by tenant if evals run in parallel.
    world: World | None = None
    down: Tool | None = None


class Apps(NamedTuple):
    """The data routes the agent's tools call, and the admin routes only the harness calls."""

    data: FastAPI
    admin: FastAPI


def failure(status: int, kind: str, message: str) -> JSONResponse:
    """Prometheus's error envelope, for every error but /readyz and the 404s that list names."""
    body = {"status": "error", "errorType": kind, "error": message}
    return JSONResponse(body, status_code=status)


def epoch(value: str) -> float:
    """Unix seconds from a number or an RFC 3339 time (UTC when it has no offset), to year 9999."""
    try:
        seconds = float(value)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise QueryError(f"cannot parse {value!r} to a valid timestamp") from None
        seconds = (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp()
    if not abs(seconds) <= MAX_EPOCH:
        raise QueryError(f"cannot parse {value!r} to a valid timestamp")
    return seconds


def loki_ns(value: str) -> int:
    """Unix nanoseconds from RFC 3339, from integer nanoseconds, or from seconds under 1e12."""
    if not re.fullmatch(r"-?\d+", value):
        return round(epoch(value) * NS)
    if len(value) > MAX_DIGITS:
        raise QueryError(f"a timestamp has at most {MAX_DIGITS} digits")
    number = int(value)
    ns = number * NS if abs(number) < 10**12 else number
    if abs(ns) > MAX_EPOCH * NS:
        raise QueryError(f"cannot parse {value!r} to a valid timestamp")
    return ns


def step_seconds(value: str) -> float:
    """A query_range step: seconds, or a duration such as 30s, 1m or 1m30s."""
    if len(value) > MAX_DIGITS:
        raise QueryError(f"a step has at most {MAX_DIGITS} characters")
    try:
        width = float(value)
    except ValueError:
        parts = STEP.findall(value)
        if not parts or "".join(n + unit for n, unit in parts) != value:
            raise QueryError(f"cannot parse {value!r} to a valid duration") from None
        width = sum(int(n) * STEP_UNITS[unit] for n, unit in parts)
    if not math.isfinite(width) or width <= 0:
        raise QueryError("zero or negative query resolution step widths are not accepted")
    return width


def sample(value: float) -> str:
    """Up to 6 significant digits and never an exponent, as Prometheus writes sample values."""
    return format(Decimal(f"{value:.6g}"), "f")


def encode(payload: list[Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def decode(cursor: str) -> object:
    try:
        return json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, TypeError, RecursionError):
        raise QueryError("invalid cursor") from None


def log_cursor(cursor: str, direction: str) -> LineKey:
    match decode(cursor):
        case [str() as owner, int() as ns, int() as stream, int() as index] if owner == direction:
            return ns, stream, index
        case ["forward" | "backward", int(), int(), int()]:
            raise QueryError("the cursor belongs to a query that reads the other direction")
        case _:
            raise QueryError("invalid cursor")


def deploy_cursor(cursor: str) -> tuple[int, str]:
    match decode(cursor):
        case ["deploy", int() as at, str() as deploy_id]:
            return at, deploy_id
        case _:
            raise QueryError("invalid cursor")


def handle_errors(app: FastAPI) -> None:
    """Every error in Prometheus's envelope, as 400 for bad input and 503 for missing data."""

    @app.exception_handler(QueryError)
    def bad_query(_request: Request, exc: Exception) -> JSONResponse:
        return failure(400, "bad_data", str(exc))

    @app.exception_handler(ArithmeticError)
    @app.exception_handler(RecursionError)
    def out_of_range(_request: Request, exc: Exception) -> JSONResponse:
        """A catch-all for numbers too large and input nested too deeply that no check caught."""
        return failure(400, "bad_data", f"a parameter is out of range: {exc}")

    @app.exception_handler(RequestValidationError)
    def bad_parameters(_request: Request, exc: Exception) -> JSONResponse:
        problems: list[Any] = list(exc.errors()) if isinstance(exc, RequestValidationError) else []
        details = "; ".join(f"{'.'.join(map(str, p['loc']))}: {p['msg']}" for p in problems)
        return failure(400, "bad_data", details or "invalid request")

    @app.exception_handler(Unavailable)
    def unavailable(_request: Request, exc: Exception) -> JSONResponse:
        return failure(503, "unavailable", str(exc))


def create_apps(settings: Settings | None = None) -> Apps:
    """The data and admin apps over one shared state: what the admin app activates, data serves."""
    settings = settings or load_settings(Settings)
    configure_logging("sim", settings.log_level)
    scenarios = load_scenarios(settings.sim_scenarios_dir)
    if not scenarios:
        log.warning("no scenarios found", extra={"directory": str(settings.sim_scenarios_dir)})
    state = State()
    app = FastAPI(title="sim", docs_url=None, redoc_url=None, openapi_url=None)
    admin = FastAPI(title="sim admin", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def down_switch(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Answers 503 for a tool marked down before FastAPI validates the parameters."""
        down = state.down
        if down and request.url.path.startswith(TOOL_ROUTES[down]):
            return failure(503, "unavailable", f"{down} backend is down (simulated)")
        return await call_next(request)

    instrument(app, "sim", settings.otel_exporter_otlp_endpoint)
    handle_errors(app)
    handle_errors(admin)

    def world() -> World:
        if state.world is None:
            raise Unavailable("no scenario is active")
        return state.world

    def selector(query: str, active: World) -> list[Matcher]:
        try:
            return parse_selector(query)
        except QueryError as problem:
            names = ", ".join(sorted({s.metric.name for s in active.series}))
            raise QueryError(
                f'{problem}. Only selectors are supported, such as name{{service="checkout"}}; '
                f"no functions, aggregations, ranges or operators. Metrics: {names}"
            ) from None

    @app.get("/healthz")
    @admin.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", response_model=None)
    def readyz() -> dict[str, str] | JSONResponse:
        if not scenarios:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ok"}

    @app.get("/api/v1/query_range")
    def query_range(query: str, start: str, end: str, step: str) -> dict[str, Any]:
        deadline = Deadline(settings.sim_request_time_limit_seconds)
        active = world()
        matchers = selector(query, active)
        first, last, width = epoch(start), epoch(end), step_seconds(step)
        if last < first:
            raise QueryError("end timestamp must not be before start time")
        span = (last - first) / width
        if span >= MAX_POINTS:
            raise QueryError(
                "exceeded maximum resolution of 11,000 points per timeseries. "
                "Try decreasing the query resolution (?step=XX)"
            )
        points = math.floor(span) + 1
        selected = active.select(matchers)
        if points * len(selected) > MAX_TOTAL_POINTS:
            raise QueryError(
                f"{len(selected)} series of {points:,} points exceed the limit of "
                f"{MAX_TOTAL_POINTS:,} points per query; narrow the selector or raise the step"
            )
        times = [first + i * width for i in range(points)]
        result: list[dict[str, Any]] = []
        for series in selected:
            values = [
                [round(t, 3), sample(v)]
                for t in deadline.watch(times)
                if (v := active.value(series, t)) is not None
            ]
            if values:
                result.append({"metric": series.labels, "values": values})
        return {"status": "success", "data": {"resultType": "matrix", "result": result}}

    @app.get("/api/v1/query")
    def query(query: str, time: str | None = None) -> dict[str, Any]:
        active = world()
        matchers = selector(query, active)
        at = float(active.anchor) if time is None else epoch(time)
        result = [
            {"metric": s.labels, "value": [round(at, 3), sample(v)]}
            for s in active.select(matchers)
            if (v := active.value(s, at)) is not None
        ]
        return {"status": "success", "data": {"resultType": "vector", "result": result}}

    @app.get("/api/v1/label/{name}/values")
    def label_values(name: str) -> dict[str, Any]:
        active = world()
        values = sorted({s.labels[name] for s in active.series if name in s.labels})
        return {"status": "success", "data": values}

    @app.get("/loki/api/v1/query_range")
    def logs(
        query: str,
        start: str | None = None,
        end: str | None = None,
        limit: int = 100,
        direction: Literal["backward", "forward"] = "backward",
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Lines with start <= time < end (the whole window by default), newest first unless
        direction is forward; next_cursor reads on from the last line of the page."""
        deadline = Deadline(settings.sim_request_time_limit_seconds)
        active = world()
        if limit > MAX_LOG_LINES:
            raise QueryError(f"limit must be at most {MAX_LOG_LINES}")
        if limit < 1:
            raise QueryError("limit must be at least 1")
        parsed = parse_log_query(query)
        first = active.start * NS if start is None else loki_ns(start)
        last = active.end * NS if end is None else loki_ns(end)
        if last < first:
            raise QueryError("end timestamp must not be before start time")
        after = None if cursor is None else log_cursor(cursor, direction)
        page, more = paginate(
            active.logs(parsed, first, last, deadline), direction == "forward", after, limit
        )
        streams: dict[int, list[list[str]]] = {}
        for line in page:
            streams.setdefault(line.stream, []).append([str(line.ns), line.text])
        result = [
            {"stream": active.scenario.logs[n].labels, "values": v} for n, v in streams.items()
        ]
        next_cursor = encode([direction, *page[-1].key]) if more and page else None
        data = {"resultType": "streams", "result": result}
        return {"status": "success", "data": data, "next_cursor": next_cursor}

    @app.get("/api/v1/deploys")
    def deploys(
        service: str | None = None, limit: int = 20, cursor: str | None = None
    ) -> dict[str, Any]:
        active = world()
        if not 1 <= limit <= 50:
            raise QueryError("limit must be between 1 and 50")
        records = [d for d in active.deploys if service is None or d.service == service]
        if cursor is not None:
            after = deploy_cursor(cursor)
            records = [d for d in records if (d.at, d.id) < after]
        page = records[:limit]
        next_cursor = encode(["deploy", page[-1].at, page[-1].id]) if len(records) > limit else None
        return {"deploys": [d.json() for d in page], "next_cursor": next_cursor}

    @app.get("/api/v1/dependencies/{service}", response_model=None)
    def dependencies(service: str) -> dict[str, Any] | JSONResponse:
        services = world().scenario.services
        if service not in services:
            body = {"error": f"unknown service {service}", "services": sorted(services)}
            return JSONResponse(body, status_code=404)
        dependents = sorted(name for name, s in services.items() if service in s.depends_on)
        depends_on = sorted(services[service].depends_on)
        return {"service": service, "depends_on": depends_on, "dependents": dependents}

    # The admin app takes no credential: it listens on its own port, on 127.0.0.1 (the internal
    # Docker network in production), and the agent is never given its address.
    @admin.get("/admin/scenarios")
    def scenario_ids() -> list[str]:
        return sorted(scenarios)

    @admin.post("/admin/scenarios/{scenario_id}/activate", response_model=None)
    def activate(scenario_id: str, body: Activation | None = None) -> dict[str, Any] | JSONResponse:
        """Makes the scenario the one every data route serves, with its own tool marked down."""
        loaded = scenarios.get(scenario_id)
        if loaded is None:
            known = sorted(scenarios)
            return JSONResponse(
                {"error": f"unknown scenario {scenario_id}", "scenarios": known}, status_code=404
            )
        scenario = loaded.scenario
        body = body or Activation()
        seed = scenario.seed if body.seed is None else body.seed
        anchor = int((body.anchor or datetime.now(UTC)).timestamp())
        active = World(scenario, seed, anchor)
        state.world, state.down = active, scenario.down
        log.info("scenario activated", extra={"scenario": scenario.id, "seed": seed})
        return admin_state() | {"alertmanager": active.alertmanager()}

    @admin.get("/admin/state")
    def admin_state() -> dict[str, Any]:
        active = state.world
        return {
            "scenario": None if active is None else active.scenario.id,
            "sha256": None if active is None else scenarios[active.scenario.id].sha256,
            "seed": None if active is None else active.seed,
            "anchor": None if active is None else rfc3339(active.anchor),
            "down": state.down,
        }

    @admin.put("/admin/down")
    def set_down(body: Down) -> dict[str, Any]:
        """Marks one tool's backend down, so its routes answer 503; null clears it."""
        state.down = body.tool
        return admin_state()

    return Apps(app, admin)


def serve(settings: Settings) -> None:
    """Serves both apps, each on its own port, until SIGINT or SIGTERM stops both."""
    apps = create_apps(settings)
    servers = [
        uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_config=None))
        for app, host, port in (
            (apps.data, settings.sim_host, settings.sim_port),
            (apps.admin, settings.sim_admin_host, settings.sim_admin_port),
        )
    ]

    async def both() -> None:
        # Each server raises the signal it caught again once it has stopped, so both stop.
        await asyncio.gather(*(server.serve() for server in servers))

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(both())


def run() -> None:
    """Console entry point; pass --reload during development."""
    try:
        settings = load_settings(Settings)
    except ConfigError as error:
        sys.exit(f"sim: {error}")
    configure_logging("sim", settings.log_level)
    if "--reload" not in sys.argv:
        serve(settings)
        return
    # A dev dependency, as --reload is: restarts on changes to the code or the scenarios.
    import watchfiles

    watchfiles.run_process(
        Path(__file__).parent, settings.sim_scenarios_dir, target=serve, args=(settings,)
    )
