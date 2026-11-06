"""Docker Compose itself reads production's two files, with every profile on: it accepts them, and
what it merges has the restart policies, log rotation and host name that the static tests read in
the files. Run on a copy of infra/ beside throwaway env files, so no real value is needed or read.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_prod_compose import INFRA, PROD

pytestmark = pytest.mark.integration

DOCKER = shutil.which("docker") or "docker"
ONE_SHOT = {"gateway-migrate", "agent-migrate", "agent-runbooks"}


def merged(folder: Path) -> dict[str, Any]:
    shutil.copytree(INFRA, folder / "infra")
    text = (INFRA / "docker-compose.prod.yml").read_text("utf-8")
    asked = set(re.findall(r"\$\{(\w+):\?", text))
    values = {name: "test-value" for name in asked} | {
        "FASTEMBED_CACHE_PATH": str(folder / "fastembed"),
        "SOURCE_INDEX_DIR": str(folder / "sources"),
        "POSTGRES_PASSWORD": "test-value",
        "GATEWAY_DB_PASSWORD": "test-value",
        "AGENT_DB_PASSWORD": "test-value",
        "GRAFANA_ADMIN_PASSWORD": "test-value",
    }
    env = "".join(f"{name}={value}\n" for name, value in values.items())
    (folder / ".env.prod").write_text(env, "utf-8")
    for name in (".env.prod.gateway", ".env.prod.agent"):
        (folder / name).write_text("", "utf-8")
    files = ["-f", "infra/docker-compose.yml", "-f", "infra/docker-compose.prod.yml"]
    profiles = ["--profile", "observability", "--profile", "chaos"]
    command = ["compose", "-p", "triagecopilot-prod", *files, "--env-file", ".env.prod", *profiles]
    done = subprocess.run(  # noqa: S603 - docker with fixed arguments, on throwaway files
        [DOCKER, *command, "config", "--format", "json"],
        cwd=folder,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_compose_accepts_production_s_files_and_merges_what_the_server_needs(
    tmp_path: Path,
) -> None:
    config = merged(tmp_path)
    services = config["services"]
    assert set(services) == set(PROD["services"])
    for name, service in services.items():
        logging = service["logging"]
        assert (name, logging["driver"]) == (name, "json-file")
        assert (name, logging["options"]) == (name, {"max-size": "10m", "max-file": "3"})
        assert (name, service.get("restart", "no")) == (
            name,
            "no" if name in ONE_SHOT else "unless-stopped",
        )
    # Compose prints this as a list or a map, and a duration in nanoseconds or as written, by
    # version.
    hosts = json.dumps(services["egress-proxy"]["extra_hosts"])
    assert ("watch-host" in hosts, "host-gateway" in hosts) == (True, True)
    assert services["agent"]["stop_grace_period"] in ("1m0s", "60s", 60_000_000_000)
    options = config["networks"]["egress"]["driver_opts"]
    assert options == {"com.docker.network.bridge.name": "tc-egress"}
