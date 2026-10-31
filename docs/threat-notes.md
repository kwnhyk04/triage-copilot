# Threat Notes

Known threats to TC's own setup that are not yet closed. Each entry gives what the threat is, why it matters, what holds today and the plan. Entries are dated; a later entry supersedes an earlier one and never edits it.

## 2026-11-01: production containers have open egress

- What: production (`triagecopilot-prod`, `make prod-up`) puts every container on one routed compose network, `triagecopilot-prod_default`.
  - Any container can open connections to the internet.
  - Any container can reach this PC's own listeners, through the network's gateway address or `host.docker.internal`. Whether the host's 127.0.0.1 ports answer that way is checked at go-live (docs/todo.md, P1.12 (4)).
  - On this PC that could reach:
    - TC's dev listeners: the sim's admin port 4231, whose state names each scenario's answer, and the dev gateway's internal listener 4201;
    - FreightAudit's and FilingLens's 127.0.0.1 listeners;
    - anything else that listens.
- Why it matters:
  - The agent handles untrusted text: alert snapshots, log lines and model output. A bug or a compromised dependency in any container could use that reach, or send data anywhere.
  - FreightAudit and FilingLens keep their containers on internal networks behind a squid egress proxy for this reason. This comes from the user on 2026-11-01; their repos were not read.
- What holds today:
  - Inbound: only the gateway publishes a port (127.0.0.1:4202), and Funnel serves only `gateway:4200` (`services/agent/tests/test_prod_compose.py`).
  - The agent calls fixed addresses: the gateway's internal listener, Postgres, ElasticMQ and the model providers. Its live tools read only what `watch` attached to the alert.
  - Nothing restricts outbound connections.
- Who needs egress:
  - The agent, to the model providers over HTTPS: `api.anthropic.com`, `generativelanguage.googleapis.com` and `integrate.api.nvidia.com`.
  - The Tailscale sidecar, to Tailscale's coordination server and relays.
  - The gateway, Postgres, ElasticMQ and the three one-shot containers need none. The Docker engine pulls the images, not the containers.
- Open points for the fix:
  - Docker publishes no port from an internal-only network. So the gateway's 127.0.0.1:4202 (for `watch`) and the sidecar's path to `gateway:4200` need a routed edge network, which must not give the gateway a way out.
  - squid carries no UDP. The sidecar either keeps its own route out or reaches Tailscale's relays through the proxy over HTTPS; this is not checked yet.
  - Lateral reach, found by the final review (2026-11-01): the internet-facing sidecar shares the one network with Postgres and ElasticMQ. ElasticMQ takes any credentials, so a compromised sidecar could queue forged investigations or agent events. The fix gives the sidecar an edge network that it shares with the gateway alone.
  - With `GATEWAY_TRUST_PROXY` set to the whole subnet (README, Deploy, check 5), the gateway also trusts the agent and the network's gateway address, which carries the host's traffic to 4202. So local processes and the agent can claim any client address for the failure count. The impact is low, since the keys and tokens are 256-bit. The edge network narrows the trusted range to it.

## 2026-11-01: the probes answer the internet without a limit

- What: Funnel serves every path of the gateway's public listener, `/healthz` and `/readyz` included. The probes take no credential and are exempt from the rate limit (`apps/gateway/src/app.ts`). Each `/readyz` runs `select 1` on the gateway's 5-connection pool (`apps/gateway/src/db.ts`), which webhook intake, the relay and the reads share.
- Why it matters: a cheap flood of `/readyz` through Funnel could slow real traffic. The web would then fall back to the demo. It reveals nothing, and it needs no key. Found by the final review of PR 2.
- What holds today: nothing beyond Tailscale's own limits on Funnel traffic.
- Plan, open: either a per-address limit on the probes (the Docker healthcheck asks from 127.0.0.1, far below any limit), or Funnel serves only `/v1/` and `/graphql`. The web reads the gateway's health through `/graphql` with its key.
- Plan, the next PR, built during the shadow run (docs/todo.md):
  - internal networks for Postgres, ElasticMQ, the gateway and the agent;
  - a squid egress proxy that allows only the model providers' hosts, used by the agent;
  - the sidecar's own route out;
  - a compose test proving that no app container reaches a routed network except through that design.
