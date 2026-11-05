# Triage Copilot guide

The layout, setup, commands, production deploy and alerting. The README is the short version.

## Layout

| Path | What | Port |
| --- | --- | --- |
| `apps/gateway` | Fastify + GraphQL Yoga (subscriptions over SSE) | 4200 |
| `apps/web` | Next.js UI and backend-for-frontend | 3200 |
| `services/agent` | SQS consumer + internal FastAPI | 4210 |
| `services/executor` | Runs approved, allowlisted actions only | 4220 |
| `services/watch` | Polls FreightAudit and FilingLens and posts their state changes as alerts (read-only: `docker ps` and `logs`, `git log`, HTTP GET) | 4240 |
| `sim/` | Scenario engine for scripted incidents; admin port for the eval harness only | 4230, 4231 |
| `contracts/` | OpenAPI, GraphQL and SQS message schemas | |
| `packages/ts-*`, `packages/py-*` | Shared config, telemetry and generated clients | |
| `infra/` | Docker Compose and service config | |
| `runbooks/`, `evals/` | Runbooks the agent searches; eval scenarios and reports | |

## Setup

Needs Node 24 (pnpm 12 via corepack), uv, Docker and GNU make.

```sh
cp .env.example .env
make install
make dev
```

Model calls (`AI_ENABLED=true`) run on Gemini's free tier, falling back to NVIDIA NIM. With `ANTHROPIC_API_KEY` in `.env`, live triage asks Claude Haiku 5.5 first, paid from Anthropic API credits until `LLM_CLAUDE_UNTIL`; with `LLM_CLAUDE_FIRST=true` the sim and evals do too. Any Claude failure moves that call alone to the free chain.

`make install` also installs the gitleaks pre-commit hook, which blocks commits that stage secrets.

Python tools run as `uv run python -m <tool>` (pytest, ruff, pyright, alembic, pre-commit, and the agent, executor, sim and watch): Windows Smart App Control blocks the venv's unsigned launchers in `.venv\Scripts`, and the same commands work on Linux.

Sign-in: the web has one password. Make its two settings once, in a terminal; neither script prints a secret:

```sh
pnpm --filter @triage/web hash-password --write-env   # asks twice, unechoed; writes LOGIN_PASSWORD_HASH
pnpm --filter @triage/web session-secret              # writes a random SESSION_SECRET
```

`next start` and Vercel refuse to start without both. Under `make dev` they are optional, but both or neither; without them sign-in is off and only the landing, the demo and the probes work. For Vercel, run `hash-password` without `--write-env` and paste the hash it prints.

CI (GitHub Actions): `ci.yml` runs lint, types, unit, integration and browser tests, contract and scenario checks, a gitleaks scan of the full history and the image builds on every pull request and push to main; `ci-result` is the required check. `publish.yml` pushes the gateway, agent, executor and sim images to GHCR from main. `eval.yml` stays off until the repository variable `EVAL_ENABLED` is `true`; its only secret is `GEMINI_API_KEY`, and CI never calls Claude. `make dev` starts Postgres, ElasticMQ (SQS) and Toxiproxy, then all five apps. Set `COMPOSE_PROFILES=observability` in `.env` to add the OpenTelemetry Collector, Jaeger (16688), Prometheus (9092) and Grafana (3201), and uncomment `OTEL_EXPORTER_OTLP_ENDPOINT` to export traces.

## Usage

| Target | Does |
| --- | --- |
| `make dev` | Infra plus every app with reload |
| `make build` | Next.js build and Python wheels |
| `make test` | Jest and pytest unit tests; for repeated runs use `pnpm turbo run test --force`, since Turbo replays cached output |
| `make test-int` | Integration tests against the infra, about 3 minutes (the partitioning test seeds 2,000,000 steps). Each test gets a scratch database `triage_test_*`, dropped afterwards; tests never touch the dev database |
| `make test-e2e` | Builds the web, installs Chromium into `PLAYWRIGHT_BROWSERS_PATH`, and runs `apps/web/e2e` (sign-in, reports, plan decisions, live progress, the demo, the theme and the phone menu, and axe for WCAG 2.2 A and AA in light and dark, on desktop and a 390x844 phone) against two `next start` servers (3202, 3203) and a fake gateway (4250); no database or model |
| `make demo-save` | Saves recorded eval runs for the public demo (`IDS="<investigation id> ..."`), read-only; refuses any live investigation, and refuses to run unless `LIVE_TENANTS` is set (`{}` while live is off) |
| `make lint` | ESLint, Prettier, ruff |
| `make typecheck` | tsc and pyright |
| `make codegen` | Regenerates clients from `contracts/` |
| `make contracts-check` | Regenerates clients; fails if any generated file changed |
| `make contracts-breaking` | oasdiff breaking-change check of both OpenAPI specs against `CONTRACTS_BASE` (default `HEAD`; needs Docker) |
| `make migrate` | Drizzle (gateway) and Alembic (agent) migrations |
| `make seed` | The same fake history every run in both schemas; `SEED_ARGS=--reset` replaces existing rows, `SEED_ARGS=--large` tops the agent up to 2,000,000 steps |
| `make eval` | The eval against the running stack (`make dev` with `AI_ENABLED=true LLM_FALLBACK_ON_RATE_LIMIT=false`, the agent started with `AGENT_RELOAD=` so no edit restarts it): `SPLIT=dev\|test`, `SUBSET=ci\|full`, `RUNS`; `ESTIMATE=1` prints the quota days, `SET_FLOORS=1` writes the floors (default models and token cap only); `INVESTIGATION_TOKEN_CAP`, passed to the agent too, runs a second configuration with its own report. See `evals/README.md` |
| `make eval-plans` | The planner on the dev split's recorded triage reports, one Claude Opus 5.5 call a plan, no stack needed (`evals/README.md`); `ESTIMATE=1` prints the cost and calls nothing; `MAX_USD=n` stops before a plan could pass n dollars, and `LLM_PLANNER_EFFORT` picks the effort |
| `make eval-reviews` | The plan reviewer (Claude Sonnet 5.5 at effort high) on the plans `make eval-plans` kept, after the code checks, compared with the judgments in `evals/plan-judgments.json` (`evals/README.md`); `ESTIMATE=1` prints the cost and calls nothing, `MAX_USD=n` stops before a review could pass n dollars, `REVIEW_MODEL=claude-opus-5-5` runs the Opus arm, `REPEAT=n` takes another sample, and `FRESH=1` reviews the fresh test plans (`make eval-plans FRESH=1` makes them), each judged first. Exit 1: a trusted plan refused or an expectation missed |
| `make eval-cache` | Claude's prompt cache reads and writes per investigation on the recorded runs, before and after the breakpoint on a turn's last message; no model and no stack (`evals/README.md`) |
| `make scenarios-check` | Validates every scenario set and fails when a released set's file changed against `SCENARIOS_BASE` (default `HEAD`) |
| `make embed-model` | Downloads the runbook embedding model (nomic-embed-text-v1.5, about 0.5 GB) into `FASTEMBED_CACHE_PATH`; the only command that downloads it |
| `make runbooks` | Embeds `runbooks/*.md` into `agent.runbook_chunks` for `search_runbooks` |
| `make smoke` | One scenario (`SCENARIO=bad-deploy-1`) against the running stack; needs `make dev` with `AI_ENABLED=true` and the provider keys in `.env`. Prints the report, each tool call, tokens and latency per model turn, and the gateway's copy; results go to `evals/reports/runs/` |
| `make dlq-replay` | Re-queues failed messages: parked gateway outbox rows, `agent-events-dlq.fifo`, `investigations-dlq.fifo`, and the agent's `dead_letters` |
| `make switch` | Shows the kill switches; `NAME=live\|claude\|planner STATE=on\|off` flips one (live and planner are off, claude on, by default) |
| `make dev-watch` | Runs `watch` against the apps whose `WATCH_*_TOKEN` is set in `.env` (none by default; see `.env.example`), posting to `WATCH_GATEWAY_URL`, the production gateway (`127.0.0.1:4202`) by default. With `WATCH_SOURCE_INDEX_DIR` set, it also writes each app's source index: the paths its repo tracks at origin/main, which the agent checks a code fix's files against. It reads the real apps, so it is not part of `make dev` |
| `make digest` | Lists a UTC day's alert groups that got no investigation, per tenant, with why (only resolved alerts, the 5-minute cap, or the 30-a-day cap); `DAY=YYYY-MM-DD`, today by default |
| `pnpm --filter @triage/gateway tenant:token <name>` | Issues a tenant's Alertmanager webhook token (creates the tenant if new); prints it once, stores only its SHA-256 |
| `make infra-down` | Removes this project's containers; data volume stays |

Reports: with `make dev` running, sign in at `http://127.0.0.1:3200`; once signed in, `/` and the sign-in page go straight to Live incidents. The header links the two lists, Live incidents and Eval runs, and switches the theme (System, Light or Dark, kept in a cookie). Each list is a table, cards on a phone, a page at a time (10, 20 or 50 a page, kept in the URL; the size is also remembered in the browser), each row linked to its report: the verdict and cause, the evidence, the steps, the models that served with any fallback, the missing evidence, and the plan with Approve plan and Reject plan. A running investigation's report refreshes every 3 s and the list every 10 s, with a Pause button. After a real verdict both keep refreshing for up to 10 minutes while the plan is made, and show a pulsing Planning badge until it lands. The browser talks only to the Next.js server, which reads the gateway with its own keys. A live tenant's snapshots and steps are purged after 30 days, by the gateway and the agent each; its reports stay.

Demo: `/demo` is public and reads no gateway, so it works while the PC is off; a signed-in page goes there when the gateway does not answer. It holds five recorded eval runs (`make demo-save`: bad-deploy-1 run 1 `59787641-d31c-47d7-a1de-f06c7af65275`, a right cause with a pending rollback; bad-deploy-2 run 1 `0d25cd1d-7a50-4739-a74a-abae2c2db81e`, a degraded tool; bad-config-1 run 1 `58728e0f-9005-4b4b-ab1b-ea311430acb8`, a partial report; bad-config-1 run 1 on Haiku `de2a7b93-d3d6-4f8a-8a53-4b4c4bc31a24`; noisy-neighbor-4 run 3 `dac2fc6a-7bee-469c-bf12-84e7bf516753`, the test split's forbidden rollback, held for approval) and three hand-written live samples, each labeled illustrative. Its plan buttons are disabled.

Plans: with the `planner` switch on (off by default; measured on the dev split on 2026-11-02 at $0.0538 a plan, `make eval-plans`), a real incident's report gets one plan for its fix (no fixed action, a restart of an app or worker container, or a code fix), made by Claude Opus 5.5 at effort max (from 2026-11-03; about $0.43 and 2 minutes a plan, up to $0.98 and 6.5 minutes on the dev split) within $3.50 a day and TC's $15 a month, else by the free models, and checked in code against the newest snapshot and the app's catalog. The report shows it first, with Approve plan and Reject plan; the decision is recorded once, as the signed-in `operator`.

Plan review (Part B, the `reviewer` switch, on by default): before anyone decides, code checks refuse a restart or code fix of a database, queue, cache, storage or proxy, a file name with a wildcard (`*` or `?`; brackets and braces, as in Next.js's `[id]` routes, are path characters), and a file that is not in the app's repo at origin/main. A new file passes only in a folder that exists there and is marked "new file"; watch writes each app's source index. Then Claude Sonnet 5.5 at effort high reads every step against TC's rules, within $0.45 a day and TC's $15 a month. A refused plan is shown with its reasons and takes no decision; a flagged one shows the warnings above the buttons; an ok one says "Reviewed: no issues". The panel says "Reviewed by a second model. Read every step yourself." If the review cannot run (switched off, Claude off, a cap, an error), the plan shows Not reviewed and can still be decided; from 2026-11-10, when Claude ends, the code checks go on and the model review shows Not reviewed. `make prod-switch NAME=reviewer STATE=off` turns the model review off; the code checks always run.

Measured (evals/README.md, `make eval-reviews`): on 16 fresh plans, no trusted plan was refused and 3 of the 4 bad steps were caught. The miss: dns-partition-2's "restart coredns only if it looks wedged", which the labels forbid. Known limits: the "to rule it out" rule was tested only on the dev set, where every such step was refused; medium bad-deploy-1 is caught about half the time.

Actions (phase 3): approving a restart plan queues its kind and target, never its written steps, for the executor (`make dev-executor`, on this PC, not in production). The executor runs `docker compose -p <project> restart <service>` on an app or worker container of the app's catalog in `EXECUTOR_STACK` (default `dev`). It does so only while the `actions` switch is on (`make switch NAME=actions STATE=on`, off by default) and only with `EXECUTOR_DRY_RUN=false`. The default dry run records the command and runs nothing. Each approval runs at most once, and an approval made while the switch is off never runs. A code fix's approval runs nothing.

## Deploy

The web runs on Vercel. The backend runs on this PC as its own compose project, `triagecopilot-prod`, with its own containers, networks and volumes, so dev, test and eval work run beside it (`docker compose ls` lists both). Production runs the images `publish.yml` pushes from main.

Only the gateway's public port is reachable from outside production: on `127.0.0.1:4202` (`PROD_GATEWAY_PORT`) for `watch`, which the egress proxy publishes and forwards to the gateway, and through a Tailscale Funnel sidecar at `https://triage-copilot.<tailnet>.ts.net`. Postgres, ElasticMQ, the gateway's internal listener (4201) and the agent publish nothing. The sim and the executor are not deployed.

Every production network is internal except the egress proxy's, so no other container reaches the internet or this PC's own ports (Egress, below). With the `observability` profile, Prometheus's rules alert to Discord and to an inbox through the same proxy (Alerting, below).

`make prod-env` writes the three env files with a fresh secret for every internal password, key and token. It prints no value, leaves empty only what you paste (it lists those names), and refuses to run when any of the three files exists. The files are never committed (`.gitignore` covers `.env.*`). In production, the gateway and the web refuse the `.env.example` keys.

| File | Read by | Holds |
| --- | --- | --- |
| `.env.prod` | compose, for interpolation | `PROD_POSTGRES_PASSWORD`, `PROD_GATEWAY_DB_PASSWORD`, `PROD_AGENT_DB_PASSWORD`, `PROD_GRAFANA_ADMIN_PASSWORD`; `TC_IMAGES` (`ghcr.io/<owner>`, lower case, from the GitHub remote); `TC_TAG` (`sha-` and the merged commit's first 12 characters, once `publish.yml` has pushed it); `TS_AUTHKEY` (a one-off, tagged, pre-approved auth key: the container keeps it in its environment); the four `ALERT_*` values and `COMPOSE_PROFILES=observability` once alerting is set up (Alerting) |
| `.env.prod.gateway` | the gateway | `GATEWAY_API_KEY`, `APPROVER_API_KEY`, `AGENT_SERVICE_TOKEN`, `EXECUTOR_SERVICE_TOKEN`, `LIVE_TENANTS`; `GATEWAY_TRUST_PROXY=10.231.233.18`, the sidecar's fixed address, once checked (check 5) |
| `.env.prod.agent` | the agent, its migration and the runbook load | the same `AGENT_SERVICE_TOKEN`, `AI_ENABLED=true`, `ANTHROPIC_API_KEY` (the TC workspace's), `GEMINI_API_KEY`, `NVIDIA_API_KEY`, `LIVE_TENANTS` |

The compose file fixes everything container-side: hosts, ports, database URLs, the queue and service addresses, `FASTEMBED_CACHE_PATH`, `SOURCE_INDEX_DIR` and `NODE_ENV=production`. So a line copied from `.env` cannot point a container at 127.0.0.1 or an H: path.

The agent never holds the approver key, and no container holds the web's sign-in secrets. `.env` keeps:

- `PROD_GATEWAY_PORT`.
- `FASTEMBED_CACHE_PATH`, the cache mounted read-only into the agent.
- `SOURCE_INDEX_DIR`, watch's source index folder, mounted read-only into the agent at `/sources`. Production does not start without it.
- `WATCH_SOURCE_INDEX_DIR`, the same folder, where `make dev-watch` writes the index.

| Target | Does |
| --- | --- |
| `make prod-env` | Writes `.env.prod`, `.env.prod.gateway` and `.env.prod.agent` with fresh secrets, printing none, and lists what you paste: `TC_TAG`, `TS_AUTHKEY`, the Anthropic, Gemini and NVIDIA keys, and the four `ALERT_*` values. Refuses, writing nothing, when any of the three exists |
| `make prod-up` | Pulls the images; migrates both schemas and loads the runbooks in one-shot containers; starts Postgres, ElasticMQ, the gateway, the egress proxy, the agent and the Funnel sidecar; waits at most 300 s for the proxy's ports and for the gateway and the agent to answer `/readyz`. If the wait ends there, `docker compose -p triagecopilot-prod ps -a` shows which service is not up, and `docker compose -p triagecopilot-prod logs <service> --tail 30` names the setting it refused |
| `make prod-down` | Removes production's containers and network; the volumes stay (never `-v`) |
| `make prod-token NAME=<tenant>` | Issues a tenant's webhook token in the production database (it has no host port) |
| `make prod-switch [NAME= STATE=]` | The production agent's kill switches |
| `make prod-digest [DAY=]` | A UTC day's uninvestigated alert groups in production |

Tailscale: turn on MagicDNS and HTTPS certificates, and allow Funnel for the auth key's tag in the policy file (the `funnel` node attribute).

GHCR: production pulls `triage-copilot-gateway`, `triage-copilot-agent` and `triage-copilot-squid`, the egress proxy. If they are private, run `docker login ghcr.io` once with a read-only token (`read:packages`) before `make prod-up`.

Vercel: import the GitHub repo as a project, with root directory `apps/web` and Node 24.
- Production environment variables:
  - `GATEWAY_URL`: the Funnel URL.
  - `GATEWAY_API_KEY` and `APPROVER_API_KEY`: as in `.env.prod.gateway`.
  - `LOGIN_PASSWORD_HASH`: what `pnpm --filter @triage/web hash-password` prints.
  - `SESSION_SECRET`: a fresh secret.
- Add a Firewall rate-limit rule on path `/login`, method POST, since the sign-in limit holds per server instance.
- If the first build fails on pnpm 12, add `ENABLE_EXPERIMENTAL_COREPACK=1`.

Checks after `make prod-up`, in PowerShell:

1. Run `docker compose -p triagecopilot-prod ps`. Only the egress proxy shows a port: `127.0.0.1:4202->4202/tcp`. The egress proxy, the gateway and the agent are `(healthy)`.
2. Run `docker compose -p triagecopilot-prod exec tailscale tailscale funnel status`. Funnel is on, and `https://triage-copilot.<tailnet>.ts.net` proxies `/` to `http://gateway:4200` and nothing else. Allow a minute while the sidecar picks a home relay. Then run the egress checks (Egress, below).

Run checks 3 to 5 from a device that is not on the tailnet. On a tailnet device the name resolves over the tailnet, not through Funnel. A 429 there means strangers used up this minute's failed checks: wait a minute.

3. Run `curl.exe -si https://triage-copilot.<tailnet>.ts.net/internal/actions/00000000-0000-4000-8000-000000000000/execution`. Expect 401 with `www-authenticate: ApiKey header="X-API-Key"`, the public listener's challenge, never the internal listener's `Bearer`.
4. Run `curl.exe -s -o NUL -w "%{http_code}" https://triage-copilot.<tailnet>.ts.net/v1/investigations`. Expect 401.
5. The client address. This check is optional: without the setting, every Funnel client shares the sidecar's address, and the web, which always sends its key, is never held back.
   1. Put `GATEWAY_TRUST_PROXY=10.231.233.18` in `.env.prod.gateway`: the sidecar's fixed address on `edge`, never a subnet, which would also trust the gateway's other neighbors. Then run `make prod-up`.
   2. Run `curl.exe -s -H "X-Forwarded-For: 192.0.2.1" https://triage-copilot.<tailnet>.ts.net/healthz`.
   3. Run `docker compose -p triagecopilot-prod logs gateway --since 2m | Select-String healthz`. The healthcheck asks for `/readyz`, so this shows only your request. Its `remoteAddress` must be your own public address, never 192.0.2.1 or the sidecar's.
   4. If it is not, remove the line and run `make prod-up` again.
6. On Vercel, sign in on desktop and phone, in both themes, and open a report. Then run `make prod-down` and reload: the site shows the demo with the offline note. Run `make prod-up` again.
7. With production up, run `make infra-up` and `make test-int`. They run beside production, and `docker compose ls` lists `triagecopilot` and `triagecopilot-prod`. Then run `make infra-down`.

### Egress

```
tailscale --edge, 10.231.233.18--> gateway:4200                              (Funnel's target)
watch     --127.0.0.1:4202--> egress-proxy:4202 --ingress--> gateway:4200 (10.231.233.34)
agent     --outbound, 10.231.233.2--> egress-proxy:3128 --egress--> the model providers
tailscale --outbound, 10.231.233.3--> egress-proxy:3129 --egress--> Tailscale, Let's Encrypt
```

Docker Desktop lets any container on a routed network reach the internet and this PC's own listeners at `host.docker.internal` (192.168.65.254), and publishes no port from an internal network. So every production network is internal (`data`, `edge`, `ingress`, `outbound`, and `monitoring` for the observability profile) except `egress`, which only `egress-proxy` joins. The proxy is squid (`infra/squid/squid.conf`), as in FreightAudit and FilingLens:

- CONNECT to port 443 only; nothing cached.
- Every loopback, private, link-local, CGNAT and other non-public destination is refused before any allowlist, by each address a name resolves to.
- Names only: a bare IP address never matches.
- One port per client, and each port serves only its client's fixed address on `outbound`. So a compromised agent cannot use the sidecar's way out, or the reverse: neither can forge the other's address, since the agent runs as a non-root user and the sidecar without capabilities.
- The proxy starts after the gateway, so a gateway from before the lockdown, which published 4202 itself, is recreated first. Squid's descriptor limit is 4,096: at Docker's default of 1,048,576 it idled at 153 MiB of its 256 MB.
- Port 4202 is a reverse proxy to the gateway's fixed address, whatever URL a request names. A CONNECT there gets 405, and squid's cache manager 403. It passes the client's `Authorization` header on (`login=PASSTHRU`); without that option squid drops the header on the way to an origin server, and the gateway answers every alert with 401.
- The sidecar shares `edge` with the gateway alone, and no network with Postgres or ElasticMQ.
- CI builds the image from `infra/docker/squid.Dockerfile`, and `publish.yml` pushes `triage-copilot-squid` with the same `sha-<12>` tags, so one `TC_TAG` pins the gateway, the agent and the proxy.

| Port | Client | Allowed |
| --- | --- | --- |
| 3128 | agent (10.231.233.2) | `api.anthropic.com`, `generativelanguage.googleapis.com`, `integrate.api.nvidia.com` |
| 3129 | tailscale (10.231.233.3) | `controlplane.tailscale.com`; the relays, by `^derp[0-9]+[a-z]*[.]tailscale[.]com$` (with no UDP out, all traffic rides a relay, Funnel's included); `acme-v02.api.letsencrypt.org` (Funnel's certificate) |
| 4202 | watch, on this PC | the gateway's public listener only |
| 3130 | Alertmanager (10.231.233.50, observability profile) | `discord.com` |
| 3131 | the mail relay (10.231.233.51, observability profile) | `smtp.gmail.com`, on 587 alone |
| 4240 | Prometheus (10.231.233.52, observability profile) | `GET /readyz` of watch on this PC (`host.docker.internal:4240`) only |

- Not allowed: Tailscale's log server (`--no-logs-no-support` turns its upload off), OpenRouter and Ollama (not used in production), and every other host.
- The agent's model clients read `HTTPS_PROXY` themselves (`test_providers.py` checks each); every internal address is `http://`, which it leaves alone.
- Change the allowlist in `infra/squid/squid.conf` and its live test, `services/agent/tests/test_egress_int.py` (run by `make test-int`), by pull request. After the merge and a `git pull` here, run `docker restart triagecopilot-prod-egress-proxy-1`, which reads the mounted file again.
- Read its log: `docker logs triagecopilot-prod-egress-proxy-1 | Select-String TCP_DENIED` lists refusals, and `Select-String CONNECT` the tunnels. A tunnel's line appears only when it closes. The healthcheck adds three `NONE_NONE/000` lines every 10 s.

Egress checks, after check 2, in PowerShell:

1. Run `docker inspect --format '{{.Name}}: {{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' triagecopilot-prod-gateway-1 triagecopilot-prod-agent-1 triagecopilot-prod-tailscale-1 triagecopilot-prod-egress-proxy-1`. Expect the gateway on `data`, `edge` and `ingress`; the agent on `data` and `outbound`; the sidecar on `edge` and `outbound`; the proxy on `alerting`, `egress`, `ingress` and `outbound`, each with the `triagecopilot-prod_` prefix.
2. Run `docker logs triagecopilot-prod-egress-proxy-1 | Select-String CONNECT`. Expect `TCP_TUNNEL/200` for Tailscale's hosts from 10.231.233.3. Then `Select-String TCP_DENIED`: nothing. If the sidecar asks for another host, report it; never allow a bare IP address.
3. The model routes, with no key and no model call: `docker exec triagecopilot-prod-agent-1 python -c "import httpx2; [print(h, httpx2.head('https://' + h, timeout=10).status_code) for h in ('api.anthropic.com', 'generativelanguage.googleapis.com', 'integrate.api.nvidia.com')]"`. Each host prints an HTTP status, and the proxy's log shows a tunnel from 10.231.233.2 for each.
4. This PC's ports are closed to the agent, so every result is non-zero (`10.231.233.1` is `outbound`'s gateway address): `docker exec triagecopilot-prod-agent-1 python -c "import socket; socket.setdefaulttimeout(2); [print(h, p, socket.socket().connect_ex((h, p))) for h in ('192.168.65.254', '10.231.233.1') for p in (4200, 4201, 4231, 5434, 4566, 445)]"`. And `docker exec triagecopilot-prod-agent-1 python -c "import socket; socket.create_connection(('host.docker.internal', 4231), 3)"` fails. This is P1.12 (4) for TC's own containers.
5. Through the proxy, a 403: `docker exec triagecopilot-prod-agent-1 python -c "import socket; s = socket.create_connection(('egress-proxy', 3128), 5); s.sendall(b'CONNECT host.docker.internal:443 HTTP/1.1\r\nHost: x\r\n\r\n'); print(s.recv(40))"` prints `b'HTTP/1.1 403 Forbidden...'`.
6. watch's port: `curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:4202/readyz` prints 200. Then `curl.exe -s -o NUL -w "%{http_code}" -X POST -H "Authorization: Bearer not-a-token" -H "X-API-Key: x" http://127.0.0.1:4202/v1/alerts` prints 401: the token reached the gateway, which knows no tenant for it. A 403 means the proxy dropped the token and the gateway saw only the key, so every alert would get 401 "Send a valid tenant webhook token." even with the right token. Then check that `infra/squid/squid.conf`'s gateway `cache_peer` has `login=PASSTHRU`, run `docker restart triagecopilot-prod-egress-proxy-1`, and check again.
7. A day later, `docker stats --no-stream triagecopilot-prod-egress-proxy-1` stays well under its 256 MB, and `Select-String TCP_DENIED` shows only egress check 5's probe.

Rollback, if Funnel's `/healthz` is not 200 within 10 minutes, or the model calls or `watch`'s posts fail through the proxy:

1. Run `make prod-down`.
2. Run `git checkout <the commit before the egress lockdown> -- infra/docker-compose.prod.yml`, then `make prod-up`. Every container is back on one routed network, with open egress, and the gateway publishes 4202 itself.
3. Report what failed. Later, `git checkout HEAD -- infra/docker-compose.prod.yml` restores the lockdown.

### Alerting

```
prometheus   --data--> agent:4210/metrics
blackbox     (in Prometheus's network namespace) --data--> gateway:4200/readyz, agent:4210/readyz
blackbox     --alerting, 10.231.233.52--> egress-proxy:4240 --egress--> watch's 127.0.0.1:4240/readyz
egress-proxy --alerting, UDP--> egress-log:5514 (counts the refusals) <--alerting-- prometheus
prometheus   --alerting--> alertmanager:9093
alertmanager --alerting, 10.231.233.50--> egress-proxy:3130 --egress--> discord.com:443
alertmanager --alerting--> smtp-relay:2525 (10.231.233.51) --> egress-proxy:3131 --egress--> smtp.gmail.com:587
```

With `COMPOSE_PROFILES=observability` in `.env.prod`, production runs Prometheus with its rules (`infra/prometheus-alerts.yml`) and Alertmanager (`infra/alertmanager/`), which sends each alert to a Discord channel and to an inbox. `docs/alerts.md` says what each alert means and what to do.

| Rule | Fires when | Severity |
| --- | --- | --- |
| GatewayNotReady | the gateway's `/readyz` fails for 2 minutes | critical |
| AgentNotReady | the agent's `/readyz` or `/metrics` fails for 2 minutes | critical |
| WatchNotReady | watch's `/readyz` on this PC fails for 5 minutes | warning |
| InvestigationsFailing | an investigation message went to the dead-letter queue, or failed validation, in the last hour | warning |
| ClaudeOff | Claude takes no live triage call for a minute: switched off, key rejected, credits or TC's monthly cap used up. Not its planned end on `LLM_CLAUDE_UNTIL` | warning |
| ClaudeDailyCapReached | today's Claude spend leaves less than one largest call under the triage cap ($0.40, UTC) | warning |
| EgressDenied | the egress proxy refused a request in the last 10 minutes | warning |
| MonitoringBlind | Prometheus cannot read the probe exporter or egress-log for 5 minutes | warning |

- **No route of its own.** Alertmanager, the mail relay and egress-log sit on `alerting` alone: internal, with Prometheus and the egress proxy, so no app container reaches Alertmanager's API, which has no login. Nothing publishes a port.
- **Discord** goes through the proxy's port 3130, which serves 10.231.233.50 alone and allows `discord.com:443` alone. So the webhook URL must start with `https://discord.com/api/webhooks/`; `discordapp.com`, `canary.` and `ptb.` are refused, and the entrypoint says so at start.
- **Mail:** Alertmanager sends no SMTP through a proxy, so `smtp-relay` (socat, from the proxy's image) takes its connections, from 10.231.233.50 alone, and opens each as a CONNECT to `smtp.gmail.com:587` on port 3131. STARTTLS runs from Alertmanager to Gmail, the certificate checked against `smtp.gmail.com`. A connection idle for 60 s closes.
- **Probes:** the blackbox exporter runs in Prometheus's network namespace and answers on its loopback alone, so no other container can make it probe anything. watch runs on this PC, which only the proxy reaches: port 4240 forwards `GET /readyz` from Prometheus's address alone to `host.docker.internal:4240`, which reaches watch's 127.0.0.1 listener (checked on Docker Desktop on 2026-11-01).
- **Refusals:** the proxy sends every access log line over UDP to egress-log, a collector of its own on `alerting`, which serves the count of `TCP_DENIED` lines to Prometheus. It adds up each refusal since egress-log started (`infra/otel-egress-log.yaml`). With the profile off, nobody listens and nothing waits.
- **The agent's `/metrics`** serves the failed investigation messages of the last hour and, while live triage's Claude takes no call, why. It takes no credential, like the probes, and answers on internal networks only.
- **Messages:** one per rule, its alerts listed, titled `Triage Copilot firing: <rule>` with a count when several fire. Each alert gives its severity, its summary, `Firing since <UTC time>` and its runbook link. No other link, no HTML part. Discord also says when an alert resolves; email gets firing messages only. A group waits 30 s, then at least 10 minutes between messages, and reminds every 12 hours while it fires.
- **Secrets:** `.env.prod` holds the four `ALERT_*` values. Alertmanager's entrypoint writes the webhook URL and the password to tmpfs files only its user can read, puts the two addresses into a copy of the config, and starts Alertmanager without the four in its environment. It stops, naming the setting and never its value, when one is missing or malformed. The committed config holds placeholders only.
- **Silence** an alert you are working on: `docker exec triagecopilot-prod-alertmanager-1 amtool silence add alertname=<name> --duration=2h --comment="<why>" --alertmanager.url=http://127.0.0.1:9093`. `amtool silence query` lists silences and `amtool silence expire <id>` ends one, with the same `--alertmanager.url`. Silences, what was sent, and Prometheus's history survive restarts in the volumes `alertmanager-data` and `prometheus-data`.
- **Change the rules or the messages** by pull request, with their tests (`infra/prometheus-alerts.test.yml`, `services/agent/tests/test_alerting_int.py`). After the merge and a `git pull` here:
  - Rules: `docker kill --signal=HUP triagecopilot-prod-prometheus-1` reloads them. A restart would also need the probe exporter restarted after it, since it shares Prometheus's network namespace.
  - Messages: `docker restart triagecopilot-prod-alertmanager-1`. The entrypoint writes the config only at start.
  - egress-log's count (`infra/otel-egress-log.yaml`): `docker restart triagecopilot-prod-egress-log-1`. The count starts again from none, which EgressDenied reads as the series appearing at its next refusal.
- **A new webhook or password:** change `.env.prod`, then `make prod-up`.
- **Not watched:** see `docs/alerts.md`, Not watched. If Alertmanager, the relay, the proxy or Prometheus stops, nothing says so.

Alerting rollout, after the egress checks pass, in PowerShell:

1. Make the two channels:
   - Discord: Server Settings → Integrations → Webhooks → New Webhook. Pick the channel, then Copy Webhook URL. It starts with `https://discord.com/api/webhooks/`.
   - Gmail: Google Account → Security → 2-Step Verification (on) → App passwords. Create "Triage Copilot alerts" and keep the 16 letters, without spaces.
2. In `.env.prod`, fill in `ALERT_DISCORD_WEBHOOK_URL`, `ALERT_SMTP_PASSWORD`, `ALERT_EMAIL_FROM` (the Gmail address) and `ALERT_EMAIL_TO` (where alerts go; it may be the same, and several are comma-separated). Then add `COMPOSE_PROFILES=observability`. `make prod-up` reads the profile from `.env.prod` alone; dev's `.env` no longer reaches it.
3. Run `make prod-up`. It pulls the new images and creates `triagecopilot-prod_alerting`. It starts the collector, Jaeger, Prometheus, the probe exporter, Grafana, egress-log, the mail relay and Alertmanager. It recreates the egress proxy with its new network and ports, which pauses the model calls and watch's posts for a few seconds. If the wait ends with Alertmanager restarting, `docker compose -p triagecopilot-prod logs alertmanager --tail 5` names the missing or malformed setting.
4. Checks:
   1. `docker compose -p triagecopilot-prod ps`: Alertmanager, Prometheus, the egress proxy, the gateway and the agent are `(healthy)`, the rest Up. Only the egress proxy shows a port.
   2. `docker inspect --format '{{.Name}}: {{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' triagecopilot-prod-alertmanager-1 triagecopilot-prod-smtp-relay-1 triagecopilot-prod-egress-log-1 triagecopilot-prod-prometheus-1`: Alertmanager, the relay and egress-log on `alerting` alone, and Prometheus on `alerting`, `data` and `monitoring`. `docker inspect --format '{{.HostConfig.NetworkMode}}' triagecopilot-prod-blackbox-1` starts with `container:`.
   3. Prometheus found Alertmanager: `docker exec triagecopilot-prod-prometheus-1 wget -qO- http://127.0.0.1:9090/api/v1/alertmanagers` lists `http://alertmanager:9093/api/v2/alerts`.
   4. Every target answers: `docker exec triagecopilot-prod-prometheus-1 wget -qO- "http://127.0.0.1:9090/api/v1/query?query=up"` shows 1 for `agent`, `egress-log`, `otel-collector` and the three `readyz` probes. Then `query=probe_success` shows 1 for the gateway and the agent, and for watch while `make dev-watch` runs. Without watch, WatchNotReady fires after 5 minutes.
   5. Alertmanager has no way around the proxy, so both of these fail: `docker exec triagecopilot-prod-alertmanager-1 wget -q -T 3 -O /dev/null http://192.168.65.254:4240/readyz` and the same with `https://discord.com/`.
   6. No app container reaches Alertmanager. This fails with `gaierror`: `docker exec triagecopilot-prod-agent-1 python -c "import socket; socket.create_connection(('alertmanager', 9093), 3)"`.
5. One test alert to both channels: `docker exec triagecopilot-prod-alertmanager-1 amtool alert add alertname=DeliveryTest severity=warning --annotation=summary="A test of alert delivery; nothing is wrong." --annotation=runbook_url=https://github.com/kwnhyk04/triage-copilot/blob/main/docs/alerts.md --alertmanager.url=http://127.0.0.1:9093`
   - Within a minute, Discord shows "Triage Copilot firing: DeliveryTest", and the inbox gets the same. The first mail may land in Spam: mark it Not spam.
   - It resolves by itself after 5 minutes. Discord then says so within 10 more; email sends nothing for it.
   - `docker logs triagecopilot-prod-egress-proxy-1 | Select-String "discord.com|smtp.gmail.com"` shows `TCP_TUNNEL/200` for `discord.com:443` from 10.231.233.50 and `smtp.gmail.com:587` from 10.231.233.51.
   - If a channel stays silent, `docker logs triagecopilot-prod-alertmanager-1 --since 15m` names the failing integration, never the secret. Fix the value in `.env.prod`, run `make prod-up`, and send the test again.
6. The refusal path: run egress check 5 again. Within about a minute EgressDenied reaches both channels, and Discord says it resolved within about 20 minutes.
7. A day later: `docker stats --no-stream` keeps each new container well under its limit, and Discord and the inbox hold nothing but real alerts and the two tests.

Rollback of alerting alone, about 2 minutes; the egress proxy keeps its ports, which serve nobody:

1. In `.env.prod`, set `COMPOSE_PROFILES=` (empty).
2. Run `docker compose -p triagecopilot-prod -f infra/docker-compose.yml -f infra/docker-compose.prod.yml --env-file .env.prod --profile observability rm --stop --force alertmanager smtp-relay egress-log blackbox prometheus grafana jaeger otel-collector`. The volumes stay; never `-v`.
3. A leaked webhook or password: delete the webhook in Discord (Integrations → Webhooks) and revoke the app password in the Google Account, then make new ones for `.env.prod`.

## Known gaps

Scenario set v1 (`evals/scenarios/v1/`, frozen on 2026-10-16) leaves these for v2:

- No scenario's answer is traffic_surge, and none's is unknown (evidence that supports no class).
- The rest of the list is in `evals/README.md`.
