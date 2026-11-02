"""The executor's safety checks against a fake gateway: these back the 0 unapproved executions gate.

test_executor_int.py (in services/agent/tests, with the integration harness) runs the same path
against the real gateway process, its database and queue, and a fake docker.
"""

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx2
import pytest

from executor.run import Effector, Target, run_action
from py_clients.clients import ExecutionsClient

ID = UUID("6f1c2a9e-8b7d-5e4f-9a3b-2c1d0e9f8a7b")
# An approved restart of FilingLens's api, which runs a container in both of its stacks.
MESSAGE: dict[str, Any] = {
    "schema_version": 2,
    "action_id": str(ID),
    "investigation_id": "1b4e28ba-2fa1-41d2-883f-0016d3cca427",
    "tenant_id": "3d6f0a8e-2b1c-4e5d-9f7a-6b5c4d3e2f1a",
    "app": "filing-lens",
    "kind": "restart_service",
    "target": "api",
    "parameters": {},
    "approved_by": "operator",
    "approved_at": "2026-11-02T12:03:12Z",
}
EXECUTION: dict[str, Any] = {
    **{key: MESSAGE[key] for key in ("action_id", "kind", "target", "parameters", "approved_by")},
    "approved_at": "2026-11-02T12:03:12Z",
    "state": "claimed",
    "dry_run": False,
    "detail": None,
    "claimed_at": "2026-11-02T12:03:13Z",
    "finished_at": None,
}
PROBLEM = {"content-type": "application/problem+json"}
API = Target("filinglens", "api")


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
        self.runs: list[Target] = []

    def __call__(self, target: Target) -> None:
        self.runs.append(target)
        if self.fail:
            raise RuntimeError("docker compose exit 1: no such service: api")


def run(
    gateway: Gateway | ExecutionsClient,
    effects: Effector,
    message: str = body(),
    **options: Any,
) -> str:
    client = gateway.client() if isinstance(gateway, Gateway) else gateway
    settings: dict[str, Any] = {
        "dry_run": False,
        "effectors": {"restart_service": effects},
        "stack": "dev",
    }
    return run_action(message, client, **(settings | options))


def test_an_approved_restart_runs_its_catalog_container_once() -> None:
    gateway, effects = Gateway(), Effects()
    assert run(gateway, effects) == "executed"
    assert run(gateway, effects) == "duplicate"
    assert effects.runs == [API]
    assert API.command() == ["compose", "-p", "filinglens", "restart", "api"]
    assert gateway.results == [{"state": "succeeded"}]
    # The same service in the other stack is that stack's compose project.
    prod = Gateway()
    assert run(prod, effects, stack="prod") == "executed"
    assert effects.runs[-1] == Target("filinglens-prod", "api")


def test_an_action_without_an_approval_row_is_refused() -> None:
    gateway, effects = Gateway(approved=False), Effects()
    assert run(gateway, effects) == "refused"
    assert effects.runs == []
    assert gateway.paths == [f"/internal/actions/{ID}/execution"]


v1: dict[str, Any] = {k: v for k, v in MESSAGE.items() if k not in ("tenant_id", "app")}
v1["schema_version"] = 1


@pytest.mark.parametrize(
    ("message", "options"),
    [
        pytest.param(json.dumps(v1), {}, id="v1-names-no-app"),
        pytest.param(body(app=None), {}, id="a-sim-tenant-s-action"),
        pytest.param(body(kind="rollback_deploy", parameters={"to_version": "v1"}), {}, id="kind"),
        pytest.param(body(kind="delete_namespace"), {}, id="kind-outside-the-contract"),
        pytest.param(body(target="payroll"), {}, id="not-in-the-catalog"),
        pytest.param(body(target="postgres"), {}, id="a-database"),
        pytest.param(body(target="rabbitmq"), {}, id="a-queue"),
        pytest.param(body(target="redis"), {}, id="a-cache"),
        pytest.param(body(target="tailscale"), {}, id="a-proxy"),
        pytest.param(body(app="freight-audit", target="minio"), {}, id="storage"),
        # FreightAudit's dev api and worker run on the host, so its dev stack restarts neither.
        pytest.param(body(app="freight-audit", target="worker"), {}, id="no-container-in-stack"),
        pytest.param(body(), {"effectors": {}}, id="no-effector"),
        pytest.param(body(target=""), {}, id="invalid-message"),
    ],
)
def test_anything_but_a_catalog_app_or_worker_restart_is_refused_before_any_call(
    message: str, options: dict[str, Any]
) -> None:
    gateway, effects = Gateway(), Effects()
    assert run(gateway, effects, message, **options) == "refused"
    assert gateway.paths == []
    assert effects.runs == []


def test_a_claim_stuck_in_claimed_is_never_retried() -> None:
    gateway, effects = Gateway(), Effects()
    gateway.execution = dict(EXECUTION)  # Claimed by an executor that died before reporting.
    assert run(gateway, effects) == "duplicate"
    assert effects.runs == []
    assert gateway.results == []


def test_a_dry_run_records_its_command_and_never_acts() -> None:
    gateway, effects = Gateway(), Effects()
    assert run(gateway, effects, dry_run=True, effectors={}) == "dry_run"
    assert effects.runs == []
    assert gateway.execution is not None and gateway.execution["dry_run"] is True
    plan = "dry run: docker compose -p filinglens restart api"
    assert gateway.results == [{"state": "succeeded", "detail": plan}]
    # The claim is used up: the approval cannot run later, dry run or not.
    assert run(gateway, effects) == "duplicate"
    assert effects.runs == []


def test_a_failed_restart_is_recorded_and_never_retried() -> None:
    gateway, effects = Gateway(), Effects(fail=True)
    assert run(gateway, effects) == "failed"
    detail = "RuntimeError: docker compose exit 1: no such service: api"
    assert gateway.results == [{"state": "failed", "detail": detail}]
    assert run(gateway, effects) == "duplicate"
    assert effects.runs == [API]


@pytest.mark.parametrize(
    "row",
    [
        pytest.param({"kind": "scale_service"}, id="kind"),
        pytest.param({"target": "ingest"}, id="target"),
        pytest.param({"parameters": {"replicas": 0}}, id="parameters"),
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
        pytest.param(answering(503), id="the-switch-unknown"),
        pytest.param(answering(409), id="the-actions-switch-off"),
        pytest.param(answering(401), id="unauthorized"),
    ],
)
def test_a_gateway_that_cannot_confirm_the_claim_means_no_action(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> None:
    effects = Effects()
    client = ExecutionsClient(
        "http://gateway.test", "token", transport=httpx2.MockTransport(handler)
    )
    assert run(client, effects) == "refused"
    assert effects.runs == []
