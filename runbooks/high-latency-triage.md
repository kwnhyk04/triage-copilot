# High latency triage

## Symptoms

- `http_latency_p99_seconds` rises for a service while its error ratio stays low.

## Checks

1. Check the request rate: a traffic surge raises latency everywhere at once.
2. Check CPU and memory of the service; saturation points at resource exhaustion.
3. Check the services it depends on: a slow dependency shows its own latency first.
4. Check cache hit ratios: a drop sends more work to the database.

## Remediation

Scale the service with `scale_service` only when CPU or memory saturate under normal traffic. For a slow dependency, investigate the dependency instead.
