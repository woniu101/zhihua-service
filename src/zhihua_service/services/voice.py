from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from zhihua_service.config import Settings
from zhihua_service.schemas import ArtifactManifest


class VoiceExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class VoiceExecution:
    process: asyncio.subprocess.Process
    output_path: Path
    relative_path: str
    request_path: Path
    error_path: Path


class IndexTtsExecutor:
    workflow_id = "indextts-2.5-v1"

    def __init__(self, settings: Settings) -> None:
        self._root = Path(settings.indextts_root).resolve()
        self._python = Path(settings.indextts_python).resolve()
        self._models = Path(settings.indextts_model_path).resolve()
        self._input_root = Path(settings.comfyui_input_path).resolve()
        self._output_root = Path(settings.comfyui_output_path).resolve()
        self._executions: dict[str, VoiceExecution] = {}

    @property
    def available(self) -> bool:
        return (
            self._root.is_dir()
            and self._python.is_file()
            and (self._models / "config.yaml").is_file()
        )

    @property
    def detail(self) -> str:
        if self.available:
            return "IndexTTS 2.5 runtime and checkpoints are available"
        return "IndexTTS 2.5 project, Python environment, or checkpoints are missing"

    async def start(
        self,
        job_id: str,
        project_id: str,
        scene_id: str,
        parameters: dict[str, object],
    ) -> str:
        if not self.available:
            raise VoiceExecutionError("indextts_unavailable", self.detail)
        text = parameters.get("text")
        remote_file = parameters.get("referenceAudioFile")
        language = parameters.get("language", "ZH")
        if not isinstance(text, str) or not text.strip() or len(text) > 12_000:
            raise VoiceExecutionError(
                "voice_text_invalid",
                "voice text must be 1 to 12000 characters",
            )
        if not isinstance(remote_file, str):
            raise VoiceExecutionError("voice_reference_missing", "referenceAudioFile is required")
        reference = self._resolve_input(remote_file)
        if not reference.is_file():
            raise VoiceExecutionError("voice_reference_missing", "reference audio is unavailable")
        if language not in {"ZH", "EN"}:
            raise VoiceExecutionError("voice_language_invalid", "language must be ZH or EN")

        relative_dir = Path("audio") / "zhihua" / project_id / scene_id / job_id
        output_dir = (self._output_root / relative_dir).resolve()
        output_dir.relative_to(self._output_root)
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / "narration.wav"
        request_path = output_dir / "request.json"
        error_path = output_dir / "stderr.log"
        request_path.write_text(
            json.dumps(
                {
                    "root": str(self._root),
                    "model_path": str(self._models),
                    "reference_audio": str(reference),
                    "text": text.strip(),
                    "language": language,
                    "output": str(output),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        worker = Path(__file__).resolve().parents[1] / "index_tts_worker.py"
        error_stream = error_path.open("wb")
        try:
            process = await asyncio.create_subprocess_exec(
                str(self._python),
                str(worker),
                "--request",
                str(request_path),
                cwd=self._root,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=error_stream,
            )
        finally:
            error_stream.close()
        self._executions[job_id] = VoiceExecution(
            process=process,
            output_path=output,
            relative_path=output.relative_to(self._output_root).as_posix(),
            request_path=request_path,
            error_path=error_path,
        )
        return f"voice:{process.pid}"

    def poll(self, job_id: str) -> ArtifactManifest | None:
        execution = self._executions.get(job_id)
        if execution is None:
            raise VoiceExecutionError(
                "voice_process_lost",
                "voice process is not attached to this service",
            )
        if execution.process.returncode is None:
            return None
        self._executions.pop(job_id, None)
        with suppress(OSError):
            execution.request_path.unlink()
        if execution.process.returncode != 0:
            detail = self._read_error(execution.error_path)
            raise VoiceExecutionError("indextts_execution_failed", detail)
        with suppress(OSError):
            execution.error_path.unlink()
        if not execution.output_path.is_file() or execution.output_path.stat().st_size == 0:
            raise VoiceExecutionError(
                "voice_output_missing",
                "IndexTTS did not create an audio file",
            )
        digest = hashlib.sha256()
        with execution.output_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        media_type = mimetypes.guess_type(execution.output_path.name)[0] or "audio/wav"
        return ArtifactManifest(
            artifact_id="narration-0",
            kind="audio",
            filename=execution.output_path.name,
            media_type=media_type,
            size_bytes=execution.output_path.stat().st_size,
            sha256=digest.hexdigest(),
            download_path=f"/api/v1/jobs/{job_id}/artifacts/narration-0",
        )

    async def cancel(self, job_id: str) -> bool:
        execution = self._executions.pop(job_id, None)
        if execution is None:
            return False
        if execution.process.returncode is None:
            execution.process.terminate()
            try:
                await asyncio.wait_for(execution.process.wait(), timeout=10)
            except TimeoutError:
                execution.process.kill()
                await execution.process.wait()
        for path in (execution.output_path, execution.request_path, execution.error_path):
            with suppress(OSError):
                path.unlink()
        return True

    async def close(self) -> None:
        for job_id in list(self._executions):
            await self.cancel(job_id)

    def relative_path(self, job_id: str) -> str:
        execution = self._executions.get(job_id)
        if execution is None:
            raise VoiceExecutionError(
                "voice_process_lost",
                "voice process is not attached to this service",
            )
        return execution.relative_path

    def _resolve_input(self, remote_file: str) -> Path:
        prefix = "zhihua-inputs/"
        if not remote_file.startswith(prefix):
            raise VoiceExecutionError("voice_reference_invalid", "reference audio path is invalid")
        name = remote_file.removeprefix(prefix)
        if not name or Path(name).name != name:
            raise VoiceExecutionError("voice_reference_invalid", "reference audio path is invalid")
        candidate = (self._input_root / name).resolve()
        candidate.relative_to(self._input_root)
        return candidate

    @staticmethod
    def _read_error(path: Path) -> str:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            return " | ".join(lines[-8:])[:2000] or "IndexTTS process failed"
        except OSError:
            return "IndexTTS process failed"
