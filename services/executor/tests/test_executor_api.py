import secrets
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from executor.main import Settings, create_app
from py_config import load_settings

TOKEN = secrets.token_urlsafe(32)


class Healthy(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200 if self.path == "/healthz" else 404)
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        """Quiet."""


@pytest.fixture
def gateway() -> Iterator[str]:
    """The gateway's internal listener, as far as /readyz asks it."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Healthy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def settings(**fields: str) -> Settings:
    return Settings(executor_service_token=SecretStr(TOKEN), **fields)  # pyright: ignore[reportArgumentType]


def test_ready_only_while_the_gateway_s_internal_listener_answers(gateway: str) -> None:
    up = TestClient(create_app(settings(gateway_internal_url=gateway), consume=False))
    assert up.get("/healthz").json() == {"status": "ok"}
    assert up.get("/readyz").json() == {"status": "ok"}
    offline = settings(gateway_internal_url="http://127.0.0.1:1")
    down = TestClient(create_app(offline, consume=False))
    assert down.get("/healthz").status_code == 200
    assert down.get("/readyz").status_code == 503


def test_dry_run_and_the_dev_stack_are_the_defaults() -> None:
    settings = load_settings(Settings)
    assert (settings.executor_dry_run, settings.executor_stack) == (True, "dev")


def test_settings_hold_no_approver_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # The executor runs approved actions, so it must never be able to approve one itself.
    monkeypatch.setenv("APPROVER_API_KEY", "approver-key-the-executor-must-not-load")
    settings = load_settings(Settings)
    assert not [name for name in Settings.model_fields if "approver" in name]
    assert "approver-key-the-executor-must-not-load" not in settings.model_dump_json()
