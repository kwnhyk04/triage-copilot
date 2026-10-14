import asyncio
import logging
import sys
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text

from agent.consumer import Consumer, sqs_client
from agent.job import Job
from agent.model import Report, ScriptedModel
from agent.partitions import maintain_partitions
from agent.settings import Settings
from py_config import ConfigError, load_settings
from py_telemetry import configure_logging, instrument

log = logging.getLogger(__name__)

# Until build step 6 adds the model providers and tools, each investigation ends with this report.
PLACEHOLDER = Report(
    summary="Not investigated: the AI pipeline arrives in build step 6.", hypotheses=[]
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings(Settings)
    configure_logging("agent", settings.log_level)
    engine = create_engine(
        settings.agent_database_url,
        pool_pre_ping=True,
        # Every job in flight holds one session for its whole run.
        pool_size=settings.agent_max_in_flight + 2,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"},
    )
    sqs = sqs_client(settings)
    job = Job(
        engine,
        ScriptedModel([PLACEHOLDER]),
        {},
        ai_enabled=settings.ai_enabled,
        daily_token_budget=settings.daily_ai_token_budget,
        tenant_cap=settings.agent_tenant_cap,
        dual_write=settings.steps_dual_write,
    )
    consumer = Consumer(
        sqs, settings.investigations_queue, job, max_in_flight=settings.agent_max_in_flight
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        stopping = asyncio.Event()
        consuming = asyncio.create_task(consumer.run(stopping))
        stop_partitions = threading.Event()
        threading.Thread(
            target=maintain_partitions,
            args=(engine, stop_partitions),
            name="partitions",
            daemon=True,
        ).start()
        yield
        # Uvicorn ends the lifespan on SIGTERM. Each job finishes and records its current step,
        # then exits without deleting its message, which redelivers once its visibility lapses.
        stopping.set()
        stop_partitions.set()
        await consuming
        engine.dispose()

    app = FastAPI(title="agent", lifespan=lifespan)
    instrument(app, "agent", settings.otel_exporter_otlp_endpoint)

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
