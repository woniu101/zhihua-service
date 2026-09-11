from fastapi import APIRouter, HTTPException, Request, status

from zhihua_service import __version__
from zhihua_service.config import Settings
from zhihua_service.schemas import (
    CapabilitiesResponse,
    EnvironmentStatusResponse,
    HealthResponse,
    JobKind,
    JobStatus,
    ReadyResponse,
    VersionResponse,
)
from zhihua_service.services.comfyui import ComfyUIClient
from zhihua_service.services.environment import get_environment_status
from zhihua_service.services.workflows import WorkflowRegistry

router = APIRouter(tags=["system"])

FEATURES = [
    "health",
    "version_handshake",
    "environment_status",
    "comfyui_readiness",
    "persistent_job_queue",
    "comfyui_job_executor",
    "job_status",
    "job_cancellation",
    "result_manifest_v1",
    "authenticated_input_upload",
    "range_artifact_download",
]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(version=__version__)


@router.get("/version", response_model=VersionResponse)
async def version(request: Request) -> VersionResponse:
    settings: Settings = request.app.state.settings
    return VersionResponse(
        service_version=__version__,
        minimum_client_version=settings.minimum_client_version,
        workflow_manifest_version=settings.workflow_manifest_version,
        model_manifest_version=settings.model_manifest_version,
    )


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities(request: Request) -> CapabilitiesResponse:
    settings: Settings = request.app.state.settings
    workflows: WorkflowRegistry = request.app.state.workflows
    comfyui: ComfyUIClient = request.app.state.comfyui
    installed_node_types = await comfyui.find_node_types(
        workflows.runtime_node_types(settings.allowed_workflows)
    )
    return CapabilitiesResponse(
        authentication_configured=settings.authentication_configured,
        features=FEATURES,
        workflows=list(settings.allowed_workflows),
        available_workflows=workflows.available_workflows(
            settings.allowed_workflows,
            installed_node_types,
        ),
        job_kinds=[kind.value for kind in JobKind],
        job_statuses=[job_status.value for job_status in JobStatus],
    )


@router.get("/environment/status", response_model=EnvironmentStatusResponse)
async def environment_status(request: Request) -> EnvironmentStatusResponse:
    settings: Settings = request.app.state.settings
    return get_environment_status(settings.comfyui_path)


@router.get("/ready", response_model=ReadyResponse)
async def ready(request: Request) -> ReadyResponse:
    client: ComfyUIClient = request.app.state.comfyui
    comfyui_status = await client.get_status()
    response = ReadyResponse(ready=comfyui_status.ready, comfyui=comfyui_status)
    if not response.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=response.model_dump(),
        )
    return response
