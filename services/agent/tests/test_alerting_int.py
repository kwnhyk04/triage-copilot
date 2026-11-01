"""Production's alert delivery, live, on throwaway networks: the real Alertmanager image with
infra/alertmanager and its entrypoint, the real egress proxy (image and squid.conf), the mail relay
and egress-log as compose runs them, at test addresses so the test runs beside production.

Discord and Gmail are fakes with certificates from a test CA, under the names the proxy allows, on
a network of their own with no route out, at documentation addresses (203.0.113.0/24) that the
proxy counts as public. Alerts reach the two fakes, one message per rule, and nothing else; no
message holds a secret or a link but its runbook's; a missing or malformed setting stops
Alertmanager naming it and never its value; and egress-log counts each of the proxy's refusals.
"""

import json
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email import message_from_string, policy
from email.message import EmailMessage
from pathlib import Path
from typing import Any, cast

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from test_alert_rules import LINK
from test_egress_int import ASK, IMAGE, docker
from test_prod_compose import INFRA, PROD, SQUID_CONF, address

pytestmark = pytest.mark.integration

DOCKER = shutil.which("docker") or "docker"
LABEL = "triagecopilot.test=alerting"
# Production's fixed addresses, moved to a block of their own (the egress test has 10.231.239).
PROD_BLOCK, TEST_BLOCK = "10.231.233.", "10.231.238."
# The fakes' network: documentation addresses, which the proxy's non-public ranges leave out.
INTERNET = "203.0.113.0/24"
DISCORD, GMAIL, NOWHERE = "203.0.113.10", "203.0.113.11", "203.0.113.99"
_FROM = re.compile(r"^FROM (python:\S+) AS runtime$", re.MULTILINE)
_python = _FROM.search((INFRA / "docker" / "python.Dockerfile").read_text("utf-8"))
assert _python
PYTHON = _python[1]
FAKES = Path(__file__).with_name("fake_receivers.py")
SERVICE = PROD["services"]["alertmanager"]
# The four settings as the user pastes them; the webhook's token and the password are secret.
TOKEN = f"webhook-token-{uuid.uuid4().hex}"
PASSWORD = f"apppassword{uuid.uuid4().hex[:8]}"
SETTINGS = {
    "ALERT_DISCORD_WEBHOOK_URL": f"https://discord.com/api/webhooks/1234567890/{TOKEN}",
    "ALERT_SMTP_PASSWORD": PASSWORD,
    "ALERT_EMAIL_FROM": "tc.alerts@example.com",
    "ALERT_EMAIL_TO": "oncall@example.com,second@example.com",
}
# What production's rules would send (prometheus-alerts.yml, its templates expanded): two alerts
# of one rule and one of another.
GATEWAY = (
    "The gateway is down or fails its readiness check: no alert reaches an investigation, and the "
    "web shows the demo."
)
FAILED = "{} investigation messages failed in the last hour ({})."
SENT = [
    ("InvestigationsFailing", "warning", {"kind": "dlq"}, FAILED.format(2, "dlq")),
    ("InvestigationsFailing", "warning", {"kind": "unreadable"}, FAILED.format(1, "unreadable")),
    ("GatewayNotReady", "critical", {"service": "gateway"}, GATEWAY),
]


def moved(prod: str) -> str:
    assert prod.startswith(PROD_BLOCK), prod
    return TEST_BLOCK + prod.removeprefix(PROD_BLOCK)


ALERTMANAGER = moved(address("alertmanager", "alerting"))
RELAY = moved(address("smtp-relay", "alerting"))
EGRESS_LOG = moved(address("egress-log", "alerting"))


def certificates(folder: Path) -> None:
    """A test CA (ca.pem), and from it a server certificate for each fake under its real name."""
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TC alerting test CA")])

    def issue(subject: x509.Name, key: ec.EllipticCurvePrivateKey) -> x509.CertificateBuilder:
        return (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=1))
        )

    ca = (
        issue(ca_name, ca_key)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    (folder / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    for name, host in (("discord", "discord.com"), ("gmail", "smtp.gmail.com")):
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
        server = x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH])
        cert = (
            issue(subject, key)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .add_extension(server, critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        (folder / f"{name}.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        private = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        (folder / f"{name}-key.pem").write_bytes(private)


def hardening(service: dict[str, Any]) -> list[str]:
    """docker run flags for a service's read-only, no-capability settings in compose."""
    flags = ["--read-only"] if service.get("read_only") else []
    flags += [f"--cap-drop={cap}" for cap in service.get("cap_drop", [])]
    flags += [f"--security-opt={opt}" for opt in service.get("security_opt", [])]
    return flags + [f"--tmpfs={path}" for path in service.get("tmpfs", [])]


def mount(source: Path, target: str) -> str:
    # Docker Desktop creates a missing bind source as an empty directory, here in the repo.
    assert source.exists(), source
    return f"--mount=type=bind,source={source},target={target},readonly"


def until[T](
    check: Callable[[], T | None],
    what: str,
    seconds: float = 120,
    hint: Callable[[], str] | None = None,
) -> T:
    """check()'s first answer that is not None, asked every second; on a timeout, hint()'s text."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if (found := check()) is not None:
            return found
        time.sleep(1)
    pytest.fail(f"timed out waiting for {what}" + (f":\n{hint()}" if hint else ""))


def quietly(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - docker with fixed arguments
        [DOCKER, *args], capture_output=True, text=True, check=False, timeout=120
    )


def answers(container: str, *probe: str) -> Callable[[], bool | None]:
    """A check for until(): True once the probe exits 0 inside the container."""

    def check() -> bool | None:
        return True if quietly("exec", container, *probe).returncode == 0 else None

    return check


@dataclass
class Delivery:
    names: dict[str, str]

    def received(self, fake: str) -> list[dict[str, Any]]:
        script = "cat /tmp/received.jsonl 2>/dev/null || true"
        lines = docker("exec", self.names[fake], "sh", "-c", script).splitlines()
        return [json.loads(line) for line in lines if line]

    def ask(self, port: int, request: str) -> int:
        """The proxy's status for a request line sent from the relay's container and address."""
        target = request.split()[1]
        script = ["bash", "-c", ASK, "_", str(port), request, target]
        return int(docker("exec", self.names["relay"], *script))

    def denied(self) -> float:
        """egress-log's count of the proxy's refusals, 0 before the first."""
        url = "http://egress-log:8889/metrics"
        text = docker("exec", self.names["alertmanager"], "wget", "-qO-", url)
        found = re.search(r"^tc_egress_denied_total(?:\{[^}]*\})? (\S+)$", text, re.MULTILINE)
        return float(found[1]) if found else 0.0

    def tunnels(self, since: float) -> set[tuple[str, str, str, str]]:
        """The proxy's access log since then, as client, result, method and target."""
        fields = [line.split() for line in docker("logs", self.names["proxy"]).splitlines()]
        return {(f[2], f[3], f[5], f[6]) for f in fields if len(f) > 6 and float(f[0]) >= since}

    def logs(self) -> str:
        """Each container's last log lines, for a failure's message; the values are the test's."""
        tails: list[str] = []
        for name in ("alertmanager", "relay", "proxy", "egress-log"):
            done = quietly("logs", "--tail", "30", self.names[name])
            tails.append(f"--- {name}\n{done.stdout}{done.stderr}")
        return "\n".join(tails)


@pytest.fixture(scope="module")
def delivery(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Delivery]:
    run = f"triagecopilot-test-alerting-{uuid.uuid4().hex[:8]}"
    nets = {name: f"{run}-{name}" for name in ("alerting", "internet")}
    who = ("proxy", "discord", "gmail", "egress-log", "relay", "alertmanager")
    names = {name: f"{run}-{name}" for name in who}
    certs = tmp_path_factory.mktemp("certs")
    certificates(certs)
    conf = tmp_path_factory.mktemp("squid") / "squid.conf"
    conf.write_text(SQUID_CONF.read_text("utf-8").replace(PROD_BLOCK, TEST_BLOCK), "utf-8")

    def start(name: str, *args: str) -> None:
        docker("run", "--detach", f"--label={LABEL}", f"--name={names[name]}", *args)

    try:
        dockerfile = f"--file={INFRA / 'docker' / 'squid.Dockerfile'}"
        docker("build", "-q", f"--tag={IMAGE}", dockerfile, str(INFRA / "squid"), timeout=900)
        [ipam] = PROD["networks"]["alerting"]["ipam"]["config"]
        subnet = [f"--subnet={moved(ipam['subnet'])}", f"--ip-range={moved(ipam['ip_range'])}"]
        docker("network", "create", f"--label={LABEL}", "--internal", *subnet, nets["alerting"])
        internet = ["--internal", f"--subnet={INTERNET}", nets["internet"]]
        docker("network", "create", f"--label={LABEL}", *internet)
        for fake, ip, kind in (("discord", DISCORD, "discord"), ("gmail", GMAIL, "smtp")):
            files = [mount(FAKES, "/fake/fake_receivers.py"), mount(certs, "/certs")]
            serve = ["python", "/fake/fake_receivers.py", kind]
            serve += [f"/certs/{fake}.pem", f"/certs/{fake}-key.pem"]
            start(fake, f"--network={nets['internet']}", f"--ip={ip}", *files, PYTHON, *serve)
        # The proxy finds the fakes under the names it allows, and watch's peer nowhere.
        hosts = [f"--add-host=discord.com={DISCORD}", f"--add-host=smtp.gmail.com={GMAIL}"]
        hosts.append(f"--add-host=host.docker.internal={NOWHERE}")
        proxy = [f"--label={LABEL}", f"--name={names['proxy']}", *hosts]
        proxy += hardening(PROD["services"]["egress-proxy"])
        squid = [f"--network={nets['internet']}", mount(conf, "/etc/squid/squid.conf"), IMAGE]
        docker("create", *proxy, *squid)
        docker("network", "connect", "--alias=egress-proxy", nets["alerting"], names["proxy"])
        docker("start", names["proxy"])
        on_alerting = f"--network={nets['alerting']}"
        collector = PROD["services"]["egress-log"]
        config = mount(INFRA / "otel-egress-log.yaml", "/etc/otelcol-contrib/config.yaml")
        logged = [on_alerting, f"--ip={EGRESS_LOG}", "--network-alias=egress-log", config]
        start("egress-log", *hardening(collector), *logged, collector["image"])
        relay = PROD["services"]["smtp-relay"]
        socat = [arg.replace(PROD_BLOCK, TEST_BLOCK) for arg in relay["entrypoint"]]
        relayed = [on_alerting, f"--ip={RELAY}", "--network-alias=smtp-relay"]
        start("relay", *hardening(relay), *relayed, f"--entrypoint={socat[0]}", IMAGE, *socat[1:])
        settings = [f"--env={name}={value}" for name, value in SETTINGS.items()]
        # The fakes' CA instead of the system's roots; the config itself is production's.
        trust = [mount(certs / "ca.pem", "/certs/ca.pem"), "--env=SSL_CERT_FILE=/certs/ca.pem"]
        files = [mount(INFRA / "alertmanager", "/etc/alertmanager"), *trust]
        placed = [on_alerting, f"--ip={ALERTMANAGER}", "--tmpfs=/alertmanager", *settings]
        entrypoint = SERVICE["entrypoint"]
        image = [f"--entrypoint={entrypoint[0]}", SERVICE["image"], *entrypoint[1:]]
        start("alertmanager", *hardening(SERVICE), *placed, *files, *image, *SERVICE["command"])

        for fake, port in (("discord", 443), ("gmail", 587)):
            connect = f"import socket; socket.create_connection(('127.0.0.1', {port}), 2)"
            until(answers(names[fake], "python", "-c", connect), f"the fake {fake}")
        am = names["alertmanager"]
        until(answers(am, "wget", "-qO-", "http://127.0.0.1:9093/-/ready"), "Alertmanager")
        until(answers(am, "wget", "-qO-", "http://egress-log:8889/metrics"), "egress-log")
        port = ["bash", "-c", "echo > /dev/tcp/127.0.0.1/3131"]
        until(answers(names["proxy"], *port), "the proxy")
        yield Delivery(names)
    finally:
        quietly("rm", "--force", *names.values())
        quietly("network", "rm", *nets.values())


def plain(raw: str) -> tuple[EmailMessage, str]:
    """A received message and its text part."""
    message = message_from_string(raw, policy=policy.default)
    body = message.get_body(("plain",))
    assert body is not None, raw
    return message, cast("EmailMessage", body).get_content()


def links(text: str) -> set[str]:
    return set(re.findall(r"https?://\S+", text))


def test_alerts_reach_the_fake_discord_and_gmail_once_per_rule_and_nothing_else(
    delivery: Delivery,
) -> None:
    since = float(docker("exec", delivery.names["proxy"], "date", "+%s.%N"))
    for name, severity, labels, summary in SENT:
        args = [f"alertname={name}", f"severity={severity}"]
        args += [f"{key}={value}" for key, value in labels.items()]
        args += [
            f"--annotation=summary={summary}",
            f"--annotation=runbook_url={LINK}{name.lower()}",
        ]
        url = "--alertmanager.url=http://127.0.0.1:9093"
        docker("exec", delivery.names["alertmanager"], "amtool", "alert", "add", *args, url)

    def both() -> bool | None:
        posts, mails = delivery.received("discord"), delivery.received("gmail")
        return True if len(posts) >= 2 and len(mails) >= 2 else None

    until(both, "a message per rule in Discord and in the inbox", hint=delivery.logs)
    time.sleep(5)
    posts, mails = delivery.received("discord"), delivery.received("gmail")

    # Discord: one message per rule, on the webhook's own path, the rule with two alerts counted.
    assert {post["path"] for post in posts} == {f"/api/webhooks/1234567890/{TOKEN}"}
    embeds = [embed for post in posts for embed in json.loads(post["body"])["embeds"]]
    titles = {embed["title"]: embed["description"] for embed in embeds}
    assert len(embeds) == 2
    assert set(titles) == {
        "Triage Copilot firing: InvestigationsFailing (2)",
        "Triage Copilot firing: GatewayNotReady",
    }
    failing = titles["Triage Copilot firing: InvestigationsFailing (2)"]
    for _, severity, _, summary in SENT[:2]:
        assert f"InvestigationsFailing ({severity}): {summary}" in failing
    assert "Firing since " in failing
    # Email: the same, sent as the account it signs in with, after STARTTLS, to each recipient.
    assert len(mails) == 2
    subjects: dict[str, str] = {}
    for mail in mails:
        signed_in = (mail["tls"], mail["mechanism"], mail["user"], mail["password"])
        assert signed_in == (True, "PLAIN", "tc.alerts@example.com", PASSWORD)
        assert mail["from"] == "MAIL FROM:<tc.alerts@example.com>"
        assert mail["to"] == ["RCPT TO:<oncall@example.com>", "RCPT TO:<second@example.com>"]
        message, text = plain(mail["data"])
        assert message.get_body(("html",)) is None
        subjects[str(message["Subject"])] = text
    assert set(subjects) == set(titles)
    gateway = subjects["Triage Copilot firing: GatewayNotReady"]
    assert f"GatewayNotReady (critical): {GATEWAY}" in gateway
    # Nothing secret, and no link but the rules' runbooks: no Alertmanager, Prometheus or silence.
    for raw in [*(post["body"] for post in posts), *(mail["data"] for mail in mails)]:
        assert (TOKEN in raw, PASSWORD in raw) == (False, False)
    runbooks = {LINK + "investigationsfailing", LINK + "gatewaynotready"}
    for text in [*titles.values(), *subjects.values()]:
        assert links(text) <= runbooks, text

    # Nothing else: the proxy carried these tunnels alone, and refused nothing.
    expected = {
        (ALERTMANAGER, "TCP_TUNNEL/200", "CONNECT", "discord.com:443"),
        (RELAY, "TCP_TUNNEL/200", "CONNECT", "smtp.gmail.com:587"),
    }

    def logged() -> set[tuple[str, str, str, str]] | None:
        found = delivery.tunnels(since)
        return found if expected <= found else None

    assert until(logged, "the proxy's log of both tunnels", 30) == expected
    # Alertmanager has no way around the proxy, by address or by name.
    for target in (f"http://{DISCORD}:443/", "https://discord.com/", f"http://{GMAIL}:587/"):
        direct = ["wget", "-q", "-T", "3", "-O", "/dev/null", target]
        assert quietly("exec", delivery.names["alertmanager"], *direct).returncode != 0
    # Neither secret shows in Alertmanager's log.
    log = quietly("logs", delivery.names["alertmanager"])
    printed = log.stdout + log.stderr
    assert (TOKEN in printed, PASSWORD in printed) == (False, False)


BROKEN = [
    *[({name: ""}, name) for name in SETTINGS],
    (
        {"ALERT_DISCORD_WEBHOOK_URL": f"https://discordapp.com/api/webhooks/1/{TOKEN}"},
        "ALERT_DISCORD_WEBHOOK_URL",
    ),
    ({"ALERT_SMTP_PASSWORD": f"{PASSWORD[:4]} {PASSWORD[4:]}"}, "ALERT_SMTP_PASSWORD"),
    ({"ALERT_EMAIL_FROM": "TC <tc.alerts@example.com>"}, "ALERT_EMAIL_FROM"),
    ({"ALERT_EMAIL_TO": "oncall@example.com;second@example.com"}, "ALERT_EMAIL_TO"),
]


def run_entrypoint(settings: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """The entrypoint with these settings, then amtool's check of the config it wrote."""
    env = [f"--env={name}={value}" for name, value in settings.items()]
    files = [mount(INFRA / "alertmanager", "/etc/alertmanager"), "--tmpfs=/tmp"]
    entrypoint = SERVICE["entrypoint"]
    # The config the entrypoint wrote on the container's own tmpfs.
    check = ["/bin/amtool", "check-config", "/tmp/alertmanager.yml"]  # noqa: S108
    image = [f"--entrypoint={entrypoint[0]}", SERVICE["image"], *entrypoint[1:], *check]
    return quietly("run", "--rm", "--network=none", "--read-only", *env, *files, *image)


def test_alertmanager_starts_with_the_four_settings_and_its_config_checks_out() -> None:
    done = run_entrypoint(SETTINGS)
    printed = done.stdout + done.stderr
    assert done.returncode == 0, printed
    assert "SUCCESS" in printed
    assert [value for value in SETTINGS.values() if value in printed] == []


@pytest.mark.parametrize(("changed", "name"), BROKEN)
def test_a_missing_or_malformed_setting_stops_alertmanager_naming_it_alone(
    changed: dict[str, str], name: str
) -> None:
    settings = SETTINGS | changed
    done = run_entrypoint(settings)
    printed = done.stdout + done.stderr
    assert done.returncode == 1, printed
    assert name in done.stderr
    assert [value for value in settings.values() if value and value in printed] == []


def test_egress_log_counts_each_refusal_of_the_proxy(delivery: Delivery) -> None:
    before = delivery.denied()
    # A tunnel the relay may open is no refusal; a host outside its allowlist is one.
    assert delivery.ask(3131, "CONNECT smtp.gmail.com:587") == 200
    assert delivery.ask(3131, "CONNECT example.com:443") == 403

    def counted() -> float | None:
        now = delivery.denied()
        return now if now > before else None

    assert until(counted, "egress-log's count", 30) == before + 1
    time.sleep(3)
    assert delivery.denied() == before + 1
