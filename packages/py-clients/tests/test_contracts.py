"""The schema examples validate here and in packages/ts-clients, so both languages agree on them."""

import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from py_clients.generated.gateway import AlertmanagerWebhook
from py_clients.generated.messages.investigation_requested_schema import InvestigationRequested
from py_clients.generated.messages.report_recorded_v1_schema import ReportRecordedV1
from py_clients.generated.messages.step_recorded_schema import StepRecorded

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
SCHEMAS = sorted((CONTRACTS / "messages").glob("*.schema.json"))


def model_for(schema_file: Path) -> type[BaseModel]:
    """Generated model of a message schema: step-recorded.v1.schema.json gives StepRecordedV1."""
    module = schema_file.name.removesuffix(".json").replace("-", "_").replace(".", "_")
    title = json.loads(schema_file.read_bytes())["title"]
    return getattr(importlib.import_module(f"py_clients.generated.messages.{module}"), title)


EXAMPLES = [
    pytest.param(schema_file, example, id=f"{schema_file.name}#{index}")
    for schema_file in SCHEMAS
    for index, example in enumerate(json.loads(schema_file.read_bytes()).get("examples", []))
]


@pytest.mark.parametrize(("schema_file", "example"), EXAMPLES)
def test_examples_validate_and_round_trip(schema_file: Path, example: dict[str, Any]) -> None:
    # Dumping reproduces the example, so Python producers emit what TypeScript consumers accept.
    assert model_for(schema_file).model_validate(example).model_dump(mode="json") == example


@pytest.mark.parametrize("version", [1, 2, 3])
def test_every_step_recorded_version_validates_as_step_recorded(version: int) -> None:
    schema_file = CONTRACTS / f"messages/step-recorded.v{version}.schema.json"
    for example in json.loads(schema_file.read_bytes())["examples"]:
        assert isinstance(StepRecorded.model_validate(example).root, model_for(schema_file))


@pytest.mark.parametrize("version", [1, 2])
def test_every_investigation_requested_version_validates_as_the_union(version: int) -> None:
    schema_file = CONTRACTS / f"messages/investigation-requested.v{version}.schema.json"
    for example in json.loads(schema_file.read_bytes())["examples"]:
        root = InvestigationRequested.model_validate(example).root
        assert isinstance(root, model_for(schema_file))


@pytest.mark.parametrize("name", ["action-proposed.v1", "action-approved.v1"])
def test_action_kinds_outside_the_allowlist_are_rejected(name: str) -> None:
    schema_file = CONTRACTS / f"messages/{name}.schema.json"
    example = json.loads(schema_file.read_bytes())["examples"][0]
    with pytest.raises(ValidationError):
        model_for(schema_file).model_validate({**example, "kind": "delete_namespace"})


REPORT = json.loads((CONTRACTS / "messages/report-recorded.v1.schema.json").read_bytes())
REPORT_EXAMPLE: dict[str, Any] = REPORT["examples"][0]


def with_report(**report: Any) -> dict[str, Any]:
    return {**REPORT_EXAMPLE, "report": {**REPORT_EXAMPLE["report"], **report}}


# The same cases run against ajv in packages/ts-clients/src/contracts.test.ts.
@pytest.mark.parametrize(
    "change",
    [{"evidence_ids": ["step-4"]}, {"confidence": 1.5}, {"root_cause_class": "cosmic_rays"}],
)
def test_report_recorded_rejects_bad_hypotheses(change: dict[str, Any]) -> None:
    first, *rest = REPORT_EXAMPLE["report"]["hypotheses"]
    message = with_report(hypotheses=[{**first, **change}, *rest])
    with pytest.raises(ValidationError):
        ReportRecordedV1.model_validate(message)


@pytest.mark.parametrize("change", [{"kind": "delete_namespace"}, {"evidence_ids": []}])
def test_report_recorded_rejects_unlisted_or_ungrounded_actions(change: dict[str, Any]) -> None:
    action = REPORT_EXAMPLE["report"]["proposed_actions"][0]
    with pytest.raises(ValidationError):
        ReportRecordedV1.model_validate(with_report(proposed_actions=[{**action, **change}]))


def test_action_proposed_is_marked_unused() -> None:
    union = json.loads((CONTRACTS / "messages/action-proposed.schema.json").read_bytes())
    assert [branch.get("deprecated") for branch in union["oneOf"]] == [True]


def test_alertmanager_example_matches_the_webhook_schema() -> None:
    example = CONTRACTS / "openapi/examples/alertmanager-webhook.json"
    payload = AlertmanagerWebhook.model_validate_json(example.read_bytes())
    assert payload.alerts[0].fingerprint == "8a0461e5e216f2b5"
