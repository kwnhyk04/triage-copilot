# Alerts

Production's alert rules (`infra/prometheus-alerts.yml`), one section each: what fired, what it stops, and what to do. Each Discord message and email links its rule's section here; delivery and silences are in the README (Deploy, Alerting). Commands run from the repo root, in PowerShell on the PC. On a Linux server, use `grep -E` for `Select-String -Pattern` and `curl` for `curl.exe`.

## GatewayNotReady

The probe of `http://gateway:4200/readyz` failed for 2 minutes: the gateway is down, or it cannot reach Postgres. watch's alerts reach no investigation, and the web shows the demo.

1. `docker compose -p triagecopilot-prod ps -a gateway postgres` shows which one is down.
2. `docker compose -p triagecopilot-prod logs gateway --since 15m` names a setting it refused, or shows the database errors.
3. Fix the cause, then `make prod-up`, which starts what stopped and waits until it is ready.

It resolves 10 minutes after the probe passes again.

## AgentNotReady

The agent's `/readyz` (Postgres and the investigations queue) failed for 2 minutes, or Prometheus could not read its `/metrics`. Investigations wait in the queue, and each message keeps its 5 receives.

1. `docker compose -p triagecopilot-prod ps -a agent postgres elasticmq`.
2. `docker compose -p triagecopilot-prod logs agent --since 15m | Select-String -Pattern "readiness check failed|metrics failed"` shows the exception.
3. Fix the cause, then `make prod-up`.

While its metrics fail, InvestigationsFailing, ClaudeOff and ClaudeDailyCapReached cannot fire.

## WatchNotReady

watch's `/readyz` on the host failed for 5 minutes. The probe goes through the egress proxy's port 4240, the only way from a container to the host. watch is not running, its last poll is over three intervals old, or that poll did not read and post everything. No FreightAudit or FilingLens incident reaches TC meanwhile.

1. On a Linux server, run `systemctl status tc-watch` and `journalctl -u tc-watch --since "15 minutes ago"`. systemd restarts watch, so a stopped unit means someone stopped it, and a crash loop shows here. On the PC, check that `make dev-watch` still runs, and run `curl.exe -s http://127.0.0.1:4240/readyz`.
2. Its log names the app whose poll failed (`poll failed`). A post the gateway refused shows as `alert not posted` with its status. A 401 means the token in `.env` is not the tenant's current one (`make prod-token` replaces it), or the proxy drops it (docs/guide.md, Deploy, egress check 6).
3. If watch answers 200 and the alert stays: `docker logs triagecopilot-prod-egress-proxy-1 --since 15m | Select-String "watch/readyz"`. A `/503` there means the proxy cannot reach watch. `WATCH_PORT` must stay 4240. On the PC, `WATCH_HOST` must stay `127.0.0.1`. On a Linux server it must be the default bridge's address, and the firewall must let `tc-egress` reach port 4240 (docs/server-move.md, Firewall).
4. Start it again: `sudo systemctl start tc-watch`, or `make dev-watch` on the PC.

## InvestigationsFailing

Investigation messages failed in the last hour, by `kind`:

- `dlq`: a message failed its 5 receives, moved to `investigations-dlq.fifo`, and the agent marked its investigation failed.
- `unreadable`: a message failed validation and was kept in `agent.dead_letters`.

1. `docker compose -p triagecopilot-prod logs agent --since 1h | Select-String -Pattern "error|failed"` shows why.
2. Fix the cause. Then `docker compose -p triagecopilot-prod exec -T agent python -m agent.dlq replay` sends both stores back to the queue. A message that fails again returns to its store.

It resolves an hour after the last failure.

## ClaudeOff

Live triage's Claude takes no call, so live triage runs on Gemini, then NVIDIA. The `reason` says why:

- `switched_off`: someone ran `make prod-switch NAME=claude STATE=off`.
- `key_rejected`: Anthropic answered 401 or 403. No Claude call happens until the agent restarts.
- `credits_exhausted`: a 402, or a spend limit. Claude stays off until the month ends (UTC).
- `monthly_cap`: TC's own $15 a month (`LLM_CLAUDE_MONTHLY_USD`) is used up, until the month ends (UTC).
- `unpriced`: a Claude model has no prices in `agent/pricing.py`, so no cap can be checked.

The credits' planned end (`LLM_CLAUDE_UNTIL`, 2026-11-10) does not fire this rule, but it ends plan reviews: see PlansUnreviewed. Today's cap has its own rule.

1. `make prod-switch` shows the switches. `docker compose -p triagecopilot-prod logs agent --since 1h | Select-String -Pattern "Anthropic|credits"` shows the provider's answer.
2. Fix by reason:
   - `switched_off`, if it was not meant: `make prod-switch NAME=claude STATE=on`.
   - `key_rejected`: put a new key in `.env.prod.agent`, then run `make prod-up`, which recreates the agent.
   - Credits or the monthly cap: check the Anthropic Console, or wait for the month.

## ClaudeDailyCapReached

Today's Claude spend (UTC) leaves less than one largest call's worst case under the triage cap (`LLM_TRIAGE_DAILY_USD`, $0.40). Live triage runs on the free models until midnight UTC, when this resolves.

Nothing needs fixing unless it fires every day. If it does, compare the day's triages (`make prod-digest`) with the flood guard's 30 a day per app before raising the cap.

## PlansUnreviewed

Plans arrived in the last hour that no model reviewed. The gateway refuses to approve such a plan, and the web shows it as "Not reviewed, cannot be approved". A human can still fix the incident by hand. Each plan's review says why it went unreviewed.

1. Open the report and read the plan's `Not reviewed:` line.
2. Fix by reason:
   - The reviewer is switched off: `make prod-switch NAME=reviewer STATE=on`.
   - Its credits ended (`LLM_CLAUDE_UNTIL`, 2026-11-10): every plan goes unreviewed until that date moves in `.env.prod.agent` and `make prod-up` recreates the agent. If Claude has really ended, switch the planner off with `make prod-switch NAME=planner STATE=off`.
   - The reviewer's cap (`LLM_REVIEWER_DAILY_USD`) is spent: it resets at midnight UTC.
   - The reviewer's model failed: `docker compose -p triagecopilot-prod logs agent --since 1h | Select-String -Pattern "reviewer|anthropic"`.

A plan is reviewed once, so one that went unreviewed stays so. It resolves an hour after the last such plan.

## EgressDenied

The egress proxy refused a request (`TCP_DENIED`) in the last 10 minutes: a container asked for a host, port or path outside its allowlist.

1. `docker logs triagecopilot-prod-egress-proxy-1 --since 15m | Select-String TCP_DENIED` names the client's address and the destination:
   - 10.231.233.2: the agent.
   - 10.231.233.3: the Funnel sidecar.
   - 10.231.233.50: Alertmanager.
   - 10.231.233.51: the mail relay.
   - 10.231.233.52: Prometheus.
2. A host an app now needs, such as a provider's new name or a new Tailscale relay: add it to `infra/squid/squid.conf` and its live test by pull request (docs/guide.md, Egress). Never allow a bare IP address.
3. Anything else counts as a compromise until it is explained. `docker compose -p triagecopilot-prod stop tailscale` takes TC off the internet in seconds.

Egress check 5 in docs/guide.md makes one refusal on purpose.

## MonitoringBlind

Prometheus could not read the probe exporter (`readyz`), egress-log, the gateway's metrics (`gateway`) or Alertmanager's (`alertmanager`) for 5 minutes, so the readiness or refusal rules, the verdict and waiting-plan messages, or AlertDeliveryFailing cannot fire.

1. `docker compose -p triagecopilot-prod ps -a blackbox egress-log prometheus gateway alertmanager`.
2. The probe exporter shares Prometheus's network namespace, so it needs a restart after Prometheus is recreated: `make prod-up` recreates both.

If Alertmanager itself is down, this alert has no way out until it is back.

## AlertDeliveryFailing

Alertmanager gave up on a message to Discord or the inbox in the last 15 minutes, after its retries; `integration` says which. An alert may never have arrived there. This message comes through the other channel. If both fail, neither says so, and only Prometheus shows it.

1. `docker logs triagecopilot-prod-alertmanager-1 --since 1h` names the integration and its error, never the secret.
2. Fix by integration:
   - `email`: a `535` means Gmail refused the app password. Make a new one from the sending account, put it in `ALERT_SMTP_PASSWORD` in `.env.prod`, and run `make prod-up`.
   - `discord`: a `404` means the webhook was deleted. Make a new one, put it in `ALERT_DISCORD_WEBHOOK_URL` in `.env.prod`, and run `make prod-up`. For anything else, check the proxy's log (EgressDenied) and Discord's status page.
3. Send the test alert (docs/guide.md, Alerting, step 5).

It resolves 15 minutes after the last failure. A verdict or plan message lost meanwhile is not sent again, but the report stays on the web.

## InvestigationVerdict

Not a fault: a live investigation reached a verdict. One Discord message, no email, with a link to the report. It names the app, the verdict and the cause class, never text a model wrote. A report the agent wrote after a stop has no verdict and sends nothing.

Open the report. The series lasts an hour, so a verdict that lands while Prometheus or Alertmanager is down for longer never sends a message. The web still has it.

## PlanWaiting

Not a fault: a plan a model reviewed waits for a human. One Discord message, no email, with a link to the report. A refused plan and one no model reviewed send nothing here; the second fires PlansUnreviewed.

Open the report and approve or reject it. An approval expires after `APPROVAL_WINDOW_HOURS` (24 by default), so an approved restart the executor has not claimed by then never runs.

## Not watched

No rule here reports these:

- Alertmanager, the mail relay, the egress proxy or Prometheus itself stopping, and the whole host going down. Delivery goes through the proxy, and all of it runs on the host.
- Both channels failing at once. AlertDeliveryFailing needs one that works.

On a Linux server, the outside monitor covers the host, Docker, the proxy, the gateway and Postgres, since its heartbeat needs all of them (docs/server-move.md, Outside monitor). It does not see Alertmanager, the mail relay, Prometheus or Funnel.

`docker logs triagecopilot-prod-alertmanager-1 --since 1h` names the integration that failed, never the secret.
