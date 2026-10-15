"""Metric names every scenario draws from; the agent's system prompt lists the same names."""

CATALOG: dict[str, str] = {
    "http_requests_per_second": "Requests the service serves per second.",
    "http_error_ratio": "Share of the service's responses that are 5xx, 0 to 1.",
    "http_latency_p99_seconds": "99th percentile request latency of the service, in seconds.",
    "cpu_usage_ratio": "CPU used over CPU allocated, 0 to 1.",
    "memory_usage_ratio": "Memory used over the memory limit, 0 to 1.",
    "db_pool_in_use": "Connections in use from the service's client database connection pool.",
    "db_pool_max": "Size of the service's client database connection pool.",
    "db_connections": "Server-side connections open on a database service.",
    "db_lock_wait_seconds": "Seconds per second that queries on a database service wait for locks.",
    "db_slow_queries_per_second": "Queries over one second, per second, on a database service.",
    "cache_hit_ratio": "Share of the service's cache lookups that hit, 0 to 1.",
}
