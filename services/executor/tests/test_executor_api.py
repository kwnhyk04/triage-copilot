from fastapi.testclient import TestClient

from executor.main import Settings, create_app


def test_health_endpoints() -> None:
    client = TestClient(create_app(Settings()))
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ok"}
