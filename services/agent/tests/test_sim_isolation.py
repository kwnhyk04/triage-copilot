"""No agent tool can reach the sim's admin routes, whose state names the scenario and so the root
cause. They listen on another port the agent is never given, and every request a tool sends goes
to SIM_URL's host and port and to a data route, whatever the model passes.
"""

import asyncio
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from agent.job import PROMPT_SHA
from agent.settings import Settings
from agent.tools import ToolContext, Tools
from py_clients.clients import AlertContextClient
from py_config import load_settings
from sim.main import Settings as SimSettings
from sim.main import create_apps

SCENARIOS = Path(__file__).resolve().parents[3] / "evals" / "scenarios" / "v1"
SIM_URL = "http://127.0.0.1:4230"
ANCHOR = datetime(2026, 10, 15, 12, tzinfo=UTC)
DATA_ROUTES = re.compile(
    r"/api/v1/query_range|/loki/api/v1/query_range|/api/v1/deploys"
    r"|/api/v1/dependencies/[A-Za-z0-9._-]+"
)
# What a confused or hostile model might pass to reach another route, host or port.
HOSTILE = [
    "../../admin/state",
    "/admin/state",
    "..%2F..%2Fadmin%2Fstate",
    "%2e%2e/%2e%2e/admin/state",
    "http://127.0.0.1:4231/admin/state",
    "//127.0.0.1:4231/admin/state",
    "@127.0.0.1:4231/admin/state",
    "127.0.0.1:4231",
    "admin/state?x=1#frag",
    "\\..\\admin\\state",
    "checkout/../../admin/scenarios",
    'up{service="x"} /admin/state',
]
# Each read tool of the sim, a valid call, and the string arguments to make hostile.
CALLS: dict[str, tuple[dict[str, Any], list[str]]] = {
    "query_metrics": ({"query": 'http_error_ratio{service="orders"}'}, ["query"]),
    "query_logs": ({"query": '{service="orders"}'}, ["query", "cursor"]),
    "list_deploys": ({}, ["service", "cursor"]),
    "get_dependencies": ({"service": "orders"}, ["service"]),
}


def test_no_tool_reaches_anything_but_the_data_routes() -> None:
    apps = create_apps(SimSettings(sim_scenarios_dir=SCENARIOS, log_level="warn"))
    TestClient(apps.admin).post(
        "/admin/scenarios/db-pool-exhaustion-1/activate", json={"anchor": ANCHOR.isoformat()}
    )
    data = httpx2.ASGITransport(app=apps.data)
    sent: list[httpx2.URL] = []

    async def route(request: httpx2.Request) -> httpx2.Response:
        sent.append(request.url)
        return await data.handle_async_request(request)

    async def main() -> list[str]:
        transport = httpx2.MockTransport(route)
        async with httpx2.AsyncClient(transport=transport, base_url=SIM_URL) as sim:
            context = AlertContextClient("http://gateway.test", "token")
            tools = Tools(sim, context, None, None).all()
            ctx = ToolContext(uuid.uuid4(), ANCHOR, 100_000, PROMPT_SHA)
            results: list[str] = []
            for name, (valid, fields) in CALLS.items():
                for field in fields:
                    for value in HOSTILE:
                        try:
                            out = await tools[name](ctx, valid | {field: value})
                        except Exception as error:  # The tools reject most of these.
                            results.append(str(error))
                        else:
                            results.append(out if isinstance(out, str) else out.text)
            context.close()
            return results

    results = asyncio.run(main())
    # Metrics and logs queries are checked by the sim, so every hostile one was sent.
    assert len(sent) >= 3 * len(HOSTILE)
    for url in sent:
        assert (url.scheme, url.host, url.port) == ("http", "127.0.0.1", 4230), url
        assert DATA_ROUTES.fullmatch(url.path), url
    for text in results:
        assert "db-pool-exhaustion" not in text
        assert "sha256" not in text


def test_the_agent_holds_no_sim_admin_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_ADMIN_URL", "http://127.0.0.1:4231")
    monkeypatch.setenv("SIM_ADMIN_PORT", "4231")
    monkeypatch.setenv("AGENT_DATABASE_URL", "postgresql+psycopg://agent@127.0.0.1/none")
    monkeypatch.setenv("AGENT_SERVICE_TOKEN", "t" * 32)
    settings = load_settings(Settings)
    assert not [name for name in Settings.model_fields if "admin" in name]
    assert "4231" not in settings.model_dump_json()
