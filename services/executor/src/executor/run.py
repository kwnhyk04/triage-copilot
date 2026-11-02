"""The only path by which an approved action runs.

An action runs only when its kind is on the allowlist, its target is an app or a worker of the
app's catalog with a container in the executor's stack, and the gateway lets the executor claim
it, which takes a recorded approval whose decision is approved and the operator's actions switch
on; and only as the message says it was approved (kind, target, parameters). Phase 3 runs one
kind, restart_service, as `docker compose -p <project> restart <service>` with the catalog's names,
never the message's text. The action id is the idempotency key: a second claim finds the first,
and the action never runs again. Any doubt refuses (fail closed): an invalid message, an
ActionApproved v1 or a sim tenant's action (no app), a kind off the allowlist, a target the
catalog does not let restart, a gateway that cannot be reached or answers with an error.
Mutating actions are never retried; one stuck in claimed waits for a human.
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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
from py_clients.generated.messages.action_approved_schema import ActionApproved
from py_clients.generated.messages.action_approved_v2_schema import ActionApprovedV2
from watch.catalog import CATALOGS, RESTARTABLE, Stack

log = logging.getLogger(__name__)

Outcome = Literal["executed", "dry_run", "failed", "duplicate", "refused"]


@dataclass(frozen=True)
class Target:
    """What a restart acts on: the app's compose project in the executor's stack, and a service of
    its catalog."""

    project: str
    service: str

    def command(self) -> list[str]:
        """The fixed argv after `docker`."""
        return ["compose", "-p", self.project, "restart", self.service]


Effector = Callable[[Target], None]
"""Performs one kind of action on its target and raises when it fails; it bounds its own calls."""

# The kinds this executor may run: phase 3 runs restarts alone. A kind added later runs only once
# it is added here, with its effector.
ALLOWED_KINDS = frozenset({"restart_service"})
DETAIL_LIMIT = 2000


def target_of(message: ActionApprovedV2, stack: Stack) -> Target | str:
    """The catalog's container the message names, or why it may not be restarted."""
    if message.app is None:
        return "a sim tenant's action names no app"
    catalog = next(c for c in CATALOGS.values() if c.app == message.app)
    try:
        service = catalog.service(message.target)
    except KeyError:
        return f"{catalog.app} has no service {message.target}"
    if service.kind not in RESTARTABLE:
        return f"{service.name} is a {service.kind}: only an app or a worker is restarted"
    if catalog.container(service, stack) is None:
        return f"{service.name} runs no container in {catalog.app}'s {stack} stack"
    return Target(catalog.projects[stack], service.name)


def run_action(
    body: str | bytes,
    gateway: ExecutionsClient,
    *,
    dry_run: bool,
    effectors: Mapping[str, Effector],
    stack: Stack,
    allowed: frozenset[str] = ALLOWED_KINDS,
) -> Outcome:
    """Runs the action an ActionApproved message names, once, or refuses.

    A dry run claims the action like a real one but only records its command, so an old approval
    never runs later.
    """
    try:
        message = ActionApproved.model_validate_json(body).root
    except ValidationError as error:
        # A kind the contract does not list fails here, before anything else.
        return _refuse("invalid ActionApproved message", errors=error.error_count())
    if not isinstance(message, ActionApprovedV2):
        return _refuse("ActionApproved v1 names no app", action_id=message.action_id)
    kind = message.kind.value
    if kind not in allowed or not (dry_run or kind in effectors):
        return _refuse("kind not allowed here", action_id=message.action_id, kind=kind)
    target = target_of(message, stack)
    if isinstance(target, str):
        return _refuse(target, action_id=message.action_id)
    try:
        created, execution = gateway.claim(message.action_id, dry_run=dry_run)
    except ApiError as error:
        if error.status == 404:
            return _refuse("no approved action has this id", action_id=message.action_id)
        # 409: the actions switch is off; 503: the gateway could not tell.
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
    approved = (kind, message.target, message.parameters)
    if (execution.kind.value, execution.target, execution.parameters) != approved:
        detail = "refused: the action differs from the one approved"
        _report(gateway, execution, ExecutionOutcome.failed, detail)
        return "refused"
    if dry_run:
        plan = "dry run: docker " + " ".join(target.command())
        _report(gateway, execution, ExecutionOutcome.succeeded, plan)
        return "dry_run"
    try:
        effectors[kind](target)
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
