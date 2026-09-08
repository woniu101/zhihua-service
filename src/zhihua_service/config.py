import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    host: str
    port: int
    api_prefix: str
    comfyui_base_url: str
    comfyui_timeout_seconds: float
    comfyui_path: str


@lru_cache
def get_settings() -> Settings:
    return Settings(
        environment=os.getenv("ZHIHUA_ENVIRONMENT", "production"),
        host=os.getenv("ZHIHUA_HOST", "127.0.0.1"),
        port=int(os.getenv("ZHIHUA_PORT", "8000")),
        api_prefix="/api/v1",
        comfyui_base_url=os.getenv(
            "ZHIHUA_COMFYUI_BASE_URL",
            "http://127.0.0.1:8188",
        ).rstrip("/"),
        comfyui_timeout_seconds=float(
            os.getenv("ZHIHUA_COMFYUI_TIMEOUT_SECONDS", "5")
        ),
        comfyui_path=os.getenv("ZHIHUA_COMFYUI_PATH", "/root/ComfyUI"),
    )

