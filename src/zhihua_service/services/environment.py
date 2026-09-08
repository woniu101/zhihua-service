import os
import sys

from zhihua_service.schemas import EnvironmentStatusResponse


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

    return EnvironmentStatusResponse(
        python_version=sys.version.split()[0],
        python_executable=sys.executable,
        torch_version=torch_version,
        cuda_available=cuda_available,
        cuda_device_count=cuda_device_count,
        comfyui_path=comfyui_path,
        comfyui_path_exists=os.path.isdir(comfyui_path),
    )

