# Database connection pool exhaustion

## Symptoms

- `db_pool_in_use` reaches `db_pool_max` for a service, and requests fail with "timeout acquiring connection from pool".
- The service's error ratio and latency rise while its request rate stays normal.
- On the database, `db_lock_wait_seconds` or `db_slow_queries_per_second` climb: connections stay checked out because their queries wait.

## Checks

1. Compare `db_pool_in_use` with `db_pool_max` for the alerting service.
2. Look at the database service's lock waits and slow queries, and its logs for lock wait timeouts or long-running statements.
3. Check whether a recent deploy of the service could leak connections; a deploy hours before the pool filled is not the cause.

## Remediation

When a long-running query holds a lock, the root cause is the database: find the blocking statement and let a human end it. Restarting or scaling the application only refills the pool while the lock holds, so do not propose it as the fix.
