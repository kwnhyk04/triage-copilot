"""The only path by which an approved action runs.

An action runs only when its kind is on the allowlist and the gateway lets the executor claim it,
which takes a recorded approval whose decision is approved, and only as the message says it was
approved (kind, target, parameters). The action id is the idempotency key:
a second claim finds the first, and the action never runs again. Any doubt refuses (fail closed):
an invalid message, a kind off the allowlist, a gateway that cannot be reached or answers with an
error. Mutating actions are never retried; one stuck in claimed waits for a human.
"""

import json
import logging
from collections.abc import Callable, Mapping
from typing import Literal

import httpx2
from pydantic import ValidationError

from py_clients.clients import ApiError, ExecutionsClient
from py_clients.generated.gateway import (
    Execution,
    ExecutionOutcome,
    ExecutionResult,
    ExecutionState,
)
from py_clients.generated.messages.action_approved_v1_schema import ActionApprovedV1

log = logging.getLogger(__name__)

Outcome = Literal["executed", "dry_run", "failed", "duplicate", "refused"]
Effector = Callable[[Execution], None]
"""Performs one kind of action on its target and raises when it fails; it bounds its own calls."""

# The kinds this executor may run. A kind the contract adds later runs only once it is added here,
# with its effector.
ALLOWED_KINDS = frozenset(
    {"rollback_deploy", "restart_service", "scale_service", "disable_feature_flag"}
)
DETAIL_LIMIT = 2000


def run_action(
    body: str | bytes,
    gateway: ExecutionsClient,
    *,
    dry_run: bool,
    effectors: Mapping[str, Effector],
    allowed: frozenset[str] = ALLOWED_KINDS,
) -> Outcome:
    """Runs the action an ActionApproved message names, once, or refuses.

    A dry run claims the action like a real one but only records its plan, so an old approval
    never runs later.
    """
    try:
        message = ActionApprovedV1.model_validate_json(body)
    except ValidationError as error:
        # A kind the contract does not list fails here, before anything else.
        return _refuse("invalid ActionApproved message", errors=error.error_count())
    if message.kind not in allowed or not (dry_run or message.kind in effectors):
        return _refuse("kind not allowed here", action_id=message.action_id, kind=message.kind)
    try:
        created, execution = gateway.claim(message.action_id, dry_run=dry_run)
    except ApiError as error:
        if error.status == 404:
            return _refuse("no approved action has this id", action_id=message.action_id)
        return _refuse(
            "the gateway refused the claim", action_id=message.action_id, status=error.status
        )
    except httpx2.HTTPError as error:
        return _refuse("the gateway is unreachable", action_id=message.action_id, error=error)
    if not created:
        if execution.state is ExecutionState.claimed:
            log.error(
                "execution stuck in claimed; it is never retried, a human must check it",
                extra={"action_id": str(execution.action_id)},
            )
        return "duplicate"

    # The message carries the action as approved: if the gateway's row now says anything else,
    # the action changed after approval or the message is forged, so nothing runs.
    kind = execution.kind
    approved = (message.kind, message.target, message.parameters)
    if (kind, execution.target, execution.parameters) != approved:
        detail = "refused: the action differs from the one approved"
        _report(gateway, execution, ExecutionOutcome.failed, detail)
        return "refused"
    if dry_run:
        parameters = json.dumps(execution.parameters, sort_keys=True)
        plan = f"dry run: {kind} {execution.target} {parameters}"
        _report(gateway, execution, ExecutionOutcome.succeeded, plan)
        return "dry_run"
    try:
        effectors[kind](execution)
    except Exception as error:  # Recorded and never retried, whatever it was.
        _report(gateway, execution, ExecutionOutcome.failed, f"{type(error).__name__}: {error}")
        return "failed"
    _report(gateway, execution, ExecutionOutcome.succeeded, None)
    return "executed"


def _report(
    gateway: ExecutionsClient, execution: Execution, state: ExecutionOutcome, detail: str | None
) -> None:
    """Records how the execution ended; when that fails, it stays claimed for a human."""
    result = ExecutionResult(state=state, detail=None if detail is None else detail[:DETAIL_LIMIT])
    try:
        gateway.report(execution.action_id, result)
    except (ApiError, httpx2.HTTPError):
        log.exception(
            "could not record the result; the execution stays claimed for a human",
            extra={"action_id": str(execution.action_id)},
        )


def _refuse(reason: str, **fields: object) -> Outcome:
    log.warning(
        "action refused", extra={"reason": reason, **{k: str(v) for k, v in fields.items()}}
    )
    return "refused"
