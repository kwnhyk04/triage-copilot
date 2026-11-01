"""make prod-env: writes production's three env files in the current folder (README, Deploy), with
a fresh secret for every internal password, key and token. What only the user has (the model
providers' keys, TS_AUTHKEY, TC_TAG and the alert settings) is left empty for them to paste;
compose, the services and Alertmanager's entrypoint each refuse an empty value as missing. It
prints the names to paste, never a value, and writes nothing when any of the three files exists.
"""

import re
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (".env.prod", ".env.prod.gateway", ".env.prod.agent")


def fresh() -> str:
    """32 random bytes in base64url: 43 characters, none that a URL, compose or make reads."""
    return secrets.token_urlsafe(32)


def images() -> str:
    """ghcr.io/<owner> of this repo's GitHub remote, in lower case as GHCR wants it."""
    git = ["git", "-C", str(ROOT), "remote", "get-url", "origin"]
    url = subprocess.run(git, capture_output=True, text=True, check=False).stdout  # noqa: S603
    found = re.search(r"github\.com[:/]([^/]+)/", url)
    if found is None:
        sys.exit("prod-env: origin is no GitHub remote, so TC_IMAGES is unknown; nothing written")
    return f"ghcr.io/{found[1].lower()}"


def contents() -> dict[str, str]:
    agent_token = fresh()
    prod = f"""\
# Production's values for compose (README, Deploy), written by make prod-env with fresh secrets.
# Never commit it; the empty values are yours to paste.
PROD_POSTGRES_PASSWORD={fresh()}
PROD_GATEWAY_DB_PASSWORD={fresh()}
PROD_AGENT_DB_PASSWORD={fresh()}
PROD_GRAFANA_ADMIN_PASSWORD={fresh()}
TC_IMAGES={images()}
# Paste: sha- and the first 12 characters of the merged commit, once publish.yml has pushed it.
TC_TAG=
# Paste: a one-off, tagged, pre-approved Tailscale auth key.
TS_AUTHKEY=
# Alerting (README, Alerting): paste all four, then add COMPOSE_PROFILES=observability.
# The Discord channel's webhook URL, starting https://discord.com/api/webhooks/.
ALERT_DISCORD_WEBHOOK_URL=
# The Gmail account's app password: its 16 letters, without spaces.
ALERT_SMTP_PASSWORD=
# The Gmail address that signs in and sends the alerts.
ALERT_EMAIL_FROM=
# Where the alerts go, comma-separated; it may be the same address.
ALERT_EMAIL_TO=
"""
    gateway = f"""\
# The production gateway's settings (README, Deploy), written by make prod-env with fresh secrets.
# Never commit it. Vercel takes GATEWAY_API_KEY and APPROVER_API_KEY from here.
GATEWAY_API_KEY={fresh()}
APPROVER_API_KEY={fresh()}
AGENT_SERVICE_TOKEN={agent_token}
EXECUTOR_SERVICE_TOKEN={fresh()}
# The live tenants as JSON once live goes on (P1.12); {{}} keeps every tenant the sim's.
LIVE_TENANTS={{}}
# After README, Deploy, check 5: GATEWAY_TRUST_PROXY=10.231.233.18
"""
    agent = f"""\
# The production agent's settings (README, Deploy), written by make prod-env. Never commit it;
# the empty values are yours to paste.
AGENT_SERVICE_TOKEN={agent_token}
AI_ENABLED=true
LIVE_TENANTS={{}}
# Paste: the TC workspace's Anthropic key. Left empty, live triage runs on the free models alone.
ANTHROPIC_API_KEY=
# Paste: Google AI Studio's key and NVIDIA build's. The agent refuses to start without them.
GEMINI_API_KEY=
NVIDIA_API_KEY=
"""
    return dict(zip(FILES, (prod, gateway, agent), strict=True))


def main() -> None:
    here = Path.cwd()
    existing = [name for name in FILES if (here / name).exists()]
    if existing:
        exist = "exists" if len(existing) == 1 else "exist"
        sys.exit(
            f"prod-env: {', '.join(existing)} {exist} already, and none of the three files is ever "
            "overwritten. Move it away first, or edit it by hand."
        )
    texts = contents()
    for name, body in texts.items():
        # "x": a file that appeared since the check above is never overwritten either.
        with (here / name).open("x", encoding="utf-8", newline="\n") as file:
            file.write(body)
    pasted = {
        name: [line.removesuffix("=") for line in body.splitlines() if line.endswith("=")]
        for name, body in texts.items()
    }
    lines = [f"prod-env: wrote {', '.join(FILES)}, each secret made fresh; none is printed."]
    for title, alerting in (
        ("before make prod-up (README, Deploy)", False),
        ("before alerting starts (README, Alerting)", True),
    ):
        lines.append(f"Paste these {title}:")
        for name, keys in pasted.items():
            due = [key for key in keys if key.startswith("ALERT_") == alerting]
            if due:
                lines.append(f"  {name}: {', '.join(due)}")
    print("\n".join(lines))  # noqa: T201 - the CLI's output


if __name__ == "__main__":
    main()
