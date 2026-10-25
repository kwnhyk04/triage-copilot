import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from watch_fakes import FakeServer, FakeTools, Route, ps_row

from watch.readers import Docker, Git, Http, ReadError, Refused

PROJECT = "freightaudit-prod"
API, REDIS = "freightaudit-prod-api-1", "freightaudit-prod-redis-1"
SINCE = datetime(2026, 10, 25, 9, 40, tzinfo=UTC)
UNTIL = datetime(2026, 10, 25, 9, 50, tzinfo=UTC)


def _ps(tools: FakeTools, *rows: str) -> None:
    tools.scenario({"docker": {"ps": {"stdout": "".join(f"{row}\n" for row in rows)}}})


def test_ps_keeps_only_exact_catalog_names_with_the_project_label(tools: FakeTools) -> None:
    _ps(
        tools,
        ps_row(API, PROJECT),
        ps_row(REDIS, PROJECT, state="restarting", status="Restarting (1) 4 seconds ago"),
        # The label filter is exact, but docker's name filter matches substrings, and none of
        # these may pass for a catalog container.
        ps_row(f"{API}-old", PROJECT),
        ps_row(f"x{API}", PROJECT),
        ps_row("freightaudit-prod-worker-1", "freightaudit"),
    )
    names = {API, REDIS, "freightaudit-prod-worker-1"}
    containers = Docker().ps(PROJECT, names)
    assert [(c.name, c.state, c.status) for c in containers] == [
        (API, "running", "Up 2 hours (healthy)"),
        (REDIS, "restarting", "Restarting (1) 4 seconds ago"),
    ]
    assert containers[0].created == datetime(2026, 10, 25, 8, tzinfo=UTC)
    [call] = tools.calls()
    assert call["argv"] == [
        "ps",
        "--all",
        "--no-trunc",
        "--filter",
        f"label=com.docker.compose.project={PROJECT}",
        "--format",
        "{{json .}}",
    ]


def test_logs_read_only_listed_containers_with_fixed_flags(tools: FakeTools) -> None:
    docker = Docker()
    _ps(tools, ps_row(API, PROJECT))
    [api] = docker.ps(PROJECT, {API})
    out = "2026-10-25T09:49:00.000000000Z first\n2026-10-25T09:49:30.500000000Z third\n"
    err = "2026-10-25T09:49:10.000000000Z second from stderr\n"
    tools.scenario({"docker": {"logs": {API: {"stdout": out, "stderr": err}}}})
    lines = docker.logs(api, SINCE, UNTIL, tail=200)
    assert [text for _, text in lines] == ["first", "second from stderr", "third"]
    assert lines[2][0] == datetime(2026, 10, 25, 9, 49, 30, 500000, tzinfo=UTC)
    assert tools.calls()[-1]["argv"] == [
        "logs",
        "--timestamps",
        "--since",
        "2026-10-25T09:40:00Z",
        "--until",
        "2026-10-25T09:50:00Z",
        "--tail",
        "200",
        "--",
        API,
    ]


def test_logs_of_a_container_ps_did_not_list_are_refused(tools: FakeTools) -> None:
    lister, other = Docker(), Docker()
    _ps(tools, ps_row(API, PROJECT))
    [api] = lister.ps(PROJECT, {API})
    with pytest.raises(Refused, match="not listed"):
        other.logs(api, SINCE, UNTIL, tail=10)
    assert len(tools.calls()) == 1


@pytest.mark.parametrize(
    "subcommand",
    ["inspect", "exec", "cp", "run", "compose", "restart", "stop", "rm", "events", "system"],
)
def test_other_docker_subcommands_are_refused_before_anything_runs(
    tools: FakeTools, subcommand: str
) -> None:
    with pytest.raises(Refused, match=f"docker {subcommand} is not allowed"):
        Docker().call(subcommand, API)
    assert tools.calls() == []


@pytest.mark.parametrize("arg", ["x;rm -rf /", "$(id)", "a|b", "a&b", "`id`", "a>b", 'a"b'])
def test_arguments_a_shell_would_read_are_refused(tools: FakeTools, arg: str) -> None:
    with pytest.raises(Refused, match="argument"):
        Docker().call("logs", "--", arg)
    assert tools.calls() == []


def test_children_get_none_of_the_environment_s_secrets(
    tools: FakeTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("WATCH_FA_TOKEN", "POSTGRES_PASSWORD", "ANTHROPIC_API_KEY", "GATEWAY_API_KEY"):
        monkeypatch.setenv(name, "canarycanarycanary99")
    _ps(tools)
    Docker().ps(PROJECT, {API})
    [call] = tools.calls()
    assert "PATH" in call["env"]
    assert not [name for name in call["env"] if any(s in name for s in ("TOKEN", "KEY", "PASS"))]


def test_a_hung_docker_times_out(tools: FakeTools) -> None:
    tools.scenario({"docker": {"sleep": 5}})
    started = time.perf_counter()
    with pytest.raises(ReadError, match="timed out"):
        Docker(timeout=0.5).ps(PROJECT, {API})
    assert time.perf_counter() - started < 4


def test_a_failing_docker_is_a_read_error(tools: FakeTools) -> None:
    stderr = "Cannot connect to the Docker daemon. Is the docker daemon running?\n"
    tools.scenario({"docker": {"ps": {"exit": 1, "stderr": stderr}}})
    with pytest.raises(ReadError, match="exit 1: Cannot connect to the Docker daemon"):
        Docker().ps(PROJECT, {API})


def test_without_docker_on_path_nothing_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(ReadError, match="docker is not on PATH"):
        Docker().ps(PROJECT, {API})


def test_git_reads_origin_main_only_with_hooks_and_drivers_off(tools: FakeTools) -> None:
    repo = Path("H:/repos/freight-audit")
    commits = "\x1eabc1234\x1f2026-10-25T09:00:00+00:00\x1fWorker: retry extract\n\nworker/a.ts\n"
    tools.scenario({"git": {"log": {"stdout": commits}}})
    [commit] = Git(repo).commits(SINCE, limit=10)
    assert (commit.sha, commit.subject, commit.paths) == (
        "abc1234",
        "Worker: retry extract",
        ("worker/a.ts",),
    )
    assert commit.at == datetime(2026, 10, 25, 9, tzinfo=UTC)
    [call] = tools.calls()
    assert call["argv"] == [
        "-C",
        str(repo),
        "--no-pager",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "log.showSignature=false",
        "log",
        "--no-textconv",
        "--no-ext-diff",
        "--no-color",
        "--name-only",
        "--format=%x1e%h%x1f%cI%x1f%s",
        "--since=2026-10-25T09:40:00Z",
        "--max-count=10",
        "refs/remotes/origin/main",
        "--",
    ]
    assert call["git_env"] == {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1",
    }


@pytest.mark.parametrize(
    "subcommand",
    ["fetch", "pull", "push", "clone", "checkout", "config", "show", "gc", "worktree", "commit"],
)
def test_other_git_subcommands_are_refused_before_anything_runs(
    tools: FakeTools, subcommand: str
) -> None:
    with pytest.raises(Refused, match=f"git {subcommand} is not allowed"):
        Git(Path("repo")).call(subcommand)
    assert tools.calls() == []


def test_commits_come_from_origin_main_of_a_real_repo(tmp_path: Path) -> None:
    git = shutil.which("git")
    assert git, "the real git, on a throwaway repo only"
    repo = tmp_path / "repo"
    identity = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]

    def run(*argv: str) -> None:
        # The test's own throwaway repo, never an app's.
        subprocess.run([git, "-C", str(repo), *identity, *argv], check=True, capture_output=True)  # noqa: S603

    repo.mkdir()
    run("init", "-q", "-b", "main")
    for name, subject in (("a.txt", "First: add a"), ("b.txt", "Second: add b")):
        (repo / name).write_text(name, encoding="utf-8")
        run("add", name)
        run("commit", "-q", "-m", subject)
    run("update-ref", "refs/remotes/origin/main", "HEAD")
    (repo / "c.txt").write_text("c", encoding="utf-8")
    run("add", "c.txt")
    run("commit", "-q", "-m", "Local only: never on origin")
    commits = Git(repo).commits(datetime(2000, 1, 1, tzinfo=UTC), limit=10)
    assert [(c.subject, c.paths) for c in commits] == [
        ("Second: add b", ("b.txt",)),
        ("First: add a", ("a.txt",)),
    ]


def test_http_gets_only_its_configured_urls(server: FakeServer) -> None:
    url = server.route("/readyz", Route(200, b'{"status":"ok"}'))
    http = Http([url])
    answer = http.get(url)
    assert (answer.status, answer.body, answer.error) == (200, '{"status":"ok"}', None)
    with pytest.raises(Refused, match="not a configured URL"):
        http.get(server.url + "/admin")
    assert [(request.method, request.path) for request in server.requests] == [("GET", "/readyz")]


def test_http_follows_no_redirect(server: FakeServer) -> None:
    target = server.route("/elsewhere", Route(200))
    url = server.route("/readyz", Route(302, headers={"location": target}))
    assert Http([url]).get(url).status == 302
    assert [request.path for request in server.requests] == ["/readyz"]


@pytest.mark.parametrize(
    "url",
    [
        "https://user:canarycanarypass09@h.example/readyz",
        "file:///etc/hosts",
        "ftp://h.example/",
        "http:///readyz",
        "https://h.example/readyz#part",
    ],
)
def test_http_refuses_urls_with_credentials_or_other_schemes(url: str) -> None:
    with pytest.raises(Refused):
        Http([url])


def test_http_bounds_time_and_size(server: FakeServer) -> None:
    slow = server.route("/slow", Route(200, b"late", delay=3))
    big = server.route("/big", Route(200, b"x" * 1_000_000))
    http = Http([slow, big], timeout=0.5)
    started = time.perf_counter()
    answer = http.get(slow)
    assert (answer.status, answer.error) == (None, "ReadTimeout")
    assert time.perf_counter() - started < 2.5
    assert len(http.get(big).body) == Http.MAX_BODY


def test_http_sends_no_cookie_and_uses_no_proxy(
    server: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    cookie = {"set-cookie": "session=canarycanarycanary98"}
    url = server.route("/readyz", Route(200, headers=cookie))
    http = Http([url])
    http.get(url)
    http.get(url)
    assert [request.headers.get("cookie") for request in server.requests] == [None, None]
