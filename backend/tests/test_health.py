from fastapi.testclient import TestClient

from app.main import app


def test_health_does_not_need_database_or_provider_keys():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "knigoskop-api"}
    assert response.headers["x-request-id"]


def test_missing_api_route_has_safe_error_and_no_cache():
    with TestClient(app) as client:
        response = client.get("/api/v1/nonexistent")
    assert response.status_code == 404
    assert response.json()["code"] == "http_404"
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"
