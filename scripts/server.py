"""What production's host runs (docs/server-move.md), each through make: `backup` dumps the
database, `restore` loads a dump, and `heartbeat` tells an outside monitor the gateway is ready.

A backup is a compressed pg_dump in BACKUP_DIR, read back in full before it counts. The newest
BACKUP_KEEP stay, and BACKUP_DEST, when set, gets a copy. A restore loads a dump into a database of
its own and swaps it in only once that worked, so a bad dump changes nothing and the old database
stays, renamed. Production's Postgres publishes no port, so each database step runs inside its
container, where the postgres user needs no password on the socket: no secret is on a command line.
The standard library alone, so the nightly timer needs no environment synced first.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, NoReturn

DOCKER = shutil.which("docker") or "docker"
RSYNC = shutil.which("rsync") or "rsync"
# Multi-arch, by digest, so the server needs no rclone of its own.
RCLONE = (
    "rclone/rclone:1.75.2@sha256:2687085f718d3c628f7fdfb77c52a1d344332aed543110bb88088f2c70d43eb5"
)
DATABASE = "triage"
SCRATCH = "triage_restore"
COUNTS = (
    "SELECT (SELECT count(*) FROM gateway.tenants), (SELECT count(*) FROM agent.investigations)"
)
# The longest a dump, a restore or a copy may run: the database is far under a gigabyte.
TIMEOUT = 1800
# A bucket and a prefix, with no room for credentials.
S3_DEST = re.compile(r"s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9](/[\w./-]*)?")
# rsync's [user@]host:path, or an absolute path such as a mounted disk; never an option or a URL.
RSYNC_DEST = re.compile(r"(?!-)(?:[\w.-]+@)?[\w.-]+:/?[\w.-][\w./-]*|/[\w.-][\w./-]*")
PING_URL = re.compile(r"https?://\S+")


class Failed(Exception):
    """A step that failed; what ran before it stays as it was."""


@dataclass(frozen=True)
class Settings:
    folder: Path
    keep: int
    dest: str
    ping: str


def refuse(message: str) -> NoReturn:
    sys.exit(f"server: {message}")


def settings() -> Settings:
    """BACKUP_* from the environment. A bad value stops it naming the setting, never its value."""
    keep = os.environ.get("BACKUP_KEEP") or "7"
    if not keep.isdecimal() or int(keep) < 1:
        refuse("BACKUP_KEEP takes a whole number of at least 1")
    dest = os.environ.get("BACKUP_DEST", "")
    if S3_DEST.fullmatch(dest):
        keys = ("RCLONE_S3_ACCESS_KEY_ID", "RCLONE_S3_SECRET_ACCESS_KEY")
        missing = [name for name in keys if not os.environ.get(name)]
        if missing:
            refuse(f"an s3:// BACKUP_DEST needs {' and '.join(missing)}")
    elif dest and not RSYNC_DEST.fullmatch(dest):
        refuse("BACKUP_DEST takes s3://bucket/prefix, or an rsync target such as user@host:/path")
    ping = os.environ.get("BACKUP_PING_URL", "")
    if ping and not PING_URL.fullmatch(ping):
        refuse("BACKUP_PING_URL takes an http or https URL")
    folder = Path(os.environ.get("BACKUP_DIR") or "backups")
    return Settings(folder, int(keep), dest, ping)


def run(
    what: str,
    argv: list[str],
    *,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | int | None = None,
) -> bytes:
    """One command under the timeout, with a fixed argv and no shell; its stdout, if piped."""
    source = subprocess.DEVNULL if stdin is None else stdin
    try:
        done = subprocess.run(  # noqa: S603 - fixed arguments, and settings checked above
            argv, stdin=source, stdout=stdout, check=True, timeout=TIMEOUT
        )
    except subprocess.CalledProcessError as error:
        raise Failed(f"{what} failed with exit {error.returncode}") from None
    except subprocess.TimeoutExpired:
        raise Failed(f"{what} timed out after {TIMEOUT} s") from None
    except OSError as error:
        raise Failed(f"{what} did not start: {error.strerror}") from None
    return done.stdout or b""


def get(what: str, url: str) -> None:
    """GET under a timeout. A failure names `what`: a ping URL works as a credential."""
    try:
        with urllib.request.urlopen(url, timeout=10):  # noqa: S310 - http or https, checked above
            pass
    except OSError as error:
        raise Failed(f"{what} failed: {error}") from None


def copy_off(path: Path, dest: str) -> None:
    """A copy to the bucket, or by rsync over ssh. rclone comes from its image and takes each key
    from the environment by name, so no key is on a command line. Its three options keep it to one
    PUT, so a key that may only write works."""
    if dest.startswith("s3://"):
        keys = [
            f"--env={name}"
            for name, value in sorted(os.environ.items())
            if name.startswith("RCLONE_") and value
        ]
        mount = f"--mount=type=bind,source={path.parent.resolve()},target=/backups,readonly"
        remote = f":s3:{dest.removeprefix('s3://').rstrip('/')}/{path.name}"
        copy = ["copyto", f"/backups/{path.name}", remote]
        options = ["--s3-no-check-bucket", "--no-check-dest", "--s3-no-head"]
        run(
            "the copy to the bucket", [DOCKER, "run", "--rm", *keys, mount, RCLONE, *copy, *options]
        )
    else:
        ssh = ["-e", "ssh -o BatchMode=yes"]
        target = dest.rstrip("/") + "/"
        run("rsync", [RSYNC, "--timeout=300", "--chmod=F600", *ssh, str(path), target])


def backup(project: str, config: Settings) -> None:
    config.folder.mkdir(parents=True, exist_ok=True)
    container = f"{project}-postgres-1"
    final = config.folder / f"{DATABASE}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.dump"
    partial = final.with_suffix(".partial")
    try:
        with partial.open("wb") as out:
            dump = ["exec", container, "pg_dump", "-U", "postgres", "-Fc", DATABASE]
            run("pg_dump", [DOCKER, *dump], stdout=out)
        # Reads every block, which a cut-off or damaged dump fails; --list reads only its contents.
        with partial.open("rb") as dumped:
            check = ["exec", "-i", container, "pg_restore", "-f", "/dev/null"]
            run("the dump's check", [DOCKER, *check], stdin=dumped)
        partial.replace(final)
    finally:
        partial.unlink(missing_ok=True)
    for old in sorted(config.folder.glob(f"{DATABASE}-*.dump"))[: -config.keep]:
        old.unlink()
    print(f"backup: {final}, {final.stat().st_size} bytes")  # noqa: T201 - the CLI's output
    if config.dest:
        copy_off(final, config.dest)
        print(f"backup: copied to {config.dest}")  # noqa: T201
    else:
        print("backup: no off-machine copy: BACKUP_DEST is empty")  # noqa: T201
    if config.ping:
        get("the outside check's ping", config.ping)


def psql(container: str, statement: str) -> None:
    options = ["-q", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres"]
    run("psql", [DOCKER, "exec", container, "psql", *options, "-c", statement])


def restore(project: str, dump: Path) -> None:
    if not dump.is_file():
        refuse(f"{dump} is not a file")
    container = f"{project}-postgres-1"
    running = run("docker ps", [DOCKER, "ps", "--format", "{{.Names}}"], stdout=subprocess.PIPE)
    apps = (f"{project}-gateway-1", f"{project}-agent-1")
    busy = [name for name in apps if name in running.decode().split()]
    if busy:
        refuse(f"stop {' and '.join(busy)} first: the apps hold the database open and write to it")
    # A leftover of a failed restore goes first; nothing else is ever dropped.
    psql(container, f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)")
    psql(container, f"CREATE DATABASE {SCRATCH}")
    with dump.open("rb") as dumped:
        load = ["exec", "-i", container, "pg_restore", "-U", "postgres", "-d", SCRATCH]
        run("pg_restore", [DOCKER, *load, "--single-transaction", "--exit-on-error"], stdin=dumped)
    kept = f"{DATABASE}_before_{datetime.now(UTC):%Y%m%d_%H%M%S}"
    # One transaction. A dump holds no database-level grants, so postgres-init.sql's are made here.
    psql(
        container,
        f"ALTER DATABASE {DATABASE} RENAME TO {kept}; "
        f"ALTER DATABASE {SCRATCH} RENAME TO {DATABASE}; "
        f"REVOKE ALL ON DATABASE {DATABASE} FROM PUBLIC; "
        f"GRANT CONNECT ON DATABASE {DATABASE} TO gateway, agent; "
        f"GRANT CREATE ON DATABASE {DATABASE} TO gateway;",
    )
    counts = ["psql", "-U", "postgres", "-d", DATABASE, "-tA", "-c", COUNTS]
    found = run("psql", [DOCKER, "exec", container, *counts], stdout=subprocess.PIPE)
    tenants, investigations = found.decode().strip().split("|")
    print(  # noqa: T201 - the CLI's output
        f"restore: {DATABASE} now holds {tenants} tenants and {investigations} investigations; "
        f"the old database stays as {kept}"
    )


def heartbeat() -> None:
    """Pings the outside check only while the gateway answers: its /readyz on the proxy's port on
    the host, which takes the host, Docker, the proxy, the gateway and Postgres to pass."""
    url = os.environ.get("HEARTBEAT_URL", "")
    if not PING_URL.fullmatch(url):
        refuse("HEARTBEAT_URL takes the outside check's http or https URL")
    port = os.environ.get("PROD_GATEWAY_PORT") or "4202"
    get("the gateway's /readyz", f"http://127.0.0.1:{port}/readyz")
    get("the outside check's ping", url)


def main() -> None:
    parser = argparse.ArgumentParser(description="Production's backup, restore and heartbeat.")
    parser.add_argument("--project", default="triagecopilot-prod", help="the compose project")
    steps = parser.add_subparsers(dest="step", required=True)
    steps.add_parser("backup")
    steps.add_parser("restore").add_argument("file", type=Path)
    steps.add_parser("heartbeat")
    args = parser.parse_args()
    # Owner-only: a dump holds every report and the tenants' token hashes.
    os.umask(0o077)
    try:
        if args.step == "backup":
            backup(args.project, settings())
        elif args.step == "restore":
            restore(args.project, args.file)
        else:
            heartbeat()
    except Failed as failed:
        refuse(str(failed))


if __name__ == "__main__":
    main()
