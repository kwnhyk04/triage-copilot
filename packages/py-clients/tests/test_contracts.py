"""The schema examples validate here and in packages/ts-clients, so both languages agree on them."""

import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from py_clients.generated.gateway import AlertmanagerWebhook
from py_clients.generated.messages.step_recorded_schema import StepRecorded
from py_clients.generated.messages.step_recorded_v1_schema import StepRecordedV1

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


def test_v1_step_recorded_validates_as_step_recorded() -> None:
    schema_file = CONTRACTS / "messages/step-recorded.v1.schema.json"
    example = json.loads(schema_file.read_bytes())["examples"][0]
    assert isinstance(StepRecorded.model_validate(example).root, StepRecordedV1)


@pytest.mark.parametrize("name", ["action-proposed.v1", "action-approved.v1"])
def test_action_kinds_outside_the_allowlist_are_rejected(name: str) -> None:
    schema_file = CONTRACTS / f"messages/{name}.schema.json"
    example = json.loads(schema_file.read_bytes())["examples"][0]
    with pytest.raises(ValidationError):
        model_for(schema_file).model_validate({**example, "kind": "delete_namespace"})


def test_alertmanager_example_matches_the_webhook_schema() -> None:
    example = CONTRACTS / "openapi/examples/alertmanager-webhook.json"
    payload = AlertmanagerWebhook.model_validate_json(example.read_bytes())
    assert payload.alerts[0].fingerprint == "3f9c2a7b1e5d8c40"
