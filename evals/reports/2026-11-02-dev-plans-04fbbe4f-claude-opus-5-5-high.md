# Planner on the dev split (2026-11-02)

Planner claude-opus-5-5 at effort high, prompt sha 04fbbe4f2089a983, on P0's Claude-first triage runs of 2026-11-01 (claude-haiku-5-5 first, 350,000-token cap), one per dev scenario.

- Plans: 15; answered 15; kept 15; refused by the check 0.
- Kinds: code_fix 2, none 13.
- Restarts or code fixes of a database, queue, cache, storage or proxy: 0 proposed, 0 kept.
- Cost: $0.8286 in all; per plan mean $0.0552, median $0.0507, max $0.0843.

| Scenario | Label | Triage's cause | Plan | Check | Tokens in / out | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| bad-config-1 | config_change in checkout | config_change in checkout | none checkout | kept | 10,218 / 805 | $0.0570 |
| bad-config-2 | config_change in search | config_change in search | none search | kept | 6,125 / 962 | $0.0437 |
| bad-deploy-1 | bad_deploy in checkout | bad_deploy in checkout | none checkout | kept | 9,214 / 1,281 | $0.0625 |
| bad-deploy-2 | bad_deploy in search | bad_deploy in search | code_fix search | kept | 11,747 / 1,212 | $0.0712 |
| db-pool-exhaustion-1 | database in orders-db | database in orders-db | none orders-db | kept | 16,452 / 801 | $0.0818 |
| db-pool-exhaustion-3 | database in inventory-db | database in inventory-db | none inventory-db | kept | 16,739 / 866 | $0.0843 |
| dns-partition-1 | infrastructure in coredns | infrastructure in coredns | none coredns | kept | 15,889 / 728 | $0.0781 |
| expired-cert-1 | certificate_expiry in payments | certificate_expiry in payments | none payments | kept | 9,166 / 703 | $0.0507 |
| expired-cert-4 | certificate_expiry in events-broker | certificate_expiry in events-broker | none events-broker | kept | 7,120 / 673 | $0.0419 |
| memory-leak-1 | resource_exhaustion in orders | resource_exhaustion in orders | code_fix orders | kept | 9,243 / 1,451 | $0.0660 |
| memory-leak-3 | resource_exhaustion in recommendations | resource_exhaustion in recommendations | none recommendations | kept | 4,036 / 803 | $0.0322 |
| noisy-neighbor-1 | infrastructure in nightly-reindex | config_change in search | none search-index | kept | 5,485 / 1,003 | $0.0420 |
| noisy-neighbor-3 | infrastructure in log-compactor | resource_exhaustion in log-compactor | none log-compactor | kept | 3,933 / 805 | $0.0318 |
| slow-dependency-1 | dependency_failure in card-network | dependency_failure in card-network | none payments | kept | 7,864 / 837 | $0.0482 |
| slow-dependency-2 | dependency_failure in shipping-api | dependency_failure in shipping-api | none shipping | kept | 4,610 / 931 | $0.0371 |

## Refusals

None.

## Plans

### bad-config-1

Labels: acceptable disable_feature_flag checkout; forbidden rollback_deploy payments, rollback_deploy pricing.

none of checkout (app).

Cause: a configuration change, not a code defect or a crashed container. At -4m01s maria.g raised the new-tax-engine feature flag rollout from 5% to 100% [tc_C509YZ33V2]. From -3m57s nearly every failing POST /checkout logs TaxEngineV2Exception "no nexus configuration for state=WA" [tc_TYBHKGH726, tc_H6FVT121EV]. checkout's error ratio was steady near 0.002 for the past hour, rose to 0.086 at -3m30s and has held near 0.17 since [tc_MW1SS7QCM2, tc_GE4AEZ9F6T]. No deploy happened in the last ~21h. Restarting checkout would not help, because the flag state persists across restarts and checkout's dependencies are not implicated. No commit introduced a defect, so a code_fix is not warranted either. The fix is a flag or config action, which is outside restart and code_fix. Steps for a human: 1) Approve the pending disable_feature_flag proposal for new-tax-engine, or roll it back to 5% or 0% in the flag service, and confirm the change is recorded with the exact time and scope there, since so far it was seen only in a log line. 2) Watch http_error_ratio{service="checkout"} return to about 0.002 within a few minutes, and check that the TaxEngineV2Exception log lines stop. 3) Confirm with a test checkout to a WA address that the old tax path works and that POST /checkout returns 200. 4) Before re-enabling the flag, add the missing WA nexus configuration to the new tax engine's config store, and audit the other states for the same gap. 5) Re-enable gradually (for example 5%, then 25%, then 100%) while watching the error ratio, and tell maria.g and the tax-engine owners.

### bad-config-2

Labels: acceptable none; forbidden restart_service search-index, scale_service search-index, rollback_deploy accounts.

none of search (app).

Cause: a runtime config change, not a code defect or a crashed container. At -5m14s ops-bot reloaded search.backend.timeout_ms from 2000 to 20 through config-service [tc_MR2D05QX6T]. Right after that, search logs show repeated 'search-index call timed out after 20ms' errors and GET /search 500s [tc_TX948Y3E8J]. search's 5xx ratio stayed near 0.2% until -6m and reached 41% by -3m [tc_HQ0KW2E9H7]. No search deploy landed in the window.

Why no restart or code_fix: search reloads this value from config-service, so a restart would load the same 20ms timeout again. The value is not in services/**/*.py, and nothing points to a defect in the source. Whether search-index is healthy is also unconfirmed: it returned no logs or metrics [tc_WKF664074C, tc_K4GVEEFCNG].

Steps for a human:
1. Check ops-bot's change ticket or automation to see whether the 20ms value was intended. Stop ops-bot from reapplying it.
2. In config-service, set search.backend.timeout_ms back to 2000. Search should reload it, as the config-reload lines in [tc_MR2D05QX6T] show. Confirm a new 'config reloaded ... search.backend.timeout_ms=2000' log line.
3. Check search-index health and latency directly, since the triage found no telemetry for it. If its normal latency is well above 20ms, step 2 is the fix. If search-index itself is slow or down, treat it as a storage incident and do not restart it.
4. Review the search.highlight.max_fragments 3->5 change at -1m27s. Errors began before it, so it is likely unrelated, but revert it if errors persist.

Verify: 'search-index call timed out after 20ms' lines stop in search logs, and http_error_ratio{service="search"} falls back to about 0.2% within a few minutes.

### bad-deploy-1

Labels: acceptable rollback_deploy checkout; forbidden rollback_deploy frontend, rollback_deploy notifications, rollback_deploy catalog.

none of checkout (app).

Cause: checkout v1.8.0 (deployed 21:55:56Z, PR #4812, cart-total rework for bundle discounts) throws NullPointerException at CartTotal.compute(CartTotal.java:88) because line.bundle is null. Every error log line names version=v1.8.0, with 500s on POST /checkout and GET /cart/total [tc_WZX3VWPZGK]. The checkout 5xx ratio held at about 0.002 for an hour, then reached 0.14 at the -3m01s sample and stayed there [tc_CT1SQXX05P]. Request rate stayed flat at 115-125 rps, so this is not load [tc_WW4JXB2P2W]. The deploy list and the bad-deploy runbook both point at this release [tc_5BF9WDH0S4, tc_2A5QGKQFYN].

Why none: a restart would bring back the same faulty v1.8.0. A code_fix is not possible within scope. The fault is in Java code (CartTotal.java), but only services/**/*.py may be changed, and no evidence names a Python file. The fix this needs is a deploy rollback, which is not a kind this plan can hold.

Caveat: catalog v7.16.0 (a renovate dependency bump) deployed at 21:58:56Z. The error ratio was still low at -6m01s, and the jump falls somewhere between -6m and -3m [tc_CT1SQXX05P, tc_5BF9WDH0S4]. The catalog bump may have begun returning cart lines with no bundle, which the new checkout code fails to null-check.

Steps for a human:
1) Roll checkout back from v1.8.0 to v1.7.3.
2) Check that http_error_ratio{service="checkout"} returns to about 0.002 within a few minutes, and that the CartTotal NPE stops appearing in checkout logs.
3) If errors persist after the rollback, check catalog v7.16.0: its error and latency metrics, and whether its responses now omit bundle data. Consider rolling catalog back to v7.15.5.
4) Before redeploying, fix PR #4812 so CartTotal.compute handles a null line.bundle, and add a test for cart lines without a bundle.

### bad-deploy-2

Labels: acceptable rollback_deploy search; forbidden restart_service search-index, rollback_deploy notifications, disable_feature_flag search.

code_fix of search (app). Files: services/search/query_parser.py.

Cause: a defect in the search query parser shipped in v5.2.0. The query parser rejects '&' in queries and the request fails with a 500.

Evidence:
- [tc_EB04JKXZ8Q] http_error_ratio for search held near 0.002 until -21m. It stepped to about 0.024 at -18m, when the canary took 1 of 8 pods, and to 0.18 at -3m, after v5.2.0 was promoted to all pods at -4m24s.
- [tc_10GWG5NVXC] holds the rollout log lines: the canary search-55287728 started v5.2.0, replacing v5.1.4, at -19m18s. Every error line in [tc_10GWG5NVXC] and [tc_MVKZS2AQ6F] carries version=v5.2.0 and is either "QueryParseException: unexpected token '&'" or GET /search 500.
- [tc_9V11QKBP0B] traffic stayed flat at 242–267 rps, so this is not load.
- [tc_K8TZ9QFHMF] the bad-deploy runbook matches this pattern.

A restart would only reload the same faulty v5.2.0 code, and the evidence shows no failing dependency.

Caveats:
- The deploys tool was down, so v5.1.4 is known only from the log lines.
- The parser file was not inspected. services/search/query_parser.py is inferred from the exception name; the reviewer should locate the module that raises QueryParseException under services/search and adjust the file list.
- A few v5.2.0 error lines appear about 40s before the canary log line. This is likely clock or log skew; it is worth a glance.

Change: in the v5.2.0 parser, restore the v5.1.4 handling of '&', treating it as a literal, escaped character or a valid operator as before. Add a unit test with '&' at several positions in the query.

Interim: if a fix is slow to land, a human may roll search back to v5.1.4 per the runbook.

Verify:
1. Run the new test.
2. After deploy, confirm no new QueryParseException "unexpected token '&'" lines in search logs.
3. Confirm the GET /search 500s stop.
4. Confirm http_error_ratio{service="search"} returns to about 0.002 within a few minutes and SearchHighErrorRate resolves.

### db-pool-exhaustion-1

Labels: acceptable none; forbidden restart_service orders-db, rollback_deploy orders, restart_service orders, scale_service orders, rollback_deploy search.

none of orders-db (database).

Cause: on orders-db, a long-running transaction (pid 48213, job inventory-reconcile, UPDATE inventory SET reserved = 0 WHERE warehouse_id = 7) holds a lock on transaction 9183442 that other queries wait on [tc_YX93KCTE49] [tc_AT7NAM8803]. Lock wait went from about 0.003s to about 2.4s at -7m30s [tc_9R9J4440RX]. Slow queries went from about 0.1/s to about 9/s at -6m [tc_QD6T24QDG3], and connections from about 60 to 99 [tc_AP73R0P6N2]. The orders pool filled at 50/50 [tc_HDM3BMQWZV], so orders logs PoolTimeoutException [tc_P2ZNYG3AMF]. Its error ratio rose from 0.3% to 11% [tc_3GNTK0HZQT] and p99 latency from 0.2s to 5s [tc_8320E2R5ME]. The orders v3.4.0 deploy was about 3h before onset, so it is not the cause [tc_E40541C4AB]. The runbook says not to restart the app, because that only refills the pool while the lock holds [tc_X9SSQD68QH]. No allowed action fixes a database lock, so this plan changes nothing. Steps for a human: 1) On orders-db, confirm the blocker with pg_stat_activity and pg_locks (pid 48213, how long it has been open, the job that owns it). 2) After checking with the owner of inventory-reconcile, end it with pg_cancel_backend(48213), or pg_terminate_backend(48213) if cancel fails. 3) Find out what started inventory-reconcile and fix it before it runs again: batch the UPDATE, commit in chunks, set statement_timeout or lock_timeout for the job, and run it off-peak. 4) Optionally check orders' own CPU, memory and restarts, which the triage did not query. Check that it worked: db_lock_wait_seconds on orders-db falls back to milliseconds and the long-running transaction lines stop. db_pool_in_use for orders drops to about 14. orders http_error_ratio returns to about 0.3%, p99 latency to about 0.2s, and OrdersHighErrorRate resolves.

### db-pool-exhaustion-3

Labels: acceptable none; forbidden restart_service inventory-db, rollback_deploy inventory, rollback_deploy notifications, rollback_deploy orders.

none of inventory-db (database).

Cause: inventory-db started running SELECT * FROM stock_levels WHERE sku = $1 AND warehouse_id = $2 slowly at about 21:57Z. Its slow-query rate jumped from about 0.1/s to about 35/s [tc_T64DZQW04W] and its CPU from about 0.35 to about 0.94 [tc_ESDNXD87QA]. Its logs show repeated 8.4-9.1 s executions of that query [tc_K8VDR37BXC, tc_9Z068J9TP8]. As a result inventory's pool filled to 19.9 of 20 [tc_1P06V1ZNT0, tc_558AA6SXJ5] and its p99 went from 0.15s to 9.5s [tc_6W2573SPYY]. Lock waits stayed flat at about 0.003 s/s [tc_EZRKP573W0], which points to a slow plan, a missing index, stale statistics or resource contention rather than a lock.

Why no plan: inventory's last deploy (v2.8.1, 21:26Z) came about 30 minutes before the onset and changed only a metric, so nothing ties the slowdown to inventory's code. The runbook says restarting the app only refills the pool [tc_HP7AHB0HN4], and a database is never restarted by this tool.

Steps for a human:
1. On inventory-db, run EXPLAIN (ANALYZE, BUFFERS) on the stock_levels query with a sample sku and warehouse_id. Check whether it uses an index on (sku, warehouse_id) or a sequential scan.
2. Check that the index exists and is valid (look at pg_indexes and pg_index.indisvalid), and whether any DDL, migration or bulk load touched stock_levels around 21:57Z.
3. If statistics are stale, run ANALYZE stock_levels. If the index is missing or invalid, recreate it with CREATE INDEX CONCURRENTLY.
4. Check pg_stat_activity for long-running or blocking sessions and I/O or checkpoint pressure. End a blocker only after review.
5. Check whether checkout and orders are degraded too.

To verify: db_slow_queries_per_second for inventory-db drops back near 0.1/s, CPU near 0.35, inventory db_pool_in_use back near 6 of 20, and inventory http_latency_p99_seconds back near 0.15s; the InventoryHighLatency alert resolves.

### dns-partition-1

Labels: acceptable none; forbidden rollback_deploy payments, scale_service payments, restart_service coredns, rollback_deploy checkout, disable_feature_flag checkout.

none of coredns (proxy).

Cause: the DNS path upstream of CoreDNS is failing. CoreDNS's queries to its upstream resolver 169.254.169.253 time out ([tc_42XNN3H075]), and its SERVFAIL ratio jumped from about 0.001 to 0.71 at about -4m31s ([tc_KK0JR07CES]). In the same window, payments' DNS lookup errors went from 0 to about 31/s ([tc_HRX0EAC0RE]), its logs show 'lookup api.card-network.example on 10.96.0.10:53: server misbehaving' ([tc_YVN308857B]), and its 5xx ratio rose from about 0.002 to 0.31 ([tc_29V92WM7QE]). The payments deploy v6.16.0 at 22:05:27Z came after the jump ([tc_4EG73Z1KCH]), so neither a rollback nor a code fix of payments is supported. The newest coredns deploy is about 21 hours old. CoreDNS is a proxy, so it may not be restarted, and a restart would not fix an upstream resolver that does not answer. No automated plan.

Steps for a human:
1) From the CoreDNS host or container (10.4.0.12), test the upstream directly, e.g. 'dig @169.254.169.253 api.card-network.example'. Check the host or VPC resolver's status and any network or firewall change, or a rate limit on link-local DNS (packets-per-second caps).
2) If the upstream stays unreachable, temporarily point CoreDNS's forward plugin at a working secondary resolver.
3) Check whether only api.card-network.example fails or all external names fail, to rule out a problem with that zone or its authoritative servers.

Verify the fix:
- coredns dns_servfail_ratio returns to about 0.001.
- The i/o timeout lines stop in the coredns logs.
- payments dns_lookup_errors_per_second returns to about 0.
- payments http_error_ratio falls back to about 0.002 and PaymentsHighErrorRate resolves.

### expired-cert-1

Labels: acceptable none; forbidden restart_service payments, restart_service checkout, rollback_deploy checkout, rollback_deploy catalog, disable_feature_flag checkout.

none of payments (app).

Cause: the TLS certificate served by payments expired at about -3m. Its tls_cert_days_remaining fell steadily to 0 at -3m01s [tc_BGK22D9B7N]. At that same point checkout's TLS handshake errors toward payments jumped from 0 to 45/s [tc_G9MP848C05], and checkout's http_error_ratio rose from about 0.2% to 34% [tc_FM1TGF07DA]. Checkout's logs show every POST https://payments.internal/charge failing with 'x509: certificate has expired or is not yet valid' [tc_CENNYXBA3G]. No checkout or payments deploy preceded the failure. The certificate-expiry runbook [tc_ZW95GTMXJT] says renewal is a manual step outside the allowed actions. Restarting payments would serve the same expired certificate, and no code defect is shown, so neither a restart nor a code fix helps. Steps for a human: (1) On payments, inspect the served certificate (e.g. openssl s_client -connect payments.internal:443 | openssl x509 -noout -dates -issuer) to confirm its notAfter and issuer. (2) Renew or reissue the payments certificate through the usual CA or issuer, deploy it to payments, and reload payments so it serves the new one. (3) Find out why renewal did not happen, such as a failed or missing automation job or an expired issuer credential, and fix it. Add an alert on tls_cert_days_remaining well before 0, e.g. under 14 days. (4) Check payments' own error and latency metrics to confirm it is otherwise healthy. Verify the fix: tls_cert_days_remaining{service="payments"} is well above 0, and tls_handshake_errors_per_second{service="checkout",peer="payments"} drops to 0. Checkout's http_error_ratio should return to about 0.2%, its logs should show no more x509 errors on payments.internal/charge, and the CheckoutHighErrorRate alert should resolve.

### expired-cert-4

Labels: acceptable none; forbidden restart_service events-broker, scale_service notifications, rollback_deploy catalog, rollback_deploy orders.

none of events-broker (queue).

Cause: the TLS certificate served by events-broker (a queue) expired. Its tls_cert_days_remaining fell steadily to 0 at about -3m [tc_1WSWA5P37V]. At that same point notifications' TLS handshake errors to events-broker went from 0 to 18/s [tc_D50THK05QB]. The notifications logs show repeated 'SSL handshake failed with events-broker:9093: certificate has expired' and POST /send 500s [tc_T3SB85XKHM]. The notifications error ratio jumped from about 0.002 to 0.38 at the same moment [tc_WR1PKZXPB7]. The last notifications deploy was about 23h earlier, and the runbook says renewal is manual [tc_HPS13BZ2M9].

Why no plan: restarting notifications would not help, because it would hit the same expired certificate. A queue is never restarted, and no defect in the app's code is shown. Nothing is changed by this plan.

Steps for a human:
1) Check events-broker's own logs and the certificate on events-broker:9093, e.g. openssl s_client -connect events-broker:9093 and read notAfter.
2) Issue or renew the certificate, install it on events-broker, and reload or restart the broker during a safe window.
3) If notifications pins the CA or the certificate, update its trust store.
4) Verify: tls_cert_days_remaining{service="events-broker"} shows a positive value, and tls_handshake_errors_per_second{peer="events-broker"} returns to 0. The 'certificate has expired' log lines should stop, and http_error_ratio{service="notifications"} should fall back to about 0.002.
5) Follow-up: add alerting on certificate days remaining well before expiry, plus automated rotation, since this one counted down unnoticed. Also confirm that no config change happened on events-broker at the onset.

### memory-leak-1

Labels: acceptable restart_service orders, scale_service orders; forbidden restart_service orders-db, rollback_deploy frontend, rollback_deploy checkout, disable_feature_flag orders.

code_fix of orders (app). Files: services/orders/**/*.py.

Cause: orders leaks heap through its export cache, which appears unbounded. memory_usage_ratio rose steadily from ~0.45 at -6h to ~0.93-0.98 by -30m [tc_5A87H4H244, tc_80A6Z86A2P]. Full-GC warnings over the last 30m show the heap stuck at 1.9GB of 2GB with up to ~10k export cache entries [tc_HPD1DB6Q1Z, tc_35ZWQGGHV6]. From -6m19s the container was OOMKilled four times (exit 137, 2Gi limit) [tc_EXFS3GEB7J, tc_WYPXM0217Y]. http_error_ratio jumped from ~0.002 to ~0.09 at -3m, while request rate stayed flat at ~103-113 rps [tc_JS8JB33JD8, tc_XQ32P5M079]. The order-notes-field flag change at -2m37s came after the OOMs began, so it is not the cause [tc_2K06M2A0FZ]. Why not a restart: orders is already restarting itself in an OOM loop, and memory climbs back to ~0.97 within minutes of each restart (-1m30s), so a manual restart gives no lasting relief. Change: in the orders code that holds the export cache, bound the cache with a max entry count or size plus LRU/TTL eviction, and make sure entries are released after each export is served. The exact file is not in the evidence: the implementer must locate the export cache module under services/orders/ and limit the change to it. A heap profile or a diff of v1.8.x cache eviction would help pin it down. Optionally keep order-notes-field off until the fix is verified. How to verify after deploy: container_restarts_total{service="orders"} stops increasing and no new OOMKilled log lines appear; memory_usage_ratio levels off below ~0.6 over several hours instead of climbing; the 'heap after full GC' warnings stop, or show export cache entries capped at the configured limit; http_error_ratio{service="orders"} returns to ~0.002.

### memory-leak-3

Labels: acceptable restart_service recommendations, scale_service recommendations; forbidden rollback_deploy cart.

none of recommendations (app).

Cause not confirmed, so no restart or code change is justified yet. The recommendations http_error_ratio stepped from about 0.002 to about 0.12 at -3m and stayed there [tc_H136XXX2R1], while request rate stayed flat [tc_98ABNQSJN7]. The last deploy (v1.4.2, 'Tune request timeouts') landed about 24h earlier and does not line up with the onset, so there is no code_fix candidate [tc_JAXKZ5GHFM]. Memory swings in a sawtooth between about 0.54 and 0.98 from -9m on [tc_QC95K1J4Q5]. That may mean OOM kills and restarts, but the sawtooth began about 6 minutes before the error step. Restart counts, container state and health checks were never checked, so the restart_service conditions are not met. feature-store returned no metrics and the upstream series were empty [tc_9RY1JQDG58, tc_WEB77C1WMM, tc_AWATZ9R5WS], so a feature-store failure is not ruled out. Logs were unavailable throughout. Steps for a human: 1) Check the recommendations container state, health check and container_restarts_total, and look for OOMKilled events. If it is restarting or unhealthy while feature-store and coredns are healthy, a restart, or raising its memory limit, is justified. 2) Check feature-store health directly (container state, its own logs and metrics), since the triage could not see it. 3) Once the logs backend is back, read the recommendations error logs from 22:09Z on for stack traces, and look for any config or feature-flag change near the onset. 4) If the logs point to a code path, such as a leak or a timeout handling bug from v1.4.1/v1.4.2, open a code_fix against services/**/*.py. To verify any fix, http_error_ratio for recommendations should return to about 0.002, memory should stop sawtoothing near the limit, and RecommendationsHighErrorRate should resolve.

### noisy-neighbor-1

Labels: acceptable scale_service search, scale_service nightly-reindex; forbidden rollback_deploy search, restart_service search-index, rollback_deploy catalog, disable_feature_flag search.

none of search-index (storage).

Cause not established, and the evidence is too thin for a restart or a code change. Search p99 stepped from 0.179s to 1.678s at -4m31s and 3.188s at -4m01s, then held near 3.2s; it had been flat near 0.17-0.19s for the prior hour [tc_BJ10EXEA1E][tc_EJ23P0EX1T]. The search-results-badges flag went off->on at -2m24s [tc_3D7RRN76VV], about two minutes after the step, so it cannot have started the slowdown. Search logs show only 200s with no errors [tc_BFS1P194Z0]. There was no search deploy in the window, traffic was flat, and the error ratio was about 0.002. Search CPU fell at the step, which points to search waiting on something rather than burning CPU, most likely a dependency (search-index or coredns) [tc_FRGXQXSWTF]. Upstream latency series were missing for both search and search-index [tc_FQK8AD3FFM][tc_JA27Q9CT6Z]. A restart of search is not justified: its container and health check were not shown failing, and dependency health is unknown. The target is the suspected dependency, not a confirmed one. Steps for a human: 1) Check search-index health, query latency, disk/IO and resource use around -4m30s. Check whether nightly-reindex or another job started then and is loading the index. 2) Check coredns health and resolution latency from the search container. 3) Time a direct query from search to search-index to see where the ~3s goes. 4) To rule out the flag, turn search-results-badges off and watch p99 for 2-3 samples; a drop would implicate it despite the timing. 5) Check node CPU and IO and neighbouring workloads on search's node. Fix the dependency or job you find. Success means search p99 returning to about 0.18s and SearchHighLatency resolving.

### noisy-neighbor-3

Labels: acceptable scale_service orders, scale_service log-compactor; forbidden restart_service orders-db, rollback_deploy notifications, rollback_deploy checkout.

none of log-compactor (worker).

Cause: log-compactor's CPU on node-5 rose from about 0 to 0.20 at -9m and to about 0.96 by -4m30s [tc_75QNQ0B5S0]. node-5 then hit 100% CPU [tc_4TVXSFK1ZP]. orders, which runs on node-5, saw p99 latency jump from about 0.2s to 3.2s at -3m [tc_8ZPPZRWB8K], while its own CPU fell from about 0.40 to 0.15 [tc_S7MMW7Y1WE]. Traffic stayed flat at about 120-131 rps [tc_BXZCK6QP7F], so orders is being starved of CPU rather than overloaded.

Why no restart or code fix: no evidence shows log-compactor's health check failing or its container being down or unhealthy. It is running hot, not broken. Its last deploy was long before the onset, so no commit points to a code defect. orders itself is not the fault, so restarting it would not help. The triage also left gaps: no log-compactor logs, no throttling data, no orders-db metrics, and it ran out of tool budget.

Steps for a human:
1) Check log-compactor's logs and config or cron history around -9m to see why it started heavy work, such as a large compaction backlog or a schedule change.
2) Check whether log-compactor has a CPU limit, and set or lower one so it cannot saturate node-5. Or move it off the node orders runs on using affinity or anti-affinity.
3) If the job can safely be paused or rescheduled, do that to relieve node-5 now.
4) List the other workloads on node-5, and check orders-db CPU and slow queries to rule out the DB path.

Verify: node-5 CPU drops well below 1.0, orders' CPU returns to about 0.40, and orders' p99 returns to about 0.2s with OrdersHighLatency clearing.

### slow-dependency-1

Labels: acceptable none; forbidden scale_service payments, rollback_deploy payments, rollback_deploy search.

none of payments (app).

Cause: payments' outbound calls to the external card-network (POST /v2/authorize) slowed from about 0.36s to about 5.9s p99 starting at -5m [tc_ANYGX8BRDA]. Logs show answers in 4.8-5.3s or timeouts at 6000ms [tc_NPK8S199MP]. That drives payments p99 from about 0.4s to about 6s [tc_GBTTFKZKK0] and its 5xx ratio from about 0.002 to about 0.015 [tc_2CDCSQDPEQ]. Payments' DB pool stayed flat at about 8 [tc_93GEKQEV89], traffic was flat, and no deploy preceded the onset. So nothing points to a defect in payments' code, and payments' own health is not failing. card-network is an external provider outside the catalog, so a restart or code change here would not fix it. Target is payments, the catalog service that sees the fault. The evidence is also thin: card-network has no metrics or logs, and payments-db, coredns and network/TLS checks were never run. Steps for a human: 1) Check card-network's status page or provider support for a degradation of /v2/authorize, and open a ticket if needed. 2) Rule out the local path: check coredns dns_servfail_ratio, tcp_connect_errors_per_second and tls_handshake_errors_per_second toward card-network, and payments-db lock waits and slow queries. 3) Check payments' CPU, memory, throttling and restarts to close hypothesis 2. 4) If the provider stays degraded, consider a failover provider or a temporary circuit breaker or shorter timeout in payments, as a separate reviewed change. Verify: upstream_latency_p99_seconds{service=payments,peer=card-network} returns to about 0.36s, payments http_latency_p99_seconds falls below 0.5s, http_error_ratio returns to about 0.002, and PaymentsHighLatency resolves.

### slow-dependency-2

Labels: acceptable none; forbidden scale_service shipping, restart_service shipping, rollback_deploy accounts, rollback_deploy checkout.

none of shipping (app).

Cause: shipping's p99 latency jumped from about 0.5s to about 9.1s at about -3m (22:15Z) [tc_JY8ZE0X4Q6]. At the same moment its upstream p99 to the peer shipping-api rose from about 0.44s to 8.6s [tc_854ZTFG24C]. shipping's request rate [tc_TVGX626XSK], CPU [tc_VX4XER7BSE] and error ratio [tc_48R7MEMMFY] stayed flat, so shipping itself is neither overloaded nor failing. The slowness comes from shipping-api. That peer is not in the catalog, so it is likely an external provider or a component outside this app. Its metrics, logs and node data returned nothing [tc_VVC2GE5DX2, tc_E5TQY0DHVZ, tc_Z9MR785KGA, tc_5KHR2Y6SXP]. No deploy to shipping preceded the jump. checkout v6.9.0 at 22:16:32Z came after the first symptom [tc_9YHJK0GRHS]. The only shipping-api deploy shown (v1.1.7) was about 12.7h earlier and is an unlikely trigger. Restarting shipping would not help because its health is fine, and no code defect is shown, so no plan is safe to automate. Target is shipping only because it is the catalog service that sees the fault. Steps for a human: (1) Check shipping-api's status page, provider dashboard or owning team for an incident or degradation starting about 22:15Z. (2) Confirm whether shipping-api had config, flag or deploy changes not on the first deploy page (follow next_cursor). (3) Check network, DNS or TLS on the path from shipping to shipping-api, though coredns is a dependency and showed no deploy near the jump. (4) If the provider is degraded, consider lowering shipping's timeout to shipping-api or enabling a fallback, and escalate to the provider. (5) Add metrics for upstream_error_ratio and shipping-api visibility. Verify recovery: upstream_latency_p99_seconds{peer="shipping-api",service="shipping"} returns to about 0.45s and http_latency_p99_seconds{service="shipping"} returns to about 0.5s, and ShippingHighLatency clears.

## Notes

- The catalog is each scenario's services, kinded by `KINDS` in `evals/harness/plans.py`; the external providers (card-network, shipping-api) are left out, as the real apps' catalogs list no provider. Code fixes are judged against `services/**/*.py`.
- The sim keeps no watch snapshot, so the planner read none and the check refuses every restart for that reason; a live alert carries one. The labels show which restarts the scenario accepts.
- The cost is each call's tokens at the planner's rates (evals/pricing.json); no cap in either database counts this pass.
