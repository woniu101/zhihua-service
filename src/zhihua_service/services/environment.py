import os
import sys
from pathlib import Path

from zhihua_service.schemas import EnvironmentStatusResponse

REQUIRED_MODELS = (
    (
        "diffusion_models/Qwen-Image_ComfyUI",
        "qwen_image_2512_fp8_e4m3fn.safetensors",
    ),
    (
        "diffusion_models/Qwen-Image-Edit_ComfyUI",
        "qwen_image_edit_2511_fp8mixed.safetensors",
    ),
    ("text_encoders", "qwen_2.5_vl_7b_fp8_scaled.safetensors"),
    ("vae", "qwen_image_vae.safetensors"),
    ("diffusion_models", "minimax_h3_fl2va_pruned_int8_convrot.safetensors"),
    ("diffusion_models", "minimax_h3_ref2va_pruned_int8_convrot.safetensors"),
    ("text_encoders", "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"),
    ("vae", "minimax_h3_video_vae_fp16.safetensors"),
    ("vae", "minimax_h3_audio_vae_fp32.safetensors"),
    ("loras", "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"),
    ("loras", "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"),
    ("SEEDVR2", "seedvr2_ema_3b_fp8_e4m3fn.safetensors"),
    ("SEEDVR2", "ema_vae_fp16.safetensors"),
)


def get_environment_status(comfyui_path: str) -> EnvironmentStatusResponse:
    torch_version: str | None = None
    cuda_available = False
    cuda_device_count = 0

    try:
        import torch

        torch_version = torch.__version__
        cuda_available = torch.cuda.is_available()
        cuda_device_count = torch.cuda.device_count()
    except ImportError:
        pass

    model_root = Path(comfyui_path) / "models"
    candidates = [
        (model_root / directory / filename, filename) for directory, filename in REQUIRED_MODELS
    ]
    available = [(path, filename) for path, filename in candidates if path.is_file()]
    public_links = sum(
        path.is_symlink() and str(path.resolve()).startswith("/model/") for path, _ in available
    )

    return EnvironmentStatusResponse(
        python_version=sys.version.split()[0],
        python_executable=sys.executable,
        torch_version=torch_version,
        cuda_available=cuda_available,
        cuda_device_count=cuda_device_count,
        comfyui_path=comfyui_path,
        comfyui_path_exists=os.path.isdir(comfyui_path),
        required_models=len(REQUIRED_MODELS),
        available_models=len(available),
        public_model_links=public_links,
        missing_models=[filename for path, filename in candidates if not path.is_file()],
    )
