# Alerts

Production's alert rules (`infra/prometheus-alerts.yml`), one section each: what fired, what it stops, and what to do. Each Discord message and email links its rule's section here; delivery and silences are in the README (Deploy, Alerting). Commands run in PowerShell from the repo root.

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

watch's `/readyz` on this PC failed for 5 minutes. The probe goes through the egress proxy's port 4240, the only way from a container to this PC. watch is not running, its last poll is over three intervals old, or that poll did not read and post everything. No FreightAudit or FilingLens incident reaches TC meanwhile.

1. Check that `make dev-watch` still runs, and run `curl.exe -s http://127.0.0.1:4240/readyz`.
2. Its log names the app whose poll failed (`poll failed`). A post the gateway refused shows as `alert not posted` with its status. A 401 means the token in `.env` is not the tenant's current one (`make prod-token` replaces it), or the proxy drops it (README, Deploy, egress check 6).
3. If watch answers 200 and the alert stays: `docker logs triagecopilot-prod-egress-proxy-1 --since 15m | Select-String "watch/readyz"`. A `/503` there means the proxy cannot reach watch: `WATCH_HOST` must stay `127.0.0.1` and `WATCH_PORT` 4240.
4. Start `make dev-watch` again.

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

The credits' planned end (`LLM_CLAUDE_UNTIL`, 2026-11-10) fires nothing. Today's cap has its own rule.

1. `make prod-switch` shows the switches. `docker compose -p triagecopilot-prod logs agent --since 1h | Select-String -Pattern "Anthropic|credits"` shows the provider's answer.
2. Fix by reason:
   - `switched_off`, if it was not meant: `make prod-switch NAME=claude STATE=on`.
   - `key_rejected`: put a new key in `.env.prod.agent`, then run `make prod-up`, which recreates the agent.
   - Credits or the monthly cap: check the Anthropic Console, or wait for the month.

## ClaudeDailyCapReached

Today's Claude spend (UTC) leaves less than one largest call's worst case under the triage cap (`LLM_TRIAGE_DAILY_USD`, $0.40). Live triage runs on the free models until midnight UTC, when this resolves.

Nothing needs fixing unless it fires every day. If it does, compare the day's triages (`make prod-digest`) with the flood guard's 30 a day per app before raising the cap.

## EgressDenied

The egress proxy refused a request (`TCP_DENIED`) in the last 10 minutes: a container asked for a host, port or path outside its allowlist.

1. `docker logs triagecopilot-prod-egress-proxy-1 --since 15m | Select-String TCP_DENIED` names the client's address and the destination:
   - 10.231.233.2: the agent.
   - 10.231.233.3: the Funnel sidecar.
   - 10.231.233.50: Alertmanager.
   - 10.231.233.51: the mail relay.
   - 10.231.233.52: Prometheus.
2. A host an app now needs, such as a provider's new name or a new Tailscale relay: add it to `infra/squid/squid.conf` and its live test by pull request (README, Egress). Never allow a bare IP address.
3. Anything else counts as a compromise until it is explained. `docker compose -p triagecopilot-prod stop tailscale` takes TC off the internet in seconds.

README's egress check 5 makes one refusal on purpose.

## MonitoringBlind

Prometheus could not read the probe exporter (`readyz`) or egress-log for 5 minutes, so the readiness or refusal rules cannot fire.

1. `docker compose -p triagecopilot-prod ps -a blackbox egress-log prometheus`.
2. The probe exporter shares Prometheus's network namespace, so it needs a restart after Prometheus is recreated: `make prod-up` recreates both.

## Not watched

Nothing reports these:

- Alertmanager, the mail relay, the egress proxy or Prometheus itself stopping. Delivery goes through the proxy.
- Google revoking the app password, which a change of the Google password does, or the webhook being deleted in Discord.

`docker logs triagecopilot-prod-alertmanager-1 --since 1h` names the integration that failed, never the secret.
