from fastapi import APIRouter, Depends, Request

from zhihua_service import __version__
from zhihua_service.config import Settings
from zhihua_service.schemas import HandshakeRequest, HandshakeResponse
from zhihua_service.security import client_version_is_supported, require_service_auth

router = APIRouter(tags=["protocol"])


@router.post(
    "/handshake",
    response_model=HandshakeResponse,
    dependencies=[Depends(require_service_auth)],
)
async def handshake(request: Request, payload: HandshakeRequest) -> HandshakeResponse:
    settings: Settings = request.app.state.settings
    reasons: list[str] = []
    selected_api_version = "v1" if "v1" in payload.supported_api_versions else None

    if selected_api_version is None:
        reasons.append("api_version_incompatible")
    if not client_version_is_supported(
        payload.client_version,
        settings.minimum_client_version,
    ):
        reasons.append("client_version_incompatible")
    if settings.workflow_manifest_version not in payload.workflow_manifest_versions:
        reasons.append("workflow_manifest_incompatible")
    if settings.model_manifest_version not in payload.model_manifest_versions:
        reasons.append("model_manifest_incompatible")

    return HandshakeResponse(
        compatible=not reasons,
        reasons=reasons,
        service_version=__version__,
        selected_api_version=selected_api_version,
        minimum_client_version=settings.minimum_client_version,
        workflow_manifest_version=settings.workflow_manifest_version,
        model_manifest_version=settings.model_manifest_version,
        capabilities=[
            "comfyui_readiness",
            "persistent_job_queue",
            "comfyui_job_executor",
            "job_recovery",
            "result_manifest_v1",
        ],
    )
