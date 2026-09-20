import asyncio
import json
import sys
from dataclasses import replace

import pytest

from zhihua_service.config import get_settings
from zhihua_service.services.voice import IndexTtsExecutor, VoiceExecutionError


def test_index_tts_availability_requires_runtime_and_checkpoint(tmp_path) -> None:
    settings = replace(
        get_settings(),
        indextts_root=str(tmp_path / "index-tts"),
        indextts_python=sys.executable,
        indextts_model_path=str(tmp_path / "index-tts" / "checkpoints"),
        comfyui_input_path=str(tmp_path / "inputs"),
        comfyui_output_path=str(tmp_path / "outputs"),
    )
    executor = IndexTtsExecutor(settings)
    assert executor.available is False
    (tmp_path / "index-tts" / "checkpoints").mkdir(parents=True)
    (tmp_path / "index-tts" / "checkpoints" / "config.yaml").write_text("model: test")
    assert executor.available is True


def test_index_tts_executor_builds_isolated_request_and_audio_manifest(
    tmp_path,
    monkeypatch,
) -> None:
    async def run() -> None:
        root = tmp_path / "index-tts"
        models = root / "checkpoints"
        inputs = tmp_path / "inputs"
        outputs = tmp_path / "outputs"
        models.mkdir(parents=True)
        inputs.mkdir()
        (models / "config.yaml").write_text("model: test")
        (inputs / "voice.wav").write_bytes(b"reference")
        runtime_python = tmp_path / "index-env" / "bin" / "python"
        runtime_python.parent.mkdir(parents=True)
        runtime_python.write_bytes(b"python")
        runtime_site_packages = tmp_path / "index-env" / "lib" / "python3.10" / "site-packages"
        runtime_site_packages.mkdir(parents=True)
        settings = replace(
            get_settings(),
            indextts_root=str(root),
            indextts_python=str(runtime_python),
            indextts_model_path=str(models),
            comfyui_input_path=str(inputs),
            comfyui_output_path=str(outputs),
        )

        class FakeProcess:
            pid = 42
            returncode = 0

        async def fake_subprocess(*args, **kwargs):
            assert str(runtime_site_packages) in kwargs["env"]["PYTHONPATH"]
            request_index = args.index("--request") + 1
            request = json.loads(open(args[request_index], encoding="utf-8").read())
            with open(request["output"], "wb") as stream:
                stream.write(b"RIFF-fake-audio")
            return FakeProcess()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
        executor = IndexTtsExecutor(settings)
        prompt_id = await executor.start(
            "job-1",
            "project-1",
            "paragraph-1",
            {"referenceAudioFile": "zhihua-inputs/voice.wav", "text": "测试旁白"},
        )
        relative = executor.relative_path("job-1")
        artifact = executor.poll("job-1")
        assert prompt_id == "voice:42"
        assert relative.endswith("narration.wav")
        assert artifact is not None
        assert artifact.kind == "audio"
        assert artifact.size_bytes == len(b"RIFF-fake-audio")

    asyncio.run(run())


def test_index_tts_rejects_untrusted_reference_path(tmp_path) -> None:
    async def run() -> None:
        root = tmp_path / "index-tts"
        models = root / "checkpoints"
        models.mkdir(parents=True)
        (models / "config.yaml").write_text("model: test")
        settings = replace(
            get_settings(),
            indextts_root=str(root),
            indextts_python=sys.executable,
            indextts_model_path=str(models),
            comfyui_input_path=str(tmp_path / "inputs"),
            comfyui_output_path=str(tmp_path / "outputs"),
        )
        with pytest.raises(VoiceExecutionError, match="path is invalid"):
            await IndexTtsExecutor(settings).start(
                "job-1",
                "project-1",
                "paragraph-1",
                {"referenceAudioFile": "../../secret.wav", "text": "测试"},
            )

    asyncio.run(run())
