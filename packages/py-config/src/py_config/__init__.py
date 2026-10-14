"""Settings from environment variables, validated once at startup with pydantic-settings."""

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvSettings(BaseSettings):
    """Base for service settings: fields read same-named env vars; empty values count as unset."""

    model_config = SettingsConfigDict(extra="ignore", frozen=True, env_ignore_empty=True)


class ConfigError(ValueError):
    """Invalid environment. The message names variables but never echoes their values."""


def load_settings[T: EnvSettings](model: type[T]) -> T:
    try:
        return model()
    except ValidationError as error:
        problems: list[str] = []
        for issue in error.errors(include_input=False, include_url=False):
            name = ".".join(map(str, issue["loc"])).upper()
            missing = issue["type"] == "missing"
            problems.append(f"{name} is required" if missing else f"{name}: {issue['msg']}")
        raise ConfigError(f"Invalid environment: {'; '.join(problems)}") from None
