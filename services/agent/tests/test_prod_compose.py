"""Production is its own compose project, and only the gateway's public port leaves it: on
127.0.0.1, for watch and Tailscale Funnel's sidecar. The gateway's internal listener, the data
services and the sim's admin port are never published, and each service gets only its secrets.
"""

import json
import re
from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[3]
INFRA = ROOT / "infra"


class _Reset(list[Any]):
    """A `!reset` value: compose drops what the base file set."""


class _Loader(yaml.SafeLoader):
    """SafeLoader that also reads compose's `!reset` tag."""


def _reset(_loader: object, _node: object) -> _Reset:
    return _Reset()


# PyYAML has no type information for its loaders' constructor registry.
cast("Any", _Loader).add_constructor("!reset", _reset)


def _compose(name: str) -> dict[str, Any]:
    text = (INFRA / name).read_text("utf-8")
    return yaml.load(text, Loader=_Loader)  # noqa: S506 - a SafeLoader subclass


BASE = _compose("docker-compose.yml")
PROD = _compose("docker-compose.prod.yml")
SERVICES: set[str] = set(BASE["services"]) | set(PROD["services"])
# Reach a container only through its own env file, never through the compose files.
SECRETS = {
    "GATEWAY_API_KEY",
    "APPROVER_API_KEY",
    "AGENT_SERVICE_TOKEN",
    "EXECUTOR_SERVICE_TOKEN",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "NVIDIA_API_KEY",
    "OPENROUTER_API_KEY",
    "LOGIN_PASSWORD_HASH",
    "SESSION_SECRET",
    "WATCH_FA_TOKEN",
    "WATCH_FL_TOKEN",
    "SIM_ADMIN_URL",
}
# Each service's env file, and the production secrets compose may hand it.
OWN: dict[str, tuple[str | None, set[str]]] = {
    "postgres": (
        None,
        {"PROD_POSTGRES_PASSWORD", "PROD_GATEWAY_DB_PASSWORD", "PROD_AGENT_DB_PASSWORD"},
    ),
    "gateway-migrate": (None, {"PROD_GATEWAY_DB_PASSWORD"}),
    "gateway": ("../.env.prod.gateway", {"PROD_GATEWAY_DB_PASSWORD"}),
    "agent-migrate": ("../.env.prod.agent", {"PROD_AGENT_DB_PASSWORD"}),
    "agent-runbooks": ("../.env.prod.agent", {"PROD_AGENT_DB_PASSWORD"}),
    "agent": ("../.env.prod.agent", {"PROD_AGENT_DB_PASSWORD"}),
    "tailscale": (None, {"TS_AUTHKEY"}),
}


def _ports(name: str) -> list[str]:
    """What a service publishes once both files merge: `!reset` drops the base file's ports."""
    prod = PROD["services"].get(name, {}).get("ports", [])
    if isinstance(prod, _Reset):
        return []
    return [*BASE["services"].get(name, {}).get("ports", []), *prod]


def _environment(name: str) -> dict[str, Any]:
    return PROD["services"].get(name, {}).get("environment", {})


def test_production_is_its_own_compose_project() -> None:
    assert PROD["name"] == "triagecopilot-prod"
    makefile = (ROOT / "Makefile").read_text("utf-8")
    assert "docker compose -p triagecopilot-prod" in makefile
    # The volumes hold production's data: no target removes them.
    assert not re.search(r"\bdown\b[^\n]*(\s-v\b|--volumes)", makefile)
    # --wait alone waits forever on a container that crash-loops before its first healthcheck,
    # as the gateway does when startup refuses a setting.
    assert re.search(r"\$\(PROD\) up -d --wait --wait-timeout \d+", makefile)


def test_only_the_gateway_publishes_and_only_on_127_0_0_1() -> None:
    published = {name: _ports(name) for name in SERVICES if _ports(name)}
    assert published == {"gateway": ["127.0.0.1:${PROD_GATEWAY_PORT:-4202}:4200"]}
    # So the internal listener (4201) and the sim's admin port (4231) never are, and no service
    # joins the host's network instead.
    for name in SERVICES:
        merged = BASE["services"].get(name, {}) | PROD["services"].get(name, {})
        assert (name, "network_mode" in merged) == (name, False)
    assert {"sim", "executor"}.isdisjoint(SERVICES)


def test_each_service_gets_only_its_own_secrets() -> None:
    for name, service in PROD["services"].items():
        env_file, allowed = OWN.get(name, (None, set()))
        assert (name, service.get("env_file")) == (name, env_file)
        environment = _environment(name)
        used = set(re.findall(r"\$\{(PROD_[A-Z_]+|TS_AUTHKEY)", json.dumps(environment)))
        assert (name, used - allowed) == (name, set())
        assert (name, SECRETS & set(environment)) == (name, set())
    postgres = _environment("postgres")
    passwords = {key: re.findall(r"\$\{(\w+)", value) for key, value in postgres.items()}
    assert passwords == {
        "POSTGRES_PASSWORD": ["PROD_POSTGRES_PASSWORD"],
        "GATEWAY_DB_PASSWORD": ["PROD_GATEWAY_DB_PASSWORD"],
        "AGENT_DB_PASSWORD": ["PROD_AGENT_DB_PASSWORD"],
    }


def test_no_container_is_pointed_at_the_host_or_a_windows_path() -> None:
    # Fixed here, so an env file copied from .env changes none of them.
    gateway, agent = _environment("gateway"), _environment("agent")
    assert gateway["NODE_ENV"] == "production"
    hosts = {gateway["GATEWAY_HOST"], gateway["GATEWAY_INTERNAL_HOST"], agent["AGENT_HOST"]}
    assert hosts == {"0.0.0.0"}  # noqa: S104 - inside the container, published on 127.0.0.1 only
    assert "@postgres:5432/" in gateway["GATEWAY_DATABASE_URL"]
    assert "@postgres:5432/" in agent["AGENT_DATABASE_URL"]
    assert (gateway["AGENT_URL"], agent["GATEWAY_INTERNAL_URL"]) == (
        "http://agent:4210",
        "http://gateway:4201",
    )
    for name in ("gateway", "gateway-migrate", "agent", "agent-migrate", "agent-runbooks"):
        values = json.dumps(_environment(name))
        assert (name, "127.0.0.1" in values or "H:" in values) == (name, False)


def test_funnel_serves_only_the_gateways_public_port() -> None:
    tailscale = PROD["services"]["tailscale"]
    assert re.fullmatch(r"tailscale/tailscale:v[\d.]+@sha256:[0-9a-f]{64}", tailscale["image"])
    assert tailscale["environment"]["TS_SERVE_CONFIG"] == "/config/serve.json"
    serve = json.loads((INFRA / "tailscale" / "serve.json").read_text("utf-8"))
    assert list(serve["TCP"]) == ["443"]
    proxies = [
        handler["Proxy"] for web in serve["Web"].values() for handler in web["Handlers"].values()
    ]
    assert proxies == ["http://gateway:4200"]
    assert list(serve["AllowFunnel"].values()) == [True]
