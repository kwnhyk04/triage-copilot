import asyncio
import logging
import sys
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import Field, SecretStr, ValidationInfo, field_validator

from py_config import ConfigError, EnvSettings, load_settings
from py_telemetry import LogLevel, configure_logging, instrument
from watch.catalog import CATALOGS, AppName, Stack
from watch.poll import Target, Watcher
from watch.readers import Refused, check_url, safe_argument
from watch.scrub import mask

log = logging.getLogger(__name__)

Token = Annotated[SecretStr, Field(min_length=32)]


def _url(url: str) -> str:
    try:
        check_url(url)
    except Refused:
        raise ValueError(
            "needs plain http or https URLs to a host, without credentials or a fragment"
        ) from None
    if urlsplit(url).query:
        raise ValueError("takes no query string: a key in it would end up in snapshots")
    return url.rstrip("/")


class Settings(EnvSettings):
    watch_host: str = "127.0.0.1"
    watch_port: int = 4240
    # Seconds between polls; a health check fires after three failed polls in a row.
    watch_interval_seconds: Annotated[float, Field(ge=5)] = 30
    # The gateway's public listener, whose webhook takes the alerts.
    gateway_url: str = "http://127.0.0.1:4200"
    # An app is watched only when its tenant's webhook token is set (gateway `tenant:token`).
    # Its stack is prod or, once that dev stack opts in, dev. Health URLs are JSON by service,
    # such as {"api": "https://<funnel host>/readyz"}; the repo gives origin/main's commits as
    # last fetched; the Prometheus URL is a dev stack's own.
    watch_fa_token: Token | None = None
    watch_fa_stack: Stack = "prod"
    watch_fa_health_urls: dict[str, str] = Field(default_factory=dict[str, str])
    watch_fa_repo: Path | None = None
    watch_fa_prometheus_url: str | None = None
    watch_fl_token: Token | None = None
    watch_fl_stack: Stack = "prod"
    watch_fl_health_urls: dict[str, str] = Field(default_factory=dict[str, str])
    watch_fl_repo: Path | None = None
    watch_fl_prometheus_url: str | None = None
    log_level: LogLevel = "info"
    otel_exporter_otlp_endpoint: str | None = None

    @field_validator("watch_fa_health_urls", "watch_fl_health_urls")
    @classmethod
    def _health_urls(cls, urls: dict[str, str], info: ValidationInfo) -> dict[str, str]:
        fa = info.field_name == "watch_fa_health_urls"
        app: AppName = "freight-audit" if fa else "filing-lens"
        services = [service.name for service in CATALOGS[app].services]
        if set(urls) - set(services):
            raise ValueError(f"takes only {app} services: {', '.join(services)}")
        return {service: _url(url) for service, url in urls.items()}

    @field_validator("gateway_url", "watch_fa_prometheus_url", "watch_fl_prometheus_url")
    @classmethod
    def _plain_url(cls, url: str | None) -> str | None:
        return None if url is None else _url(url)

    @field_validator("watch_fa_repo", "watch_fl_repo")
    @classmethod
    def _repo(cls, repo: Path | None) -> Path | None:
        # git gets it on its command line, where watch allows no character a shell would read.
        if repo is not None and not safe_argument(str(repo)):
            raise ValueError("takes a path of letters, digits, spaces and . _ - : / \\ only")
        return repo

    def targets(self) -> list[Target]:
        """The apps whose webhook token is set; the others are not watched."""
        apps: list[tuple[AppName, Token | None, Stack, dict[str, str], Path | None, str | None]] = [
            (
                "freight-audit",
                self.watch_fa_token,
                self.watch_fa_stack,
                self.watch_fa_health_urls,
                self.watch_fa_repo,
                self.watch_fa_prometheus_url,
            ),
            (
                "filing-lens",
                self.watch_fl_token,
                self.watch_fl_stack,
                self.watch_fl_health_urls,
                self.watch_fl_repo,
                self.watch_fl_prometheus_url,
            ),
        ]
        return [
            Target(CATALOGS[app], stack, token.get_secret_value(), health, repo, prometheus)
            for app, token, stack, health, repo, prometheus in apps
            if token
        ]


@dataclass
class Status:
    # time.monotonic() when the last poll of every app finished, and whether each saw everything.
    polled_at: float | None = None
    ok: bool = False


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings(Settings)
    configure_logging("watch", settings.log_level)
    watchers = [Watcher(target, settings.gateway_url) for target in settings.targets()]
    if not watchers:
        log.warning("no app is watched: set WATCH_FA_TOKEN or WATCH_FL_TOKEN")
    interval = settings.watch_interval_seconds
    status = Status()

    async def watch(stopping: asyncio.Event) -> None:
        while not stopping.is_set():
            ok = True
            for watcher in watchers:
                try:
                    await asyncio.to_thread(watcher.poll, datetime.now(UTC))
                except Exception as error:  # one app's failed poll never stops the others
                    extra = {"app": watcher.app, "error": mask(repr(error))}
                    log.warning("poll failed", extra=extra)
                    ok = False
                else:
                    ok = ok and watcher.ok
            status.polled_at, status.ok = time.monotonic(), ok
            with suppress(TimeoutError):
                await asyncio.wait_for(stopping.wait(), interval)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        stopping = asyncio.Event()
        watching = asyncio.create_task(watch(stopping))
        yield
        stopping.set()
        await watching
        for watcher in watchers:
            watcher.close()

    app = FastAPI(title="watch", lifespan=lifespan)
    instrument(app, "watch", settings.otel_exporter_otlp_endpoint)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        """Ready while the last poll, at most three intervals ago, read and posted everything."""
        polled = status.polled_at
        if polled is not None and time.monotonic() - polled <= 3 * interval and status.ok:
            return JSONResponse({"status": "ok"})
        return JSONResponse({"status": "unavailable"}, status_code=503)

    return app


def run() -> None:
    """Console entry point."""
    try:
        settings = load_settings(Settings)
    except ConfigError as error:
        sys.exit(f"watch: {error}")
    configure_logging("watch", settings.log_level)
    uvicorn.run(
        "watch.main:create_app",
        factory=True,
        host=settings.watch_host,
        port=settings.watch_port,
        log_config=None,
    )
