"""Small process boundary around the official IndexTTS 2.5 Python API.

This module intentionally owns no queue or HTTP concerns.  The service starts it with the
IndexTTS virtual environment so model dependencies stay out of zhihua-service itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _use_soundfile_audio_io() -> None:
    """Keep IndexTTS WAV I/O independent from torchaudio's optional TorchCodec."""
    import soundfile
    import torch
    import torchaudio

    def load_audio(
        uri: str | Path,
        frame_offset: int = 0,
        num_frames: int = -1,
        normalize: bool = True,
        channels_first: bool = True,
        **_: object,
    ) -> tuple[torch.Tensor, int]:
        del normalize
        frames = num_frames if num_frames >= 0 else -1
        samples, sample_rate = soundfile.read(
            str(uri),
            start=frame_offset,
            frames=frames,
            dtype="float32",
            always_2d=True,
        )
        waveform = torch.from_numpy(samples.copy())
        if channels_first:
            waveform = waveform.transpose(0, 1)
        return waveform, sample_rate

    def save_audio(
        uri: str | Path,
        src: torch.Tensor,
        sample_rate: int,
        channels_first: bool = True,
        **_: object,
    ) -> None:
        samples = src.detach().to(device="cpu", dtype=torch.float32).numpy()
        if channels_first:
            samples = samples.T
        soundfile.write(str(uri), samples, sample_rate, subtype="PCM_16")

    torchaudio.load = load_audio
    torchaudio.save = save_audio


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))

    root = Path(request["root"]).resolve()
    sys.path.insert(0, str(root))
    _use_soundfile_audio_io()
    from indextts.infer_v2_5 import IndexTTS2  # type: ignore[import-not-found]

    tts = IndexTTS2(
        cfg_path=str(Path(request["model_path"]) / "config.yaml"),
        model_dir=str(Path(request["model_path"])),
        use_bf16=True,
        use_cuda_kernel=False,
        use_deepspeed=False,
        use_accel=False,
        use_qwen_emo=False,
    )
    tts.infer(
        spk_audio_prompt=request["reference_audio"],
        text=request["text"],
        lang={"ZH": "zh", "EN": "en"}.get(request.get("language", "ZH"), "zh"),
        output_path=request["output"],
        verbose=False,
        text_normalization=True,
    )


if __name__ == "__main__":
    main()
