"""The agent's internal API: only backend services call it, with the service token."""

import hashlib
import hmac
from collections.abc import Mapping
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import SecretStr
from sqlalchemy import func, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from agent.db import Investigation, ToolCall
from agent.job import Job
from py_clients.generated.agent import InvestigationState, InvestigationStatus, WaitReason


def problem(
    status: int, detail: str | None = None, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    """RFC 9457 problem details, the contract's error format."""
    body: dict[str, object] = {"title": HTTPStatus(status).phrase, "status": status}
    if detail:
        body["detail"] = detail
    return JSONResponse(body, status, headers, media_type="application/problem+json")


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def add_internal_api(app: FastAPI, job: Job, service_token: SecretStr) -> None:
    """Serves /internal/* behind the service token, and turns every error into problem details."""
    expected = _digest(service_token.get_secret_value())

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        scheme, _, token = (authorization or "").partition(" ")
        # Equal-length digests let compare_digest run without revealing how much matched.
        if scheme.lower() != "bearer" or not hmac.compare_digest(_digest(token), expected):
            raise HTTPException(401, "Send a valid service token.", {"WWW-Authenticate": "Bearer"})

    router = APIRouter(prefix="/internal", dependencies=[Depends(authorize)])

    @router.get("/investigations/{investigation_id}/state")
    def state(investigation_id: UUID) -> InvestigationState:
        found = _state(job, investigation_id)
        if found is None:
            raise HTTPException(404, "No investigation has this id.")
        return found

    app.include_router(router)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, error: StarletteHTTPException) -> JSONResponse:
        return problem(error.status_code, str(error.detail), error.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid(_request: Request, error: RequestValidationError) -> JSONResponse:
        issues = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()[:10]]
        return problem(400, "; ".join(issues))


def _state(job: Job, inv: UUID) -> InvestigationState | None:
    """The latest checkpoint, and whether a parked investigation could run now."""
    calls = select(func.count(ToolCall.id)).where(ToolCall.investigation_id == Investigation.id)
    query = select(
        Investigation.status,
        Investigation.step_count,
        Investigation.budget_tokens_used,
        Investigation.updated_at,
        Investigation.tenant_id,
        Investigation.waiting_reason,
        Investigation.waiting_since,
        calls.scalar_subquery(),
    ).where(Investigation.id == inv)
    with job.engine.connect() as conn:
        with conn.begin():
            row = conn.execute(query).one_or_none()
        if row is None:
            return None
        status, steps, tokens, updated_at, tenant, reason, since, tool_calls = row
        resumable = job.resumable(conn, inv, reason, tenant, new=steps == 0)
    return InvestigationState(
        investigation_id=inv,
        # queued_budget was step 4's in-place budget wait; the API never had it.
        status=InvestigationStatus("queued" if status == "queued_budget" else status),
        cancel_requested=False,  # Cancel is not built yet.
        steps_taken=steps,
        tool_calls=tool_calls,
        tokens_used=tokens,
        updated_at=updated_at,
        waiting_for=None if reason is None else WaitReason(reason),
        waiting_since=since,
        resumable=resumable,
    )
