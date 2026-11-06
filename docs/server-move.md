# Moving to a Linux server

How to move Triage Copilot's backend from the Windows PC to one always-on Linux server, an Oracle Cloud ARM instance or an x86 VPS. The web stays on Vercel and needs no change. Work through the sections in order. `<angle brackets>` mark what only you know. Commands run on the server as the user `tc` in `/opt/triage-copilot`, unless the section says otherwise.

## Before the move

1. **The Claude date.** `LLM_CLAUDE_UNTIL` is 2026-11-10. From that date the plan reviewer stops, and no plan can be approved. Move the date in `.env.prod.agent` first if the credits run on (`docs/alerts.md`, PlansUnreviewed).
2. **FreightAudit and FilingLens.** watch reads their Docker containers and their git clones on its own machine, so they must run on this server too. Move them first, or TC sees no incident from them.
3. **Images for both architectures.** Only tags published since multi-arch landed have arm64. Check the tag you will deploy:

   ```sh
   docker buildx imagetools inspect ghcr.io/kwnhyk04/triage-copilot-agent:sha-<12> | grep Platform
   ```

   It must list `linux/amd64` and `linux/arm64`. The GHCR packages are public, so the server needs no `docker login`.

4. **The outage.** Funnel and alert intake are down from the first step on the PC until `make prod-up` ends on the server. Expect about 30 minutes the first time. watch holds nothing while it is stopped, so an incident in that window is seen only if it is still happening afterwards.

## Server size

| Item | Size |
| --- | --- |
| Core at idle: agent, gateway, ElasticMQ, Postgres, Tailscale, squid | 373 MiB |
| Alerting at idle: Prometheus, its exporter, Alertmanager, egress-log, mail relay | 97 MiB |
| Tracing and dashboards at idle, unused: Grafana, the collector, Jaeger | 269 MiB |
| The agent once its embedding model is loaded | about 1 GB, limit 2 GB |
| Images, one full set | about 6.6 GB; each deploy adds up to 2.6 GB until old tags are removed |
| Embedding model | 523 MB |
| Database | 68 MB today; under 2 GB at worst with the flood caps and 30-day retention |

TC alone is comfortable on 4 GB of memory and 40 GB of disk, and 20 GB of disk is the floor. FreightAudit's and FilingLens's own needs were not measured, so add theirs. Use Ubuntu 24.04, the release these steps were written for.

Oracle's Always Free tier, from Oracle's Always Free page on 2026-11-06:

- A1 Arm instances get 1,500 OCPU hours and 9,000 GB hours a month, equal to 2 OCPUs and 12 GB of memory on an Always Free tenancy. The default boot volume is 50 GB, out of 200 GB of free block storage.
- Oracle may reclaim an idle instance. It counts as idle when, over 7 days, CPU at the 95th percentile, network and, on A1, memory are all under 20%. TC alone sits under all three, since 20% of 12 GB is 2.4 GB. Three apps on one server may clear the memory line, but this was not measured. Check `free -h` after a week.
- A1 capacity is often unavailable in a region. This comes from community reports, not Oracle's page.

## Firewall

Inbound, the server needs SSH alone. Funnel dials out, so 80 and 443 stay closed. Allow only TCP 22, in the provider's firewall too (on Oracle, the subnet's security list), and from your own address if it is stable.

Outbound, it needs TCP 443 (registries, Tailscale, the model providers, Discord, the outside monitor, your bucket), TCP 587 (Gmail), DNS and NTP. Inside, one rule is needed: the egress proxy's bridge must reach watch's port on the host.

With ufw:

```sh
sudo ufw allow OpenSSH
sudo ufw allow in on tc-egress to any port 4240 proto tcp
sudo ufw enable
```

Oracle's Ubuntu images ship their own `iptables` rules that reject most inbound traffic, so skip ufw there and add the rule ahead of them:

```sh
sudo iptables -I INPUT -i tc-egress -p tcp --dport 4240 -j ACCEPT
sudo netfilter-persistent save
```

Docker publishes only `127.0.0.1:4202`, which no outside host can reach.

## Install Docker and the tools

As a user with sudo, on the server:

```sh
sudo apt-get update
sudo apt-get install -y ca-certificates curl git make rsync
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
```

Log limits for every container on the server, FreightAudit's and FilingLens's too. TC's own compose file sets the same. Docker reads this at start, and it applies to containers made after that:

```sh
echo '{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }' | sudo tee /etc/docker/daemon.json
sudo systemctl enable docker
sudo systemctl restart docker
```

The deploy user, and the folders outside the checkout. Everyone in the `docker` group is root in all but name, so add nobody else:

```sh
sudo useradd --create-home --shell /bin/bash --groups docker tc
sudo mkdir -p /opt/triage-copilot /srv/triage-copilot/fastembed /srv/triage-copilot/sources /srv/apps
sudo chown tc:tc /opt/triage-copilot /srv/triage-copilot/fastembed /srv/triage-copilot/sources /srv/apps
sudo chmod 755 /srv/triage-copilot /srv/triage-copilot/fastembed /srv/triage-copilot/sources
```

Make these folders before the first `make prod-up`. Docker creates a missing bind source itself, owned by root, and watch could then not write there.

## The checkout

The containers read their config from this checkout, so it must be the commit of `TC_TAG`:

```sh
sudo -iu tc
umask 022
git clone https://github.com/kwnhyk04/triage-copilot.git /opt/triage-copilot
cd /opt/triage-copilot
git checkout <the commit of TC_TAG>
find infra -type f ! -perm -o=r
```

The last command must print nothing. The containers run as their own non-root users, so every mounted file needs to be readable by others. A clone under `umask 022` gives that, and one under a stricter umask leaves Squid, Prometheus and Alertmanager unable to start.

## The env files

Four plain files beside the repo. `.gitignore` covers all of them, and they are never committed. Never send them by chat or email, and never paste one into a ticket or a prompt. They travel over SSH alone.

Open each on the server over SSH and type or paste it in, or copy the three production files from the PC:

```powershell
scp .env.prod .env.prod.gateway .env.prod.agent tc@<server>:/opt/triage-copilot/
```

Then, on the server:

```sh
chmod 600 .env .env.prod .env.prod.gateway .env.prod.agent
git status --short
```

`git status` must list none of them. Keep the values the PC uses. `GATEWAY_API_KEY` and `APPROVER_API_KEY` must match Vercel's, and the database passwords reach Postgres from `.env.prod` when its volume is first made. In `.env.prod`, set `TC_TAG` to the tag you checked above.

### The server's .env

`.env` is the host's own file, which `make` reads. It is much shorter than the PC's, so write it fresh rather than copying it. Values cannot hold `#` or `$`.

```sh
# Production's port for watch, and the two folders the containers mount.
PROD_GATEWAY_PORT=4202
FASTEMBED_CACHE_PATH=/srv/triage-copilot/fastembed
SOURCE_INDEX_DIR=/srv/triage-copilot/sources

# The base compose file asks for these four. Production overrides each, so any value does.
POSTGRES_PASSWORD=unused
GATEWAY_DB_PASSWORD=unused
AGENT_DB_PASSWORD=unused
GRAFANA_ADMIN_PASSWORD=unused

# watch. WATCH_HOST is the default bridge's address, which this prints:
#   docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'
# On Docker Engine a container cannot reach the host's 127.0.0.1.
WATCH_HOST=<the bridge's address>
WATCH_PORT=4240
WATCH_GATEWAY_URL=http://127.0.0.1:4202
WATCH_SOURCE_INDEX_DIR=/srv/triage-copilot/sources
WATCH_FA_TOKEN=<as on the PC>
WATCH_FA_HEALTH_URLS=<as on the PC>
WATCH_FA_REPO=/srv/apps/freight-audit
WATCH_FL_TOKEN=<as on the PC>
WATCH_FL_HEALTH_URLS=<as on the PC>
WATCH_FL_REPO=/srv/apps/filing-lens

# Backups and the outside monitor; filled in below.
BACKUP_DIR=/opt/triage-copilot/backups
BACKUP_KEEP=7
BACKUP_DEST=
BACKUP_PING_URL=
HEARTBEAT_URL=
```

Do not add `WATCH_STATE_DIR`. systemd sets it, and an empty line here would override that.

## The embedding model and the clones

```sh
make embed-model
```

This downloads the 523 MB model into `FASTEMBED_CACHE_PATH`, and the Python packages into `.venv`. Run it as `tc`: the agent reads the folder as another user, so its files must stay readable by others.

Clone each watched app as `tc`. watch ignores git's global config, so `safe.directory` cannot help a clone owned by someone else. A private repo needs a read-only deploy key.

```sh
git clone <FreightAudit's repo> /srv/apps/freight-audit
git clone <FilingLens's repo> /srv/apps/filing-lens
```

## The move

### On the PC

Stop writes first, so the dump is the last word. Everything here is in the repo's folder.

1. Stop watch: press Ctrl+C in the terminal that runs `make dev-watch`.
2. Stop what takes traffic, leaving Postgres up:

   ```powershell
   docker compose -p triagecopilot-prod stop tailscale gateway agent
   ```

3. Dump the database. Never start the server on a fresh database. Tenant ids come from the database, so a fresh one gets new ids, and `LIVE_TENANTS` and watch's tokens would no longer match:

   ```powershell
   make prod-backup
   ```

   It prints the file's name, `backups\triage-<stamp>.dump`.

4. Save the Tailscale node. This file is as secret as a password:

   ```powershell
   docker run --rm -v triagecopilot-prod_tailscale-state:/state:ro -v "${PWD}:/out" alpine:3.22 tar -C /state -czf /out/tailscale-state.tgz .
   ```

5. Copy both to the server:

   ```powershell
   ssh tc@<server> "mkdir -p -m 700 /opt/triage-copilot/backups"
   scp backups\triage-<stamp>.dump tc@<server>:/opt/triage-copilot/backups/
   scp tailscale-state.tgz tc@<server>:/tmp/
   Remove-Item tailscale-state.tgz
   ```

6. Stop production on the PC. Its volumes stay, and they are the way back:

   ```powershell
   make prod-down
   ```

### On the server

1. Put the Tailscale node in place. The node keeps its identity, so its Funnel URL does not change, and neither DNS nor Vercel's `GATEWAY_URL` needs touching:

   ```sh
   docker volume create --label com.docker.compose.project=triagecopilot-prod --label com.docker.compose.volume=tailscale-state triagecopilot-prod_tailscale-state
   docker run --rm -v triagecopilot-prod_tailscale-state:/state -v /tmp:/in:ro alpine:3.22 tar -C /state -xzf /in/tailscale-state.tgz
   rm /tmp/tailscale-state.tgz
   ```

   Never run the PC's sidecar and this one at once: one identity on two machines fights itself.

   The other way is a fresh node: remove the old device in Tailscale's admin console first, put a new one-off, tagged, pre-approved key in `TS_AUTHKEY`, and skip this step. The name is free again, so the URL stays. Moving the node is the better choice, since the way back then needs no console work.

2. Restore the database. Compose starts Postgres alone, which runs `postgres-init.sql` on the new volume, and the dump loads into a database of its own before it takes `triage`'s place. It prints the tenants and investigations it holds, so compare them with the PC:

   ```sh
   make prod-restore FILE=backups/triage-<stamp>.dump
   ```

   If `make prod-up` ran first by mistake, nothing is lost: stop the gateway and the agent as on the PC, then restore. The empty database stays as `triage_before_<stamp>`.

3. Start everything, in compose's order: migrations, which find nothing to do, then the runbooks, the gateway, the egress proxy, the agent, Funnel and alerting:

   ```sh
   make prod-up
   ```

4. Install the services, which starts watch (Services, below).
5. Run the checks (Checks, below).
6. Turn on the backup and the outside monitor (Backups and Outside monitor, below). The move is not finished until both report.

## Services

systemd runs what is not a container: watch, and three timers. The units assume `/opt/triage-copilot` and the user `tc`. As a user with sudo:

```sh
cd /opt/triage-copilot
sudo install -m 644 infra/systemd/tc-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tc-watch.service tc-backup.timer tc-heartbeat.timer tc-fetch.timer
```

| Unit | Does |
| --- | --- |
| `tc-watch.service` | watch, restarted whenever it exits. Its firing alerts stay in `/var/lib/tc-watch`, so a restart neither fires them again nor leaves them open |
| `tc-backup.timer` | `make prod-backup` at 03:30 UTC, or at the next boot if the server was off then |
| `tc-heartbeat.timer` | `make prod-heartbeat` every 5 minutes |
| `tc-fetch.timer` | `make prod-fetch` every 10 minutes: `git fetch` of each watched app's origin/main, which watch never fetches |

Containers restart by themselves (`restart: unless-stopped`), so Docker brings them back after a reboot. The one-shot migrations do not rerun at boot and need not.

- Logs: `journalctl -u tc-watch -f`.
- Status: `systemctl status tc-watch`, and `systemctl list-timers 'tc-*'`.
- A changed unit: repeat the `install` and `daemon-reload` lines.

With cron instead of the timers, run `crontab -e` as `tc`. cron's short `PATH` leaves out `uv`, so set it. watch still needs its service, since cron cannot restart it.

```cron
PATH=/usr/local/bin:/usr/bin:/bin
30 3 * * * cd /opt/triage-copilot && make prod-backup
*/5 * * * * cd /opt/triage-copilot && make prod-heartbeat
*/10 * * * * cd /opt/triage-copilot && make prod-fetch
```

## Checks

Run these after `make prod-up`, with watch started.

1. `docker compose -p triagecopilot-prod ps`: only the egress proxy publishes a port, the one line with `->`, `127.0.0.1:4202->4202/tcp`. The rest list only what their images expose. The egress proxy, the gateway, the agent, Postgres, Alertmanager and Prometheus are `(healthy)`.
2. `docker compose -p triagecopilot-prod exec tailscale tailscale funnel status`: Funnel is on, and `https://triage-copilot.<tailnet>.ts.net` proxies `/` to `http://gateway:4200` alone. Allow a minute for the sidecar to pick a relay.
3. From a device not on the tailnet: `curl -s -o /dev/null -w "%{http_code}\n" https://triage-copilot.<tailnet>.ts.net/v1/investigations` prints 401.
4. The tenants came across. These ids must be the keys of `LIVE_TENANTS` in `.env.prod.gateway` and `.env.prod.agent`:

   ```sh
   docker exec triagecopilot-prod-postgres-1 psql -U postgres -d triage -c "SELECT id, name FROM gateway.tenants"
   ```

5. The web: sign in on Vercel and open a report.
6. The kill switches came across: `make prod-switch`.
7. watch: `curl -s http://$(grep ^WATCH_HOST= .env | cut -d= -f2):4240/readyz` prints `{"status":"ok"}`, and `journalctl -u tc-watch --since "5 minutes ago"` shows polls and no `alert not posted`.
8. The proxy reaches watch, and Prometheus reads every target. Each line ends in 1:

   ```sh
   docker exec triagecopilot-prod-prometheus-1 wget -qO- "http://127.0.0.1:9090/api/v1/query?query=probe_success"
   docker exec triagecopilot-prod-prometheus-1 wget -qO- "http://127.0.0.1:9090/api/v1/query?query=up"
   ```

   If watch's probe stays 0, the firewall rule or `WATCH_HOST` is wrong (Firewall, above).

9. This machine's ports are closed to the agent, so every result is non-zero:

   ```sh
   docker exec triagecopilot-prod-agent-1 python -c "import socket; socket.setdefaulttimeout(2); [print(h, p, socket.socket().connect_ex((h, p))) for h in ('<the bridge address>', '10.231.233.1') for p in (22, 4200, 4201, 4240, 5434)]"
   ```

10. One test alert to both channels (`docs/guide.md`, Alerting, step 5).
11. Reboot once with `sudo reboot`. Everything returns without a command: step 1, and `systemctl status tc-watch`.

`docs/guide.md` still gives its checks for PowerShell. On the server, use `grep` for `Select-String`, `curl` for `curl.exe`, and the bridge's address for 192.168.65.254.

## Backups

`make prod-backup` dumps `triage` with `pg_dump -Fc`, which compresses, and reads the dump back in full before it counts. The newest `BACKUP_KEEP` (7) stay in `BACKUP_DIR`, readable by `tc` alone. A dump holds every report and the tenants' token hashes. It is not encrypted, so treat a copy as you treat the database.

`BACKUP_DEST` is empty by default, which keeps the dumps on this disk. The move is not done until it names somewhere else. A failed copy keeps the dump, fails the job, and sends no ping. A failed ping fails the job too, though the dump and its copy are safe by then.

**A bucket.** Any S3-compatible service works. In `.env`:

```sh
BACKUP_DEST=s3://<bucket>/triage-copilot
RCLONE_S3_PROVIDER=Other
RCLONE_S3_ENDPOINT=https://<the service's S3 endpoint>
RCLONE_S3_REGION=<only where the service asks for one>
RCLONE_S3_ACCESS_KEY_ID=<key>
RCLONE_S3_SECRET_ACCESS_KEY=<secret>
```

The copy runs from rclone's pinned image, so the server needs no rclone. Give the key write access to this bucket alone, so a compromised server cannot delete its own backups. Then have the bucket expire old objects with a lifecycle rule of about 30 days, since the script deletes nothing there. Use another provider than the server's if you can: on Oracle, its 20 GB of free object storage shares an account with the instance.

**An rsync target.** In `.env`:

```sh
BACKUP_DEST=<user>@<host>:/backups/triage-copilot
```

This runs over ssh with no prompt, so `tc` needs a key with no passphrase that the target accepts, and the target's host key in `known_hosts`. Connect once by hand as `tc`. The target must have rsync too.

**Run one now:** `make prod-backup`, then check that the file arrived.

**Restore a backup.** Stop what writes, restore, and start again. The database it replaces stays as `triage_before_<stamp>`:

```sh
docker compose -p triagecopilot-prod stop tailscale gateway agent
make prod-restore FILE=backups/triage-<stamp>.dump
make prod-up
```

A write-only key cannot read the bucket, so download an older dump through the provider's console. Once you are sure of the restore, drop the old database:

```sh
docker exec triagecopilot-prod-postgres-1 psql -U postgres -c 'DROP DATABASE "triage_before_<stamp>"'
```

**A restore drill**, safe beside production. It loads the newest dump into a throwaway Postgres and prints what it holds. Do it monthly:

```sh
docker run -d --name tc-drill-postgres-1 -e POSTGRES_DB=triage -e POSTGRES_PASSWORD=drill -e GATEWAY_DB_PASSWORD=drill -e AGENT_DB_PASSWORD=drill --mount type=bind,source=$PWD/infra/postgres-init.sql,target=/docker-entrypoint-initdb.d/init.sql,readonly pgvector/pgvector:0.8.7-pg18-trixie
sleep 15
uv run --no-project python scripts/server.py --project tc-drill restore "$(ls -t backups/triage-*.dump | head -1)"
docker rm -f -v tc-drill-postgres-1
```

## Outside monitor

Prometheus and Alertmanager run on this server, so they cannot say that it is gone. Something outside must notice the silence. [Healthchecks.io](https://healthchecks.io) does this: its free Hobbyist plan monitors 20 jobs, and Discord is one of its integrations. The server pings it, and when the pings stop it alerts Discord.

1. Sign up, and add the Discord integration under Integrations, pointing it at your alert channel.
2. Make a check named `tc-server` with a period of 5 minutes and a grace time of 5 minutes. Put its ping URL (`https://hc-ping.com/<uuid>`) in `.env` as `HEARTBEAT_URL`.
3. Make a check named `tc-backup` with a period of 1 day and a grace time of 2 hours. Put its ping URL in `.env` as `BACKUP_PING_URL`.
4. Test it: `sudo systemctl stop tc-heartbeat.timer`. Within about 10 minutes Discord says `tc-server` is down. Run `sudo systemctl start tc-heartbeat.timer`, and Discord says it is up again.

`make prod-heartbeat` pings only while the gateway answers `/readyz` on `127.0.0.1:4202`. That takes the server, Docker, the egress proxy, the gateway and Postgres, so the check also goes down when any of them does. It does not see Funnel. Anyone with a ping URL can send pings, so keep both out of the repo. The job runs on the host, so nothing is added to the egress proxy's allowlist.

Pause both checks in Healthchecks before a planned stop, or Discord will tell you about it.

## Roll back to the PC

The PC's volumes hold production as it was at the move. Keep them for a week, and at any point until then:

1. On the server, stop everything that acts. If the server took real alerts, also take its data:

   ```sh
   sudo systemctl stop tc-watch tc-heartbeat.timer tc-backup.timer tc-fetch.timer
   docker compose -p triagecopilot-prod stop tailscale gateway agent
   make prod-backup
   make prod-down
   ```

2. Pause the two checks in Healthchecks.
3. On the PC, if the server took real alerts, copy its newest dump back and restore it. The PC's own database stays as `triage_before_<stamp>`:

   ```powershell
   scp tc@<server>:/opt/triage-copilot/backups/triage-<stamp>.dump backups\
   make prod-restore FILE=backups\triage-<stamp>.dump
   ```

4. Start production on the PC, then watch:

   ```powershell
   make prod-up
   make dev-watch
   ```

The PC's `tailscale-state` volume was never changed, so its sidecar returns as the same node and Funnel URL. The server's sidecar must be stopped first, which step 1 does. If you took the fresh-node way, remove the server's device in the admin console and put a new key in `.env.prod` on the PC first.

## After the move

- A week later: drop `triage_before_<stamp>` on the server. On the PC, remove production's volumes only when you are sure.
- A deploy: on the server, `git pull`, `git checkout` the new commit, set `TC_TAG` in `.env.prod`, then `make prod-up`. If the commit changed a mounted config, restart that container (`docs/guide.md`, Alerting). Then remove the old tag's three images, since nothing prunes them: `docker image rm <the image>:sha-<old>`.

## What this was tested on

Rehearsed on 2026-11-06 in a throwaway Ubuntu 24.04 amd64 container with systemd as PID 1, Docker Engine 29.9.3, Compose 5.7.0 and uv 0.13.7, with every value fake and no internet once the images were pulled. These ran from this page's own text:

- The install section, `make embed-model`, and anonymous pulls from GHCR.
- `make prod-up`: every container up, every bind mount readable by its container's non-root user.
- The Tailscale volume steps, and a restore onto a brand-new volume: tenant ids identical, and the migrations found nothing to do.
- The systemd units. watch, killed twice with `SIGKILL`, came back each time, and no alert fired twice.
- The ufw rule both ways: watch's probe failed without it and passed with it.
- A copy to a real S3 server on the same machine, an rsync to a local folder, and the restore drill.
- A full restart of systemd and Docker together: every long-running container returned with no command, the probes passed, and the firewall rule held.
- `make prod-down`, then `make prod-up` on the kept volumes.

All five images built for arm64 under emulation, and the agent's native libraries loaded on it. CI builds them on real arm64 runners.

Not rehearsed, so watch these on the day:

- Real arm64 hardware, and memory under real load.
- Tailscale and Funnel: a real login, and the moved node reconnecting. The rehearsal had no internet.
- Discord, Gmail, a provider's real bucket, rsync over ssh, and Healthchecks.io.
- Oracle's own `iptables` rules. The commands above come from memory of its images, so check them with `sudo iptables -L INPUT -n`.
- A clone from GitHub, since the rehearsed tree was copied in from a branch not yet pushed.
