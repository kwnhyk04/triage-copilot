import pytest
from fastapi.testclient import TestClient

from py_config import load_settings
from sim.main import Settings, create_app


def test_health_endpoints() -> None:
    client = TestClient(create_app(Settings()))
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ok"}


def test_settings_hold_no_approver_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPROVER_API_KEY", "approver-key-the-sim-must-not-load-000")
    settings = load_settings(Settings)
    assert not [name for name in Settings.model_fields if "approver" in name]
    assert "approver-key-the-sim-must-not-load" not in settings.model_dump_json()
