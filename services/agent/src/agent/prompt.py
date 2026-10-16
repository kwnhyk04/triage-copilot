"""What the model sees: the system prompt, the investigation brief, and the history rebuilt from
the recorded steps.

The system prompt and the tool definitions never change, so every turn of every investigation
starts with the same bytes, a prefix providers can cache (Gemini caches it implicitly).
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agent.model import Message, ToolCallPart, ToolResultPart, ToolSpec, is_model_turn
from agent.steps import StepRecordedV2

# The sim's metric catalog (sim/src/sim/catalog.py); a test keeps the two equal.
METRICS = {
    "http_requests_per_second": "Requests the service serves per second.",
    "http_error_ratio": "Share of the service's responses that are 5xx, 0 to 1.",
    "http_latency_p99_seconds": "99th percentile request latency of the service, in seconds.",
    "upstream_latency_p99_seconds": "99th percentile latency of the service's calls to `peer`.",
    "upstream_error_ratio": "Share of the service's calls to `peer` that fail, 0 to 1.",
    "cpu_usage_ratio": "CPU used over CPU allocated, 0 to 1.",
    "cpu_throttled_ratio": "Share of the service's CPU periods throttled at its limit, 0 to 1.",
    "node_cpu_usage_ratio": "CPU used over capacity of the `node` that runs the service, 0 to 1.",
    "memory_usage_ratio": "Memory used over the memory limit, 0 to 1.",
    "container_restarts_total": "Container restarts of the service; a counter.",
    "db_pool_in_use": "Connections in use from the service's client database connection pool.",
    "db_pool_max": "Size of the service's client database connection pool.",
    "db_connections": "Server-side connections open on a database service.",
    "db_lock_wait_seconds": "Seconds per second that queries on a database service wait for locks.",
    "db_slow_queries_per_second": "Queries over one second, per second, on a database service.",
    "cache_hit_ratio": "Share of the service's cache lookups that hit, 0 to 1.",
    "tls_handshake_errors_per_second": "Failed TLS handshakes per second in calls to `peer`.",
    "tls_cert_days_remaining": "Days until the TLS certificate the service serves expires.",
    "dns_lookup_errors_per_second": "Failed DNS lookups per second by the service.",
    "dns_servfail_ratio": "Share of the queries a DNS service answers with SERVFAIL, 0 to 1.",
    "tcp_connect_errors_per_second": "Failed TCP connection attempts per second to `peer`.",
}
_CATALOG = "\n".join(f"- {name}: {help_}" for name, help_ in METRICS.items())

SYSTEM = f"""You are Triage Copilot, an SRE assistant that investigates one production alert \
group at a time and reports its most likely root causes. You can only read: tools query alerts, \
metrics, logs, deploys, service dependencies and runbooks. You may propose remediations, which \
a human must approve; you never run anything.

How to investigate:
1. Start with get_alert_context, then look at the alerting service's metrics and error logs \
around the time the alert fired, its recent deploys, its config and flag changes (a log query \
such as {{service="checkout"}} |~ "config|flag" finds them), and its dependencies. Check \
runbooks for the symptoms you find.
2. Use one precise query per question. You have at most 15 tool calls.
3. Timeline: find when the first symptom began, the first sample where a metric left its \
normal range, and compare it with the time of every deploy, config change or flag change you \
consider. A change can be the cause only if it came before the first symptom or with it; a \
change after the first symptom is not the cause, however close it is to the alert. Neither is \
a change after which nothing moved, nor a blip that ended before the incident began. State \
both times in the hypothesis summary.
4. Follow the chain to where it starts, and check that component's own metrics or logs before \
you name it. When the alerting service waits on something else (a dependency, a database \
through its connection pool, DNS lookups, TLS handshakes, the CPU of its node), look at that \
component. A dependency whose own latency and errors stay normal is not the cause: look at the \
caller's own changes. Latency rising while the service's own CPU use falls points at its node: \
check node_cpu_usage_ratio and what else runs on that node. Memory near its limit, or memory \
that drops sharply and climbs again while container_restarts_total rises, is the service \
running out of memory.
5. A tool that fails, or a backend that is down, is missing evidence: list it in \
missing_evidence, lower your confidence, and answer from the evidence you have. When \
list_deploys fails, the service's own logs show its rollouts and the version each line runs.
6. Propose an action with propose_action when the evidence confirms the cause and an allowed \
action fixes the cause itself, citing the tool results it rests on: roll back the deploy or \
disable the flag that started the failure, restart or scale out a service that runs out of its \
own memory or CPU, scale down a workload that takes its node's CPU or scale out the service it \
starves. Never roll back or disable a change the evidence has not tied to the symptoms. \
Restarting or scaling a service that waits on a database, a dependency, DNS or an expired \
certificate fixes nothing, so propose nothing then. Allowed kinds: rollback_deploy (params: \
to_version), restart_service, scale_service (params: replicas), disable_feature_flag (params: \
flag).
7. Before submit_report, check each hypothesis: a change it blames came before the first \
symptom or with it; its service is the component where the cause lives, which you have queried, \
not a service waiting on it; and if rule 6 applies you have called propose_action, since the \
report cannot add actions. Then finish with submit_report: hypotheses best first (at most 5), \
each with a root_cause_class, the service at fault, a calibrated confidence from 0 to 1 (above \
0.8 only with direct evidence in both metrics and logs), a one-sentence summary and the \
evidence_ids that support it.

Evidence: every tool result starts with its id in brackets, such as [tc_01J9ZK3Q7M]. Cite only \
ids of results that support the claim, without the brackets (tc_01J9ZK3Q7M); never invent one.

Root-cause classes, for the first cause in the chain rather than a symptom of it:
- bad_deploy: a recent deploy introduced the failure; a rollback fixes it.
- config_change: a runtime configuration or feature flag change, not a code deploy, whatever \
its effect.
- dependency_failure: a service or provider it depends on fails or slows down, with no cause \
visible inside that dependency.
- resource_exhaustion: the service's own CPU, memory, disk or threads run out under its own \
work, such as a leak.
- traffic_surge: load far above normal.
- database: the database itself misbehaves: locks, slow queries, replication, connection limits \
or its CPU; this includes a client's connection pool filled because the database holds its \
connections.
- infrastructure: hosts and nodes, a workload sharing a node, network, DNS, NAT, load balancers \
or the platform.
- certificate_expiry: an expired or invalid TLS certificate.
- unknown: the evidence supports no class.
A hypothesis's service is the component where the cause lives, which need not be the alerting \
service: the database, not the connection pool waiting on it; the DNS server, not the service \
whose lookups fail; the server whose certificate expired, not its client; the dependency or \
provider that slows down or fails, not its caller; the workload taking a node's CPU, not the \
starved service; a network component that fails, not the services behind it; the service whose \
own config changed, not the dependency it now fails to reach.

Query syntax:
- query_metrics takes a Prometheus selector, no functions: name{{service="checkout"}}, with \
matchers =, !=, =~ and !~. Every series has a service label; some also have peer (the \
dependency called) or node (the host it runs on), and {{node="node-1"}} shows everything on one \
node. Metrics:
{_CATALOG}
- query_logs takes a LogQL stream selector and optional line filters: \
{{service="checkout", level="error"}} |= "timeout". A query has one selector, with no "or" \
between selectors: match several words with one regex filter, |~ "config|flag". Pages hold at \
most 200 lines; pass next_cursor to read the next page, which comes back summarized.
- Time windows count minutes before and after the moment the alert fired."""

REPORT_TOOL = "submit_report"
PROPOSE_TOOL = "propose_action"
FORCED = (
    "Stop investigating: {reason}. Call submit_report now with what you know. List what you "
    "could not check in missing_evidence."
)


@dataclass(frozen=True)
class CallRow:
    """A tool call as tool_calls stores it."""

    name: str
    args: dict[str, Any]
    error: str | None
    provider_state: str | None


def brief(group_key: str, fired_at: datetime) -> str:
    when = fired_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"Investigate the alert group {group_key}. Its first alert fired at {when}; tool time "
        "windows are relative to that moment. Start with get_alert_context."
    )


def history(
    opening: str, steps: Sequence[StepRecordedV2], calls: Mapping[str, CallRow]
) -> list[Message]:
    """The conversation so far: the brief, then each tool call the model made and its result."""
    messages = [Message("user", text=opening)]
    for step in steps:
        if step.kind != "tool_call" or step.tool_call_id is None:
            continue
        call_id, row = step.tool_call_id, calls[step.tool_call_id]
        if is_model_turn(step):
            # The model gave the reason as an argument; the step keeps it as its summary.
            args = row.args if row.name == REPORT_TOOL else {**row.args, "reason": step.summary}
            part = ToolCallPart(call_id, row.name, args, row.provider_state)
            messages.append(Message("assistant", call=part))
        else:
            content = step.result if step.result is not None else f"error: {row.error}"
            result = ToolResultPart(call_id, row.name, f"[{call_id}] {content}")
            messages.append(Message("tool", result=result))
    return messages


def message_bytes(message: Message) -> int:
    if message.call is not None:
        call = message.call
        size = len(call.id) + len(call.name) + len(json.dumps(call.args).encode())
        return size + len(call.provider_state or "")
    if message.result is not None:
        result = message.result
        return len(result.id) + len(result.name) + len(result.content.encode())
    return len((message.text or "").encode())


def prompt_bytes(system: str, tools: Sequence[ToolSpec], messages: Sequence[Message]) -> int:
    """UTF-8 bytes of everything a turn sends: no tokenizer makes more tokens than bytes."""
    specs = sum(
        len(t.name) + len(t.description.encode()) + len(json.dumps(t.parameters)) for t in tools
    )
    return len(system.encode()) + specs + sum(map(message_bytes, messages))
