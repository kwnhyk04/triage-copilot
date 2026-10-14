import logging
import sys
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import boto3
import uvicorn
from botocore.config import Config
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text

from agent.consumer import WAIT_SECONDS, consume
from agent.partitions import maintain_partitions
from agent.settings import Settings
from py_clients.generated.messages.investigation_requested_v1_schema import InvestigationRequestedV1
from py_config import ConfigError, load_settings
from py_telemetry import configure_logging, instrument

if TYPE_CHECKING:
    # Stub-only package from the dev group; never imported at runtime.
    from types_boto3_sqs import SQSClient

log = logging.getLogger(__name__)


def handle(request: InvestigationRequestedV1) -> None:
    """Records the request; the investigation loop replaces this in build step 6."""
    log.info("investigation requested", extra={"investigation_id": str(request.investigation_id)})


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings(Settings)
    configure_logging("agent", settings.log_level)
    engine = create_engine(
        settings.agent_database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"},
    )
    sqs: SQSClient = boto3.client(
        "sqs",
        endpoint_url=settings.sqs_endpoint_url,
        region_name=settings.aws_region,
        config=Config(
            connect_timeout=5, read_timeout=WAIT_SECONDS + 10, retries={"max_attempts": 2}
        ),
    )
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        worker = threading.Thread(
            target=consume,
            args=(sqs, settings.investigations_queue, handle, stop),
            name="consumer",
            daemon=True,
        )
        worker.start()
        threading.Thread(
            target=maintain_partitions, args=(engine, stop), name="partitions", daemon=True
        ).start()
        yield
        stop.set()
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
