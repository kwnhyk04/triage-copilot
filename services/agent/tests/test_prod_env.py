"""`make prod-env` (scripts/prod_env.py) writes production's three env files in the folder it runs
in: a fresh secret for every internal password, key and token, never printed, empty values only
for what the user pastes, and nothing at all when any of the three files exists already.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "prod_env.py"
COMPOSE = ROOT / "infra" / "docker-compose.prod.yml"
FILES = (".env.prod", ".env.prod.gateway", ".env.prod.agent")
# Made fresh by the script, per file. The agent's token is the gateway's.
SECRETS = {
    ".env.prod": {
        "PROD_POSTGRES_PASSWORD",
        "PROD_GATEWAY_DB_PASSWORD",
        "PROD_AGENT_DB_PASSWORD",
        "PROD_GRAFANA_ADMIN_PASSWORD",
    },
    ".env.prod.gateway": {
        "GATEWAY_API_KEY",
        "APPROVER_API_KEY",
        "AGENT_SERVICE_TOKEN",
        "EXECUTOR_SERVICE_TOKEN",
    },
    ".env.prod.agent": {"AGENT_SERVICE_TOKEN"},
}
# What only the user has: left empty, which compose, the services and the alert entrypoint each
# refuse as missing.
PASTED = {
    ".env.prod": {
        "TC_TAG",
        "TS_AUTHKEY",
        "ALERT_DISCORD_WEBHOOK_URL",
        "ALERT_SMTP_PASSWORD",
        "ALERT_EMAIL_FROM",
        "ALERT_EMAIL_TO",
    },
    ".env.prod.gateway": set[str](),
    ".env.prod.agent": {"ANTHROPIC_API_KEY", "GEMINI_API_KEY", "NVIDIA_API_KEY"},
}


def run(folder: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this repo's script, no outside input
        [sys.executable, str(SCRIPT)], cwd=folder, capture_output=True, text=True, check=False
    )


def parse(path: Path) -> dict[str, str]:
    """KEY=value lines; comments and blank lines skipped."""
    lines = path.read_text("utf-8").splitlines()
    pairs = [line.split("=", 1) for line in lines if line.strip() and not line.startswith("#")]
    return {key: value for key, value in pairs}


def written(folder: Path) -> dict[str, dict[str, str]]:
    done = run(folder)
    assert done.returncode == 0, done.stderr
    return {name: parse(folder / name) for name in FILES}


def test_every_internal_secret_is_fresh_and_its_own(tmp_path: Path) -> None:
    files = written(tmp_path)
    secrets = [(name, key, files[name][key]) for name, keys in SECRETS.items() for key in keys]
    for name, key, value in secrets:
        # 32 random bytes in base64url, as README's old one-liner made them.
        assert (name, key, re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is not None) == (
            name,
            key,
            True,
        )
    agent_token = files[".env.prod.agent"]["AGENT_SERVICE_TOKEN"]
    assert agent_token == files[".env.prod.gateway"]["AGENT_SERVICE_TOKEN"]
    own = [value for name, _, value in secrets if name != ".env.prod.agent"]
    assert len(set(own)) == len(own)
    # Another run makes other secrets.
    (again := tmp_path / "again").mkdir()
    other = written(again)
    assert {value for _, _, value in secrets}.isdisjoint(
        other[name][key] for name, keys in SECRETS.items() for key in keys
    )


def test_only_what_the_user_pastes_is_left_empty(tmp_path: Path) -> None:
    files = written(tmp_path)
    for name in FILES:
        empty = {key for key, value in files[name].items() if value == ""}
        assert (name, empty) == (name, PASTED[name])


def test_compose_finds_every_value_it_asks_env_prod_for(tmp_path: Path) -> None:
    prod = written(tmp_path)[".env.prod"]
    asked = set(re.findall(r"\$\{(\w+):\?set \1 in \.env\.prod\}", COMPOSE.read_text("utf-8")))
    assert {"PROD_POSTGRES_PASSWORD", "TC_IMAGES", "TC_TAG", "TS_AUTHKEY"} <= asked
    assert asked <= set(prod)
    # The images' owner comes from this repo's GitHub remote, in lower case as GHCR wants it.
    assert prod["TC_IMAGES"] == "ghcr.io/kwnhyk04"


def test_the_services_start_with_live_off_and_the_models_on(tmp_path: Path) -> None:
    files = written(tmp_path)
    assert files[".env.prod.gateway"]["LIVE_TENANTS"] == "{}"
    assert files[".env.prod.agent"]["LIVE_TENANTS"] == "{}"
    assert files[".env.prod.agent"]["AI_ENABLED"] == "true"


def test_it_prints_no_value_and_names_what_to_paste(tmp_path: Path) -> None:
    done = run(tmp_path)
    assert done.returncode == 0, done.stderr
    printed = done.stdout + done.stderr
    for name, keys in SECRETS.items():
        values = parse(tmp_path / name)
        for key in keys:
            assert (name, key, values[key] in printed) == (name, key, False)
    for keys in PASTED.values():
        for key in keys:
            assert key in printed


def test_it_refuses_to_overwrite_and_then_writes_nothing(tmp_path: Path) -> None:
    kept = tmp_path / ".env.prod.agent"
    kept.write_text("ANTHROPIC_API_KEY=kept\n", "utf-8")

    done = run(tmp_path)

    assert done.returncode != 0
    assert ".env.prod.agent" in done.stderr
    assert sorted(path.name for path in tmp_path.iterdir()) == [".env.prod.agent"]
    assert kept.read_text("utf-8") == "ANTHROPIC_API_KEY=kept\n"
