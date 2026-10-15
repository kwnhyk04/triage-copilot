"""The executor's safety checks against a fake gateway: these back the 0 unapproved executions gate.

test_executor_int.py (in services/agent/tests, with the integration harness) runs the same checks
against the real gateway process and its database.
"""

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx2
import pytest

from executor.run import Effector, run_action
from py_clients.clients import ExecutionsClient
from py_clients.generated.gateway import Execution

ID = UUID("9f8b6c1e-3d2a-4f5b-8e7d-6c5b4a392817")
MESSAGE = {
    "schema_version": 1,
    "action_id": str(ID),
    "investigation_id": "1b4e28ba-2fa1-41d2-883f-0016d3cca427",
    "kind": "rollback_deploy",
    "target": "checkout",
    "parameters": {"to_version": "v1.7.3"},
    "approved_by": "oncall@example.com",
    "approved_at": "2026-10-15T12:03:12Z",
}
EXECUTION = {
    **{key: MESSAGE[key] for key in ("action_id", "kind", "target", "parameters", "approved_by")},
    "approved_at": "2026-10-15T12:03:12Z",
    "state": "claimed",
    "dry_run": False,
    "detail": None,
    "claimed_at": "2026-10-15T12:03:13Z",
    "finished_at": None,
}
PROBLEM = {"content-type": "application/problem+json"}


def body(**change: Any) -> str:
    return json.dumps({**MESSAGE, **change})


class Gateway:
    """The gateway's claim and result routes, with one execution per action id."""

    def __init__(self, approved: bool = True, row: dict[str, Any] | None = None) -> None:
        self.approved, self.row = approved, row or {}
        self.execution: dict[str, Any] | None = None
        self.paths: list[str] = []
        self.results: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.paths.append(request.url.path)
        if request.url.path.endswith("/result"):
            self.results.append(json.loads(request.content))
            assert self.execution is not None
            self.execution |= self.results[-1]
            return httpx2.Response(200, json=self.execution)
        if not self.approved:
            return httpx2.Response(404, headers=PROBLEM, json={"title": "Not Found", "status": 404})
        if self.execution is not None:
            return httpx2.Response(200, json=self.execution)
        dry_run = json.loads(request.content)["dry_run"]
        self.execution = {**EXECUTION, **self.row, "dry_run": dry_run}
        return httpx2.Response(201, json=self.execution)

    def client(self) -> ExecutionsClient:
        return ExecutionsClient(
            "http://gateway.test", "token", transport=httpx2.MockTransport(self)
        )


class Effects:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.runs: list[UUID] = []

    def __call__(self, execution: Execution) -> None:
        self.runs.append(execution.action_id)
        if self.fail:
            raise RuntimeError("rollback timed out")


def run(
    gateway: Gateway | ExecutionsClient,
    effects: Effector,
    message: str = body(),
    **options: Any,
) -> str:
    client = gateway.client() if isinstance(gateway, Gateway) else gateway
    settings: dict[str, Any] = {"dry_run": False, "effectors": {"rollback_deploy": effects}}
    return run_action(message, client, **(settings | options))


def test_an_action_without_an_approval_row_is_refused() -> None:
    gateway, effects = Gateway(approved=False), Effects()
    assert run(gateway, effects) == "refused"
    assert effects.runs == []
    assert gateway.paths == [f"/internal/actions/{ID}/execution"]


@pytest.mark.parametrize(
    ("message", "options"),
    [
        pytest.param(body(kind="delete_namespace"), {}, id="kind-outside-the-contract"),
        pytest.param(
            body(), {"allowed": frozenset({"restart_service"})}, id="kind-off-the-allowlist"
        ),
        pytest.param(body(kind="restart_service"), {}, id="kind-without-an-effector"),
        pytest.param(body(target=""), {}, id="invalid-message"),
    ],
)
def test_a_kind_not_on_the_allowlist_is_refused_before_any_call(
    message: str, options: dict[str, Any]
) -> None:
    gateway, effects = Gateway(), Effects()
    assert run(gateway, effects, message, **options) == "refused"
    assert gateway.paths == []
    assert effects.runs == []


def test_replaying_the_same_idempotency_key_executes_once() -> None:
    gateway, effects = Gateway(), Effects()
    assert run(gateway, effects) == "executed"
    assert run(gateway, effects) == "duplicate"
    assert run(gateway, effects) == "duplicate"
    assert effects.runs == [ID]
    assert gateway.results == [{"state": "succeeded"}]


def test_a_claim_stuck_in_claimed_is_never_retried() -> None:
    gateway, effects = Gateway(), Effects()
    gateway.execution = dict(EXECUTION)  # Claimed by an executor that died before reporting.
    assert run(gateway, effects) == "duplicate"
    assert effects.runs == []
    assert gateway.results == []


def test_a_dry_run_records_its_plan_and_never_acts() -> None:
    gateway, effects = Gateway(), Effects()
    assert run(gateway, effects, dry_run=True, effectors={}) == "dry_run"
    assert effects.runs == []
    assert gateway.execution is not None and gateway.execution["dry_run"] is True
    plan = 'dry run: rollback_deploy checkout {"to_version": "v1.7.3"}'
    assert gateway.results == [{"state": "succeeded", "detail": plan}]
    # The claim is used up: the approval cannot run later, dry run or not.
    assert run(gateway, effects) == "duplicate"
    assert effects.runs == []


def test_a_failed_action_is_recorded_and_never_retried() -> None:
    gateway, effects = Gateway(), Effects(fail=True)
    assert run(gateway, effects) == "failed"
    assert gateway.results == [{"state": "failed", "detail": "RuntimeError: rollback timed out"}]
    assert run(gateway, effects) == "duplicate"
    assert effects.runs == [ID]


@pytest.mark.parametrize(
    "row",
    [
        pytest.param({"kind": "scale_service"}, id="kind"),
        pytest.param({"target": "payments"}, id="target"),
        pytest.param({"parameters": {"to_version": "v1.0.0"}}, id="parameters"),
    ],
)
def test_an_action_that_differs_from_the_approved_message_never_runs(row: dict[str, Any]) -> None:
    # Changed after its approval, or a forged message reusing an approved id: refused, recorded.
    gateway, effects = Gateway(row=row), Effects()
    assert run(gateway, effects) == "refused"
    assert effects.runs == []
    detail = "refused: the action differs from the one approved"
    assert gateway.results == [{"state": "failed", "detail": detail}]


def raising(error: Exception) -> Callable[[httpx2.Request], httpx2.Response]:
    def handle(request: httpx2.Request) -> httpx2.Response:
        raise error

    return handle


def answering(status: int) -> Callable[[httpx2.Request], httpx2.Response]:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, headers=PROBLEM, json={"title": "Error", "status": status})

    return handle


@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(raising(httpx2.ConnectError("connection refused")), id="unreachable"),
        pytest.param(raising(httpx2.ReadTimeout("timed out")), id="timeout"),
        pytest.param(answering(503), id="unavailable"),
        pytest.param(answering(401), id="unauthorized"),
    ],
)
def test_a_gateway_that_cannot_confirm_the_approval_means_no_action(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> None:
    effects = Effects()
    client = ExecutionsClient(
        "http://gateway.test", "token", transport=httpx2.MockTransport(handler)
    )
    assert run(client, effects) == "refused"
    assert effects.runs == []
