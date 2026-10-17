from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, model_validator

from py_config import EnvSettings
from py_telemetry import LogLevel

Provider = Literal["gemini", "anthropic", "nvidia", "openrouter", "ollama"]
RoleName = Literal["primary", "fallback", "small"]
ROLES: tuple[RoleName, ...] = ("primary", "fallback", "small")
# Each provider's key setting; ollama runs locally without one.
KEY_SETTINGS: dict[Provider, str | None] = {
    "gemini": "gemini_api_key",
    "anthropic": "anthropic_api_key",
    "nvidia": "nvidia_api_key",
    "openrouter": "openrouter_api_key",
    "ollama": None,
}


@dataclass(frozen=True)
class Role:
    """One model role: where to call it, with which key, its free-tier limits (0: none), and for
    Claude the day its API credits expire, from which on (UTC) it is never called."""

    name: RoleName
    provider: Provider
    model: str
    base_url: str | None
    rpm: int
    rpd: int
    api_key: str | None = field(default=None, repr=False)
    until: date | None = None


class Settings(EnvSettings):
    agent_host: str = "127.0.0.1"
    agent_port: int = 4210
    agent_database_url: str
    # Bearer token of the internal API (/internal/*); the gateway's recovery sweeper sends it. The
    # agent also sends it to the gateway's internal listener for alert context.
    agent_service_token: Annotated[SecretStr, Field(min_length=32)]
    sqs_endpoint_url: str | None = None
    aws_region: str = "us-east-1"
    investigations_queue: str = "investigations.fifo"
    # StepRecorded and ReportRecorded for the gateway's mirror tables.
    agent_events_queue: str = "agent-events.fifo"
    # Messages one agent process works on at once, and investigations one tenant runs at once.
    # SQS returns at most 10 messages per receive.
    agent_max_in_flight: Annotated[int, Field(ge=1, le=10)] = 5
    agent_tenant_cap: Annotated[int, Field(ge=1)] = 5
    # false stops every model call. New investigations park (waiting for "budget") while it is
    # off or the model tokens spent today (UTC) reach the budget; others park at their next turn
    # while it is off.
    ai_enabled: bool = False
    daily_ai_token_budget: Annotated[int, Field(ge=0)] = 1_000_000
    # Also write steps to investigation_steps_p while it is partitioned online (docs/migrations.md).
    steps_dual_write: bool = False
    log_level: LogLevel = "info"
    otel_exporter_otlp_endpoint: str | None = None
    gateway_internal_url: str = "http://127.0.0.1:4201"
    sim_url: str = "http://127.0.0.1:4230"
    llm_primary_provider: Provider = "gemini"
    llm_primary_model: str = "gemini-3.5-flash-lite"
    llm_primary_base_url: str | None = None
    llm_primary_rpm: Annotated[int, Field(ge=0)] = 10
    # Google answered "daily quota spent" after 275 requests on 2026-10-16 (measured, not a
    # documented limit); stop a little short, so the agent parks before a 429.
    llm_primary_rpd: Annotated[int, Field(ge=0)] = 260
    llm_fallback_provider: Provider = "nvidia"
    llm_fallback_model: str = "deepseek-ai/deepseek-v4.1-flash"
    llm_fallback_base_url: str | None = None
    llm_fallback_rpm: Annotated[int, Field(ge=0)] = 40
    llm_fallback_rpd: Annotated[int, Field(ge=0)] = 0
    llm_small_provider: Provider = "gemini"
    llm_small_model: str = "gemini-3.1-flash-lite"
    llm_small_base_url: str | None = None
    llm_small_rpm: Annotated[int, Field(ge=0)] = 10
    llm_small_rpd: Annotated[int, Field(ge=0)] = 500
    # A 429 or a spent daily quota on the primary moves the turn to the fallback; evals set false.
    llm_fallback_on_rate_limit: bool = True
    # Claude as the first fallback of every role, after Gemini and before the free fallbacks,
    # while its API credits last and before LLM_CLAUDE_UNTIL (UTC). Off by default: free only.
    llm_claude_fallback: bool = False
    llm_claude_model: str = "claude-haiku-5-5"
    llm_claude_rpm: Annotated[int, Field(ge=0)] = 50
    llm_claude_until: date = date(2026, 11, 10)
    gemini_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    nvidia_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    # Local embedding model cache for search_runbooks; it must be set (on H:) whenever model calls
    # are on, so fastembed never falls back to a temp dir on C:.
    fastembed_cache_path: Path | None = None
    runbooks_dir: Path = Path("runbooks")

    @model_validator(mode="after")
    def _needs_for_model_calls(self) -> "Settings":
        """With model calls on, every role's provider needs its key, and runbooks need the cache."""
        if not self.ai_enabled:
            return self
        if self.fastembed_cache_path is None:
            raise ValueError("FASTEMBED_CACHE_PATH is required when AI_ENABLED is true")
        for name in ROLES:
            key = KEY_SETTINGS[getattr(self, f"llm_{name}_provider")]
            if key is not None and getattr(self, key) is None:
                raise ValueError(f"{key.upper()} is required by LLM_{name.upper()}_PROVIDER")
        if self.llm_claude_fallback and self.anthropic_api_key is None:
            raise ValueError("ANTHROPIC_API_KEY is required by LLM_CLAUDE_FALLBACK")
        return self

    def role(self, name: RoleName) -> Role:
        provider: Provider = getattr(self, f"llm_{name}_provider")
        key = KEY_SETTINGS[provider]
        secret: SecretStr | None = getattr(self, key) if key else None
        return Role(
            name=name,
            provider=provider,
            model=getattr(self, f"llm_{name}_model"),
            base_url=getattr(self, f"llm_{name}_base_url"),
            rpm=getattr(self, f"llm_{name}_rpm"),
            rpd=getattr(self, f"llm_{name}_rpd"),
            api_key=secret.get_secret_value() if secret else None,
            until=self.llm_claude_until if provider == "anthropic" else None,
        )

    def claude(self) -> Role | None:
        """The Claude fallback, when LLM_CLAUDE_FALLBACK turns it on; it has no daily cap."""
        if not self.llm_claude_fallback:
            return None
        secret = self.anthropic_api_key
        return Role(
            name="fallback",
            provider="anthropic",
            model=self.llm_claude_model,
            base_url=None,
            rpm=self.llm_claude_rpm,
            rpd=0,
            api_key=secret.get_secret_value() if secret else None,
            until=self.llm_claude_until,
        )
