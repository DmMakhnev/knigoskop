from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_health_and_disabled_providers():
    client = TestClient(create_app(Settings(_env_file=None)))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    status = client.get("/api/v1/status")
    assert status.status_code == 200
    assert status.json()["capabilities"] == {"ai": False, "tts": False}


def test_cors_allows_only_configured_origin():
    client = TestClient(create_app(Settings(_env_file=None)))
    allowed = client.get("/health", headers={"Origin": "http://localhost:5173"})
    denied = client.get("/health", headers={"Origin": "https://untrusted.example"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in denied.headers
