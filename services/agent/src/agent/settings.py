from typing import Annotated

from pydantic import Field, SecretStr

from py_config import EnvSettings
from py_telemetry import LogLevel


class Settings(EnvSettings):
    agent_host: str = "127.0.0.1"
    agent_port: int = 4210
    agent_database_url: str
    # Bearer token of the internal API (/internal/*); the gateway's recovery sweeper sends it.
    agent_service_token: Annotated[SecretStr, Field(min_length=32)]
    sqs_endpoint_url: str | None = None
    aws_region: str = "us-east-1"
    investigations_queue: str = "investigations.fifo"
    # Messages one agent process works on at once, and investigations one tenant runs at once.
    # SQS returns at most 10 messages per receive.
    agent_max_in_flight: Annotated[int, Field(ge=1, le=10)] = 5
    agent_tenant_cap: Annotated[int, Field(ge=1)] = 5
    # false stops every model call. New investigations park (waiting for "budget") while it is
    # off or the model tokens spent today (UTC) reach the budget; others park at their next turn
    # while it is off.
    ai_enabled: bool = True
    daily_ai_token_budget: Annotated[int, Field(ge=0)] = 1_000_000
    # Also write steps to investigation_steps_p while it is partitioned online (docs/migrations.md).
    steps_dual_write: bool = False
    log_level: LogLevel = "info"
    otel_exporter_otlp_endpoint: str | None = None
