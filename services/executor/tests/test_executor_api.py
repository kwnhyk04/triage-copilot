import pytest
from fastapi.testclient import TestClient

from executor.main import Settings, create_app
from py_config import load_settings


def test_health_endpoints() -> None:
    client = TestClient(create_app(Settings()))
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ok"}


def test_settings_hold_no_approver_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # The executor runs approved actions, so it must never be able to approve one itself.
    monkeypatch.setenv("APPROVER_API_KEY", "approver-key-the-executor-must-not-load")
    settings = load_settings(Settings)
    assert not [name for name in Settings.model_fields if "approver" in name]
    assert "approver-key-the-executor-must-not-load" not in settings.model_dump_json()
