import secrets
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from agent.main import create_app
from agent.settings import Settings
from py_config import ConfigError, load_settings

TOKEN = secrets.token_urlsafe(32)
# Nothing listens on port 1, so dependency checks fail fast without infra.
OFFLINE = Settings(
    agent_database_url="postgresql+psycopg://agent:unused@127.0.0.1:1/triage",
    sqs_endpoint_url="http://127.0.0.1:1",
    agent_service_token=SecretStr(TOKEN),
)
STATE = "/internal/investigations/1b4e28ba-2fa1-41d2-883f-0016d3cca427/state"


def test_healthz() -> None:
    response = TestClient(create_app(OFFLINE)).get("/healthz")
    assert response.json() == {"status": "ok"}


def test_settings_hold_no_approver_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even if the key leaks into its environment, the agent's config has no place for it.
    monkeypatch.setenv("APPROVER_API_KEY", "approver-key-the-agent-must-not-load-0")
    monkeypatch.setenv("AGENT_DATABASE_URL", OFFLINE.agent_database_url)
    monkeypatch.setenv("AGENT_SERVICE_TOKEN", TOKEN)
    settings = load_settings(Settings)
    assert not [name for name in Settings.model_fields if "approver" in name]
    assert "approver-key-the-agent-must-not-load" not in settings.model_dump_json()


def test_the_free_chain_is_the_default_and_claude_needs_its_key() -> None:
    assert OFFLINE.claude() is None
    keys = {"gemini_api_key": SecretStr("g"), "nvidia_api_key": SecretStr("n")}
    on: dict[str, Any] = {
        **OFFLINE.model_dump(),
        **keys,
        "ai_enabled": True,
        "fastembed_cache_path": Path("cache"),
        "llm_claude_fallback": True,
    }
    with pytest.raises(
        ValidationError, match="ANTHROPIC_API_KEY is required by LLM_CLAUDE_FALLBACK"
    ):
        Settings(**on)
    keyed: dict[str, Any] = {**on, "anthropic_api_key": SecretStr("sk-ant-test")}
    claude = Settings(**keyed).claude()
    assert claude is not None
    assert (claude.name, claude.provider, claude.model) == (
        "fallback",
        "anthropic",
        "claude-haiku-5-5",
    )
    assert (claude.until, claude.api_key) == (date(2026, 11, 10), "sk-ant-test")


def test_every_claude_role_has_tc_s_monthly_cap_and_needs_a_priced_model() -> None:
    keyed: dict[str, Any] = {
        **OFFLINE.model_dump(),
        "anthropic_api_key": SecretStr("sk-ant-test"),
        "llm_claude_fallback": True,
        "llm_small_provider": "anthropic",
        "llm_small_model": "claude-haiku-5-5",
    }
    settings = Settings(**keyed)
    claude, triage = settings.claude(), settings.triage()
    assert claude is not None and triage is not None
    # Evals included: any role on Anthropic counts against the same $15 a month.
    assert [r.monthly_usd for r in (claude, triage, settings.role("small"))] == [15.0] * 3
    assert settings.role("primary").monthly_usd is None
    lower: dict[str, Any] = {**keyed, "llm_claude_monthly_usd": 5}
    assert Settings(**lower).role("small").monthly_usd == 5
    unpriced: dict[str, Any] = {**keyed, "llm_small_model": "claude-sonnet-5-5"}
    with pytest.raises(ValidationError, match=r"claude-sonnet-5-5 has no prices in agent/"):
        Settings(**unpriced)


def test_the_planner_is_opus_at_medium_effort_under_its_own_daily_cap() -> None:
    assert OFFLINE.planner() is None
    keyed: dict[str, Any] = {**OFFLINE.model_dump(), "anthropic_api_key": SecretStr("sk-ant-test")}
    planner = Settings(**keyed).planner()
    assert planner is not None
    assert (planner.name, planner.provider, planner.model, planner.effort) == (
        "planner",
        "anthropic",
        "claude-opus-5-5",
        "medium",
    )
    # $1.00 a UTC day of its own, inside TC's $15 a month, until Claude's credits end.
    caps = (planner.daily_usd, planner.monthly_usd, planner.until)
    assert caps == (1.0, 15.0, date(2026, 11, 10))
    assert planner.api_key == "sk-ant-test"
    for bad in ({"llm_planner_model": "claude-sonnet-5-5"}, {"llm_planner_effort": "extreme"}):
        changed: dict[str, Any] = {**keyed, **bad}
        with pytest.raises(ValidationError):
            Settings(**changed)


def test_live_tenants_map_each_tenant_to_its_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_DATABASE_URL", OFFLINE.agent_database_url)
    monkeypatch.setenv("AGENT_SERVICE_TOKEN", TOKEN)
    monkeypatch.delenv("LIVE_TENANTS", raising=False)
    assert load_settings(Settings).live_tenants == {}
    tenant = "0b6f2d1e-3c4a-4f5e-8a9b-1c2d3e4f5a6b"
    monkeypatch.setenv("LIVE_TENANTS", f'{{"{tenant}": "freight-audit"}}')
    assert load_settings(Settings).live_tenants == {UUID(tenant): "freight-audit"}
    monkeypatch.setenv("LIVE_TENANTS", f'{{"{tenant}": "payroll"}}')
    with pytest.raises(ConfigError):
        load_settings(Settings)


def test_settings_refuse_a_missing_or_short_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_DATABASE_URL", OFFLINE.agent_database_url)
    monkeypatch.delenv("AGENT_SERVICE_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="AGENT_SERVICE_TOKEN is required"):
        load_settings(Settings)
    monkeypatch.setenv("AGENT_SERVICE_TOKEN", "too-short")
    with pytest.raises(ConfigError, match="AGENT_SERVICE_TOKEN: ") as raised:
        load_settings(Settings)
    assert "too-short" not in str(raised.value)


def test_readyz_is_unavailable_without_dependencies() -> None:
    response = TestClient(create_app(OFFLINE)).get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


@pytest.mark.parametrize(
    "authorization", [None, f"Bearer wrong-{TOKEN}", f"Basic {TOKEN}", f"Bearer {TOKEN} extra"]
)
def test_internal_api_needs_the_service_token(authorization: str | None) -> None:
    headers = {} if authorization is None else {"Authorization": authorization}
    response = TestClient(create_app(OFFLINE)).get(STATE, headers=headers)

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {
        "title": "Unauthorized",
        "status": 401,
        "detail": "Send a valid service token.",
    }


def test_internal_api_answers_a_bad_id_with_problem_details() -> None:
    client = TestClient(create_app(OFFLINE), headers={"Authorization": f"Bearer {TOKEN}"})
    response = client.get("/internal/investigations/not-a-uuid/state")

    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["detail"].startswith("path.investigation_id: ")
