import sys

import uvicorn
from fastapi import FastAPI

from py_config import ConfigError, EnvSettings, load_settings
from py_telemetry import LogLevel, configure_logging, instrument


class Settings(EnvSettings):
    executor_host: str = "127.0.0.1"
    executor_port: int = 4220
    log_level: LogLevel = "info"
    otel_exporter_otlp_endpoint: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings(Settings)
    configure_logging("executor", settings.log_level)
    app = FastAPI(title="executor")
    instrument(app, "executor", settings.otel_exporter_otlp_endpoint)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        # No dependencies yet; approval and action checks arrive with the executor build step.
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
