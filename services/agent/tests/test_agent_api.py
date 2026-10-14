from fastapi.testclient import TestClient

from agent.main import create_app
from agent.settings import Settings

# Nothing listens on port 1, so dependency checks fail fast without infra.
OFFLINE = Settings(
    agent_database_url="postgresql+psycopg://agent:unused@127.0.0.1:1/triage",
    sqs_endpoint_url="http://127.0.0.1:1",
)


def test_healthz() -> None:
    response = TestClient(create_app(OFFLINE)).get("/healthz")
    assert response.json() == {"status": "ok"}


def test_readyz_is_unavailable_without_dependencies() -> None:
    response = TestClient(create_app(OFFLINE)).get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
