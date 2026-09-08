from fastapi.testclient import TestClient

from zhihua_service.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "zhihua-service",
        "version": "0.1.0",
    }


def test_version() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/version")

    assert response.status_code == 200
    assert response.json() == {
        "service_version": "0.1.0",
        "api_version": "v1",
    }


def test_capabilities() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/capabilities")

    assert response.status_code == 200
    assert response.json()["features"] == [
        "health",
        "readiness",
        "environment_status",
        "comfyui_status",
    ]

