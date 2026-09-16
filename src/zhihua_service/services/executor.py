import asyncio
import hashlib
import logging
import mimetypes
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

from zhihua_service.schemas import ArtifactManifest, JobKind, JobResponse, JobStatus, ResultManifest
from zhihua_service.services.comfyui import (
    ComfyUIClient,
    ComfyUIError,
    ComfyUIOutput,
    ComfyUIPromptRejectedError,
    ComfyUIUnavailableError,
)
from zhihua_service.services.jobs import JobStore
from zhihua_service.services.voice import IndexTtsExecutor, VoiceExecutionError
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
        voice_executor: IndexTtsExecutor | None = None,
        retry_delay_seconds: float = 30.0,
    ) -> None:
        self._jobs = jobs
        self._comfyui = comfyui
        self._workflows = workflows
        self._output_directory = Path(output_directory)
        self._input_directory = Path(input_directory).resolve() if input_directory else None
        self._voice = voice_executor
        self._retry_delay_seconds = retry_delay_seconds
        self._deferred_until = 0.0
        self._progress_tasks: dict[str, asyncio.Task[None]] = {}

    async def tick(self) -> None:
        loop = asyncio.get_running_loop()
        if loop.time() < self._deferred_until:
            return

        running, _ = self._jobs.list(status=JobStatus.RUNNING, limit=1)
        if running:
            if running[0].kind is JobKind.VOICE_CLONE:
                await self._poll_voice(running[0])
                return
            self._ensure_progress_watcher(running[0].id, running[0].prompt_id)
            await self._poll(running[0].id, running[0].prompt_id)
            return

        claimed = self._jobs.claim_next()
        if claimed is None:
            return
        job, parameters = claimed
        if job.kind is JobKind.VOICE_CLONE:
            await self._start_voice(job, parameters)
            return
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
            self._ensure_progress_watcher(job.id, prompt_id)

    async def _start_voice(self, job: JobResponse, parameters: dict[str, object]) -> None:
        if self._voice is None:
            self._jobs.defer(job.id, "indextts_unavailable", "IndexTTS executor is not configured")
            return
        try:
            prompt_id = await self._voice.start(job.id, job.project_id, job.scene_id, parameters)
        except VoiceExecutionError as exc:
            if exc.code == "indextts_unavailable":
                self._jobs.defer(job.id, exc.code, str(exc))
            else:
                self._jobs.fail(job.id, exc.code, str(exc))
                self._cleanup_input_files(parameters)
            return
        self._jobs.mark_running(job.id, prompt_id, "IndexTTS 2.5 is loading the voice model")

    async def _poll_voice(self, job: JobResponse) -> None:
        if self._voice is None:
            self._jobs.fail(job.id, "voice_process_lost", "IndexTTS executor is not configured")
            return
        try:
            relative_path = self._voice.relative_path(job.id)
            artifact = self._voice.poll(job.id)
        except VoiceExecutionError as exc:
            self._jobs.fail(job.id, exc.code, str(exc))
            self._cleanup_input_files(self._jobs.parameters(job.id))
            return
        if artifact is None:
            self._jobs.mark_waiting(
                job.id,
                "IndexTTS 2.5 is cloning and synthesizing the narration",
            )
            return
        self._jobs.mark_finalizing(job.id)
        self._jobs.register_artifact(job.id, artifact.artifact_id, relative_path)
        self._jobs.complete(
            job.id,
            ResultManifest(
                job_id=job.id,
                workflow_id=job.workflow_id,
                prompt_id=job.prompt_id,
                created_at=datetime.now(timezone.utc),
                artifacts=[artifact],
            ),
        )
        self._cleanup_input_files(self._jobs.parameters(job.id))

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
            current = self._jobs.get(job_id)
            if not current.progress_measured:
                self._jobs.mark_waiting(job_id, "ComfyUI is loading models or preparing generation")
            return
        if history.state == "failed":
            await self._stop_progress_watcher(job_id)
            self._jobs.fail(
                job_id,
                "comfyui_execution_failed",
                history.detail or "ComfyUI execution failed",
            )
            self._cleanup_input_files(self._jobs.parameters(job_id))
            return

        await self._stop_progress_watcher(job_id)
        self._jobs.mark_finalizing(job_id)
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

    def _ensure_progress_watcher(self, job_id: str, prompt_id: str | None) -> None:
        if not prompt_id:
            return
        existing = self._progress_tasks.get(job_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._watch_progress(job_id, prompt_id),
            name=f"zhihua-progress-{job_id}",
        )
        self._progress_tasks[job_id] = task

    async def _watch_progress(self, job_id: str, prompt_id: str) -> None:
        started_at = asyncio.get_running_loop().time()
        try:
            async for event in self._comfyui.stream_progress(
                client_id=job_id,
                prompt_id=prompt_id,
            ):
                if self._jobs.get(job_id).status != JobStatus.RUNNING:
                    return
                elapsed = max(0.0, asyncio.get_running_loop().time() - started_at)
                eta_seconds = None
                if event.current > 0:
                    eta_seconds = round(elapsed * (event.total - event.current) / event.current)
                detail = "ComfyUI is generating"
                if event.node_id:
                    detail = f"ComfyUI is generating node {event.node_id}"
                self._jobs.update_progress(
                    job_id,
                    current=event.current,
                    total=event.total,
                    eta_seconds=eta_seconds,
                    detail=detail,
                )
        except asyncio.CancelledError:
            raise
        except ComfyUIError:
            # History polling remains authoritative if the optional live stream disconnects.
            logger.info("progress stream disconnected for job %s", job_id)

    async def _stop_progress_watcher(self, job_id: str) -> None:
        task = self._progress_tasks.pop(job_id, None)
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def close(self) -> None:
        tasks = list(self._progress_tasks.values())
        self._progress_tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        if self._voice is not None:
            await self._voice.close()

    async def cancel_remote(self, job: JobResponse) -> bool:
        if job.kind is JobKind.VOICE_CLONE:
            return self._voice is not None and await self._voice.cancel(job.id)
        if not job.prompt_id:
            return False
        target = await self._comfyui.cancel_prompt(job.prompt_id)
        return target.value != "not_found"

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
