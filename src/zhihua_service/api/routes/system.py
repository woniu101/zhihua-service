from fastapi import APIRouter, HTTPException, Request, status

from zhihua_service import __version__
from zhihua_service.config import get_settings
from zhihua_service.schemas import (
    CapabilitiesResponse,
    EnvironmentStatusResponse,
    HealthResponse,
    ReadyResponse,
    VersionResponse,
)
from zhihua_service.services.comfyui import ComfyUIClient
from zhihua_service.services.environment import get_environment_status

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(version=__version__)


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    return VersionResponse(service_version=__version__)


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities() -> CapabilitiesResponse:
    return CapabilitiesResponse(
        features=["health", "readiness", "environment_status", "comfyui_status"]
    )


@router.get("/environment/status", response_model=EnvironmentStatusResponse)
async def environment_status() -> EnvironmentStatusResponse:
    return get_environment_status(get_settings().comfyui_path)


@router.get("/ready", response_model=ReadyResponse)
async def ready(request: Request) -> ReadyResponse:
    client: ComfyUIClient = request.app.state.comfyui
    comfyui_status = await client.get_status()
    response = ReadyResponse(ready=comfyui_status.connected, comfyui=comfyui_status)
    if not response.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=response.model_dump(),
        )
    return response

