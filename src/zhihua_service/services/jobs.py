import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zhihua_service.schemas import (
    JobCreateRequest,
    JobResponse,
    JobStatus,
    QueueStatusResponse,
    ResultManifest,
)


class JobNotFoundError(LookupError):
    pass


class JobConflictError(RuntimeError):
    pass


class JobStore:
    def __init__(self, database_path: str) -> None:
        self._database_path = database_path
        self._lock = threading.RLock()
        self._initialized = False

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self._database_path != ":memory:":
            Path(self._database_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            if self._initialized:
                return
            with self._connection() as connection:
                connection.executescript(
                    """
                    PRAGMA journal_mode=WAL;
                    CREATE TABLE IF NOT EXISTS jobs (
                        id TEXT PRIMARY KEY,
                        client_request_id TEXT NOT NULL UNIQUE,
                        project_id TEXT NOT NULL,
                        scene_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        workflow_id TEXT NOT NULL,
                        parameters_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        prompt_id TEXT,
                        progress REAL NOT NULL DEFAULT 0,
                        error_code TEXT,
                        error_message TEXT,
                        status_detail TEXT,
                        result_manifest_json TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_jobs_status_updated
                    ON jobs(status, updated_at DESC);
                    """
                )
                columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
                }
                if "status_detail" not in columns:
                    connection.execute("ALTER TABLE jobs ADD COLUMN status_detail TEXT")
            self._initialized = True

    def create(self, request: JobCreateRequest) -> tuple[JobResponse, bool]:
        self._ensure_schema()
        parameters_json = json.dumps(
            request.parameters,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock, self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM jobs WHERE client_request_id = ?",
                (request.client_request_id,),
            ).fetchone()
            if existing is not None:
                same_request = (
                    existing["project_id"] == request.project_id
                    and existing["scene_id"] == request.scene_id
                    and existing["kind"] == request.kind.value
                    and existing["workflow_id"] == request.workflow_id
                    and existing["parameters_json"] == parameters_json
                )
                if not same_request:
                    raise JobConflictError(
                        "client_request_id was already used for a different request"
                    )
                return self._to_response(existing), False

            now = datetime.now(timezone.utc).isoformat()
            job_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO jobs (
                    id, client_request_id, project_id, scene_id, kind,
                    workflow_id, parameters_json, status, progress,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    request.client_request_id,
                    request.project_id,
                    request.scene_id,
                    request.kind.value,
                    request.workflow_id,
                    parameters_json,
                    JobStatus.QUEUED.value,
                    0.0,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            assert row is not None
            return self._to_response(row), True

    def get(self, job_id: str) -> JobResponse:
        self._ensure_schema()
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return self._to_response(row)

    def list(
        self,
        *,
        status: JobStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[JobResponse], int]:
        self._ensure_schema()
        where = ""
        values: list[object] = []
        if status is not None:
            where = " WHERE status = ?"
            values.append(status.value)
        with self._lock, self._connection() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM jobs{where}",
                    values,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT * FROM jobs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*values, limit, offset],
            ).fetchall()
        return [self._to_response(row) for row in rows], total

    def claim_next(self) -> tuple[JobResponse, dict[str, Any]] | None:
        self._ensure_schema()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (JobStatus.QUEUED.value,),
            ).fetchone()
            if row is None:
                return None
            now = datetime.now(timezone.utc).isoformat()
            changed = connection.execute(
                """
                UPDATE jobs
                SET status = ?, progress = ?, status_detail = NULL,
                    error_code = NULL, error_message = NULL, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    JobStatus.PREPARING.value,
                    0.02,
                    now,
                    row["id"],
                    JobStatus.QUEUED.value,
                ),
            ).rowcount
            if changed != 1:
                return None
            claimed = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (row["id"],),
            ).fetchone()
            assert claimed is not None
            parameters = json.loads(claimed["parameters_json"])
            return self._to_response(claimed), parameters

    def mark_running(self, job_id: str, prompt_id: str) -> JobResponse:
        return self._update(
            job_id,
            status=JobStatus.RUNNING,
            progress=0.1,
            prompt_id=prompt_id,
            status_detail="ComfyUI accepted the prompt",
            clear_error=True,
        )

    def update_progress(self, job_id: str, progress: float) -> JobResponse:
        return self._update(
            job_id,
            progress=max(0.0, min(progress, 0.99)),
            status_detail="ComfyUI is executing the prompt",
        )

    def annotate(self, job_id: str, code: str, detail: str) -> JobResponse:
        return self._update(
            job_id,
            status_detail=detail,
            error_code=code,
            error_message=detail,
        )

    def defer(self, job_id: str, code: str, detail: str) -> JobResponse:
        return self._update(
            job_id,
            status=JobStatus.QUEUED,
            progress=0.0,
            prompt_id=None,
            status_detail=detail,
            error_code=code,
            error_message=detail,
        )

    def fail(self, job_id: str, code: str, detail: str) -> JobResponse:
        return self._update(
            job_id,
            status=JobStatus.FAILED,
            status_detail=detail,
            error_code=code,
            error_message=detail,
        )

    def recover_incomplete(self) -> int:
        self._ensure_schema()
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection() as connection:
            changed = connection.execute(
                """
                UPDATE jobs
                SET status = ?, progress = 0,
                    status_detail = ?, updated_at = ?
                WHERE status = ? AND prompt_id IS NULL
                """,
                (
                    JobStatus.QUEUED.value,
                    "Recovered after service restart",
                    now,
                    JobStatus.PREPARING.value,
                ),
            ).rowcount
        return int(changed)

    def queue_status(self) -> QueueStatusResponse:
        self._ensure_schema()
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
            ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        active_states = {
            JobStatus.PREPARING.value,
            JobStatus.UPLOADING.value,
            JobStatus.RUNNING.value,
            JobStatus.UPSCALING.value,
            JobStatus.DOWNLOADING.value,
        }
        terminal_states = {
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
            JobStatus.INTERRUPTED.value,
        }
        return QueueStatusResponse(
            active=sum(counts.get(item, 0) for item in active_states),
            queued=counts.get(JobStatus.QUEUED.value, 0)
            + counts.get(JobStatus.WAITING_FOR_COMPUTE.value, 0),
            terminal=sum(counts.get(item, 0) for item in terminal_states),
            by_status=counts,
        )

    def cancel(self, job_id: str) -> JobResponse:
        current = self.get(job_id)
        if current.status in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            raise JobConflictError(f"job cannot be cancelled from {current.status.value}")
        return self._update(
            job_id,
            status=JobStatus.CANCELLED,
            status_detail="Cancelled by client",
        )

    def complete(self, job_id: str, manifest: ResultManifest) -> JobResponse:
        current = self.get(job_id)
        if manifest.job_id != current.id or manifest.workflow_id != current.workflow_id:
            raise JobConflictError("result manifest does not match the job")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, progress = 1, prompt_id = ?,
                    status_detail = ?, error_code = NULL, error_message = NULL,
                    result_manifest_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    JobStatus.COMPLETED.value,
                    manifest.prompt_id,
                    "ComfyUI execution completed",
                    manifest.model_dump_json(),
                    now,
                    job_id,
                ),
            )
        return self.get(job_id)

    def _update(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        progress: float | None = None,
        prompt_id: str | None | object = ...,
        status_detail: str | None | object = ...,
        error_code: str | None | object = ...,
        error_message: str | None | object = ...,
        clear_error: bool = False,
    ) -> JobResponse:
        self.get(job_id)
        assignments = ["updated_at = ?"]
        values: list[object] = [datetime.now(timezone.utc).isoformat()]
        for column, value in (
            ("status", status.value if status else None),
            ("progress", progress),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(value)
        for column, value in (
            ("prompt_id", prompt_id),
            ("status_detail", status_detail),
            ("error_code", error_code),
            ("error_message", error_message),
        ):
            if value is not ...:
                assignments.append(f"{column} = ?")
                values.append(value)
        if clear_error:
            assignments.extend(["error_code = NULL", "error_message = NULL"])
        values.append(job_id)
        with self._lock, self._connection() as connection:
            connection.execute(
                f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
        return self.get(job_id)

    @staticmethod
    def _to_response(row: sqlite3.Row) -> JobResponse:
        manifest = (
            ResultManifest.model_validate_json(row["result_manifest_json"])
            if row["result_manifest_json"]
            else None
        )
        return JobResponse(
            id=row["id"],
            client_request_id=row["client_request_id"],
            project_id=row["project_id"],
            scene_id=row["scene_id"],
            kind=row["kind"],
            workflow_id=row["workflow_id"],
            status=row["status"],
            prompt_id=row["prompt_id"],
            progress=row["progress"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            status_detail=row["status_detail"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            result_manifest=manifest,
        )
