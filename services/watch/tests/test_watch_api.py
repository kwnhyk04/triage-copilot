import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from watch_fakes import FakeServer, FakeTools, Route, ps_row

from py_config import ConfigError, load_settings
from watch.catalog import CATALOGS
from watch.main import Settings, create_app

# A planted webhook token, long enough for the setting; it must never show in output.
PLANTED = "canarycanarycanarycanarycanarycanary17"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No WATCH_* setting from the developer's environment reaches these tests."""
    for app in ("FA", "FL"):
        for name in ("TOKEN", "STACK", "HEALTH_URLS", "REPO", "PROMETHEUS_URL"):
            monkeypatch.delenv(f"WATCH_{app}_{name}", raising=False)
    monkeypatch.delenv("WATCH_INTERVAL_SECONDS", raising=False)


def _ready(client: TestClient, status: int) -> None:
    deadline = time.monotonic() + 10
    while client.get("/readyz").status_code != status:
        assert time.monotonic() < deadline, f"/readyz never answered {status}"
        time.sleep(0.05)


def _running(tools: FakeTools) -> None:
    fa = CATALOGS["freight-audit"]
    names = [name for service in fa.services if (name := fa.container(service, "prod"))]
    rows = [ps_row(name, fa.projects["prod"]) for name in names]
    tools.scenario({"docker": {"ps": {"stdout": "\n".join(rows) + "\n"}}})


def test_ready_once_a_poll_saw_everything(tools: FakeTools, server: FakeServer) -> None:
    _running(tools)
    url = server.route("/readyz", Route(200))
    settings = Settings(
        watch_interval_seconds=5,
        watch_fa_token=SecretStr(PLANTED),
        watch_fa_health_urls={"api": url},
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        _ready(client, 200)
        assert client.get("/readyz").json() == {"status": "ok"}
    assert [request.path for request in server.requests][:1] == ["/readyz"]


def test_not_ready_while_a_poll_cannot_see_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    settings = Settings(watch_interval_seconds=5, watch_fa_token=SecretStr(PLANTED))
    with TestClient(create_app(settings)) as client:
        _ready(client, 503)
        assert client.get("/readyz").json() == {"status": "unavailable"}
        assert client.get("/healthz").status_code == 200


def test_only_apps_with_a_token_are_watched(monkeypatch: pytest.MonkeyPatch) -> None:
    assert load_settings(Settings).targets() == []
    monkeypatch.setenv("WATCH_FL_TOKEN", PLANTED)
    monkeypatch.setenv("WATCH_FL_STACK", "dev")
    monkeypatch.setenv("WATCH_FL_HEALTH_URLS", json.dumps({"api": "http://127.0.0.1:4100/readyz"}))
    monkeypatch.setenv("WATCH_FL_PROMETHEUS_URL", "http://127.0.0.1:9091/")
    [target] = load_settings(Settings).targets()
    assert (target.catalog.app, target.stack, target.token) == ("filing-lens", "dev", PLANTED)
    assert target.health == {"api": "http://127.0.0.1:4100/readyz"}
    assert target.prometheus == "http://127.0.0.1:9091"


def test_tokens_stay_out_of_every_printout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WATCH_FA_TOKEN", PLANTED)
    settings = load_settings(Settings)
    [target] = settings.targets()
    assert target.token == PLANTED
    for text in (repr(settings), settings.model_dump_json(), repr(target)):
        assert PLANTED not in text


@pytest.mark.parametrize(
    ("name", "value", "problem"),
    [
        ("WATCH_FA_HEALTH_URLS", '{"nginx": "http://127.0.0.1:1/x"}', "takes only freight-audit"),
        ("WATCH_FA_HEALTH_URLS", f'{{"api": "https://u:{PLANTED}@h.example/r"}}', "credentials"),
        ("WATCH_FA_HEALTH_URLS", f'{{"api": "https://h.example/r?key={PLANTED}"}}', "query"),
        ("WATCH_FL_PROMETHEUS_URL", f"ftp://{PLANTED}.example/", "plain http or https"),
        ("WATCH_FL_REPO", f"H:/repos/{PLANTED}&calc", "path of letters"),
        ("WATCH_FA_TOKEN", "short", "at least 32"),
    ],
)
def test_bad_settings_stop_startup_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, problem: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=problem) as raised:
        load_settings(Settings)
    assert name in str(raised.value)
    assert PLANTED not in str(raised.value)
    assert "nginx" not in str(raised.value)


def test_settings_hold_no_key_but_the_webhook_tokens() -> None:
    names = list(Settings.model_fields)
    assert [name for name in names if "token" in name] == ["watch_fa_token", "watch_fl_token"]
    keys = ("approver", "api_key", "database")
    assert not [name for name in names if any(key in name for key in keys)]
