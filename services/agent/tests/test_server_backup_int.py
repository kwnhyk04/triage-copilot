"""The backup and restore (scripts/server.py), live: real Postgres containers set up as compose sets
up production's, both migration chains and the seed, a real pg_dump, and a restore into a second,
fresh Postgres, as on the day of the server move. Tenant ids, row counts, sequences, ownership and
grants all come across, each role logs in with its old rights, and both chains find nothing left to
do. A dump cut short changes nothing, the database a restore replaces stays, and the real rclone
reaches a real S3 server.
"""

import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from test_prod_compose import BASE, INFRA, ROOT

pytestmark = pytest.mark.integration

DOCKER = shutil.which("docker") or "docker"
PNPM = shutil.which("pnpm") or "pnpm"
SCRIPT = ROOT / "scripts" / "server.py"
IMAGE = BASE["services"]["postgres"]["image"]
LABEL = "triagecopilot.test=backup"
_PINNED = re.search(r'"(rclone/rclone:[\d.]+@sha256:\w+)"', SCRIPT.read_text("utf-8"))
assert _PINNED
RCLONE = _PINNED[1]
PASSWORDS = {
    "POSTGRES_PASSWORD": uuid.uuid4().hex,
    "GATEWAY_DB_PASSWORD": uuid.uuid4().hex,
    "AGENT_DB_PASSWORD": uuid.uuid4().hex,
}
ACL = """
    SELECT (acl).grantee::regrole, (acl).privilege_type
    FROM (SELECT aclexplode(datacl) AS acl FROM pg_database WHERE datname = 'triage') AS grants
    ORDER BY 1, 2
"""


def docker(*args: str, timeout: float = 120) -> str:
    done = subprocess.run(  # noqa: S603 - docker with fixed arguments
        [DOCKER, *args], capture_output=True, text=True, check=False, timeout=timeout
    )
    assert done.returncode == 0, f"docker {args[0]}: {done.stderr.strip()}"
    return done.stdout.strip()


@dataclass
class Postgres:
    """A throwaway server under compose's name for `project`, which is what the script reaches."""

    project: str

    @property
    def container(self) -> str:
        return f"{self.project}-postgres-1"

    @property
    def port(self) -> str:
        return docker("port", self.container, "5432/tcp").splitlines()[0].rsplit(":", 1)[1]

    def url(self, role: str, password: str) -> str:
        return f"postgresql://{role}:{password}@127.0.0.1:{self.port}/triage"

    def query(self, sql: str, database: str = "triage") -> list[str]:
        psql = ["psql", "-U", "postgres", "-d", database, "-tA", "-c", sql]
        return docker("exec", self.container, *psql).splitlines()

    def contents(self) -> dict[str, list[str]]:
        """What must come across: each table's rows, the tenants' ids, sequences and ownership."""
        tables = self.query(
            "SELECT schemaname || '.' || tablename FROM pg_tables"
            " WHERE schemaname NOT IN ('pg_catalog', 'information_schema') ORDER BY 1"
        )
        # Table names from this throwaway server's own catalog.
        counts = " UNION ALL ".join(
            f"SELECT '{name}', count(*) FROM {name}"  # noqa: S608
            for name in tables
        )
        return {
            "rows": self.query(f"{counts} ORDER BY 1"),
            "tenants": self.query("SELECT id FROM gateway.tenants ORDER BY id"),
            "sequences": self.query(
                "SELECT schemaname, sequencename, last_value FROM pg_sequences ORDER BY 1, 2"
            ),
            "owners": self.query(
                "SELECT schemaname, tablename, tableowner FROM pg_tables"
                " WHERE schemaname IN ('gateway', 'agent') ORDER BY 1, 2"
            ),
            "schemas": self.query(
                "SELECT nspname, nspowner::regrole FROM pg_namespace"
                " WHERE nspname IN ('gateway', 'agent') ORDER BY 1"
            ),
            "extensions": self.query("SELECT extname FROM pg_extension ORDER BY 1"),
            "grants": self.query(ACL),
        }


def start(project: str) -> Postgres:
    server = Postgres(project)
    env = [f"--env={name}={value}" for name, value in PASSWORDS.items()]
    init = INFRA / "postgres-init.sql"
    mount = f"--mount=type=bind,source={init},target=/docker-entrypoint-initdb.d/init.sql,readonly"
    docker(
        "run",
        "--detach",
        f"--label={LABEL}",
        f"--name={server.container}",
        "--env=POSTGRES_DB=triage",
        "--env=POSTGRES_USER=postgres",
        *env,
        mount,
        "--publish=127.0.0.1::5432",
        IMAGE,
    )
    ready = ["pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", "triage"]
    deadline = time.monotonic() + 60
    while subprocess.run(  # noqa: S603 - docker with fixed arguments
        [DOCKER, "exec", server.container, *ready], capture_output=True, check=False
    ).returncode:
        assert time.monotonic() < deadline, "Postgres never got ready"
        time.sleep(0.5)
    return server


def script(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this repo's script, no outside input
        [sys.executable, str(SCRIPT), *args],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def migrate(server: Postgres, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both chains, as make prod-up runs them after a restore."""
    gateway = server.url("gateway", PASSWORDS["GATEWAY_DB_PASSWORD"])
    agent = server.url("agent", PASSWORDS["AGENT_DB_PASSWORD"])
    monkeypatch.setenv("GATEWAY_DATABASE_URL", gateway)
    monkeypatch.setenv("AGENT_DATABASE_URL", agent.replace("postgresql:", "postgresql+psycopg:"))
    done = subprocess.run(  # noqa: S603 - a fixed command from this file
        [PNPM, "--filter", "@triage/gateway", "db:migrate"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    command.upgrade(Config(str(ROOT / "services" / "agent" / "alembic.ini")), "head")


@dataclass
class Backup:
    source: Postgres
    dump: Path
    run: str


@pytest.fixture(scope="module")
def made(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Backup]:
    run = f"triagecopilot-test-backup-{uuid.uuid4().hex[:8]}"
    folder = tmp_path_factory.mktemp("backups")
    try:
        source = start(f"{run}-source")
        with pytest.MonkeyPatch.context() as monkeypatch:
            migrate(source, monkeypatch)
            seed = [sys.executable, str(ROOT / "scripts" / "seed.py")]
            done = subprocess.run(seed, cwd=ROOT, capture_output=True, text=True, check=False)  # noqa: S603
            assert done.returncode == 0, done.stdout + done.stderr
        done = script("--project", source.project, "backup", BACKUP_DIR=str(folder))
        assert done.returncode == 0, done.stderr
        [dump] = folder.glob("triage-*.dump")
        yield Backup(source, dump, run)
    finally:
        names = docker("ps", "--all", "--quiet", f"--filter=label={LABEL}").split()
        if names:
            docker("rm", "--force", "--volumes", *names)


def test_a_dump_is_compressed_and_in_postgres_s_custom_format(made: Backup) -> None:
    assert made.dump.read_bytes()[:5] == b"PGDMP"
    [size] = made.source.query("SELECT pg_database_size('triage')")
    assert made.dump.stat().st_size < int(size) / 2


def test_a_restore_on_a_fresh_server_brings_everything_across(
    made: Backup, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = start(f"{made.run}-target")
    expected = made.source.contents()
    assert len(expected["tenants"]) == 5

    done = script("--project", target.project, "restore", str(made.dump))
    assert done.returncode == 0, done.stderr
    assert "5 tenants" in done.stdout
    assert target.contents() == expected

    # make prod-up's migrations find nothing to do, and change nothing.
    migrate(target, monkeypatch)
    assert target.contents() == expected

    # Each role logs in with its old password and keeps to its own schema.
    gateway = target.url("gateway", PASSWORDS["GATEWAY_DB_PASSWORD"])
    with psycopg.connect(gateway) as conn:
        assert conn.execute("SELECT count(*) FROM gateway.tenants").fetchone() == (5,)
    agent = target.url("agent", PASSWORDS["AGENT_DB_PASSWORD"])
    with psycopg.connect(agent) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("DELETE FROM gateway.tenants")


def test_a_restore_keeps_the_database_it_replaces(made: Backup) -> None:
    target = start(f"{made.run}-replaced")
    assert script("--project", target.project, "restore", str(made.dump)).returncode == 0
    table = "public.written_after_the_dump"
    target.query(f"CREATE TABLE {table} (id int)")

    done = script("--project", target.project, "restore", str(made.dump))
    assert done.returncode == 0, done.stderr
    assert target.query(f"SELECT to_regclass('{table}')") == []
    kept = sorted(
        target.query("SELECT datname FROM pg_database WHERE datname LIKE 'triage_before_%'")
    )
    # The empty database of the first restore, then the one with the later write.
    assert len(kept) == 2
    assert kept[1] in done.stdout
    assert target.query(f"SELECT to_regclass('{table}')", kept[1]) == ["written_after_the_dump"]


def test_a_dump_cut_short_fails_its_check_and_changes_nothing(made: Backup, tmp_path: Path) -> None:
    whole = made.dump.read_bytes()
    cut = tmp_path / "cut.dump"
    cut.write_bytes(whole[: len(whole) // 2])

    # The check a backup runs before it keeps a dump.
    with cut.open("rb") as dumped:
        check = subprocess.run(  # noqa: S603 - docker with fixed arguments
            [DOCKER, "exec", "-i", made.source.container, "pg_restore", "-f", "/dev/null"],
            stdin=dumped,
            capture_output=True,
            check=False,
        )
    assert check.returncode != 0

    target = start(f"{made.run}-cut")
    assert script("--project", target.project, "restore", str(made.dump)).returncode == 0
    before = target.contents()
    done = script("--project", target.project, "restore", str(cut))
    assert done.returncode != 0
    assert target.contents() == before
    assert len(target.query("SELECT 1 FROM pg_database WHERE datname LIKE 'triage_before_%'")) == 1


def test_a_backup_of_nothing_fails_and_keeps_nothing(tmp_path: Path) -> None:
    done = script("--project", "no-such-project", "backup", BACKUP_DIR=str(tmp_path))
    assert done.returncode != 0
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def bucket(made: Backup) -> str:
    """A real S3 server, rclone's own, on Docker's default network with the script's rclone."""
    name = f"{made.run}-bucket"
    serve = "rclone serve s3 --auth-key tc-key,tc-secret --addr :9000 /data"
    shell = ["--entrypoint=sh", RCLONE, "-c", f"mkdir -p /data/tc-backups && exec {serve}"]
    docker("run", "--detach", f"--label={LABEL}", f"--name={name}", *shell)
    deadline = time.monotonic() + 30
    while subprocess.run(  # noqa: S603 - docker with fixed arguments
        [DOCKER, "exec", name, "nc", "-z", "127.0.0.1", "9000"], capture_output=True, check=False
    ).returncode:
        assert time.monotonic() < deadline, "the S3 server never answered"
        time.sleep(0.5)
    return name


def test_a_backup_reaches_a_bucket_through_the_real_rclone(
    made: Backup, bucket: str, tmp_path: Path
) -> None:
    ips = "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"
    env = {
        "BACKUP_DIR": str(tmp_path),
        "BACKUP_DEST": "s3://tc-backups/nightly",
        "RCLONE_S3_PROVIDER": "Other",
        "RCLONE_S3_ENDPOINT": f"http://{docker('inspect', f'--format={ips}', bucket)}:9000",
        "RCLONE_S3_ACCESS_KEY_ID": "tc-key",
        "RCLONE_S3_SECRET_ACCESS_KEY": "tc-secret",
    }
    done = script("--project", made.source.project, "backup", **env)
    assert done.returncode == 0, done.stderr
    [dump] = tmp_path.glob("triage-*.dump")
    found = f"/data/tc-backups/nightly/{dump.name}"
    assert docker("exec", bucket, "stat", "-c", "%s", found) == str(dump.stat().st_size)
    assert "tc-secret" not in done.stdout + done.stderr

    # A key the bucket refuses fails the backup, with the dump kept and the key not printed.
    wrong = env | {"RCLONE_S3_SECRET_ACCESS_KEY": "not-the-key"}
    refused = script("--project", made.source.project, "backup", **wrong)
    assert refused.returncode != 0
    assert "copy to the bucket" in refused.stderr
    assert "not-the-key" not in refused.stdout + refused.stderr
    assert len(list(tmp_path.glob("triage-*.dump"))) == 2


@pytest.mark.skipif(shutil.which("rsync") is None, reason="needs rsync, which CI's runners have")
def test_a_backup_reaches_an_rsync_target_through_the_real_rsync(
    made: Backup, tmp_path: Path
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    env = {"BACKUP_DIR": str(tmp_path / "local"), "BACKUP_DEST": str(target)}
    done = script("--project", made.source.project, "backup", **env)
    assert done.returncode == 0, done.stderr
    [copied] = target.glob("triage-*.dump")
    assert copied.read_bytes()[:5] == b"PGDMP"
    assert copied.stat().st_mode & 0o777 == 0o600
