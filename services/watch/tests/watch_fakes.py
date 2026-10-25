"""A fake docker and git on PATH and loopback fake servers, so no test reaches a real container,
repo or network.

The fakes answer from `scenario.json` beside them and append each call (argv, the names of the
environment variables they got, and the values of the GIT_* ones) to `calls.jsonl`.
"""

import json
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

FAKE = r"""
import json, os, sys, time
from pathlib import Path

here = Path(__file__).parent
tool, argv = sys.argv[1], sys.argv[2:]
git_env = {k: v for k, v in os.environ.items() if k.startswith("GIT_")}
with (here / "calls.jsonl").open("a", encoding="utf-8") as calls:
    call = {"tool": tool, "argv": argv, "env": sorted(os.environ), "git_env": git_env}
    calls.write(json.dumps(call) + "\n")
scenario = json.loads((here / "scenario.json").read_text(encoding="utf-8")).get(tool, {})
sub, rest = "", iter(argv)
for arg in rest:
    if arg in ("-C", "-c"):
        next(rest)
    elif not arg.startswith("-"):
        sub = arg
        break
answer = scenario.get(sub, {})
if sub == "logs":
    answer = answer.get(argv[-1], {})
time.sleep(scenario.get("sleep", 0))
sys.stdout.write(answer.get("stdout", ""))
sys.stderr.write(answer.get("stderr", ""))
sys.exit(answer.get("exit", 0))
"""


def ps_row(
    name: str,
    project: str,
    *,
    state: str = "running",
    status: str = "Up 2 hours (healthy)",
    image: str = "ghcr.io/example/app:sha-1234567",
    created: str = "2026-10-25 08:00:00 +0000 UTC",
) -> str:
    """One line of `docker ps --format '{{json .}}'`, with the label list docker prints."""
    labels = (
        f"com.docker.compose.project={project},com.docker.compose.service={name},"
        "com.docker.compose.project.config_files=a.yml,b.yml"
    )
    return json.dumps(
        {
            "Command": '"docker-entrypoint.s…"',
            "CreatedAt": created,
            "ID": "0123456789ab",
            "Image": image,
            "Labels": labels,
            "Names": name,
            "Networks": f"{project}_data",
            "RunningFor": "2 hours ago",
            "State": state,
            "Status": status,
        }
    )


class FakeTools:
    """`docker` and `git` launchers in their own folder, which is all of PATH."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        script = folder / "fake_tool.py"
        script.write_text(FAKE, encoding="utf-8")
        # Isolated and without site, which the stdlib-only fake never needs: it starts faster.
        python = f'"{sys.executable}" -I -S "{script}"'
        for tool in ("docker", "git"):
            if os.name == "nt":
                launcher = folder / f"{tool}.cmd"
                launcher.write_text(f"@{python} {tool} %*\r\n", encoding="utf-8")
            else:
                launcher = folder / tool
                launcher.write_text(f'#!/bin/sh\nexec {python} {tool} "$@"\n', encoding="utf-8")
                launcher.chmod(0o755)
        self.scenario({})

    def scenario(self, scenario: dict[str, Any]) -> None:
        (self.folder / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")

    def calls(self) -> list[dict[str, Any]]:
        path = self.folder / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@dataclass
class Route:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict[str, str])
    delay: float = 0.0


@dataclass
class Request:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


Answer = Route | Callable[[Request], Route]


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        """A client that gave up, as a test's timeout does, is expected."""


class FakeServer:
    """A loopback HTTP server that answers from its routes and records every request."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Answer] = {}
        self.requests: list[Request] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def answer(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                headers = {name.lower(): value for name, value in self.headers.items()}
                request = Request(self.command, self.path, headers, self.rfile.read(length))
                server.requests.append(request)
                found = server.routes.get((self.command, self.path.partition("?")[0]), Route(404))
                route = found(request) if callable(found) else found
                threading.Event().wait(route.delay)
                self.send_response(route.status)
                for name, value in route.headers.items():
                    self.send_header(name, value)
                self.send_header("content-length", str(len(route.body)))
                self.end_headers()
                self.wfile.write(route.body)

            def do_GET(self) -> None:
                self.answer()

            def do_POST(self) -> None:
                self.answer()

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = _QuietServer(("127.0.0.1", 0), Handler)
        serve = {"poll_interval": 0.05}  # so close() returns at once
        threading.Thread(target=self._server.serve_forever, kwargs=serve, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def route(self, path: str, answer: Answer, method: str = "GET") -> str:
        self.routes[(method, path)] = answer
        return self.url + path

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
