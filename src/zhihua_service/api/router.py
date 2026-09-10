from fastapi import APIRouter

from zhihua_service.api.routes import comfyui, handshake, inputs, jobs, system

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(comfyui.router)
api_router.include_router(handshake.router)
api_router.include_router(jobs.router)
api_router.include_router(inputs.router)
