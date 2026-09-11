from dataclasses import replace

from fastapi.testclient import TestClient

from zhihua_service.main import app, settings

TOKEN = "test-token-that-is-longer-than-32-characters"


def test_handshake_requires_bearer_token() -> None:
    with TestClient(app) as client:
        client.app.state.settings = replace(settings, service_token=TOKEN)
        response = client.post(
            "/api/v1/handshake",
            json={
                "client_version": "0.1.0",
                "supported_api_versions": ["v1"],
                "workflow_manifest_versions": ["zhihua-workflows-2026.09.11"],
                "model_manifest_versions": ["public-models-2026.09.08"],
            },
        )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_service_token"


def test_handshake_accepts_matching_contract() -> None:
    with TestClient(app) as client:
        client.app.state.settings = replace(settings, service_token=TOKEN)
        response = client.post(
            "/api/v1/handshake",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "client_version": "0.1.0",
                "supported_api_versions": ["v1"],
                "workflow_manifest_versions": ["zhihua-workflows-2026.09.11"],
                "model_manifest_versions": ["public-models-2026.09.08"],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["compatible"] is True
    assert payload["reasons"] == []
    assert payload["selected_api_version"] == "v1"


def test_handshake_reports_every_incompatible_layer() -> None:
    with TestClient(app) as client:
        client.app.state.settings = replace(settings, service_token=TOKEN)
        response = client.post(
            "/api/v1/handshake",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "client_version": "bad-version",
                "supported_api_versions": ["v2"],
                "workflow_manifest_versions": ["old-workflows"],
                "model_manifest_versions": ["old-models"],
            },
        )

    assert response.status_code == 200
    assert response.json()["reasons"] == [
        "api_version_incompatible",
        "client_version_incompatible",
        "workflow_manifest_incompatible",
        "model_manifest_incompatible",
    ]
