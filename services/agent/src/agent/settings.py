from py_config import EnvSettings
from py_telemetry import LogLevel


class Settings(EnvSettings):
    agent_host: str = "127.0.0.1"
    agent_port: int = 4210
    agent_database_url: str
    sqs_endpoint_url: str | None = None
    aws_region: str = "us-east-1"
    investigations_queue: str = "investigations.fifo"
    log_level: LogLevel = "info"
    otel_exporter_otlp_endpoint: str | None = None
