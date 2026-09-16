import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from zhihua_service import __version__
from zhihua_service.api.router import api_router
from zhihua_service.config import get_settings
from zhihua_service.services.comfyui import ComfyUIClient
from zhihua_service.services.executor import JobProcessor, run_job_worker, stop_job_worker
from zhihua_service.services.jobs import JobStore
from zhihua_service.services.voice import IndexTtsExecutor
from zhihua_service.services.workflows import WorkflowRegistry

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    http_client = httpx.AsyncClient(timeout=settings.comfyui_timeout_seconds)
    comfyui = ComfyUIClient(http_client, settings.comfyui_base_url)
    jobs = JobStore(settings.jobs_database_path)
    jobs.recover_incomplete()
    workflows = WorkflowRegistry(settings.workflow_directory)
    voice_executor = IndexTtsExecutor(settings)
    processor = JobProcessor(
        jobs,
        comfyui,
        workflows,
        output_directory=settings.comfyui_output_path,
        input_directory=settings.comfyui_input_path,
        voice_executor=voice_executor,
        retry_delay_seconds=settings.worker_retry_delay_seconds,
    )
    worker = asyncio.create_task(
        run_job_worker(processor, settings.worker_poll_interval_seconds),
        name="zhihua-job-worker",
    )
    app.state.settings = settings
    app.state.comfyui = comfyui
    app.state.jobs = jobs
    app.state.workflows = workflows
    app.state.job_processor = processor
    app.state.voice_executor = voice_executor
    try:
        yield
    finally:
        await stop_job_worker(worker)
        await processor.close()
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
