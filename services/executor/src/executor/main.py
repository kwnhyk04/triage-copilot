import sys
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

import boto3
import httpx2
import uvicorn
from botocore.config import Config
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import Field, SecretStr

from executor.consumer import consume
from executor.restart import restart_effector
from executor.run import run_action
from py_clients.clients import ExecutionsClient
from py_config import ConfigError, EnvSettings, load_settings
from py_telemetry import LogLevel, configure_logging, instrument
from watch.catalog import Stack


class Settings(EnvSettings):
    executor_host: str = "127.0.0.1"
    executor_port: int = 4220
    log_level: LogLevel = "info"
    otel_exporter_otlp_endpoint: str | None = None
    # The gateway's internal listener, where approved actions are claimed and their ends recorded.
    executor_service_token: Annotated[SecretStr, Field(min_length=32)]
    gateway_internal_url: str = "http://127.0.0.1:4201"
    # True records each approved restart's command and runs nothing; the claim is used up anyway.
    executor_dry_run: bool = True
    # The stack whose containers a restart acts on (catalog projects); production runs no executor.
    executor_stack: Stack = "dev"
    sqs_endpoint_url: str | None = None
    aws_region: str = "us-east-1"
    actions_queue: str = "actions.fifo"


def start_consumer(settings: Settings, stopping: threading.Event) -> threading.Thread:
    """Reads actions.fifo in a thread of its own: each message is run_action'd, then deleted."""
    config = Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2})
    sqs = boto3.client(
        "sqs",
        endpoint_url=settings.sqs_endpoint_url,
        region_name=settings.aws_region,
        config=config,
    )
    queue_url = sqs.get_queue_url(QueueName=settings.actions_queue)["QueueUrl"]
    token = settings.executor_service_token.get_secret_value()
    gateway = ExecutionsClient(settings.gateway_internal_url, token, timeout=10)
    effectors = {"restart_service": restart_effector()}

    def handle(body: str) -> None:
        run_action(
            body,
            gateway,
            dry_run=settings.executor_dry_run,
            effectors=effectors,
            stack=settings.executor_stack,
        )

    thread = threading.Thread(
        target=consume, args=(sqs, queue_url, handle, stopping), name="actions", daemon=True
    )
    thread.start()
    return thread


def create_app(settings: Settings | None = None, *, consume: bool = True) -> FastAPI:
    settings = settings or load_settings(Settings)
    configure_logging("executor", settings.log_level)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        stopping = threading.Event()
        thread = start_consumer(settings, stopping) if consume else None
        yield
        stopping.set()
        if thread is not None:
            thread.join(30)

    app = FastAPI(title="executor", lifespan=lifespan)
    instrument(app, "executor", settings.otel_exporter_otlp_endpoint)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", response_model=None)
    def readyz() -> dict[str, str] | JSONResponse:
        # Ready while the gateway's internal listener, where every claim goes, answers.
        try:
            up = httpx2.get(f"{settings.gateway_internal_url}/healthz", timeout=2).status_code
        except httpx2.HTTPError:
            up = None
        if up != 200:
            body = {"title": "Service Unavailable", "status": 503, "detail": "gateway internal"}
            return JSONResponse(body, 503, media_type="application/problem+json")
        return {"status": "ok"}

    return app


def run() -> None:
    """Console entry point; pass --reload during development."""
    try:
        settings = load_settings(Settings)
    except ConfigError as error:
        sys.exit(f"executor: {error}")
    configure_logging("executor", settings.log_level)
    uvicorn.run(
        "executor.main:create_app",
        factory=True,
        host=settings.executor_host,
        port=settings.executor_port,
        reload="--reload" in sys.argv,
        log_config=None,
    )
