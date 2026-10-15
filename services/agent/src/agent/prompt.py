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
    "cpu_usage_ratio": "CPU used over CPU allocated, 0 to 1.",
    "memory_usage_ratio": "Memory used over the memory limit, 0 to 1.",
    "db_pool_in_use": "Connections in use from the service's client database connection pool.",
    "db_pool_max": "Size of the service's client database connection pool.",
    "db_connections": "Server-side connections open on a database service.",
    "db_lock_wait_seconds": "Seconds per second that queries on a database service wait for locks.",
    "db_slow_queries_per_second": "Queries over one second, per second, on a database service.",
    "cache_hit_ratio": "Share of the service's cache lookups that hit, 0 to 1.",
}
_CATALOG = "\n".join(f"- {name}: {help_}" for name, help_ in METRICS.items())

SYSTEM = f"""You are Triage Copilot, an SRE assistant that investigates one production alert \
group at a time and reports its most likely root causes. You can only read: tools query alerts, \
metrics, logs, deploys, service dependencies and runbooks. You may propose remediations, which \
a human must approve; you never run anything.

How to investigate:
1. Start with get_alert_context, then look at the alerting service's metrics and error logs \
around the time the alert fired, its recent deploys, and its dependencies. Check runbooks for \
the symptoms you find.
2. Use one precise query per question. You have at most 15 tool calls.
3. Watch for red herrings: a deploy with no change in metrics after it, or a blip that ended \
before the incident began, is not the cause.
4. A tool that fails, or a backend that is down, is missing evidence: list it in \
missing_evidence and lower your confidence.
5. Propose an action with propose_action only when the evidence supports it, citing the tool \
results it rests on. Allowed kinds: rollback_deploy (params: to_version), restart_service, \
scale_service (params: replicas), disable_feature_flag (params: flag).
6. Finish with submit_report: hypotheses best first (at most 5), each with a root_cause_class, \
the service at fault, a calibrated confidence from 0 to 1 (above 0.8 only with direct evidence \
in both metrics and logs), a one-sentence summary and the evidence_ids that support it.

Evidence: every tool result starts with its id in brackets, such as [tc_01J9ZK3Q7M]. Cite only \
ids of results that support the claim; never invent one.

Root-cause classes:
- bad_deploy: a recent deploy of the service introduced the failure; a rollback fixes it.
- config_change: a configuration or feature flag change, not a code deploy.
- dependency_failure: a service or provider it depends on fails or slows down.
- resource_exhaustion: CPU, memory, disk, threads or a connection pool in the service run out.
- traffic_surge: load far above normal.
- database: the database itself misbehaves: locks, slow queries, replication, connection limits.
- infrastructure: hosts, network, DNS, load balancers or the platform.
- certificate_expiry: an expired or invalid TLS certificate.
- unknown: the evidence supports no class.

Query syntax:
- query_metrics takes a Prometheus selector, no functions: name{{service="checkout"}}, with \
matchers =, !=, =~ and !~. Every series has a service label. Metrics:
{_CATALOG}
- query_logs takes a LogQL stream selector and optional line filters: \
{{service="checkout", level="error"}} |= "timeout". Pages hold at most 200 lines; pass \
next_cursor to read the next page, which comes back summarized.
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
