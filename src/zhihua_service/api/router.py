from fastapi import APIRouter

from zhihua_service.api.routes import comfyui, system

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(comfyui.router)

