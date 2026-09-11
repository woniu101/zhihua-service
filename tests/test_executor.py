import asyncio
import hashlib
import json

import httpx

from zhihua_service.schemas import JobCreateRequest, JobKind, JobStatus
from zhihua_service.services.comfyui import ComfyUIClient
from zhihua_service.services.executor import JobProcessor
from zhihua_service.services.jobs import JobStore
from zhihua_service.services.workflows import WorkflowRegistry


def _request(request_id: str) -> JobCreateRequest:
    return JobCreateRequest(
        client_request_id=request_id,
        project_id="project-1",
        scene_id="scene-1",
        kind=JobKind.VIDEO_CANDIDATE,
        workflow_id="h3-flf2v-turbo-v1",
        parameters={
            "prompt": "storm over mountains",
            "duration_seconds": 5,
        },
    )


def _write_workflow(directory) -> None:
    directory.mkdir()
    (directory / "h3-flf2v-turbo-v1.json").write_text(
        json.dumps(
            {
                "prompt": {
                    "12": {
                        "class_type": "FakeVideoNode",
                        "inputs": {"text": "", "length": 0},
                    }
                },
                "bindings": {
                    "prompt": [["12", "inputs", "text"]],
                    "duration_seconds": [["12", "inputs", "length"]],
                },
            }
        ),
        encoding="utf-8",
    )


def test_processor_submits_polls_and_persists_result_manifest(tmp_path) -> None:
    async def run() -> None:
        workflow_directory = tmp_path / "workflows"
        output_directory = tmp_path / "output"
        _write_workflow(workflow_directory)
        generated = output_directory / "project" / "clip.mp4"
        generated.parent.mkdir(parents=True)
        generated.write_bytes(b"fake-video-content")

        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            if request.method == "POST" and request.url.path == "/prompt":
                body = json.loads(request.content)
                assert body["client_id"]
                assert body["prompt"]["12"]["inputs"] == {
                    "text": "storm over mountains",
                    "length": 5,
                }
                return httpx.Response(200, json={"prompt_id": "prompt-123"})
            if request.method == "GET" and request.url.path == "/history/prompt-123":
                return httpx.Response(
                    200,
                    json={
                        "prompt-123": {
                            "status": {"completed": True, "status_str": "success"},
                            "outputs": {
                                "27": {
                                    "gifs": [
                                        {
                                            "filename": "clip.mp4",
                                            "subfolder": "project",
                                            "type": "output",
                                        }
                                    ]
                                }
                            },
                        }
                    },
                )
            raise AssertionError(f"unexpected request: {request.method} {request.url}")

        store = JobStore(str(tmp_path / "jobs.sqlite3"))
        created, _ = store.create(_request("request-complete"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            processor = JobProcessor(
                store,
                ComfyUIClient(http_client, "http://comfy.test"),
                WorkflowRegistry(str(workflow_directory)),
                output_directory=str(output_directory),
            )
            await processor.tick()
            running = store.get(created.id)
            assert running.status is JobStatus.RUNNING
            assert running.progress == 0.1
            assert running.prompt_id == "prompt-123"

            await processor.tick()

        completed = store.get(created.id)
        assert completed.status is JobStatus.COMPLETED
        assert completed.progress == 1
        assert completed.result_manifest is not None
        assert completed.result_manifest.prompt_id == "prompt-123"
        assert len(completed.result_manifest.artifacts) == 1
        artifact = completed.result_manifest.artifacts[0]
        assert artifact.kind == "video"
        assert artifact.filename == "clip.mp4"
        assert artifact.sha256 == hashlib.sha256(b"fake-video-content").hexdigest()
        assert calls == ["POST /prompt", "GET /history/prompt-123"]

    asyncio.run(run())


def test_processor_keeps_job_queued_when_comfyui_is_offline(tmp_path) -> None:
    async def run() -> None:
        workflow_directory = tmp_path / "workflows"
        _write_workflow(workflow_directory)

        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("offline", request=request)

        store = JobStore(str(tmp_path / "jobs.sqlite3"))
        created, _ = store.create(_request("request-offline"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            processor = JobProcessor(
                store,
                ComfyUIClient(http_client, "http://comfy.test"),
                WorkflowRegistry(str(workflow_directory)),
                output_directory=str(tmp_path / "output"),
            )
            await processor.tick()
            await processor.tick()

        queued = store.get(created.id)
        assert calls == 1
        assert queued.status is JobStatus.QUEUED
        assert queued.progress == 0
        assert queued.error_code == "comfyui_unavailable"
        assert queued.status_detail == "ConnectError"

    asyncio.run(run())


def test_processor_records_comfyui_execution_failure(tmp_path) -> None:
    async def run() -> None:
        workflow_directory = tmp_path / "workflows"
        _write_workflow(workflow_directory)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(200, json={"prompt_id": "prompt-failed"})
            return httpx.Response(
                200,
                json={
                    "prompt-failed": {
                        "status": {
                            "completed": False,
                            "status_str": "error",
                            "messages": [
                                [
                                    "execution_error",
                                    {"exception_message": "model input is invalid"},
                                ]
                            ],
                        }
                    }
                },
            )

        store = JobStore(str(tmp_path / "jobs.sqlite3"))
        created, _ = store.create(_request("request-failed"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            processor = JobProcessor(
                store,
                ComfyUIClient(http_client, "http://comfy.test"),
                WorkflowRegistry(str(workflow_directory)),
                output_directory=str(tmp_path / "output"),
            )
            await processor.tick()
            await processor.tick()

        failed = store.get(created.id)
        assert failed.status is JobStatus.FAILED
        assert failed.error_code == "comfyui_execution_failed"
        assert failed.error_message == "model input is invalid"

    asyncio.run(run())


def test_missing_workflow_template_is_explained_without_http_call(tmp_path) -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("ComfyUI must not be called without a workflow template")

        store = JobStore(str(tmp_path / "jobs.sqlite3"))
        created, _ = store.create(_request("request-missing-workflow"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            processor = JobProcessor(
                store,
                ComfyUIClient(http_client, "http://comfy.test"),
                WorkflowRegistry(str(tmp_path / "missing")),
                output_directory=str(tmp_path / "output"),
            )
            await processor.tick()

        queued = store.get(created.id)
        assert queued.status is JobStatus.QUEUED
        assert queued.error_code == "workflow_template_missing"
        assert "not installed" in (queued.status_detail or "")

    asyncio.run(run())


def test_workflow_registry_keeps_modes_distinct_and_supports_legacy_alias(tmp_path) -> None:
    directory = tmp_path / "workflows"
    directory.mkdir()
    for workflow_id, marker in (
        ("h3-t2v-turbo-v1", "t2v"),
        ("h3-i2v-turbo-v1", "i2v"),
        ("h3-flf2v-turbo-v1", "flf2v"),
        ("h3-ref2va-turbo-v1", "ref2va"),
    ):
        (directory / f"{workflow_id}.json").write_text(
            json.dumps({"1": {"class_type": marker, "inputs": {}}}),
            encoding="utf-8",
        )

    registry = WorkflowRegistry(str(directory))
    assert registry.build_prompt("h3-t2v-turbo-v1", {})["1"]["class_type"] == "t2v"
    assert registry.build_prompt("h3-i2v-turbo-v1", {})["1"]["class_type"] == "i2v"
    assert registry.build_prompt("h3-flf2v-turbo-v1", {})["1"]["class_type"] == "flf2v"
    assert registry.build_prompt("h3-ref2va-turbo-v1", {})["1"]["class_type"] == "ref2va"
    assert registry.build_prompt("h3-fl2v-turbo-v1", {})["1"]["class_type"] == "flf2v"
    assert registry.available_workflows(
        (
            "h3-t2v-turbo-v1",
            "h3-ref2va-high-v1",
            "h3-fl2v-turbo-v1",
        )
    ) == ["h3-t2v-turbo-v1", "h3-fl2v-turbo-v1"]


def test_workflow_availability_filters_missing_runtime_nodes(tmp_path) -> None:
    directory = tmp_path / "workflows"
    directory.mkdir()
    (directory / "seedvr2-1080p-v1.json").write_text(
        json.dumps(
            {
                "prompt": {"1": {"class_type": "SeedVR2VideoUpscaler", "inputs": {}}},
                "requiredNodeTypes": ["SeedVR2VideoUpscaler", "SeedVR2LoadDiTModel"],
            }
        ),
        encoding="utf-8",
    )
    registry = WorkflowRegistry(str(directory))

    assert registry.runtime_node_types(("seedvr2-1080p-v1",)) == {
        "SeedVR2VideoUpscaler",
        "SeedVR2LoadDiTModel",
    }
    assert registry.available_workflows(
        ("seedvr2-1080p-v1",), {"SeedVR2VideoUpscaler"}
    ) == []
    assert registry.available_workflows(
        ("seedvr2-1080p-v1",),
        {"SeedVR2VideoUpscaler", "SeedVR2LoadDiTModel"},
    ) == ["seedvr2-1080p-v1"]
