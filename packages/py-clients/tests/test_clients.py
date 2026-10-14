import inspect
import json
from pathlib import Path
from uuid import UUID

import httpx2
import pytest

from py_clients import clients
from py_clients.clients import AgentClient, AlertsClient, ApiError, GatewayClient
from py_clients.generated import gateway

ID = UUID("9f8b6c1e-3d2a-4f5b-8e7d-6c5b4a392817")
WEBHOOK = (
    Path(__file__).resolve().parents[3] / "contracts/openapi/examples/alertmanager-webhook.json"
)


def recorder(
    response: httpx2.Response,
) -> tuple[list[httpx2.Request], httpx2.MockTransport]:
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return response

    return seen, httpx2.MockTransport(handle)


def test_python_clients_cannot_approve_or_reject() -> None:
    # Only the web app's server records human decisions; no Python service gets a code path.
    assert "/v1/actions" not in inspect.getsource(clients)


def test_gateway_client_sends_api_key_and_raises_problem_details() -> None:
    problem = {"title": "Too many requests", "status": 429}
    headers = {"content-type": "application/problem+json", "retry-after": "5"}
    seen, transport = recorder(httpx2.Response(429, headers=headers, json=problem))

    with (
        GatewayClient("http://gateway.test", "key-1", transport=transport) as client,
        pytest.raises(ApiError) as raised,
    ):
        client.list_investigations(limit=10, cursor="abc")

    assert (raised.value.status, raised.value.retry_after) == (429, 5)
    assert raised.value.problem is not None
    assert raised.value.problem.title == "Too many requests"
    assert seen[0].headers["x-api-key"] == "key-1"
    assert dict(seen[0].url.params) == {"limit": "10", "cursor": "abc"}


def test_alerts_client_sends_only_the_alertmanager_token() -> None:
    accepted = {"group_id": str(ID), "deduped": False}
    seen, transport = recorder(httpx2.Response(202, json=accepted))
    payload = gateway.AlertmanagerWebhook.model_validate_json(WEBHOOK.read_bytes())

    with AlertsClient("http://gateway.test", "am-token", transport=transport) as client:
        assert client.post(payload).deduped is False

    assert seen[0].headers["authorization"] == "Bearer am-token"
    assert "x-api-key" not in seen[0].headers
    assert json.loads(seen[0].content)["alerts"][0]["fingerprint"] == "3f9c2a7b1e5d8c40"


def test_agent_client_sends_the_service_token() -> None:
    state = {
        "investigation_id": str(ID),
        "status": "running",
        "cancel_requested": True,
        "steps_taken": 3,
        "tool_calls": 2,
        "tokens_used": 5430,
        "updated_at": "2026-10-14T12:00:52Z",
    }
    seen, transport = recorder(httpx2.Response(202, json=state))

    with AgentClient("http://agent.test", "svc-token", transport=transport) as client:
        assert client.cancel_investigation(ID).cancel_requested is True

    assert seen[0].url.path == f"/internal/investigations/{ID}/cancel"
    assert seen[0].headers["authorization"] == "Bearer svc-token"
