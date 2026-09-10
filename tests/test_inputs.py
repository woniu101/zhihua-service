from dataclasses import replace

from fastapi.testclient import TestClient

from zhihua_service.main import app, settings

TOKEN = "test-token-that-is-longer-than-32-characters"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "X-Zhihua-API-Version": "v1",
    "X-Zhihua-Client-Version": "0.1.0",
}


def test_upload_places_valid_media_in_isolated_comfyui_directory(tmp_path) -> None:
    input_root = tmp_path / "input" / "zhihua-inputs"
    with TestClient(app) as client:
        client.app.state.settings = replace(
            settings,
            service_token=TOKEN,
            comfyui_input_path=str(input_root),
            maximum_input_bytes=1024,
        )
        response = client.post(
            "/api/v1/inputs?filename=reference.png",
            headers=HEADERS,
            content=b"\x89PNG\r\n\x1a\n" + b"fake-png-content",
        )

        assert response.status_code == 201
        payload = response.json()
        assert payload["remote_file"].startswith("zhihua-inputs/")
        stored = input_root / payload["input_id"]
        assert stored.read_bytes().startswith(b"\x89PNG")

        deleted = client.delete(
            f"/api/v1/inputs/{payload['input_id']}",
            headers=HEADERS,
        )
        assert deleted.status_code == 204
        assert not stored.exists()


def test_upload_rejects_spoofed_or_oversized_input(tmp_path) -> None:
    with TestClient(app) as client:
        client.app.state.settings = replace(
            settings,
            service_token=TOKEN,
            comfyui_input_path=str(tmp_path / "inputs"),
            maximum_input_bytes=16,
        )
        spoofed = client.post(
            "/api/v1/inputs?filename=reference.png",
            headers=HEADERS,
            content=b"not-a-png",
        )
        oversized = client.post(
            "/api/v1/inputs?filename=reference.mp4",
            headers=HEADERS,
            content=b"0" * 17,
        )

    assert spoofed.status_code == 415
    assert oversized.status_code == 413
