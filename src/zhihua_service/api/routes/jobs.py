from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from starlette.concurrency import run_in_threadpool

from zhihua_service.config import Settings
from zhihua_service.schemas import (
    JobCancelResponse,
    JobCreateRequest,
    JobListResponse,
    JobResponse,
    JobStatus,
    QueueStatusResponse,
    ResultManifest,
)
from zhihua_service.security import require_protocol_headers
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "workflow_not_allowed",
                "allowed_workflows": list(settings.allowed_workflows),
            },
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
    try:
        job = await run_in_threadpool(_store(request).cancel, job_id)
    except JobNotFoundError as exc:
        raise _not_found(job_id) from exc
    except JobConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "job_not_cancellable"},
        ) from exc
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
