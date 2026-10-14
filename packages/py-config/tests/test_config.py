from typing import Literal

import pytest

from py_config import ConfigError, EnvSettings, load_settings


class Settings(EnvSettings):
    app_port: int = 4210
    app_database_url: str
    log_level: Literal["debug", "info"] = "info"


def test_reads_env_vars_and_treats_empty_values_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PORT", "4300")
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://db")
    monkeypatch.setenv("LOG_LEVEL", "")
    settings = load_settings(Settings)
    assert (settings.app_port, settings.app_database_url, settings.log_level) == (
        4300,
        "postgresql://db",
        "info",
    )


def test_names_every_problem_without_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PORT", "secret-value")
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv("LOG_LEVEL", "loud")
    with pytest.raises(ConfigError) as caught:
        load_settings(Settings)
    message = str(caught.value)
    for name in ("APP_PORT", "APP_DATABASE_URL", "LOG_LEVEL"):
        assert name in message
    assert "secret-value" not in message
