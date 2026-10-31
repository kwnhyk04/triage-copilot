from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator

from agent.pricing import RATES
from py_config import EnvSettings
from py_telemetry import LogLevel

Provider = Literal["gemini", "anthropic", "nvidia", "openrouter", "ollama"]
# The roles a model chain is built from, each with its own LLM_<ROLE>_* settings.
ChainRole = Literal["primary", "fallback", "small"]
# planner: the plan for a real incident's fix (P2), whichever model of its chain makes it.
RoleName = Literal[ChainRole, "planner"]
# Claude's output_config.effort.
Effort = Literal["low", "medium", "high", "xhigh", "max"]
# The apps whose real incidents TC triages (docs/context.md, Real Incidents).
LiveApp = Literal["freight-audit", "filing-lens"]
ROLES: tuple[ChainRole, ...] = ("primary", "fallback", "small")
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
    # Every Claude role: what its calls may spend in a UTC day, in US dollars (agent.pricing); the
    # planner's under its own cap, every other one under the triage cap.
    daily_usd: float | None = None
    # Every Claude role: what all of TC's Claude calls may spend in a UTC month, evals included.
    monthly_usd: float | None = None
    # The planner's Claude: how hard it thinks; None leaves the model's default.
    effort: Effort | None = None


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
    # Model tokens one investigation may use, cached input included, in thousands (report names
    # carry it); below 60,000 the final report's reserve stops it before its first turn.
    investigation_token_cap: Annotated[int, Field(ge=60_000, multiple_of=1_000)] = 150_000
    # Tenants whose alerts are real incidents, each with the app it watches, as JSON
    # {"<tenant uuid>": "freight-audit"}; every other tenant's alerts come from the sim.
    live_tenants: dict[UUID, LiveApp] = Field(default_factory=dict[UUID, LiveApp])
    # Live triage's own token cap and daily budget, so Claude is never cut off mid-triage (sized
    # from Haiku's second test-split run) and the sim's runs keep the eval's.
    live_investigation_token_cap: Annotated[int, Field(ge=60_000, multiple_of=1_000)] = 350_000
    live_daily_ai_token_budget: Annotated[int, Field(ge=0)] = 25_000_000
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
    # The sim's chain asks Claude (LLM_TRIAGE_MODEL) first, as live triage's does, then the free
    # chain. Off by default: the free tiers stay the default (AGENTS.md).
    llm_claude_first: bool = False
    llm_claude_rpm: Annotated[int, Field(ge=0)] = 50
    llm_claude_until: date = date(2026, 11, 10)
    # Live triage asks Claude first, while ANTHROPIC_API_KEY is set and before LLM_CLAUDE_UNTIL,
    # then the free chain; so does the sim with LLM_CLAUDE_FIRST. Any Claude call but a plan runs
    # only if today's Claude spend (UTC) plus the call's worst case stays within
    # LLM_TRIAGE_DAILY_USD.
    llm_triage_model: str = "claude-haiku-5-5"
    llm_triage_daily_usd: Annotated[float, Field(ge=0)] = 0.40
    # TC's share of the Anthropic account a calendar month (UTC), evals included: any Claude call
    # runs only if this month's Claude spend plus the call's worst case stays within it.
    llm_claude_monthly_usd: Annotated[float, Field(ge=0)] = 15.0
    # The plan for a real incident's fix asks Claude first, while ANTHROPIC_API_KEY is set and
    # before LLM_CLAUDE_UNTIL, then the free chain. A Claude plan runs only if today's plans (UTC)
    # plus its worst case stay within LLM_PLANNER_DAILY_USD.
    llm_planner_model: str = "claude-opus-5-5"
    llm_planner_effort: Effort = "medium"
    llm_planner_daily_usd: Annotated[float, Field(ge=0)] = 1.00
    gemini_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    nvidia_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    # Local embedding model cache for search_runbooks; it must be set (on H:) whenever model calls
    # are on, so fastembed never falls back to a temp dir on C:.
    fastembed_cache_path: Path | None = None
    runbooks_dir: Path = Path("runbooks")

    @field_validator("llm_triage_model", "llm_planner_model")
    @classmethod
    def _priced(cls, model: str) -> str:
        # The dollar cap cannot be checked without the model's prices.
        if model not in RATES:
            raise ValueError(f"takes a model agent/pricing.py has prices for: {', '.join(RATES)}")
        return model

    @model_validator(mode="after")
    def _claude_priced(self) -> "Settings":
        """TC's monthly Claude cap needs the prices of every Claude model a role may call."""
        models = [
            getattr(self, f"llm_{name}_model")
            for name in ROLES
            if getattr(self, f"llm_{name}_provider") == "anthropic"
        ]
        for model in models:
            if model not in RATES:
                raise ValueError(f"{model} has no prices in agent/pricing.py: {', '.join(RATES)}")
        return self

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
        if self.llm_claude_first and self.anthropic_api_key is None:
            raise ValueError("ANTHROPIC_API_KEY is required by LLM_CLAUDE_FIRST")
        return self

    def role(self, name: ChainRole) -> Role:
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
            daily_usd=self.llm_triage_daily_usd if provider == "anthropic" else None,
            monthly_usd=self.llm_claude_monthly_usd if provider == "anthropic" else None,
        )

    def triage(self) -> Role | None:
        """The Claude every turn and summary asks first (live triage's, and the sim's with
        LLM_CLAUDE_FIRST), when ANTHROPIC_API_KEY is set."""
        secret = self.anthropic_api_key
        if secret is None:
            return None
        return Role(
            name="primary",
            provider="anthropic",
            model=self.llm_triage_model,
            base_url=None,
            rpm=self.llm_claude_rpm,
            rpd=0,
            api_key=secret.get_secret_value(),
            until=self.llm_claude_until,
            daily_usd=self.llm_triage_daily_usd,
            monthly_usd=self.llm_claude_monthly_usd,
        )

    def planner(self) -> Role | None:
        """The planner's Claude, first in its chain, when ANTHROPIC_API_KEY is set."""
        secret = self.anthropic_api_key
        if secret is None:
            return None
        return Role(
            name="planner",
            provider="anthropic",
            model=self.llm_planner_model,
            base_url=None,
            rpm=self.llm_claude_rpm,
            rpd=0,
            api_key=secret.get_secret_value(),
            until=self.llm_claude_until,
            daily_usd=self.llm_planner_daily_usd,
            monthly_usd=self.llm_claude_monthly_usd,
            effort=self.llm_planner_effort,
        )
