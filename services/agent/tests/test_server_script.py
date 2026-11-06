"""scripts/server.py with a fake docker and rsync on PATH: what it runs, in which order, what it
keeps, and that it stops on any failure without touching what is already safe. No secret reaches a
command line. test_server_backup_int.py runs the same script against real Postgres containers.
"""

import json
import os
import stat
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "server.py"
POSTGRES = "triagecopilot-prod-postgres-1"
SECRET = "planted-secret-key-value"  # noqa: S105 - planted, to be looked for in the output
FAKE = r"""
import json, os, sys
from pathlib import Path

here = Path(__file__).parent
tool, argv = sys.argv[1], sys.argv[2:]
fed = sys.stdin.buffer.read() if tool == "docker" and "-i" in argv else b""
env = {k: v for k, v in os.environ.items() if k.startswith(("RCLONE_", "BACKUP_"))}
with (here / "calls.jsonl").open("a", encoding="utf-8") as calls:
    call = {"tool": tool, "argv": argv, "env": env, "fed": fed.decode("latin-1")}
    calls.write(json.dumps(call) + "\n")
scenario = json.loads((here / "scenario.json").read_text(encoding="utf-8"))
for word, answer in scenario.items():
    if word == tool or word in argv:
        sys.stdout.buffer.write(answer.get("stdout", "").encode())
        sys.exit(answer.get("exit", 0))
"""


class Tools:
    """`docker` and `rsync` launchers in their own folder, which is all of PATH."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir()
        script = folder / "fake_tool.py"
        script.write_text(FAKE, encoding="utf-8")
        python = f'"{sys.executable}" -I -S "{script}"'
        for tool in ("docker", "rsync"):
            if os.name == "nt":
                (folder / f"{tool}.cmd").write_text(f"@{python} {tool} %*\r\n", encoding="utf-8")
            else:
                launcher = folder / tool
                launcher.write_text(f'#!/bin/sh\nexec {python} {tool} "$@"\n', encoding="utf-8")
                launcher.chmod(0o755)
        self.scenario({})

    def scenario(self, answers: dict[str, dict[str, Any]]) -> None:
        (self.folder / "scenario.json").write_text(json.dumps(answers), encoding="utf-8")

    def calls(self) -> list[dict[str, Any]]:
        path = self.folder / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def words(self) -> list[str]:
        """Each docker call by the word that says what it did."""
        known = ("pg_dump", "pg_restore", "psql", "ps", "run")
        return [next(word for word in known if word in call["argv"]) for call in self.calls()]


@dataclass
class Ping:
    """A loopback server standing in for the outside check and the gateway's probe."""

    status: int = 200
    paths: list[str] = field(default_factory=list[str])
    port: int = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/ping-uuid"


@pytest.fixture
def ping() -> Iterator[Ping]:
    state = Ping()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state.paths.append(self.path)
            self.send_response(state.status)
            self.send_header("content-length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}).start()
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture
def tools(tmp_path: Path) -> Tools:
    return Tools(tmp_path / "bin")


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    return tmp_path / "backups"


def run(tools: Tools, folder: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    kept = ("SYSTEMROOT", "TEMP", "TMP")
    environ = {name: os.environ[name] for name in kept if name in os.environ}
    environ |= {"PATH": str(tools.folder), "BACKUP_DIR": str(folder), **env}
    return subprocess.run(  # noqa: S603 - this repo's script, no outside input
        [sys.executable, str(SCRIPT), *args],
        env=environ,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _dumps(folder: Path) -> list[str]:
    return sorted(path.name for path in folder.iterdir())


def test_a_backup_dumps_checks_and_keeps_one_file(tools: Tools, folder: Path) -> None:
    tools.scenario({"pg_dump": {"stdout": "PGDMP-bytes"}})
    done = run(tools, folder, "backup")
    assert done.returncode == 0, done.stderr
    [name] = _dumps(folder)
    assert name.startswith("triage-")
    assert name.endswith("Z.dump")
    assert (folder / name).read_bytes() == b"PGDMP-bytes"
    dump, check = tools.calls()
    assert dump["argv"] == ["exec", POSTGRES, "pg_dump", "-U", "postgres", "-Fc", "triage"]
    # Every block is read back: --list reads only the table of contents at the file's start.
    assert check["argv"] == ["exec", "-i", POSTGRES, "pg_restore", "-f", "/dev/null"]
    assert check["fed"] == "PGDMP-bytes"
    assert "no off-machine copy" in done.stdout


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX file modes")
def test_a_backup_is_readable_by_its_owner_alone(tools: Tools, folder: Path) -> None:
    tools.scenario({"pg_dump": {"stdout": "x"}})
    assert run(tools, folder, "backup").returncode == 0
    [path] = folder.iterdir()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_a_failed_dump_leaves_no_file_and_no_rotation(tools: Tools, folder: Path) -> None:
    folder.mkdir()
    old = [f"triage-2026110{day}T033000Z.dump" for day in range(1, 9)]
    for name in old:
        (folder / name).write_text("old", "utf-8")
    tools.scenario({"pg_dump": {"stdout": "half", "exit": 1}})
    done = run(tools, folder, "backup")
    assert done.returncode != 0
    assert "pg_dump" in done.stderr
    assert _dumps(folder) == old


def test_a_dump_that_fails_its_check_is_never_kept(tools: Tools, folder: Path) -> None:
    tools.scenario({"pg_restore": {"exit": 1}, "pg_dump": {"stdout": "truncated"}})
    done = run(tools, folder, "backup")
    assert done.returncode != 0
    assert _dumps(folder) == []


def test_the_newest_seven_stay_and_other_files_are_left_alone(tools: Tools, folder: Path) -> None:
    folder.mkdir()
    for day in range(1, 10):
        (folder / f"triage-2026100{day}T033000Z.dump").write_text("old", "utf-8")
    (folder / "notes.txt").write_text("mine", "utf-8")
    tools.scenario({"pg_dump": {"stdout": "new"}})
    assert run(tools, folder, "backup").returncode == 0
    dumps = [name for name in _dumps(folder) if name.endswith(".dump")]
    assert len(dumps) == 7
    assert dumps[0] == "triage-20261004T033000Z.dump"
    assert "notes.txt" in _dumps(folder)


def test_backup_keep_changes_how_many_stay(tools: Tools, folder: Path) -> None:
    folder.mkdir()
    (folder / "triage-20261001T033000Z.dump").write_text("old", "utf-8")
    tools.scenario({"pg_dump": {"stdout": "new"}})
    assert run(tools, folder, "backup", BACKUP_KEEP="1").returncode == 0
    assert len(_dumps(folder)) == 1


S3_ENV = {
    "BACKUP_DEST": "s3://tc-backups/nightly",
    "RCLONE_S3_ACCESS_KEY_ID": "AKIDEXAMPLE",
    "RCLONE_S3_SECRET_ACCESS_KEY": SECRET,
    "RCLONE_S3_ENDPOINT": "https://bucket.example",
    "RCLONE_S3_PROVIDER": "Other",
    "RCLONE_S3_REGION": "",
}


def test_a_bucket_copy_runs_rclone_with_no_key_on_its_command_line(
    tools: Tools, folder: Path
) -> None:
    tools.scenario({"pg_dump": {"stdout": "x"}})
    done = run(tools, folder, "backup", **S3_ENV)
    assert done.returncode == 0, done.stderr
    [name] = _dumps(folder)
    *_, copy = tools.calls()
    argv = copy["argv"]
    assert argv[:2] == ["run", "--rm"]
    # Names alone: docker takes each value from its own environment, never from its arguments.
    assert "--env=RCLONE_S3_SECRET_ACCESS_KEY" in argv
    assert "--env=RCLONE_S3_ACCESS_KEY_ID" in argv
    assert copy["env"]["RCLONE_S3_SECRET_ACCESS_KEY"] == SECRET
    assert SECRET not in " ".join(argv)
    assert SECRET not in done.stdout + done.stderr
    # An empty line in .env is no setting: rclone would take it as one.
    assert "--env=RCLONE_S3_REGION" not in argv
    assert next(arg for arg in argv if arg.startswith("rclone/rclone:1.")).count("@sha256:") == 1
    verb = argv.index("copyto")
    assert argv[verb + 1 : verb + 3] == [f"/backups/{name}", f":s3:tc-backups/nightly/{name}"]
    # So a key that may only write works: rclone asks for nothing else of the bucket.
    assert {"--s3-no-check-bucket", "--no-check-dest", "--s3-no-head"} <= set(argv)


def test_an_rsync_copy_goes_to_the_target_over_ssh_with_no_prompt(
    tools: Tools, folder: Path
) -> None:
    tools.scenario({"pg_dump": {"stdout": "x"}})
    done = run(tools, folder, "backup", BACKUP_DEST="tc@backup.example:/srv/tc/")
    assert done.returncode == 0, done.stderr
    [name] = _dumps(folder)
    *_, copy = tools.calls()
    assert copy["tool"] == "rsync"
    assert copy["argv"][:4] == ["--timeout=300", "--chmod=F600", "-e", "ssh -o BatchMode=yes"]
    assert copy["argv"][4:] == [str(folder / name), "tc@backup.example:/srv/tc/"]


def test_a_failed_copy_keeps_the_local_dump_fails_and_sends_no_ping(
    tools: Tools, folder: Path, ping: Ping
) -> None:
    tools.scenario({"pg_dump": {"stdout": "x"}, "rsync": {"exit": 12}})
    done = run(tools, folder, "backup", BACKUP_DEST="tc@h.example:/b", BACKUP_PING_URL=ping.url)
    assert done.returncode != 0
    assert "rsync" in done.stderr
    assert len(_dumps(folder)) == 1
    assert ping.paths == []


def test_a_good_backup_pings_the_outside_check_after_everything(
    tools: Tools, folder: Path, ping: Ping
) -> None:
    tools.scenario({"pg_dump": {"stdout": "x"}})
    done = run(tools, folder, "backup", BACKUP_PING_URL=ping.url)
    assert done.returncode == 0, done.stderr
    assert ping.paths == ["/ping-uuid"]
    assert "ping-uuid" not in done.stdout + done.stderr


def test_a_failed_dump_sends_no_ping(tools: Tools, folder: Path, ping: Ping) -> None:
    tools.scenario({"pg_dump": {"exit": 1}})
    assert run(tools, folder, "backup", BACKUP_PING_URL=ping.url).returncode != 0
    assert ping.paths == []


@pytest.mark.parametrize(
    ("env", "named"),
    [
        ({"BACKUP_DEST": f"s3://key:{SECRET}@bucket/x"}, "BACKUP_DEST"),
        ({"BACKUP_DEST": f"--rsh=sh{SECRET}"}, "BACKUP_DEST"),
        ({"BACKUP_DEST": f"https://{SECRET}.example/x"}, "BACKUP_DEST"),
        ({"BACKUP_DEST": "s3://tc-backups/x"}, "RCLONE_S3_ACCESS_KEY_ID"),
        ({"BACKUP_KEEP": "0"}, "BACKUP_KEEP"),
        ({"BACKUP_KEEP": "lots"}, "BACKUP_KEEP"),
        ({"BACKUP_PING_URL": f"ftp://{SECRET}.example/"}, "BACKUP_PING_URL"),
    ],
)
def test_a_bad_setting_stops_it_before_anything_runs_naming_it_alone(
    tools: Tools, folder: Path, env: dict[str, str], named: str
) -> None:
    done = run(tools, folder, "backup", **env)
    assert done.returncode != 0
    assert named in done.stderr
    assert SECRET not in done.stdout + done.stderr
    assert tools.calls() == []


def _restorable(dump: Path, running: str = "") -> dict[str, dict[str, Any]]:
    dump.write_text("PGDMP", "utf-8")
    return {"ps": {"stdout": running}, "-tA": {"stdout": "2|40"}}


def test_a_restore_loads_a_scratch_database_and_only_then_swaps_it_in(
    tools: Tools, folder: Path, tmp_path: Path
) -> None:
    dump = tmp_path / "triage.dump"
    tools.scenario(_restorable(dump, f"{POSTGRES}\n"))
    done = run(tools, folder, "restore", str(dump))
    assert done.returncode == 0, done.stderr
    assert tools.words() == ["ps", "psql", "psql", "pg_restore", "psql", "psql"]
    _, drop, create, load, swap, _ = tools.calls()
    assert "DROP DATABASE IF EXISTS triage_restore WITH (FORCE)" in drop["argv"]
    assert "CREATE DATABASE triage_restore" in create["argv"]
    assert load["fed"] == "PGDMP"
    assert load["argv"] == [
        "exec",
        "-i",
        POSTGRES,
        "pg_restore",
        "-U",
        "postgres",
        "-d",
        "triage_restore",
        "--single-transaction",
        "--exit-on-error",
    ]
    statement = swap["argv"][-1]
    # Atomic, the old database kept, and the grants a plain dump leaves out (postgres-init.sql).
    assert statement.index("RENAME TO triage_before_") < statement.index(
        "ALTER DATABASE triage_restore RENAME TO triage;"
    )
    assert "DROP" not in statement
    assert "REVOKE ALL ON DATABASE triage FROM PUBLIC" in statement
    assert "GRANT CONNECT ON DATABASE triage TO gateway, agent" in statement
    assert "GRANT CREATE ON DATABASE triage TO gateway" in statement
    assert "2 tenants" in done.stdout
    assert "40 investigations" in done.stdout
    assert "triage_before_" in done.stdout


def test_a_failed_load_never_reaches_the_swap(tools: Tools, folder: Path, tmp_path: Path) -> None:
    dump = tmp_path / "triage.dump"
    tools.scenario({"pg_restore": {"exit": 1}, **_restorable(dump)})
    done = run(tools, folder, "restore", str(dump))
    assert done.returncode != 0
    assert "pg_restore" in done.stderr
    assert tools.words() == ["ps", "psql", "psql", "pg_restore"]


@pytest.mark.parametrize("service", ["gateway", "agent"])
def test_a_restore_refuses_while_an_app_holds_the_database(
    tools: Tools, folder: Path, tmp_path: Path, service: str
) -> None:
    dump = tmp_path / "triage.dump"
    running = f"{POSTGRES}\ntriagecopilot-prod-{service}-1\n"
    tools.scenario(_restorable(dump, running))
    done = run(tools, folder, "restore", str(dump))
    assert done.returncode != 0
    assert f"triagecopilot-prod-{service}-1" in done.stderr
    assert tools.words() == ["ps"]


def test_a_restore_of_no_file_runs_nothing(tools: Tools, folder: Path, tmp_path: Path) -> None:
    done = run(tools, folder, "restore", str(tmp_path / "missing.dump"))
    assert done.returncode != 0
    assert "missing.dump" in done.stderr
    assert tools.calls() == []


def test_the_project_option_names_other_containers(tools: Tools, folder: Path) -> None:
    tools.scenario({"pg_dump": {"stdout": "x"}})
    assert run(tools, folder, "--project", "other", "backup").returncode == 0
    assert tools.calls()[0]["argv"][1] == "other-postgres-1"


def test_a_heartbeat_pings_only_while_the_gateway_is_ready(
    tools: Tools, folder: Path, ping: Ping
) -> None:
    env = {"HEARTBEAT_URL": ping.url, "PROD_GATEWAY_PORT": str(ping.port)}
    done = run(tools, folder, "heartbeat", **env)
    assert done.returncode == 0, done.stderr
    assert ping.paths == ["/readyz", "/ping-uuid"]
    assert "ping-uuid" not in done.stdout + done.stderr
    ping.paths.clear()
    ping.status = 503
    done = run(tools, folder, "heartbeat", **env)
    assert done.returncode != 0
    assert ping.paths == ["/readyz"]


def test_a_heartbeat_without_its_url_says_so(tools: Tools, folder: Path) -> None:
    done = run(tools, folder, "heartbeat")
    assert done.returncode != 0
    assert "HEARTBEAT_URL" in done.stderr
