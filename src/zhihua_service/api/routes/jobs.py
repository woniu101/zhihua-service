from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from zhihua_service.config import Settings
from zhihua_service.schemas import (
    JobCancelResponse,
    JobCreateRequest,
    JobKind,
    JobListResponse,
    JobResponse,
    JobStatus,
    QueueStatusResponse,
    ResultManifest,
)
from zhihua_service.security import require_protocol_headers
from zhihua_service.services.comfyui import (
    ComfyUICancelTarget,
    ComfyUIError,
    ComfyUIUnavailableError,
)
from zhihua_service.services.jobs import (
    JobConflictError,
    JobNotFoundError,
    JobStore,
)

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    dependencies=[Depends(require_protocol_headers)],
)


def _store(request: Request) -> JobStore:
    return request.app.state.jobs


def _not_found(job_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "job_not_found", "job_id": job_id},
    )


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    request: Request,
    response: Response,
    payload: JobCreateRequest,
) -> JobResponse:
    settings: Settings = request.app.state.settings
    if payload.workflow_id not in settings.allowed_workflows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "workflow_not_allowed",
                "allowed_workflows": list(settings.allowed_workflows),
            },
        )
    if (payload.kind is JobKind.VOICE_CLONE) != (payload.workflow_id == "indextts-2.5-v1"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "job_workflow_mismatch"},
        )
    try:
        job, created = await run_in_threadpool(_store(request).create, payload)
    except JobConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "idempotency_conflict"},
        ) from exc
    if not created:
        response.status_code = status.HTTP_200_OK
    return job


@router.get("", response_model=JobListResponse)
async def list_jobs(
    request: Request,
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobListResponse:
    jobs, total = await run_in_threadpool(
        _store(request).list,
        status=job_status,
        limit=limit,
        offset=offset,
    )
    return JobListResponse(jobs=jobs, total=total)


@router.get("/queue", response_model=QueueStatusResponse)
async def queue_status(request: Request) -> QueueStatusResponse:
    return await run_in_threadpool(_store(request).queue_status)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(request: Request, job_id: str) -> JobResponse:
    try:
        return await run_in_threadpool(_store(request).get, job_id)
    except JobNotFoundError as exc:
        raise _not_found(job_id) from exc


@router.post("/{job_id}/cancel", response_model=JobCancelResponse)
async def cancel_job(request: Request, job_id: str) -> JobCancelResponse:
    store = _store(request)
    try:
        current = await run_in_threadpool(store.get, job_id)
        parameters = await run_in_threadpool(store.parameters, job_id)
    except JobNotFoundError as exc:
        raise _not_found(job_id) from exc

    if current.status == JobStatus.RUNNING:
        try:
            if current.kind is JobKind.VOICE_CLONE:
                cancelled = await request.app.state.job_processor.cancel_remote(current)
            else:
                if not current.prompt_id:
                    cancelled = False
                else:
                    target = await request.app.state.comfyui.cancel_prompt(current.prompt_id)
                    cancelled = target is not ComfyUICancelTarget.NOT_FOUND
        except ComfyUIUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        except ComfyUIError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        if not cancelled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "cancel_target_not_found"},
            )

    try:
        job = await run_in_threadpool(store.cancel, job_id)
    except JobConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "job_not_cancellable"},
        ) from exc
    await run_in_threadpool(
        request.app.state.job_processor.cleanup_input_parameters,
        parameters,
    )
    return JobCancelResponse(id=job.id, status=job.status)


@router.get("/{job_id}/result", response_model=ResultManifest)
async def get_result(request: Request, job_id: str) -> ResultManifest:
    try:
        job = await run_in_threadpool(_store(request).get, job_id)
    except JobNotFoundError as exc:
        raise _not_found(job_id) from exc
    if job.result_manifest is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "result_not_ready", "status": job.status.value},
        )
    return job.result_manifest


@router.get("/{job_id}/artifacts/{artifact_id}", response_class=FileResponse)
async def download_artifact(
    request: Request,
    job_id: str,
    artifact_id: str,
) -> FileResponse:
    store = _store(request)
    try:
        job = await run_in_threadpool(store.get, job_id)
    except JobNotFoundError as exc:
        raise _not_found(job_id) from exc
    artifact = next(
        (
            item
            for item in (job.result_manifest.artifacts if job.result_manifest else [])
            if item.artifact_id == artifact_id
        ),
        None,
    )
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "artifact_not_found", "artifact_id": artifact_id},
        )
    try:
        relative_path = await run_in_threadpool(store.artifact_path, job_id, artifact_id)
    except JobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "artifact_not_found", "artifact_id": artifact_id},
        ) from exc
    root = Path(request.app.state.settings.comfyui_output_path).resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "artifact_path_invalid"},
        ) from exc
    if not candidate.is_file() or candidate.stat().st_size != artifact.size_bytes:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"code": "artifact_file_unavailable"},
        )
    return FileResponse(
        candidate,
        media_type=artifact.media_type,
        filename=artifact.filename,
        headers={
            "ETag": f'"sha256-{artifact.sha256}"',
            "X-Content-SHA256": artifact.sha256,
            "Cache-Control": "private, no-store",
        },
    )
