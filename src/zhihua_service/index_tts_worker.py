"""Small process boundary around the official IndexTTS 2.5 Python API.

This module intentionally owns no queue or HTTP concerns.  The service starts it with the
IndexTTS virtual environment so model dependencies stay out of zhihua-service itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))

    root = Path(request["root"]).resolve()
    sys.path.insert(0, str(root))
    from indextts.infer_v2 import IndexTTS2  # type: ignore[import-not-found]

    tts = IndexTTS2(
        cfg_path=str(Path(request["model_path"]) / "config.yaml"),
        model_dir=str(Path(request["model_path"])),
        use_bf16=True,
    )
    tts.infer(
        spk_audio_prompt=request["reference_audio"],
        text=request["text"],
        lang=request.get("language", "ZH"),
        output_path=request["output"],
    )


if __name__ == "__main__":
    main()
