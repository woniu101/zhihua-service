from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from zhihua_service import __version__
from zhihua_service.api.router import api_router
from zhihua_service.config import get_settings
from zhihua_service.services.comfyui import ComfyUIClient
from zhihua_service.services.jobs import JobStore

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    http_client = httpx.AsyncClient(timeout=settings.comfyui_timeout_seconds)
    app.state.settings = settings
    app.state.comfyui = ComfyUIClient(http_client, settings.comfyui_base_url)
    app.state.jobs = JobStore(settings.jobs_database_path)
    try:
        yield
    finally:
        await http_client.aclose()


app = FastAPI(
    title="Zhihua Service API",
    version=__version__,
    lifespan=lifespan,
)
app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {
        "service": "zhihua-service",
        "version": __version__,
        "docs": "/docs",
    }
