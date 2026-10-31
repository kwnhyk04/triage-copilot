"""Production's egress proxy, live, on throwaway networks: the real squid image and squid.conf,
with the compose file's networks and hardening, at test addresses so it runs beside production.
Each client tunnels to its own hosts only, everything else gets 403, and watch's port reaches the
gateway alone. Allowed tunnels open to the real hosts; nothing is sent through them.
"""

import re
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from test_prod_compose import INFRA, PROD, SQUID_CONF, address

pytestmark = pytest.mark.integration

DOCKER = shutil.which("docker") or "docker"
IMAGE = "triagecopilot-squid:test"
# Any HTTP server on 4200 stands in for the gateway: toxiproxy's API, an image make infra-up pulls.
FAKE_GATEWAY = "ghcr.io/shopify/toxiproxy:2.12.0"
# Production's fixed addresses, moved to a block of their own so the test runs beside production.
PROD_BLOCK, TEST_BLOCK = "10.231.233.", "10.231.239."
LABEL = "triagecopilot.test=egress"
CLIENTS = ("agent", "tailscale", "other")
# Names in the relays' pattern that resolve to non-public addresses inside the proxy: a private
# address is refused by every address a name resolves to, even for an allowed name.
PRIVATE = {
    "derp901.tailscale.com": "10.0.0.1",
    "derp902.tailscale.com": "127.0.0.1",
    "derp903.tailscale.com": "169.254.169.254",
    "derp904.tailscale.com": "100.100.100.100",
    "derp905.tailscale.com": "192.168.65.254",
    "derp906.tailscale.com": "172.17.0.1",
    "derp907.tailscale.com": "fd00::1",
    "derp908.tailscale.com": "0.0.0.0",  # noqa: S104 - a destination to refuse, not a bind
    "derp909.tailscale.com": "fe80::1",
    "derp910.tailscale.com": "64:ff9b::7f00:1",
    "derp911.tailscale.com": "ff02::1",
}
# Asks the proxy one request line from inside a client's container and prints the status code.
ASK = (
    'exec 3<>/dev/tcp/egress-proxy/"$1" || exit 2; '
    'printf "%s HTTP/1.1\\r\\nHost: %s\\r\\n\\r\\n" "$2" "$3" >&3; '
    'IFS=" " read -r -t 20 _ status _ <&3 || exit 3; echo "$status"'
)


def moved(prod: str) -> str:
    """A production address or range, in the test block."""
    assert prod.startswith(PROD_BLOCK), prod
    return TEST_BLOCK + prod.removeprefix(PROD_BLOCK)


GATEWAY = moved(address("gateway", "ingress"))
OPEN, REFUSED = 200, 403
# Squid's own answer to a CONNECT on an accelerator port, before any rule and any peer.
NO_TUNNEL = 405
CASES = [
    # Each client's own hosts, on 443.
    ("agent", 3128, "CONNECT api.anthropic.com:443", OPEN),
    ("agent", 3128, "CONNECT generativelanguage.googleapis.com:443", OPEN),
    ("agent", 3128, "CONNECT integrate.api.nvidia.com:443", OPEN),
    ("tailscale", 3129, "CONNECT controlplane.tailscale.com:443", OPEN),
    ("tailscale", 3129, "CONNECT derp1.tailscale.com:443", OPEN),
    ("tailscale", 3129, "CONNECT acme-v02.api.letsencrypt.org:443", OPEN),
    # Another client's host, port or address.
    ("agent", 3128, "CONNECT controlplane.tailscale.com:443", REFUSED),
    ("tailscale", 3129, "CONNECT api.anthropic.com:443", REFUSED),
    ("agent", 3129, "CONNECT controlplane.tailscale.com:443", REFUSED),
    ("tailscale", 3128, "CONNECT api.anthropic.com:443", REFUSED),
    ("other", 3128, "CONNECT api.anthropic.com:443", REFUSED),
    ("other", 3129, "CONNECT controlplane.tailscale.com:443", REFUSED),
    # No plain request, no other port, no other name: look-alikes and Tailscale's log server too.
    ("agent", 3128, "GET http://api.anthropic.com/", REFUSED),
    ("agent", 3128, "CONNECT api.anthropic.com:80", REFUSED),
    ("agent", 3128, "CONNECT example.com:443", REFUSED),
    ("agent", 3128, "CONNECT api.anthropic.com.example.com:443", REFUSED),
    ("agent", 3128, "CONNECT 1.1.1.1:443", REFUSED),
    ("tailscale", 3129, "CONNECT log.tailscale.com:443", REFUSED),
    ("tailscale", 3129, "CONNECT derp1.tailscale.com.example.com:443", REFUSED),
    # This PC, the containers beside the proxy and the private ranges, by address and by name.
    ("agent", 3128, "CONNECT host.docker.internal:443", REFUSED),
    ("agent", 3128, f"CONNECT {GATEWAY}:4200", REFUSED),
    ("agent", 3128, "CONNECT 127.0.0.1:443", REFUSED),
    ("agent", 3128, "CONNECT [::1]:443", REFUSED),
    *[("tailscale", 3129, f"CONNECT {name}:443", REFUSED) for name in PRIVATE],
    # watch's port opens no tunnel for a client either.
    ("agent", 4202, "CONNECT api.anthropic.com:443", NO_TUNNEL),
]


def docker(*args: str, timeout: float = 120) -> str:
    done = subprocess.run(  # noqa: S603 - docker with fixed arguments
        [DOCKER, *args], capture_output=True, text=True, check=False, timeout=timeout
    )
    assert done.returncode == 0, f"docker {args[0]}: {done.stderr.strip()}"
    return done.stdout.strip()


def _quietly(*args: str) -> None:
    """A cleanup step; whatever was never created is skipped."""
    subprocess.run([DOCKER, *args], capture_output=True, check=False)  # noqa: S603


def _start(name: str, *args: str) -> None:
    docker("run", "--detach", f"--label={LABEL}", f"--name={name}", *args)


def _conf() -> str:
    """squid.conf with its fixed addresses, each one that compose pins, moved to the test block."""
    conf = SQUID_CONF.read_text("utf-8")
    pinned = {address("agent", "outbound"), address("tailscale", "outbound")}
    pinned.add(address("gateway", "ingress"))
    assert set(re.findall(r"10\.231\.233\.\d+", conf)) == pinned
    return conf.replace(PROD_BLOCK, TEST_BLOCK)


def _network(name: str, prod: str) -> None:
    settings = PROD["networks"][prod]
    args = ["--internal"] if settings.get("internal") else []
    for config in settings.get("ipam", {}).get("config", []):
        args += [f"--subnet={moved(config['subnet'])}", f"--ip-range={moved(config['ip_range'])}"]
    docker("network", "create", f"--label={LABEL}", *args, name)


def _wait(container: str, host: str, port: int) -> None:
    """Until a connection from inside the container to host:port opens."""
    probe = ["exec", container, "bash", "-c", f"echo > /dev/tcp/{host}/{port}"]
    for _ in range(60):
        done = subprocess.run([DOCKER, *probe], capture_output=True, check=False)  # noqa: S603
        if done.returncode == 0:
            return
        time.sleep(0.5)
    pytest.fail(f"{host}:{port} never answered inside {container}")


@dataclass
class Proxy:
    name: str
    clients: dict[str, str]
    watch_port: int

    def ask(self, client: str, port: int, request: str) -> int:
        """The proxy's status for one request line, sent from inside the client's container."""
        target = request.split()[1]
        host = target.split("/")[2] if "://" in target else target
        script = ["bash", "-c", ASK, "_", str(port), request, host]
        return int(docker("exec", self.clients[client], *script))


@pytest.fixture(scope="module")
def proxy(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Proxy]:
    run = f"triagecopilot-test-egress-{uuid.uuid4().hex[:8]}"
    nets = {prod: f"{run}-{prod}" for prod in ("outbound", "ingress", "egress")}
    names = {who: f"{run}-{who}" for who in ("proxy", "gateway", *CLIENTS)}
    conf = tmp_path_factory.mktemp("squid") / "squid.conf"
    conf.write_text(_conf(), "utf-8")
    service = PROD["services"]["egress-proxy"]
    # As compose runs it: read-only, no capability, its memory limit, and a tmpfs for each path
    # squid writes.
    hardening = ["--read-only", f"--memory={service['mem_limit']}"]
    hardening += [f"--cap-drop={cap}" for cap in service["cap_drop"]]
    hardening += [f"--tmpfs={path}" for path in service["tmpfs"]]
    hardening += [f"--security-opt={opt}" for opt in service["security_opt"]]
    hardening += [f"--add-host={name}={ip}" for name, ip in PRIVATE.items()]
    mount = f"--mount=type=bind,source={conf},target=/etc/squid/squid.conf,readonly"
    try:
        dockerfile = f"--file={INFRA / 'docker' / 'squid.Dockerfile'}"
        docker("build", "-q", f"--tag={IMAGE}", dockerfile, str(INFRA / "squid"), timeout=900)
        for prod, net in nets.items():
            _network(net, prod)
        fake = [FAKE_GATEWAY, "-host=0.0.0.0", "-port=4200"]
        _start(names["gateway"], f"--network={nets['ingress']}", f"--ip={GATEWAY}", *fake)
        egress = [f"--network={nets['egress']}", "--publish=127.0.0.1::4202", mount, IMAGE]
        docker("create", f"--label={LABEL}", f"--name={names['proxy']}", *hardening, *egress)
        docker("network", "connect", "--alias=egress-proxy", nets["outbound"], names["proxy"])
        docker("network", "connect", nets["ingress"], names["proxy"])
        docker("start", names["proxy"])
        for who in CLIENTS:
            fixed = [] if who == "other" else [f"--ip={moved(address(who, 'outbound'))}"]
            idle = ["--entrypoint=sleep", IMAGE, "infinity"]
            _start(names[who], f"--network={nets['outbound']}", *fixed, *idle)
        _wait(names["proxy"], "127.0.0.1", 3128)
        _wait(names["proxy"], GATEWAY, 4200)
        published = docker("port", names["proxy"], "4202/tcp").splitlines()[0]
        clients = {who: names[who] for who in CLIENTS}
        yield Proxy(names["proxy"], clients, int(published.rsplit(":", 1)[1]))
    finally:
        _quietly("rm", "--force", *names.values())
        _quietly("network", "rm", *nets.values())


@pytest.mark.parametrize(("client", "port", "request_line", "status"), CASES)
def test_each_client_reaches_only_its_own_hosts(
    proxy: Proxy, client: str, port: int, request_line: str, status: int
) -> None:
    assert proxy.ask(client, port, request_line) == status


def test_the_proxy_idles_well_under_its_memory_limit(proxy: Proxy) -> None:
    # Squid sizes its tables by its descriptor limit: at Docker's 1,048,576 it idled at 153 MiB of
    # its 256, before any traffic.
    used = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", proxy.name)
    found = re.match(r"([\d.]+)(KiB|MiB|GiB)", used)
    assert found, used
    mib = float(found[1]) * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}[found[2]]
    limit = int(PROD["services"]["egress-proxy"]["mem_limit"].removesuffix("m"))
    assert mib < limit / 4, used


def _through_watch_port(proxy: Proxy, request_line: str) -> tuple[int, bytes]:
    with socket.create_connection(("127.0.0.1", proxy.watch_port), timeout=20) as conn:
        conn.sendall(f"{request_line} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
        reply = b""
        while chunk := conn.recv(65536):
            reply += chunk
    head, _, body = reply.partition(b"\r\n\r\n")
    return int(head.split()[1]), body


def test_watch_s_port_reaches_the_gateway_alone(proxy: Proxy) -> None:
    # toxiproxy's version answers for the gateway.
    status, body = _through_watch_port(proxy, "GET /version")
    assert (status, b"2.12" in body) == (200, True)
    # A full URL still reaches the gateway, never the host it names.
    status, body = _through_watch_port(proxy, "GET http://example.com/version")
    assert (status, b"2.12" in body) == (200, True)
    assert _through_watch_port(proxy, "CONNECT example.com:443")[0] == NO_TUNNEL
    # Squid's cache manager would show its traffic and config to any process on this PC.
    assert _through_watch_port(proxy, "GET cache_object://localhost/info")[0] == REFUSED
