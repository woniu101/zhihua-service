from dataclasses import replace
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from zhihua_service.main import app, settings
from zhihua_service.schemas import ArtifactManifest, JobCreateRequest, JobStatus, ResultManifest
from zhihua_service.services.comfyui import ComfyUICancelTarget
from zhihua_service.services.jobs import JobStore

TOKEN = "test-token-that-is-longer-than-32-characters"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "X-Zhihua-API-Version": "v1",
    "X-Zhihua-Client-Version": "0.1.0",
}


def _payload(request_id: str = "request-1") -> dict[str, object]:
    return {
        "client_request_id": request_id,
        "project_id": "project-1",
        "scene_id": "scene-1",
        "kind": "video_candidate",
        "workflow_id": "h3-flf2v-turbo-v1",
        "parameters": {"duration_seconds": 5, "aspect_ratio": "16:9"},
    }


def test_job_routes_require_configured_token() -> None:
    with TestClient(app) as client:
        client.app.state.settings = replace(settings, service_token="")
        response = client.get(
            "/api/v1/jobs/queue",
            headers={
                "X-Zhihua-API-Version": "v1",
                "X-Zhihua-Client-Version": "0.1.0",
            },
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "service_token_not_configured"


def test_job_protocol_headers_are_enforced(tmp_path) -> None:
    with TestClient(app) as client:
        client.app.state.settings = replace(settings, service_token=TOKEN)
        client.app.state.jobs = JobStore(str(tmp_path / "jobs.sqlite3"))
        response = client.post(
            "/api/v1/jobs",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json=_payload(),
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "api_version_incompatible"


def test_job_queue_is_persistent_idempotent_and_cancellable(tmp_path) -> None:
    database_path = str(tmp_path / "jobs.sqlite3")
    with TestClient(app) as client:
        client.app.state.settings = replace(settings, service_token=TOKEN)
        client.app.state.jobs = JobStore(database_path)

        created = client.post("/api/v1/jobs", headers=HEADERS, json=_payload())
        repeated = client.post("/api/v1/jobs", headers=HEADERS, json=_payload())
        queue = client.get("/api/v1/jobs/queue", headers=HEADERS)

        assert created.status_code == 201
        assert repeated.status_code == 200
        assert repeated.json()["id"] == created.json()["id"]
        assert queue.json()["queued"] == 1

        cancelled = client.post(
            f"/api/v1/jobs/{created.json()['id']}/cancel",
            headers=HEADERS,
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

    reopened = JobStore(database_path)
    persisted = reopened.get(created.json()["id"])
    assert persisted.status.value == "cancelled"


def test_running_job_is_interrupted_in_comfyui_before_being_cancelled(tmp_path) -> None:
    class FakeComfyUI:
        def __init__(self) -> None:
            self.cancelled: list[str] = []

        async def cancel_prompt(self, prompt_id: str) -> ComfyUICancelTarget:
            self.cancelled.append(prompt_id)
            return ComfyUICancelTarget.RUNNING

    database_path = str(tmp_path / "jobs.sqlite3")
    fake = FakeComfyUI()
    with TestClient(app) as client:
        client.app.state.settings = replace(settings, service_token=TOKEN)
        store = JobStore(database_path)
        client.app.state.jobs = store
        client.app.state.comfyui = fake
        created, _ = store.create(JobCreateRequest(**_payload("request-running-cancel")))
        claimed = store.claim_next()
        assert claimed is not None
        store.mark_running(created.id, "prompt-running")

        response = client.post(f"/api/v1/jobs/{created.id}/cancel", headers=HEADERS)

        assert response.status_code == 200
        assert response.json()["status"] == JobStatus.CANCELLED.value
        assert fake.cancelled == ["prompt-running"]


def test_idempotency_conflict_and_workflow_allowlist(tmp_path) -> None:
    with TestClient(app) as client:
        client.app.state.settings = replace(settings, service_token=TOKEN)
        client.app.state.jobs = JobStore(str(tmp_path / "jobs.sqlite3"))

        assert client.post("/api/v1/jobs", headers=HEADERS, json=_payload()).status_code == 201
        changed = _payload()
        changed["scene_id"] = "scene-2"
        conflict = client.post("/api/v1/jobs", headers=HEADERS, json=changed)
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "idempotency_conflict"

        unsupported = _payload("request-2")
        unsupported["workflow_id"] = "arbitrary-workflow"
        rejected = client.post("/api/v1/jobs", headers=HEADERS, json=unsupported)
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "workflow_not_allowed"


def test_result_manifest_is_returned_only_after_completion(tmp_path) -> None:
    with TestClient(app) as client:
        client.app.state.settings = replace(settings, service_token=TOKEN)
        store = JobStore(str(tmp_path / "jobs.sqlite3"))
        client.app.state.jobs = store

        created = client.post(
            "/api/v1/jobs",
            headers=HEADERS,
            json=_payload("request-result"),
        ).json()
        pending = client.get(
            f"/api/v1/jobs/{created['id']}/result",
            headers=HEADERS,
        )
        assert pending.status_code == 409
        assert pending.json()["detail"]["code"] == "result_not_ready"

        manifest = ResultManifest(
            job_id=created["id"],
            workflow_id=created["workflow_id"],
            prompt_id="prompt-123",
            created_at=datetime.now(timezone.utc),
            artifacts=[
                ArtifactManifest(
                    artifact_id="artifact-1",
                    kind="video",
                    filename="scene-1.mp4",
                    media_type="video/mp4",
                    size_bytes=1234,
                    sha256="a" * 64,
                    download_path=(f"/api/v1/jobs/{created['id']}/artifacts/artifact-1"),
                )
            ],
        )
        store.complete(created["id"], manifest)

        completed = client.get(
            f"/api/v1/jobs/{created['id']}/result",
            headers=HEADERS,
        )
        assert completed.status_code == 200
        assert completed.json()["prompt_id"] == "prompt-123"
        assert completed.json()["artifacts"][0]["sha256"] == "a" * 64


def test_artifact_download_is_authenticated_scoped_and_supports_ranges(tmp_path) -> None:
    output_root = tmp_path / "output"
    output_file = output_root / "video" / "scene-1.mp4"
    output_file.parent.mkdir(parents=True)
    output_file.write_bytes(b"0123456789")
    with TestClient(app) as client:
        client.app.state.settings = replace(
            settings,
            service_token=TOKEN,
            comfyui_output_path=str(output_root),
        )
        store = JobStore(str(tmp_path / "jobs.sqlite3"))
        client.app.state.jobs = store
        created = client.post(
            "/api/v1/jobs",
            headers=HEADERS,
            json=_payload("request-download"),
        ).json()
        manifest = ResultManifest(
            job_id=created["id"],
            workflow_id=created["workflow_id"],
            prompt_id="prompt-download",
            created_at=datetime.now(timezone.utc),
            artifacts=[
                ArtifactManifest(
                    artifact_id="video-0",
                    kind="video",
                    filename="scene-1.mp4",
                    media_type="video/mp4",
                    size_bytes=10,
                    sha256="b" * 64,
                    download_path=(f"/api/v1/jobs/{created['id']}/artifacts/video-0"),
                )
            ],
        )
        store.register_artifact(created["id"], "video-0", "video/scene-1.mp4")
        store.complete(created["id"], manifest)

        unauthenticated = client.get(f"/api/v1/jobs/{created['id']}/artifacts/video-0")
        ranged = client.get(
            f"/api/v1/jobs/{created['id']}/artifacts/video-0",
            headers={**HEADERS, "Range": "bytes=2-5"},
        )
        missing = client.get(
            f"/api/v1/jobs/{created['id']}/artifacts/other",
            headers=HEADERS,
        )

    assert unauthenticated.status_code == 401
    assert ranged.status_code == 206
    assert ranged.content == b"2345"
    assert ranged.headers["x-content-sha256"] == "b" * 64
    assert missing.status_code == 404
