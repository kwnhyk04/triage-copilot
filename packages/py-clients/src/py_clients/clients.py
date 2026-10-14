"""Thin httpx2 clients over the generated models. Every call has a timeout."""

from __future__ import annotations

from contextlib import suppress
from typing import Self
from uuid import UUID

import httpx2
from pydantic import BaseModel, ValidationError

from py_clients.generated import agent, gateway

DEFAULT_TIMEOUT = 10.0


class ApiError(Exception):
    """A non-2xx response; `problem` holds its RFC 9457 body when it has one."""

    def __init__(self, response: httpx2.Response) -> None:
        self.status = response.status_code
        self.problem: gateway.Problem | None = None
        # Both APIs share the RFC 9457 shape, so the gateway model parses either.
        if response.headers.get("content-type", "").startswith("application/problem+json"):
            with suppress(ValidationError):
                self.problem = gateway.Problem.model_validate_json(response.content)
        retry_after = response.headers.get("retry-after", "")
        self.retry_after = int(retry_after) if retry_after.isdigit() else None
        title = self.problem.title if self.problem else response.reason_phrase
        super().__init__(f"{self.status} {title}")


class _Client:
    def __init__(
        self,
        base_url: str,
        headers: dict[str, str],
        timeout: float,
        transport: httpx2.BaseTransport | None,
    ) -> None:
        self._http = httpx2.Client(
            base_url=base_url, headers=headers, timeout=timeout, transport=transport
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _send(
        self,
        method: str,
        url: str,
        *,
        body: BaseModel | None = None,
        params: dict[str, str | int] | None = None,
    ) -> bytes:
        # Unset optional fields are left out: the request schemas allow no nulls.
        json = body.model_dump(mode="json", exclude_none=True) if body else None
        response = self._http.request(method, url, json=json, params=params)
        if not response.is_success:
            raise ApiError(response)
        return response.content


class AlertsClient(_Client):
    """The gateway's Alertmanager webhook, authenticated with the Alertmanager bearer token."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        super().__init__(base_url, {"Authorization": f"Bearer {token}"}, timeout, transport)

    def post(self, payload: gateway.AlertmanagerWebhook) -> gateway.AlertAccepted:
        content = self._send("POST", "/v1/alerts", body=payload)
        return gateway.AlertAccepted.model_validate_json(content)


class GatewayClient(_Client):
    """The gateway's read routes, authenticated with the X-API-Key header.

    Approve and reject are left out on purpose: only the web backend-for-frontend records a
    human decision, so no Python service (the agent least of all) gets a way to approve.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        super().__init__(base_url, {"X-API-Key": api_key}, timeout, transport)

    def list_investigations(
        self, *, limit: int | None = None, cursor: str | None = None
    ) -> gateway.InvestigationPage:
        """One page, newest first; pass the page's next_cursor as `cursor` for the next one."""
        params = {k: v for k, v in (("limit", limit), ("cursor", cursor)) if v is not None}
        content = self._send("GET", "/v1/investigations", params=params)
        return gateway.InvestigationPage.model_validate_json(content)

    def get_investigation(self, investigation_id: UUID) -> gateway.Investigation:
        content = self._send("GET", f"/v1/investigations/{investigation_id}")
        return gateway.Investigation.model_validate_json(content)


class AgentClient(_Client):
    """The agent's internal API, authenticated with the service token."""

    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        super().__init__(base_url, {"Authorization": f"Bearer {service_token}"}, timeout, transport)

    def cancel_investigation(self, investigation_id: UUID) -> agent.InvestigationState:
        content = self._send("POST", f"/internal/investigations/{investigation_id}/cancel")
        return agent.InvestigationState.model_validate_json(content)

    def get_investigation_state(self, investigation_id: UUID) -> agent.InvestigationState:
        content = self._send("GET", f"/internal/investigations/{investigation_id}/state")
        return agent.InvestigationState.model_validate_json(content)
