"""Production is its own compose project, and only its egress proxy has a route out or a published
port: 127.0.0.1:4202 for watch, which it forwards to the gateway. The data services, the gateway's
internal listener and the sim's admin port are never published, each service gets only its
secrets, and each proxy client reaches the proxy on its own port from its fixed address. Alert
delivery (the observability profile) sits on `alerting` with the proxy alone, and Prometheus's
probes, its rules' inputs and the proxy's refusals reach it by the addresses the configs name.
"""

import ipaddress
import json
import re
from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[3]
INFRA = ROOT / "infra"
SQUID_CONF = INFRA / "squid" / "squid.conf"
ALERTMANAGER = INFRA / "alertmanager"
# Alertmanager's settings, in .env.prod; compose hands them to it alone.
ALERT_SETTINGS = {
    "ALERT_DISCORD_WEBHOOK_URL",
    "ALERT_SMTP_PASSWORD",
    "ALERT_EMAIL_FROM",
    "ALERT_EMAIL_TO",
}
# Alert delivery's containers, all in the observability profile.
ALERTING = ("alertmanager", "smtp-relay", "blackbox", "egress-log")


class _Reset(list[Any]):
    """A `!reset` value: compose drops what the base file set."""


class _Loader(yaml.SafeLoader):
    """SafeLoader that also reads compose's `!reset` tag."""


def _reset(_loader: object, _node: object) -> _Reset:
    return _Reset()


# PyYAML has no type information for its loaders' constructor registry.
cast("Any", _Loader).add_constructor("!reset", _reset)


def _compose(name: str) -> dict[str, Any]:
    text = (INFRA / name).read_text("utf-8")
    return yaml.load(text, Loader=_Loader)  # noqa: S506 - a SafeLoader subclass


BASE = _compose("docker-compose.yml")
PROD = _compose("docker-compose.prod.yml")
SERVICES: set[str] = set(BASE["services"]) | set(PROD["services"])
# Reach a container only through its own env file, never through the compose files.
SECRETS = {
    "GATEWAY_API_KEY",
    "APPROVER_API_KEY",
    "AGENT_SERVICE_TOKEN",
    "EXECUTOR_SERVICE_TOKEN",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "NVIDIA_API_KEY",
    "OPENROUTER_API_KEY",
    "LOGIN_PASSWORD_HASH",
    "SESSION_SECRET",
    "WATCH_FA_TOKEN",
    "WATCH_FL_TOKEN",
    "SIM_ADMIN_URL",
}
# Each service's env file, and the production secrets compose may hand it.
OWN: dict[str, tuple[str | None, set[str]]] = {
    "postgres": (
        None,
        {"PROD_POSTGRES_PASSWORD", "PROD_GATEWAY_DB_PASSWORD", "PROD_AGENT_DB_PASSWORD"},
    ),
    "gateway-migrate": (None, {"PROD_GATEWAY_DB_PASSWORD"}),
    "gateway": ("../.env.prod.gateway", {"PROD_GATEWAY_DB_PASSWORD"}),
    "agent-migrate": ("../.env.prod.agent", {"PROD_AGENT_DB_PASSWORD"}),
    "agent-runbooks": ("../.env.prod.agent", {"PROD_AGENT_DB_PASSWORD"}),
    "agent": ("../.env.prod.agent", {"PROD_AGENT_DB_PASSWORD"}),
    "tailscale": (None, {"TS_AUTHKEY"}),
    "grafana": (None, {"PROD_GRAFANA_ADMIN_PASSWORD"}),
    "alertmanager": (None, ALERT_SETTINGS),
}


def _ports(name: str) -> list[str]:
    """What a service publishes once both files merge: `!reset` drops the base file's ports."""
    prod = PROD["services"].get(name, {}).get("ports", [])
    if isinstance(prod, _Reset):
        return []
    return [*BASE["services"].get(name, {}).get("ports", []), *prod]


def _environment(name: str) -> dict[str, Any]:
    return PROD["services"].get(name, {}).get("environment", {})


def networks(name: str) -> dict[str, dict[str, Any]]:
    """The networks a service joins in production, with their settings. The base file names none,
    so a service production leaves alone joins compose's routed `default`; one that shares another
    container's network namespace joins none of its own."""
    service = PROD["services"].get(name, {})
    if "network_mode" in service:
        return {}
    joined = service.get("networks", ["default"])
    if isinstance(joined, list):
        return {network: {} for network in cast("list[str]", joined)}
    return {key: value or {} for key, value in cast("dict[str, Any]", joined).items()}


def address(name: str, network: str) -> str:
    """A service's fixed address on one network."""
    return networks(name)[network]["ipv4_address"]


def test_production_is_its_own_compose_project() -> None:
    assert PROD["name"] == "triagecopilot-prod"
    makefile = (ROOT / "Makefile").read_text("utf-8")
    assert "docker compose -p triagecopilot-prod" in makefile
    # The volumes hold production's data: no target removes them.
    assert not re.search(r"\bdown\b[^\n]*(\s-v\b|--volumes)", makefile)
    # --wait alone waits forever on a container that crash-loops before its first healthcheck,
    # as the gateway does when startup refuses a setting.
    assert re.search(r"\$\(PROD\) up -d --wait --wait-timeout \d+", makefile)


def test_only_the_egress_proxy_publishes_and_only_on_127_0_0_1() -> None:
    # Docker publishes no port from an internal network, so the proxy carries watch's port.
    published = {name: _ports(name) for name in SERVICES if _ports(name)}
    assert published == {"egress-proxy": ["127.0.0.1:${PROD_GATEWAY_PORT:-4202}:4202"]}
    # So the internal listener (4201) and the sim's admin port (4231) never are, and no service
    # joins the host's network instead. The probe exporter shares Prometheus's network namespace,
    # so no other container can ask it to probe anything.
    modes = {
        name: merged["network_mode"]
        for name in SERVICES
        if "network_mode"
        in (merged := BASE["services"].get(name, {}) | PROD["services"].get(name, {}))
    }
    assert modes == {"blackbox": "service:prometheus"}
    assert {"sim", "executor"}.isdisjoint(SERVICES)


def test_only_the_egress_proxy_has_a_route_out() -> None:
    # Docker Desktop lets any container on a routed network reach the internet and this PC's own
    # listeners (host.docker.internal), so every network but the proxy's `egress` is internal.
    routed = {name for name, network in PROD["networks"].items() if not network.get("internal")}
    assert routed == {"egress"}
    # The internet-facing sidecar shares `edge` with the gateway alone and no network with
    # Postgres or ElasticMQ; `ingress` carries watch's port from the proxy to the gateway.
    # `alerting` holds the alert path and the proxy alone, so no app container reaches
    # Alertmanager's API, which has no login. Prometheus also joins `data` for the agent's metrics
    # and the readiness probes, which its exporter sends from Prometheus's own namespace.
    assert {name: set(networks(name)) for name in SERVICES} == {
        "postgres": {"data"},
        "elasticmq": {"data"},
        "toxiproxy": {"data"},
        "otel-collector": {"data", "monitoring"},
        "jaeger": {"monitoring"},
        "prometheus": {"data", "monitoring", "alerting"},
        "blackbox": set(),
        "grafana": {"monitoring"},
        "alertmanager": {"alerting"},
        "smtp-relay": {"alerting"},
        "egress-log": {"alerting"},
        "gateway-migrate": {"data"},
        "gateway": {"data", "edge", "ingress"},
        "agent-migrate": {"data"},
        "agent-runbooks": {"data"},
        "agent": {"data", "outbound"},
        "tailscale": {"edge", "outbound"},
        "egress-proxy": {"outbound", "ingress", "egress", "alerting"},
    }


def test_each_proxy_client_has_its_own_port_and_fixed_address() -> None:
    conf = SQUID_CONF.read_text("utf-8")
    for name, port in (("agent", 3128), ("tailscale", 3129)):
        environment = _environment(name)
        assert environment["HTTPS_PROXY"] == f"http://egress-proxy:{port}"
        # Every internal address is http://, which HTTPS_PROXY leaves alone.
        assert "HTTP_PROXY" not in environment
        assert re.search(rf"^http_port {port} name={name}$", conf, re.MULTILINE)
        ip = re.escape(address(name, "outbound"))
        assert re.search(rf"^acl {name}_ip src {ip}$", conf, re.MULTILINE)
    # Tailscale's log upload would need its log server, which the proxy does not allow.
    assert _environment("tailscale")["TS_TAILSCALED_EXTRA_ARGS"] == "--no-logs-no-support"
    # The gateway trusts the sidecar's X-Forwarded-For at this fixed address (README, Deploy).
    assert address("tailscale", "edge") == "10.231.233.18"


def _alertmanager_config() -> dict[str, Any]:
    return yaml.safe_load((ALERTMANAGER / "alertmanager.yml").read_text("utf-8"))


def test_alert_delivery_reaches_the_proxy_on_its_own_ports_from_fixed_addresses() -> None:
    conf = SQUID_CONF.read_text("utf-8")
    config = _alertmanager_config()
    [receiver] = config["receivers"]
    [discord] = receiver["discord_configs"]
    # Alertmanager sends Discord's webhooks through its own proxy port.
    assert discord["http_config"]["proxy_url"] == "http://egress-proxy:3130"
    # It sends no SMTP through a proxy, so mail goes to the relay, which tunnels each connection
    # through the relay's own proxy port to Gmail, and takes connections from Alertmanager alone.
    relay = PROD["services"]["smtp-relay"]["entrypoint"]
    assert "PROXY:egress-proxy:smtp.gmail.com:587,proxyport=3131" in relay
    # TCP4: socat takes a /32 range only with the address family given.
    listen = f"TCP4-LISTEN:2525,fork,reuseaddr,range={address('alertmanager', 'alerting')}/32"
    assert listen in relay
    assert config["global"]["smtp_smarthost"] == "smtp-relay:2525"
    # STARTTLS runs from Alertmanager to Gmail, the certificate checked against Gmail's name.
    assert config["global"]["smtp_require_tls"] is True
    assert config["global"]["smtp_tls_config"] == {"server_name": "smtp.gmail.com"}
    for name, port, acl in (
        ("alertmanager", 3130, "alertmanager"),
        ("smtp-relay", 3131, "smtp_relay"),
    ):
        assert re.search(rf"^http_port {port} name={acl}$", conf, re.MULTILINE)
        ip = re.escape(address(name, "alerting"))
        assert re.search(rf"^acl {acl}_ip src {ip}$", conf, re.MULTILINE)


def test_alert_delivery_runs_only_with_the_observability_profile_and_hardened() -> None:
    assert BASE["services"]["prometheus"]["profiles"] == ["observability"]
    for name in ALERTING:
        service = PROD["services"][name]
        assert (name, service["profiles"]) == (name, ["observability"])
        hardening = (service["read_only"], service["cap_drop"], service["security_opt"])
        assert (name, hardening) == (name, (True, ["ALL"], ["no-new-privileges:true"]))
    # The relay is socat from the proxy's own image, at the same tag.
    assert PROD["services"]["smtp-relay"]["image"] == PROD["services"]["egress-proxy"]["image"]
    # One collector version for the apps' telemetry and the proxy's refusals.
    assert PROD["services"]["egress-log"]["image"] == BASE["services"]["otel-collector"]["image"]


def _prometheus_config() -> dict[str, Any]:
    return yaml.safe_load((INFRA / "prometheus.prod.yml").read_text("utf-8"))


def _job(name: str) -> dict[str, Any]:
    [job] = [job for job in _prometheus_config()["scrape_configs"] if job["job_name"] == name]
    return job


def test_prometheus_loads_production_s_config_and_rules_and_alerts_alertmanager() -> None:
    mounts = PROD["services"]["prometheus"]["volumes"]
    assert "./prometheus.prod.yml:/etc/prometheus/prometheus.yml:ro" in mounts
    assert "./prometheus-alerts.yml:/etc/prometheus/alerts.yml:ro" in mounts
    config = _prometheus_config()
    assert config["rule_files"] == ["/etc/prometheus/alerts.yml"]
    [manager] = config["alerting"]["alertmanagers"]
    assert manager["static_configs"] == [{"targets": ["alertmanager:9093"]}]
    # The agent's metrics feed the investigation and Claude rules; its `service` label lets
    # AgentNotReady join their scrape with the agent's probe.
    agent = [{"targets": ["agent:4210"], "labels": {"service": "agent"}}]
    assert _job("agent")["static_configs"] == agent


def test_each_readiness_probe_runs_from_inside_prometheus() -> None:
    blackbox = PROD["services"]["blackbox"]
    assert "--web.listen-address=127.0.0.1:9115" in blackbox["command"]
    probes = _job("readyz")
    [exporter] = [
        rule for rule in probes["relabel_configs"] if rule.get("target_label") == "__address__"
    ]
    assert exporter["replacement"] == "127.0.0.1:9115"
    targets = {
        target: group["labels"]["service"]
        for group in probes["static_configs"]
        for target in group["targets"]
    }
    # watch runs on this PC, which only the proxy reaches: its probe port forwards /readyz there.
    assert targets == {
        "http://gateway:4200/readyz": "gateway",
        "http://agent:4210/readyz": "agent",
        "http://egress-proxy:4240/readyz": "watch",
    }
    conf = SQUID_CONF.read_text("utf-8")
    probe = r"^http_port 4240 accel defaultsite=watch no-vhost name=watch_probe$"
    assert re.search(probe, conf, re.MULTILINE)
    assert re.search(
        r"^cache_peer host\.docker\.internal parent 4240 0 .*\bname=watch_host\b", conf, re.M
    )
    ip = re.escape(address("prometheus", "alerting"))
    assert re.search(rf"^acl prometheus_ip src {ip}$", conf, re.MULTILINE)


def test_the_proxy_logs_every_request_to_egress_log_which_prometheus_reads() -> None:
    conf = SQUID_CONF.read_text("utf-8")
    collector = yaml.safe_load((INFRA / "otel-egress-log.yaml").read_text("utf-8"))
    port = collector["receivers"]["udp_log"]["listen_address"].rsplit(":", 1)[1]
    ip = re.escape(address("egress-log", "alerting"))
    assert re.search(rf"^access_log udp://{ip}:{port}\b", conf, re.MULTILINE)
    assert PROD["services"]["egress-log"]["volumes"] == [
        "./otel-egress-log.yaml:/etc/otelcol-contrib/config.yaml:ro"
    ]
    exporter = collector["exporters"]["prometheus"]["endpoint"].rsplit(":", 1)[1]
    assert _job("egress-log")["static_configs"] == [{"targets": [f"egress-log:{exporter}"]}]


def test_the_proxy_s_healthcheck_checks_every_port_it_serves() -> None:
    served = re.findall(r"^http_port (\d+)", SQUID_CONF.read_text("utf-8"), re.MULTILINE)
    check = " ".join(PROD["services"]["egress-proxy"]["healthcheck"]["test"])
    looped = re.search(r"for port in ([\d ]+);", check)
    assert looped
    assert sorted(looped[1].split()) == sorted(served)


def test_fixed_addresses_sit_outside_the_range_docker_hands_out() -> None:
    pinned = {name: net for name, net in PROD["networks"].items() if "ipam" in net}
    assert set(pinned) == {"outbound", "edge", "ingress", "alerting"}
    for network, settings in pinned.items():
        [config] = settings["ipam"]["config"]
        subnet = ipaddress.IPv4Network(config["subnet"])
        # TC's own block, beside FreightAudit's 10.231.231.0/24 and FilingLens's 10.231.232.0/24.
        assert subnet.subnet_of(ipaddress.IPv4Network("10.231.233.0/24"))
        pool = ipaddress.IPv4Network(config["ip_range"])
        fixed = [
            ipaddress.IPv4Address(joined["ipv4_address"])
            for name in SERVICES
            if "ipv4_address" in (joined := networks(name).get(network, {}))
        ]
        assert fixed
        assert all(ip in subnet and ip not in pool for ip in fixed)


def test_watch_s_port_reaches_the_gateway_alone() -> None:
    conf = SQUID_CONF.read_text("utf-8")
    assert re.search(r"^http_port 4202 accel defaultsite=gateway no-vhost name=watch$", conf, re.M)
    # By address, which squid resolves only at start and then hourly, so a re-created gateway
    # keeps it.
    gateway = re.escape(address("gateway", "ingress"))
    assert re.search(rf"^cache_peer {gateway} parent 4200 0 .*\bname=gateway\b", conf, re.M)


def test_watch_s_token_passes_to_the_gateway_alone() -> None:
    # Squid drops a client's Authorization on its way to an originserver peer unless the peer has
    # login=PASS or login=PASSTHRU (squid 6.14, src/http.cc), and the gateway then refuses every
    # alert with 401. PASSTHRU passes only what the client sent; the probe of watch sends none.
    conf = SQUID_CONF.read_text("utf-8")
    logins = {
        name: re.findall(r"\blogin=\S+", peer)
        for peer, name in re.findall(r"^(cache_peer .*\bname=(\w+).*)$", conf, re.M)
    }
    assert logins == {"gateway": ["login=PASSTHRU"], "watch_host": []}


def test_the_gateway_frees_4202_before_the_proxy_takes_it() -> None:
    # Before the lockdown the gateway published 4202 itself; compose must recreate it first, or the
    # proxy's start fails with "port is already allocated".
    depends_on = PROD["services"]["egress-proxy"]["depends_on"]
    assert depends_on == {"gateway": {"condition": "service_started"}}


def test_the_sidecar_runs_without_capabilities() -> None:
    # It shares `outbound` with the agent, and the proxy tells them apart by address alone: with no
    # NET_RAW it cannot forge the agent's, and the agent runs as a non-root user.
    tailscale = PROD["services"]["tailscale"]
    assert tailscale["cap_drop"] == ["ALL"]
    assert tailscale["security_opt"] == ["no-new-privileges:true"]
    assert "USER app" in (INFRA / "docker" / "python.Dockerfile").read_text("utf-8")


def test_the_egress_proxy_runs_ci_s_squid_image_hardened() -> None:
    proxy = PROD["services"]["egress-proxy"]
    assert proxy["read_only"] is True
    assert proxy["cap_drop"] == ["ALL"]
    assert proxy["security_opt"] == ["no-new-privileges:true"]
    assert proxy["volumes"] == ["./squid/squid.conf:/etc/squid/squid.conf:ro"]
    dockerfile = (INFRA / "docker" / "squid.Dockerfile").read_text("utf-8")
    assert re.search(r"^FROM ubuntu:24\.04@sha256:[0-9a-f]{64}$", dockerfile, re.MULTILINE)
    # Every image production runs is one that CI builds and publish.yml pushes.
    images = {
        found[1]
        for service in PROD["services"].values()
        if (found := re.search(r"/triage-copilot-(\w+):\$\{TC_TAG", service.get("image", "")))
    }
    assert images == {"gateway", "agent", "squid"}
    for workflow in ("ci.yml", "publish.yml"):
        text = (ROOT / ".github" / "workflows" / workflow).read_text("utf-8")
        include = yaml.safe_load(text)["jobs"]["images"]["strategy"]["matrix"]["include"]
        built = {entry["service"]: entry["file"] for entry in include}
        assert images <= set(built)
        assert built["squid"] == "infra/docker/squid.Dockerfile"


def test_each_service_gets_only_its_own_secrets() -> None:
    for name, service in PROD["services"].items():
        env_file, allowed = OWN.get(name, (None, set()))
        assert (name, service.get("env_file")) == (name, env_file)
        environment = _environment(name)
        pattern = r"\$\{(PROD_[A-Z_]+|TS_AUTHKEY|ALERT_[A-Z_]+)"
        used = set(re.findall(pattern, json.dumps(environment)))
        assert (name, used - allowed) == (name, set())
        assert (name, SECRETS & set(environment)) == (name, set())
    # Compose asks for a `:?` value even for a service whose profile is off, so a required alert
    # setting would stop every production command until alerting is set up; the entrypoint
    # checks them instead.
    alerting = _environment("alertmanager")
    assert alerting == {name: f"${{{name}:-}}" for name in ALERT_SETTINGS}
    # Production's Grafana has a password of its own, never dev's from .env.
    grafana = re.findall(r"\$\{(\w+)", _environment("grafana")["GF_SECURITY_ADMIN_PASSWORD"])
    assert grafana == ["PROD_GRAFANA_ADMIN_PASSWORD"]
    postgres = _environment("postgres")
    passwords = {key: re.findall(r"\$\{(\w+)", value) for key, value in postgres.items()}
    assert passwords == {
        "POSTGRES_PASSWORD": ["PROD_POSTGRES_PASSWORD"],
        "GATEWAY_DB_PASSWORD": ["PROD_GATEWAY_DB_PASSWORD"],
        "AGENT_DB_PASSWORD": ["PROD_AGENT_DB_PASSWORD"],
    }


def test_no_container_is_pointed_at_the_host_or_a_windows_path() -> None:
    # Fixed here, so an env file copied from .env changes none of them.
    gateway, agent = _environment("gateway"), _environment("agent")
    assert gateway["NODE_ENV"] == "production"
    hosts = {gateway["GATEWAY_HOST"], gateway["GATEWAY_INTERNAL_HOST"], agent["AGENT_HOST"]}
    assert hosts == {"0.0.0.0"}  # noqa: S104 - inside the container, on internal networks only
    assert "@postgres:5432/" in gateway["GATEWAY_DATABASE_URL"]
    assert "@postgres:5432/" in agent["AGENT_DATABASE_URL"]
    assert (gateway["AGENT_URL"], agent["GATEWAY_INTERNAL_URL"]) == (
        "http://agent:4210",
        "http://gateway:4201",
    )
    for name in ("gateway", "gateway-migrate", "agent", "agent-migrate", "agent-runbooks"):
        values = json.dumps(_environment(name))
        assert (name, "127.0.0.1" in values or "H:" in values) == (name, False)


def test_funnel_serves_only_the_gateways_public_port() -> None:
    tailscale = PROD["services"]["tailscale"]
    assert re.fullmatch(r"tailscale/tailscale:v[\d.]+@sha256:[0-9a-f]{64}", tailscale["image"])
    assert tailscale["environment"]["TS_SERVE_CONFIG"] == "/config/serve.json"
    serve = json.loads((INFRA / "tailscale" / "serve.json").read_text("utf-8"))
    assert list(serve["TCP"]) == ["443"]
    proxies = [
        handler["Proxy"] for web in serve["Web"].values() for handler in web["Handlers"].values()
    ]
    assert proxies == ["http://gateway:4200"]
    assert list(serve["AllowFunnel"].values()) == [True]
