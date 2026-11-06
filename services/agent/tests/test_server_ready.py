"""What the move to one always-on Linux server needs (docs/server-move.md): every project image on
both architectures under one tag, no Docker Desktop name in production's config, a restart policy
and log rotation on every container, and systemd units for what runs on the host.
"""

import configparser
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_prod_compose import INFRA, PROD, ROOT, SERVICES, SQUID_CONF

from agent import runbooks

WORKFLOWS = ROOT / ".github" / "workflows"
ARCHES = ["amd64", "arm64"]
RUNNER = "${{ matrix.arch == 'arm64' && 'ubuntu-24.04-arm' || 'ubuntu-24.04' }}"
IMAGES = ["gateway", "agent", "executor", "sim", "squid"]
# Containers that run once and exit: migrations and the runbook load.
ONE_SHOT = {"gateway-migrate", "agent-migrate", "agent-runbooks"}
UNITS = INFRA / "systemd"


def _workflow(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text("utf-8"))


def _step(job: dict[str, Any], action: str) -> dict[str, Any]:
    [step] = [step for step in job["steps"] if step.get("uses", "").startswith(action)]
    return step


@pytest.mark.parametrize("name", ["ci.yml", "publish.yml"])
def test_every_image_builds_on_both_architectures_on_native_runners(name: str) -> None:
    job = _workflow(name)["jobs"]["images"]
    matrix = job["strategy"]["matrix"]
    assert matrix["arch"] == ARCHES
    assert matrix["service"] == IMAGES
    assert [entry["service"] for entry in matrix["include"]] == IMAGES
    assert job["runs-on"] == RUNNER
    build = _step(job, "docker/build-push-action")
    assert build["with"]["platforms"] == "linux/${{ matrix.arch }}"


def test_publish_keeps_one_tag_for_both_architectures() -> None:
    workflow = _workflow("publish.yml")
    images = workflow["jobs"]["images"]
    tags = " ".join(step.get("run", "") for step in images["steps"])
    # Each build pushes its architecture's own tag, never the tags production pins.
    assert '"tags=$image:sha-$(echo "$SHA" | cut -c1-12)-$ARCH" >> "$GITHUB_OUTPUT"' in tags
    manifests = workflow["jobs"]["manifests"]
    assert manifests["needs"] == "images"
    assert manifests["strategy"]["matrix"]["service"] == IMAGES
    assert manifests["permissions"] == {"packages": "write"}
    run = " ".join(step.get("run", "") for step in manifests["steps"])
    assert 'docker buildx imagetools create --tag "$image:$tag" --tag "$image:main"' in run
    assert '"$image:$tag-amd64" "$image:$tag-arm64"' in run


def test_no_docker_desktop_name_is_left_in_production_s_config() -> None:
    # host.docker.internal is Docker Desktop's own name; Docker Engine has no such name, so squid's
    # peer would never resolve and WatchNotReady would fire for good.
    for path in (
        SQUID_CONF,
        INFRA / "docker-compose.prod.yml",
        INFRA / "prometheus.prod.yml",
        INFRA / "docker-compose.yml",
    ):
        text = path.read_text("utf-8")
        assert (path.name, "host.docker.internal" in text) == (path.name, False)
        assert (path.name, "192.168.65" in text) == (path.name, False)


def test_the_proxy_reaches_the_host_by_a_name_compose_gives_it() -> None:
    # host-gateway works on Docker Desktop and Docker Engine alike.
    assert PROD["services"]["egress-proxy"]["extra_hosts"] == ["watch-host:host-gateway"]
    conf = SQUID_CONF.read_text("utf-8")
    assert re.search(r"^cache_peer watch-host parent 4240 0 .*\bname=watch_host\b", conf, re.M)
    # So the host's firewall can allow this one bridge: a generated name changes with each
    # make prod-down.
    options = PROD["networks"]["egress"]["driver_opts"]
    assert options == {"com.docker.network.bridge.name": "tc-egress"}


def test_every_long_running_container_restarts_and_every_one_shot_does_not() -> None:
    for name in SERVICES:
        policy = PROD["services"][name].get("restart")
        assert (name, policy) == (name, None if name in ONE_SHOT else "unless-stopped")


def test_every_container_s_logs_rotate() -> None:
    # Docker's default keeps a container's log forever, and the disk is shared with two more apps.
    rotated = {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}
    for name in SERVICES:
        assert (name, PROD["services"][name].get("logging")) == (name, rotated)


def test_the_agent_gets_time_to_finish_its_step_when_stopped() -> None:
    # Docker kills a container 10 s after SIGTERM by default, and a job killed mid-step pays again
    # for the model call on redelivery.
    assert PROD["services"]["agent"]["stop_grace_period"] == "60s"


def _unit(name: str) -> configparser.ConfigParser:
    # strict=False: systemd allows a key twice, and optionxform keeps its capitals.
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = lambda optionstr: optionstr  # type: ignore[method-assign]
    parser.read_string((UNITS / name).read_text("utf-8"))
    return parser


def test_watch_is_a_service_systemd_restarts_for_good() -> None:
    unit = _unit("tc-watch.service")
    assert unit["Service"]["Restart"] == "always"
    # 0 turns off the limit that would leave watch stopped after five quick failures.
    assert unit["Unit"]["StartLimitIntervalSec"] == "0"
    assert unit["Service"]["ExecStart"] == "/usr/bin/make prod-watch"
    assert unit["Service"]["WorkingDirectory"] == "/opt/triage-copilot"
    assert unit["Service"]["User"] == "tc"
    # Its bind address is the default bridge's, which exists once Docker is up.
    assert "docker.service" in unit["Unit"]["After"]
    assert unit["Install"]["WantedBy"] == "multi-user.target"
    # systemd makes the state folder, owned by the unit's user, so a reboot keeps it.
    assert unit["Service"]["StateDirectory"] == "tc-watch"
    assert unit["Service"]["Environment"] == "WATCH_STATE_DIR=/var/lib/tc-watch"


@pytest.mark.parametrize(
    ("name", "target", "every"),
    [
        ("tc-backup", "prod-backup", "OnCalendar"),
        ("tc-heartbeat", "prod-heartbeat", "OnUnitActiveSec"),
        ("tc-fetch", "prod-fetch", "OnUnitActiveSec"),
    ],
)
def test_each_timer_runs_one_make_target_as_the_deploy_user(
    name: str, target: str, every: str
) -> None:
    service = _unit(f"{name}.service")
    assert service["Service"]["Type"] == "oneshot"
    assert service["Service"]["ExecStart"] == f"/usr/bin/make {target}"
    assert service["Service"]["User"] == "tc"
    assert service["Service"]["WorkingDirectory"] == "/opt/triage-copilot"
    timer = _unit(f"{name}.timer")
    assert every in timer["Timer"]
    assert timer["Install"]["WantedBy"] == "timers.target"


def test_a_backup_missed_while_the_server_was_off_runs_at_the_next_boot() -> None:
    assert _unit("tc-backup.timer")["Timer"]["Persistent"] == "true"


def test_the_heartbeat_stops_when_the_server_does_within_minutes() -> None:
    timer = _unit("tc-heartbeat.timer")["Timer"]
    assert timer["OnBootSec"] == "2min"
    assert timer["OnUnitActiveSec"] == "5min"


def test_each_host_secret_reaches_its_own_make_target_alone() -> None:
    makefile = (ROOT / "Makefile").read_text("utf-8")
    owners = {
        "RCLONE_S3_ACCESS_KEY_ID": "prod-backup",
        "RCLONE_S3_SECRET_ACCESS_KEY": "prod-backup",
        "BACKUP_PING_URL": "prod-backup",
        "HEARTBEAT_URL": "prod-heartbeat",
    }
    for name, target in owners.items():
        assert re.search(rf"^unexport .*\b{name}\b", makefile, re.MULTILINE), name
        assert f"{target}: export {name} := $({name})" in makefile
    for name in ("WATCH_FA_TOKEN", "WATCH_FL_TOKEN"):
        assert f"prod-watch: export {name} := $({name})" in makefile


def test_the_host_jobs_need_no_environment_synced_and_watch_no_dev_tools() -> None:
    makefile = (ROOT / "Makefile").read_text("utf-8")
    for step in ("backup", "heartbeat"):
        assert f"uv run --no-project python scripts/server.py {step}" in makefile
    assert 'uv run --no-project python scripts/server.py restore "$(FILE)"' in makefile
    assert "uv run --frozen --no-dev --package watch python -m watch" in makefile
    # Postgres alone, up and ready, so a restore on a fresh server has something to restore into.
    assert "$(PROD) up -d --wait postgres" in makefile


def test_env_example_lists_every_setting_of_the_host_jobs() -> None:
    example = (ROOT / ".env.example").read_text("utf-8")
    for name in ("BACKUP_DIR", "BACKUP_KEEP", "RCLONE_S3_PROVIDER"):
        assert re.search(rf"^{name}=.+$", example, re.MULTILINE), name
    # Empty by default: a backup stays on the machine until the user names a destination.
    for name in (
        "BACKUP_DEST",
        "BACKUP_PING_URL",
        "HEARTBEAT_URL",
        "RCLONE_S3_ENDPOINT",
        "RCLONE_S3_REGION",
        "RCLONE_S3_ACCESS_KEY_ID",
        "RCLONE_S3_SECRET_ACCESS_KEY",
    ):
        assert re.search(rf"^{name}=$", example, re.MULTILINE), name
    # Commented out: an empty line in .env would override what systemd sets for watch.
    assert re.search(r"^# WATCH_STATE_DIR=", example, re.MULTILINE)


class _Embedder:
    """Stands in for fastembed, so no test downloads a model."""

    def __init__(self, _cache: Path, *, download: bool) -> None:
        assert download

    def embed(self, _texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.0, 0.0]]


def _cli(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["agent.runbooks", *args])
    monkeypatch.setattr(runbooks, "FastEmbedder", _Embedder)
    for name in ("AGENT_DATABASE_URL", "AGENT_SERVICE_TOKEN", "FASTEMBED_CACHE_PATH"):
        monkeypatch.delenv(name, raising=False)


def test_the_model_downloads_with_no_setting_but_its_folder(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # A new server's short .env has no database URL or token when make embed-model runs. The
    # rehearsal of docs/server-move.md stopped here: "AGENT_DATABASE_URL is required".
    _cli(monkeypatch, "download")
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))
    runbooks.main()
    assert f"3 dimensions in {tmp_path}" in capsys.readouterr().out


def test_the_model_download_without_its_folder_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _cli(monkeypatch, "download")
    with pytest.raises(SystemExit, match="FASTEMBED_CACHE_PATH is required"):
        runbooks.main()


def test_loading_the_runbooks_still_needs_the_whole_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _cli(monkeypatch, "ingest")
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))
    with pytest.raises(SystemExit, match="AGENT_DATABASE_URL"):
        runbooks.main()


def test_the_runbook_is_public_so_a_clone_on_the_server_has_it() -> None:
    assert "!docs/server-move.md" in (ROOT / ".gitignore").read_text("utf-8").splitlines()
    assert "/backups/" in (ROOT / ".gitignore").read_text("utf-8").splitlines()
    assert "backups" in (ROOT / ".dockerignore").read_text("utf-8").splitlines()
