from fastapi import APIRouter, Request

from zhihua_service.schemas import ComfyUIStatusResponse
from zhihua_service.services.comfyui import ComfyUIClient

router = APIRouter(prefix="/comfyui", tags=["comfyui"])


@router.get("/status", response_model=ComfyUIStatusResponse)
async def comfyui_status(request: Request) -> ComfyUIStatusResponse:
    client: ComfyUIClient = request.app.state.comfyui
    return await client.get_status()

