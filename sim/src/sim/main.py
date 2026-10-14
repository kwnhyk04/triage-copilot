import sys

import uvicorn
from fastapi import FastAPI

from py_config import ConfigError, EnvSettings, load_settings
from py_telemetry import LogLevel, configure_logging, instrument


class Settings(EnvSettings):
    sim_host: str = "127.0.0.1"
    sim_port: int = 4230
    log_level: LogLevel = "info"
    otel_exporter_otlp_endpoint: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings(Settings)
    configure_logging("sim", settings.log_level)
    app = FastAPI(title="sim")
    instrument(app, "sim", settings.otel_exporter_otlp_endpoint)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        # Scenarios load from files once the scenario engine exists; nothing to check yet.
        return {"status": "ok"}

    return app


def run() -> None:
    """Console entry point; pass --reload during development."""
    try:
        settings = load_settings(Settings)
    except ConfigError as error:
        sys.exit(f"sim: {error}")
    configure_logging("sim", settings.log_level)
    uvicorn.run(
        "sim.main:create_app",
        factory=True,
        host=settings.sim_host,
        port=settings.sim_port,
        reload="--reload" in sys.argv,
        log_config=None,
    )
