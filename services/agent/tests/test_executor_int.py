"""End to end: the executor's safety checks against the gateway process and its database.

It sits with the agent's integration tests, which hold the harness both services need: scratch
databases and the gateway process (test_parking_int.gateway). Decisions are written straight into
the gateway's tables, as its decision store records them; the forged messages for unapproved
actions stand for anything that could put an ActionApproved on the queue.
"""

import json
import os
import secrets
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_parking_int import EXECUTOR_TOKEN, free_port, gateway

from executor.run import Effector, run_action
from py_clients.clients import ExecutionsClient
from py_clients.generated.gateway import Execution
from py_clients.generated.messages.action_approved_v1_schema import ActionApprovedV1

if TYPE_CHECKING:
    from conftest import ScratchDb

pytestmark = pytest.mark.integration


def propose(decision: str | None) -> tuple[UUID, str]:
    """A proposed rollback as the gateway stores it, decided as given (None: still pending), and
    an ActionApproved message naming it."""
    action, investigation = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(os.environ["GATEWAY_DATABASE_URL"]) as conn:
        name = f"tenant-{secrets.token_hex(4)}"
        tenant = conn.execute(
            "INSERT INTO gateway.tenants (name) VALUES (%s) RETURNING id", (name,)
        ).fetchone()
        assert tenant is not None
        conn.execute(
            "INSERT INTO gateway.alert_groups (tenant_id, group_key, window_start, severity,"
            " status, investigation_id)"
            " VALUES (%s, %s, now(), 'critical', 'awaiting_approval', %s)",
            (tenant[0], f"group-{investigation}", investigation),
        )
        conn.execute(
            "INSERT INTO gateway.proposed_actions (id, tenant_id, investigation_id, kind, target,"
            " parameters, rationale, proposed_at) VALUES (%s, %s, %s, 'rollback_deploy',"
            " 'checkout', %s, 'Errors began with v1.8.0.', now())",
            (action, tenant[0], investigation, Jsonb({"to_version": "v1.7.3"})),
        )
        if decision is not None:
            conn.execute(
                "INSERT INTO gateway.approvals (action_id, approver, decision)"
                " VALUES (%s, 'oncall@example.com', %s)",
                (action, decision),
            )
    message = ActionApprovedV1.model_validate(
        {
            "schema_version": 1,
            "action_id": action,
            "investigation_id": investigation,
            "kind": "rollback_deploy",
            "target": "checkout",
            "parameters": {"to_version": "v1.7.3"},
            "approved_by": "oncall@example.com",
            "approved_at": datetime.now(UTC),
        }
    )
    return action, message.model_dump_json()


def executions() -> dict[UUID, tuple[str, bool]]:
    with psycopg.connect(os.environ["GATEWAY_DATABASE_URL"]) as conn:
        rows = conn.execute("SELECT action_id, state::text, dry_run FROM gateway.executions")
        return {action: (state, dry_run) for action, state, dry_run in rows}


def test_the_executor_runs_only_approved_allowlisted_actions_once(scratch_db: "ScratchDb") -> None:
    scratch_db.migrate_gateway()
    approved, approved_body = propose("approved")
    planned, planned_body = propose("approved")
    untouched, untouched_body = propose("approved")
    unapproved = [body for _, body in (propose(None), propose("rejected"))]
    unknown = json.loads(approved_body) | {"action_id": str(uuid.uuid4())}
    off_the_list = json.loads(untouched_body) | {"kind": "delete_namespace"}
    effects: list[UUID] = []

    def record(execution: Execution) -> None:
        effects.append(execution.action_id)

    effectors: dict[str, Effector] = {"rollback_deploy": record}
    port = free_port()
    url = f"http://127.0.0.1:{port}"

    def run(client: ExecutionsClient, body: str, dry_run: bool = False) -> str:
        return run_action(body, client, dry_run=dry_run, effectors=effectors)

    with gateway("http://127.0.0.1:1/unused.fifo", "http://127.0.0.1:1", port):
        with ExecutionsClient(url, EXECUTOR_TOKEN, timeout=5) as client:
            assert run(client, approved_body) == "executed"
            # The same idempotency key, replayed: the action runs once.
            assert run(client, approved_body) == "duplicate"
            # No approval row (pending), a rejection, an action never proposed: refused.
            for body in [*unapproved, json.dumps(unknown)]:
                assert run(client, body) == "refused"
            # Off the allowlist: refused before the gateway is asked.
            assert run(client, json.dumps(off_the_list)) == "refused"
            assert run(client, planned_body, dry_run=True) == "dry_run"
        # Another service's credential claims nothing.
        with ExecutionsClient(url, secrets.token_urlsafe(32), timeout=5) as stranger:
            assert run(stranger, untouched_body) == "refused"
    # The gateway is gone: nothing confirms the approval, so nothing runs (fail closed).
    with ExecutionsClient(url, EXECUTOR_TOKEN, timeout=2) as client:
        assert run(client, untouched_body) == "refused"

    assert effects == [approved]
    assert executions() == {approved: ("succeeded", False), planned: ("succeeded", True)}
    assert untouched not in executions()
