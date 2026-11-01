"""Production's monitoring configs, each checked by the image that runs it: promtool runs the alert
rules' unit tests (infra/prometheus-alerts.test.yml) and loads Prometheus's production config with
its rules; the probe exporter and egress-log's collector load theirs. No network is attached.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from test_prod_compose import BASE, INFRA, PROD

pytestmark = pytest.mark.integration

DOCKER = shutil.which("docker") or "docker"
PROMETHEUS = BASE["services"]["prometheus"]["image"]


def run(image: str, mounts: dict[Path, str], *args: str, entrypoint: str | None = None) -> str:
    """The image's output for args, with each file mounted read-only; fails on a non-zero exit."""
    # Docker Desktop creates a missing bind source as an empty directory, here in the repo.
    assert [src for src in mounts if not src.is_file()] == []
    binds = [f"--mount=type=bind,source={src},target={dst},readonly" for src, dst in mounts.items()]
    command = [DOCKER, "run", "--rm", "--network=none", *binds]
    if entrypoint:
        command.append(f"--entrypoint={entrypoint}")
    done = subprocess.run(  # noqa: S603 - docker with fixed arguments
        [*command, image, *args], capture_output=True, text=True, check=False, timeout=300
    )
    output = done.stdout + done.stderr
    assert done.returncode == 0, output
    return output


RULES = {INFRA / "prometheus-alerts.yml": "/etc/prometheus/alerts.yml"}


def test_each_rule_fires_on_its_condition_and_waits_as_written() -> None:
    tests = {INFRA / "prometheus-alerts.test.yml": "/etc/prometheus/alerts.test.yml"}
    output = run(
        PROMETHEUS,
        RULES | tests,
        "test",
        "rules",
        "/etc/prometheus/alerts.test.yml",
        entrypoint="promtool",
    )
    assert "SUCCESS" in output


def test_prometheus_loads_production_s_config_and_rules() -> None:
    config = {INFRA / "prometheus.prod.yml": "/etc/prometheus/prometheus.yml"}
    output = run(
        PROMETHEUS,
        RULES | config,
        "check",
        "config",
        "/etc/prometheus/prometheus.yml",
        entrypoint="promtool",
    )
    assert "8 rules found" in output


def test_the_probe_exporter_loads_its_config() -> None:
    image = PROD["services"]["blackbox"]["image"]
    config = {INFRA / "blackbox.yml": "/etc/blackbox_exporter/config.yml"}
    run(image, config, "--config.file=/etc/blackbox_exporter/config.yml", "--config.check")


def test_egress_log_loads_its_config() -> None:
    image = PROD["services"]["egress-log"]["image"]
    config = {INFRA / "otel-egress-log.yaml": "/etc/otelcol-contrib/config.yaml"}
    run(image, config, "validate", "--config=/etc/otelcol-contrib/config.yaml")
