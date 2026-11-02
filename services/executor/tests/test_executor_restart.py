"""The restart effector against a fake docker on PATH, which records each call and runs nothing:
only an approved, catalog-listed restart reaches docker, as one fixed command."""

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from test_executor_run import ID, Gateway, body, v1

from executor.restart import restart_effector
from executor.run import Target, run_action

FAKE = r"""
import json, os, sys, time
from pathlib import Path

here = Path(__file__).parent
call = {"argv": sys.argv[1:], "env": sorted(os.environ), "cwd": sorted(os.listdir("."))}
with (here / "calls.jsonl").open("a", encoding="utf-8") as calls:
    calls.write(json.dumps(call) + "\n")
time.sleep(float((here / "sleep").read_text()) if (here / "sleep").exists() else 0)
sys.stderr.write("first line\nno such service: worker\n")
sys.exit(int((here / "exit").read_text()) if (here / "exit").exists() else 0)
"""


@pytest.fixture
def docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake `docker` that is all of PATH, beside a secret the effector must not pass on."""
    (tmp_path / "fake_docker.py").write_text(FAKE, encoding="utf-8")
    python = f'"{sys.executable}" -I -S "{tmp_path / "fake_docker.py"}"'
    if os.name == "nt":
        (tmp_path / "docker.cmd").write_text(f"@{python} %*\r\n", encoding="utf-8")
    else:
        launcher = tmp_path / "docker"
        launcher.write_text(f'#!/bin/sh\nexec {python} "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("EXECUTOR_SERVICE_TOKEN", "executor-secret-a-child-must-not-see-00")
    return tmp_path


def calls(folder: Path) -> list[dict[str, Any]]:
    path = folder / "calls.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return [json.loads(line) for line in lines]


def test_only_approved_catalog_restarts_reach_docker_as_one_fixed_command(docker: Path) -> None:
    effectors = {"restart_service": restart_effector()}

    def run(message: str, gateway: Gateway, dry_run: bool = False) -> str:
        options: dict[str, Any] = {"dry_run": dry_run, "effectors": effectors, "stack": "dev"}
        return run_action(message, gateway.client(), **options)

    assert run(body(), Gateway()) == "executed"
    for refused in (
        body(target="postgres"),
        body(app=None),
        json.dumps(v1),
        body(app="freight-audit", target="worker"),
    ):
        assert run(refused, Gateway()) == "refused"
    assert run(body(), Gateway(approved=False)) == "refused"
    assert run(body(), Gateway(), dry_run=True) == "dry_run"

    [call] = calls(docker)
    assert call["argv"] == ["compose", "-p", "filinglens", "restart", "api"]
    # An empty working directory: compose reads no project file or .env of its own there.
    assert call["cwd"] == []
    assert "EXECUTOR_SERVICE_TOKEN" not in call["env"]
    assert str(ID) not in json.dumps(call)


def test_a_failed_or_slow_restart_raises_with_docker_s_last_line(docker: Path) -> None:
    (docker / "exit").write_text("1", encoding="utf-8")
    with pytest.raises(RuntimeError, match="exit 1: no such service: worker"):
        restart_effector()(Target("filinglens", "api"))
    (docker / "exit").write_text("0", encoding="utf-8")
    (docker / "sleep").write_text("5", encoding="utf-8")
    with pytest.raises(RuntimeError, match="timed out after 1 s"):
        restart_effector(timeout=1)(Target("filinglens", "api"))


def test_no_docker_on_path_is_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(RuntimeError, match="docker is not on PATH"):
        restart_effector()(Target("filinglens", "api"))
