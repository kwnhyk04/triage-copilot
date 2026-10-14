"""JSON logs and optional OpenTelemetry tracing for the Python services."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

LogLevel = Literal["debug", "info", "warn", "error"]

# Attributes every LogRecord has, plus uvicorn's ANSI-colored copy of the message. Anything else
# came from `extra=` and is logged as a field.
_STANDARD = {
    *vars(logging.LogRecord("", 0, "", 0, "", None, None)),
    "message",
    "asctime",
    "color_message",
}


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(service: str, level: LogLevel) -> None:
    """Sends every logger, uvicorn's included, to stdout as JSON."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)


def instrument(app: FastAPI, service: str, otlp_endpoint: str | None) -> None:
    """Traces requests over OTLP/HTTP when an endpoint is set; otherwise does nothing."""
    if not otlp_endpoint:
        return
    exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces", timeout=5)
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
