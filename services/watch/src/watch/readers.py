"""Hardened readers: the only way watch reaches docker, git and HTTP.

Every command is a fixed argv with no shell, under a timeout, with a minimal environment, so none
of TC's settings or secrets reach a child process. Docker may only list (`ps`) and read logs,
git may only `log` origin/main, and HTTP may only GET the configured URLs, never following a
redirect. `docker inspect` is never run: a container's environment holds keys.
"""

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast
from urllib.parse import urlsplit

import httpx2

from watch.scrub import mask

PROJECT_LABEL = "com.docker.compose.project"
# What a child process may see of watch's environment; everything else, secrets included, stays.
_ENV_KEPT = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "LANG",
        "LC_ALL",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_CONFIG",
    }
)
# Every argument: the letters, digits and punctuation of the fixed commands, nothing a shell reads.
_ARGUMENT = re.compile(r"[\w.:/\\=%{}@+, -]*")
# A line of `docker logs --timestamps`: RFC 3339 in UTC with nanoseconds, a space, the line.
_STAMPED = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z ?(.*)", re.S)


class Refused(Exception):
    """A call watch may not make; nothing was run or sent."""


class ReadError(Exception):
    """An allowed call that failed: no program, a non-zero exit, a timeout or bad output."""


def safe_argument(text: str) -> bool:
    """Whether `text` may be part of a command line watch runs."""
    return _ARGUMENT.fullmatch(text) is not None


def stamp(at: datetime) -> str:
    """UTC to the second, as docker and git take it."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(
    name: str,
    program: str | None,
    argv: Sequence[str],
    timeout: float,
    env: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    bad = next((arg for arg in argv if not safe_argument(arg)), None)
    if bad is not None:
        raise Refused(f"argument {mask(bad)!r} is not allowed")
    if program is None:
        raise ReadError(f"{name} is not on PATH")
    environ = {k: v for k, v in os.environ.items() if k.upper() in _ENV_KEPT} | dict(env or {})
    try:
        # A fixed argv, checked above, and no shell.
        process = subprocess.Popen(  # noqa: S603
            [program, *argv],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environ,
        )
    except OSError as error:
        raise ReadError(f"{name} did not start: {error.strerror}") from None
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # The pipes are left to close on their own: closing them now would wait for any
        # grandchild still holding them.
        process.kill()
        process.wait()
        raise ReadError(f"{name} timed out after {timeout:g} s") from None
    stdout, stderr = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    if process.returncode:
        last = next((line for line in reversed(stderr.splitlines()) if line.strip()), "")
        raise ReadError(f"{name} exit {process.returncode}: {mask(last)[:300]}")
    return stdout, stderr


def _object(line: str, what: str) -> dict[str, Any]:
    try:
        value: object = json.loads(line)
    except ValueError:
        value = None
    if not isinstance(value, dict):
        raise ReadError(f"{what} printed a line that is not a JSON object")
    return cast("dict[str, Any]", value)


def _created(value: str) -> datetime | None:
    """docker's CreatedAt, such as `2026-10-25 08:00:00 +0000 UTC`."""
    try:
        return datetime.strptime(value[:25], "%Y-%m-%d %H:%M:%S %z").astimezone(UTC)
    except ValueError:
        return None


def _stamped(text: str) -> Iterator[tuple[datetime, str]]:
    for line in text.splitlines():
        if found := _STAMPED.fullmatch(line):
            fraction = (found[2] or "0")[:6].ljust(6, "0")
            yield datetime.fromisoformat(f"{found[1]}.{fraction}+00:00"), found[3]


@dataclass(frozen=True)
class Container:
    name: str
    image: str
    state: str
    status: str
    created: datetime | None


class Docker:
    """`docker ps` and `docker logs`, the only docker commands watch runs."""

    SUBCOMMANDS = frozenset({"ps", "logs"})

    def __init__(self, timeout: float = 10.0) -> None:
        self.program = shutil.which("docker")
        self.timeout = timeout
        self._listed: set[str] = set()

    def call(self, subcommand: str, *args: str) -> tuple[str, str]:
        if subcommand not in self.SUBCOMMANDS:
            raise Refused(f"docker {subcommand} is not allowed")
        return _run("docker", self.program, [subcommand, *args], self.timeout)

    def ps(self, project: str, names: Collection[str]) -> list[Container]:
        """The project's containers among `names`, matched exactly and checked for the project's
        compose label: docker's own name filter matches substrings."""
        out, _ = self.call(
            "ps",
            "--all",
            "--no-trunc",
            "--filter",
            f"label={PROJECT_LABEL}={project}",
            "--format",
            "{{json .}}",
        )
        containers: list[Container] = []
        for line in filter(None, out.splitlines()):
            row = _object(line, "docker ps")
            name, labels = str(row.get("Names")), str(row.get("Labels", "")).split(",")
            if name in names and f"{PROJECT_LABEL}={project}" in labels:
                containers.append(
                    Container(
                        name,
                        str(row.get("Image", "")),
                        str(row.get("State", "")),
                        str(row.get("Status", "")),
                        _created(str(row.get("CreatedAt", ""))),
                    )
                )
        self._listed = {container.name for container in containers}
        return containers

    def logs(
        self, container: Container, since: datetime, until: datetime, tail: int
    ) -> list[tuple[datetime, str]]:
        """The container's lines from `since` to `until`, oldest first, stdout and stderr merged."""
        if container.name not in self._listed:
            raise Refused(f"{container.name} is not listed by this reader's last docker ps")
        out, err = self.call(
            "logs",
            "--timestamps",
            "--since",
            stamp(since),
            "--until",
            stamp(until),
            "--tail",
            str(tail),
            "--",
            container.name,
        )
        return sorted([*_stamped(out), *_stamped(err)], key=lambda line: line[0])


@dataclass(frozen=True)
class Commit:
    sha: str
    at: datetime
    subject: str
    paths: tuple[str, ...]


class Git:
    """`git log` of origin/main as last fetched, the only git command watch runs. It never
    fetches; hooks, fsmonitor, textconv and external diff drivers, signature checks, lazy fetches
    of a partial clone, and the user's and system's config are all off. It lists changed paths,
    never file contents."""

    SUBCOMMANDS = frozenset({"log"})
    REF = "refs/remotes/origin/main"
    ENV: ClassVar[dict[str, str]] = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1",
    }

    def __init__(self, repo: Path, timeout: float = 10.0) -> None:
        self.program = shutil.which("git")
        self.repo = repo
        self.timeout = timeout

    def call(self, subcommand: str, *args: str) -> str:
        if subcommand not in self.SUBCOMMANDS:
            raise Refused(f"git {subcommand} is not allowed")
        argv = [
            "-C",
            str(self.repo),
            "--no-pager",
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "log.showSignature=false",
            subcommand,
            *args,
        ]
        return _run("git", self.program, argv, self.timeout, self.ENV)[0]

    def commits(self, since: datetime, limit: int) -> list[Commit]:
        out = self.call(
            "log",
            "--no-textconv",
            "--no-ext-diff",
            "--no-color",
            "--name-only",
            "--format=%x1e%h%x1f%cI%x1f%s",
            f"--since={stamp(since)}",
            f"--max-count={limit}",
            self.REF,
            "--",
        )
        commits: list[Commit] = []
        for record in out.split("\x1e")[1:]:
            head, *paths = record.splitlines() or [""]
            sha, at, subject = [*head.split("\x1f", 2), "", ""][:3]
            try:
                when = datetime.fromisoformat(at).astimezone(UTC)
            except ValueError:
                raise ReadError("git log printed a commit without its date") from None
            commits.append(Commit(sha, when, subject, tuple(filter(None, paths))))
        return commits


def check_url(url: str) -> None:
    """Refuses any URL but plain http or https to a host, without credentials or a fragment."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Refused(f"{mask(url)} is not an http or https URL with a host")
    if "@" in parts.netloc or parts.fragment:
        raise Refused(f"{mask(url)} carries credentials or a fragment")


@dataclass(frozen=True)
class Answer:
    status: int | None
    body: str
    ms: int
    error: str | None


class Http:
    """GET on the configured URLs only: no redirect followed, no proxy or credentials from the
    environment, no cookie kept, and at most MAX_BODY bytes read."""

    MAX_BODY = 64 * 1024

    def __init__(self, urls: Collection[str], timeout: float = 5.0) -> None:
        for url in urls:
            check_url(url)
        self.urls = frozenset(urls)
        self._client = httpx2.Client(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            headers={"user-agent": "triage-copilot-watch"},
        )

    def get(self, url: str) -> Answer:
        if url not in self.urls:
            raise Refused(f"GET {mask(url)} is not a configured URL")
        started = time.perf_counter()
        status, body, error = None, b"", None
        try:
            with self._client.stream("GET", url) as response:
                status = response.status_code
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) >= self.MAX_BODY:
                        break
        except httpx2.HTTPError as caught:
            status, error = None, type(caught).__name__
        finally:
            self._client.cookies.clear()
        ms = round((time.perf_counter() - started) * 1000)
        return Answer(status, body[: self.MAX_BODY].decode("utf-8", "replace"), ms, error)

    def close(self) -> None:
        self._client.close()
