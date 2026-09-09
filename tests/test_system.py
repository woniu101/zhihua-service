from fastapi.testclient import TestClient

from zhihua_service.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "zhihua-service",
        "version": "0.2.0",
    }


def test_version_contract() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/version")

    assert response.status_code == 200
    assert response.json() == {
        "service_version": "0.2.0",
        "api_version": "v1",
        "minimum_client_version": "0.1.0",
        "workflow_manifest_version": "h3-workflows-2026.09.08",
        "model_manifest_version": "public-models-2026.09.08",
    }


def test_capabilities_describe_secure_job_contract() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert payload["authentication_required"] is True
    assert payload["features"] == [
        "health",
        "version_handshake",
        "environment_status",
        "comfyui_readiness",
        "persistent_job_queue",
        "job_status",
        "job_cancellation",
        "result_manifest_v1",
    ]
    assert "video_reference_remake" in payload["job_kinds"]
    assert "interrupted" in payload["job_statuses"]
