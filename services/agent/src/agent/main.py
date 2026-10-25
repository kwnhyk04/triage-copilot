import asyncio
import logging
import sys
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx2
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, create_engine, text

from agent.api import add_internal_api
from agent.consumer import Consumer, sqs_client
from agent.job import Job
from agent.live import live_tools
from agent.llm import Binding, Limiter, Router, spent
from agent.model import Model, ScriptedModel
from agent.outbox import Relay, sqs_sender
from agent.partitions import maintain_partitions
from agent.providers import make_provider
from agent.retention import maintain_retention
from agent.runbooks import FastEmbedder, RunbookSearch
from agent.settings import Role, Settings
from agent.tools import Tools
from py_clients.clients import AlertContextClient
from py_config import ConfigError, load_settings
from py_telemetry import configure_logging, instrument

log = logging.getLogger(__name__)

# Every request to the sim; a tool's own timeout bounds its retries on top.
SIM_TIMEOUT = 10.0


def make_routers(settings: Settings, engine: Engine) -> tuple[Router, Router]:
    """The sim's router (the primary, fallback and small roles, and Claude when it is on) and live
    triage's (Claude first while ANTHROPIC_API_KEY is set, then the same free roles). They share
    one limiter, so a model's minute counts every request to it."""

    def is_spent(role: Role, worst_usd: float) -> str | None:
        with engine.connect() as conn:
            return spent(conn, role, worst_usd)

    primary, fallback, small = (
        Binding(role, make_provider(role))
        for role in (settings.role(n) for n in ("primary", "fallback", "small"))
    )
    limiter = Limiter(is_spent)
    claude, triage = settings.claude(), settings.triage()
    on_rate_limit = settings.llm_fallback_on_rate_limit
    sim = Router(
        primary,
        fallback,
        small,
        fallback_on_rate_limit=on_rate_limit,
        limiter=limiter,
        claude=Binding(claude, make_provider(claude)) if claude else None,
    )
    if triage is None and settings.live_tenants:
        log.warning("live triage runs on the free models only: ANTHROPIC_API_KEY is not set")
    live = Router(
        primary,
        fallback,
        small,
        fallback_on_rate_limit=on_rate_limit,
        limiter=limiter,
        claude=Binding(triage, make_provider(triage)) if triage else None,
        claude_first=True,
    )
    return sim, live


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings(Settings)
    configure_logging("agent", settings.log_level)
    engine = create_engine(
        settings.agent_database_url,
        pool_pre_ping=True,
        # Every job in flight holds one session for its whole run, and the relay needs one more.
        pool_size=settings.agent_max_in_flight + 3,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"},
    )
    sqs = sqs_client(settings)
    relay = Relay(engine, sqs_sender(sqs, settings.agent_events_queue))
    # With AI_ENABLED=false every investigation parks before its first model turn, so these
    # placeholders are never asked for one.
    model: Model
    live_model: Model
    model, live_model = (
        make_routers(settings, engine) if settings.ai_enabled else (ScriptedModel([]),) * 2
    )
    sim = httpx2.AsyncClient(base_url=settings.sim_url, timeout=SIM_TIMEOUT)
    token = settings.agent_service_token.get_secret_value()
    alerts = AlertContextClient(settings.gateway_internal_url, token)
    cache = settings.fastembed_cache_path
    runbooks = RunbookSearch(engine, FastEmbedder(cache)) if cache else None
    summarize = model.summarize if isinstance(model, Router) else None
    job = Job(
        engine,
        model,
        Tools(sim, alerts, runbooks, summarize).all(),
        ai_enabled=settings.ai_enabled,
        daily_token_budget=settings.daily_ai_token_budget,
        tenant_cap=settings.agent_tenant_cap,
        token_cap=settings.investigation_token_cap,
        dual_write=settings.steps_dual_write,
        on_commit=relay.nudge,
        live_tenants=settings.live_tenants,
        live_tools=live_tools(alerts, runbooks),
        live_model=live_model,
        live_token_cap=settings.live_investigation_token_cap,
        live_daily_token_budget=settings.live_daily_ai_token_budget,
    )
    consumer = Consumer(
        sqs, settings.investigations_queue, job, max_in_flight=settings.agent_max_in_flight
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        stopping = asyncio.Event()
        consuming = asyncio.create_task(consumer.run(stopping))
        relaying = asyncio.create_task(relay.run(stopping))
        stop_maintenance = threading.Event()
        threading.Thread(
            target=maintain_partitions,
            args=(engine, stop_maintenance),
            name="partitions",
            daemon=True,
        ).start()
        threading.Thread(
            target=maintain_retention,
            args=(engine, set(settings.live_tenants), stop_maintenance),
            name="retention",
            daemon=True,
        ).start()
        yield
        # Uvicorn ends the lifespan on SIGTERM. Each job finishes and records its current step,
        # then exits without deleting its message, which redelivers once its visibility lapses.
        stopping.set()
        stop_maintenance.set()
        await consuming
        await relaying
        await sim.aclose()
        alerts.close()
        engine.dispose()

    app = FastAPI(title="agent", lifespan=lifespan)
    instrument(app, "agent", settings.otel_exporter_otlp_endpoint)
    add_internal_api(app, job, settings.agent_service_token)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        try:
            with engine.connect() as connection:
                connection.execute(text("select 1"))
            sqs.get_queue_url(QueueName=settings.investigations_queue)
        except Exception:
            log.warning("readiness check failed", exc_info=True)
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    return app


def run() -> None:
    """Console entry point; pass --reload during development."""
    try:
        settings = load_settings(Settings)
    except ConfigError as error:
        sys.exit(f"agent: {error}")
    configure_logging("agent", settings.log_level)
    uvicorn.run(
        "agent.main:create_app",
        factory=True,
        host=settings.agent_host,
        port=settings.agent_port,
        reload="--reload" in sys.argv,
        log_config=None,
    )
