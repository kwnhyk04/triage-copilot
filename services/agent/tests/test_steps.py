"""Story B: stored v1 step events replay as v2, and v2 events travel as StepRecorded v3 messages.

After an intended change to the upcaster, rewrite the golden file with UPDATE_GOLDEN=1.
"""

import copy
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from agent.steps import StepRecordedV2, step_message, upcast
from py_clients.generated.messages.step_recorded_schema import StepRecorded
from py_clients.generated.messages.step_recorded_v3_schema import StepRecordedV3

GOLDEN = Path(__file__).parent / "golden" / "replay-v1-investigation.json"
V2_EVENT = {
    "kind": "tool_call",
    "summary": "query_metrics on checkout",
    "tool_name": "query_metrics",
    "tool_call_id": "tc_01J9ZK3Q7M",
    "result": "Error rate rose from 0.2% to 14% at 11:52.",
    "latency_ms": 840,
    "tokens": {"input": 5120, "output": 310},
}
# What a v3 message adds around the stored event.
ENVELOPE = {"schema_version", "investigation_id", "seq", "recorded_at"}
# One change to V2_EVENT per case, and whether the limits allow it.
LIMITS = [
    pytest.param({"summary": ""}, False, id="summary-empty"),
    pytest.param({"summary": "x"}, True, id="summary-one-char"),
    pytest.param({"summary": "x" * 2000}, True, id="summary-at-2000"),
    pytest.param({"summary": "\N{GRINNING FACE}" * 2000}, True, id="summary-2000-code-points"),
    pytest.param({"summary": "x" * 2001}, False, id="summary-over-2000"),
    pytest.param({"latency_ms": 0}, True, id="latency-zero"),
    pytest.param({"latency_ms": -1}, False, id="latency-negative"),
    pytest.param({"tokens": {"input": 0, "output": 0}}, True, id="tokens-zero"),
    pytest.param({"tokens": {"input": -1, "output": 0}}, False, id="input-tokens-negative"),
    pytest.param({"tokens": {"input": 0, "output": -1}}, False, id="output-tokens-negative"),
]


def v1(
    kind: str, summary: str, tool: str | None, call_id: str | None, observation: str | None
) -> tuple[int, dict[str, Any]]:
    event = {"kind": kind, "summary": summary, "tool_name": tool, "tool_call_id": call_id}
    return 1, {**event, "observation": observation}


# One investigation's rows as a v1 release stored them: (schema_version, event).
V1_INVESTIGATION = [
    v1(
        "tool_call",
        "query_metrics on checkout",
        "query_metrics",
        "tc_01J9ZK3Q7M",
        "Error rate rose from 0.2% to 14% at 11:52.",
    ),
    v1("tool_call", "search_logs on checkout timed out", "search_logs", "tc_01J9ZK4B2X", None),
    v1(
        "tool_call",
        "get_deploys on checkout",
        "get_deploys",
        "tc_01J9ZK5C3Y",
        "v1.8.0 shipped at 11:50, two minutes before the errors.",
    ),
    v1("hypothesis", "bad_deploy in checkout", None, None, None),
    v1("action", "propose rollback_deploy to v1.7.3 for checkout", None, None, None),
    v1(
        "report", "Checkout errors follow the v1.8.0 deploy; roll back to v1.7.3.", None, None, None
    ),
]


def test_v1_investigation_replays_as_the_golden_v2_steps() -> None:
    replayed = [
        upcast(version, event).model_dump(mode="json") for version, event in V1_INVESTIGATION
    ]
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.write_text(json.dumps(replayed, indent=2) + "\n", newline="\n")
    assert replayed == json.loads(GOLDEN.read_text())


def test_upcasting_leaves_the_stored_events_unchanged() -> None:
    stored = copy.deepcopy(V1_INVESTIGATION)
    for version, event in stored:
        upcast(version, event)
    assert stored == V1_INVESTIGATION


def test_v2_events_pass_through_unchanged() -> None:
    assert upcast(2, V2_EVENT).model_dump(mode="json") == V2_EVENT


def test_a_v1_row_with_a_v2_field_is_rejected() -> None:
    _, event = V1_INVESTIGATION[0]
    with pytest.raises(ValidationError):
        upcast(1, {**event, "result": "v2 field in a v1 row"})


def test_the_v3_message_has_exactly_the_stored_v2_fields() -> None:
    assert set(StepRecordedV3.model_fields) - ENVELOPE == set(StepRecordedV2.model_fields)


@pytest.mark.parametrize(("change", "allowed"), LIMITS)
def test_stored_v2_events_and_v3_messages_share_each_limit(
    change: dict[str, Any], allowed: bool
) -> None:
    event = {**V2_EVENT, **change}
    envelope = {"schema_version": 3, "investigation_id": str(UUID(int=1)), "seq": 1}
    message = {**event, **envelope, "recorded_at": "2026-10-14T12:00:41Z"}
    # A stored event that breaks a limit cannot even be built, so it is never written.
    for model, data in ((StepRecordedV2, event), (StepRecordedV3, message)):
        if allowed:
            model.model_validate(data)
        else:
            with pytest.raises(ValidationError):
                model.model_validate(data)


def test_stored_events_become_v3_messages_unchanged() -> None:
    events = [upcast(version, event) for version, event in [*V1_INVESTIGATION, (2, V2_EVENT)]]
    recorded_at = datetime(2026, 10, 14, 12, 0, 41, tzinfo=UTC)
    for seq, event in enumerate(events, 1):
        wire = step_message(UUID(int=1), seq, recorded_at, event).model_dump(mode="json")
        assert {key: wire[key] for key in wire.keys() - ENVELOPE} == event.model_dump(mode="json")
        # What a consumer reads back is the v3 message.
        assert isinstance(StepRecorded.model_validate(wire).root, StepRecordedV3)
