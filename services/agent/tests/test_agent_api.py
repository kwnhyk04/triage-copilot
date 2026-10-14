import pytest
from fastapi.testclient import TestClient

from agent.main import create_app
from agent.settings import Settings
from py_config import load_settings

# Nothing listens on port 1, so dependency checks fail fast without infra.
OFFLINE = Settings(
    agent_database_url="postgresql+psycopg://agent:unused@127.0.0.1:1/triage",
    sqs_endpoint_url="http://127.0.0.1:1",
)


def test_healthz() -> None:
    response = TestClient(create_app(OFFLINE)).get("/healthz")
    assert response.json() == {"status": "ok"}


def test_settings_hold_no_approver_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even if the key leaks into its environment, the agent's config has no place for it.
    monkeypatch.setenv("APPROVER_API_KEY", "approver-key-the-agent-must-not-load-0")
    monkeypatch.setenv("AGENT_DATABASE_URL", OFFLINE.agent_database_url)
    settings = load_settings(Settings)
    assert not [name for name in Settings.model_fields if "approver" in name]
    assert "approver-key-the-agent-must-not-load" not in settings.model_dump_json()


def test_readyz_is_unavailable_without_dependencies() -> None:
    response = TestClient(create_app(OFFLINE)).get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
