import asyncio
import hashlib
import logging
import mimetypes
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

from zhihua_service.schemas import ArtifactManifest, JobStatus, ResultManifest
from zhihua_service.services.comfyui import (
    ComfyUIClient,
    ComfyUIError,
    ComfyUIOutput,
    ComfyUIPromptRejectedError,
    ComfyUIUnavailableError,
)
from zhihua_service.services.jobs import JobStore
from zhihua_service.services.workflows import (
    WorkflowRegistry,
    WorkflowTemplateError,
    WorkflowTemplateMissingError,
)

logger = logging.getLogger(__name__)


class JobProcessor:
    def __init__(
        self,
        jobs: JobStore,
        comfyui: ComfyUIClient,
        workflows: WorkflowRegistry,
        *,
        output_directory: str,
        input_directory: str | None = None,
        retry_delay_seconds: float = 30.0,
    ) -> None:
        self._jobs = jobs
        self._comfyui = comfyui
        self._workflows = workflows
        self._output_directory = Path(output_directory)
        self._input_directory = Path(input_directory).resolve() if input_directory else None
        self._retry_delay_seconds = retry_delay_seconds
        self._deferred_until = 0.0

    async def tick(self) -> None:
        loop = asyncio.get_running_loop()
        if loop.time() < self._deferred_until:
            return

        running, _ = self._jobs.list(status=JobStatus.RUNNING, limit=1)
        if running:
            await self._poll(running[0].id, running[0].prompt_id)
            return

        claimed = self._jobs.claim_next()
        if claimed is None:
            return
        job, parameters = claimed
        try:
            output_kind = "image" if job.kind.value.startswith("image_") else "video"
            parameters = {
                **parameters,
                "outputPrefix": f"{output_kind}/zhihua/{job.project_id}/{job.scene_id}/{job.id}",
            }
            prompt = self._workflows.build_prompt(job.workflow_id, parameters)
            prompt_id = await self._comfyui.submit_prompt(prompt, client_id=job.id)
        except (WorkflowTemplateMissingError, ComfyUIUnavailableError) as exc:
            self._jobs.defer(job.id, exc.code, str(exc))
            self._deferred_until = loop.time() + self._retry_delay_seconds
        except (WorkflowTemplateError, ComfyUIPromptRejectedError) as exc:
            self._jobs.fail(job.id, exc.code, str(exc))
            self._cleanup_input_files(parameters)
        except ComfyUIError as exc:
            self._jobs.defer(job.id, exc.code, str(exc))
        else:
            current = self._jobs.get(job.id)
            if current.status == JobStatus.CANCELLED:
                try:
                    await self._comfyui.cancel_prompt(prompt_id)
                except ComfyUIError:
                    logger.exception("failed to stop prompt for cancelled job %s", job.id)
                self.cleanup_inputs(job.id)
                return
            self._jobs.mark_running(job.id, prompt_id)

    async def _poll(self, job_id: str, prompt_id: str | None) -> None:
        if not prompt_id:
            self._jobs.fail(job_id, "missing_prompt_id", "running job has no ComfyUI prompt id")
            return
        try:
            history = await self._comfyui.get_history(prompt_id)
        except ComfyUIUnavailableError as exc:
            self._jobs.annotate(job_id, exc.code, str(exc))
            self._deferred_until = asyncio.get_running_loop().time() + self._retry_delay_seconds
            return
        except ComfyUIError as exc:
            self._jobs.fail(job_id, exc.code, str(exc))
            return

        if self._jobs.get(job_id).status != JobStatus.RUNNING:
            return

        if history.state == "pending":
            self._jobs.update_progress(job_id, 0.15)
            return
        if history.state == "failed":
            self._jobs.fail(
                job_id,
                "comfyui_execution_failed",
                history.detail or "ComfyUI execution failed",
            )
            self._cleanup_input_files(self._jobs.parameters(job_id))
            return

        job = self._jobs.get(job_id)
        resolved_artifacts = [
            artifact
            for index, output in enumerate(history.outputs)
            if (artifact := self._artifact(job_id, output, index)) is not None
        ]
        artifacts = []
        for artifact, relative_path in resolved_artifacts:
            self._jobs.register_artifact(job_id, artifact.artifact_id, relative_path)
            artifacts.append(artifact)
        manifest = ResultManifest(
            job_id=job.id,
            workflow_id=job.workflow_id,
            prompt_id=prompt_id,
            created_at=datetime.now(timezone.utc),
            artifacts=artifacts,
        )
        self._jobs.complete(job_id, manifest)
        self._cleanup_input_files(self._jobs.parameters(job_id))

    def _cleanup_input_files(self, parameters: dict[str, object]) -> None:
        root = self._input_directory
        if root is None:
            return
        for key, value in parameters.items():
            if not key.endswith("File") or not isinstance(value, str):
                continue
            prefix = "zhihua-inputs/"
            if not value.startswith(prefix):
                continue
            relative = Path(value.removeprefix(prefix))
            if relative.name != str(relative) or not relative.name:
                continue
            candidate = (root / relative.name).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            with suppress(OSError):
                candidate.unlink()

    def cleanup_inputs(self, job_id: str) -> None:
        self._cleanup_input_files(self._jobs.parameters(job_id))

    def cleanup_input_parameters(self, parameters: dict[str, object]) -> None:
        self._cleanup_input_files(parameters)

    def _artifact(
        self,
        job_id: str,
        output: ComfyUIOutput,
        index: int,
    ) -> tuple[ArtifactManifest, str] | None:
        if output.storage_type != "output":
            return None
        root = self._output_directory.resolve()
        candidate = (root / output.subfolder / output.filename).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        if not candidate.is_file():
            return None

        digest = hashlib.sha256()
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if media_type.startswith("video/"):
            kind = "video"
        elif media_type.startswith("image/"):
            kind = "image"
        elif media_type.startswith("audio/"):
            kind = "audio"
        else:
            kind = "metadata"
        artifact_id = f"{output.node_id}-{index}"
        manifest = ArtifactManifest(
            artifact_id=artifact_id,
            kind=kind,
            filename=candidate.name,
            media_type=media_type,
            size_bytes=candidate.stat().st_size,
            sha256=digest.hexdigest(),
            download_path=f"/api/v1/jobs/{job_id}/artifacts/{artifact_id}",
        )
        return manifest, candidate.relative_to(root).as_posix()


async def run_job_worker(processor: JobProcessor, poll_interval_seconds: float) -> None:
    while True:
        try:
            await processor.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A malformed job must not terminate the long-running service worker.
            logger.exception("job worker tick failed")
            await asyncio.sleep(poll_interval_seconds)
            continue
        await asyncio.sleep(poll_interval_seconds)


async def stop_job_worker(task: asyncio.Task[None]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
