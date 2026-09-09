import os
from dataclasses import dataclass
from functools import lru_cache

DEFAULT_WORKFLOWS = (
    "h3-fl2v-turbo-v1",
    "h3-ref2va-turbo-v1",
    "seedvr2-1080p-v1",
)


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    host: str
    port: int
    api_prefix: str
    comfyui_base_url: str
    comfyui_timeout_seconds: float
    comfyui_path: str
    service_token: str
    jobs_database_path: str
    minimum_client_version: str
    workflow_manifest_version: str
    model_manifest_version: str
    allowed_workflows: tuple[str, ...]

    @property
    def authentication_configured(self) -> bool:
        return len(self.service_token) >= 32


@lru_cache
def get_settings() -> Settings:
    workflows = tuple(
        item.strip()
        for item in os.getenv(
            "ZHIHUA_ALLOWED_WORKFLOWS",
            ",".join(DEFAULT_WORKFLOWS),
        ).split(",")
        if item.strip()
    )
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
        service_token=os.getenv("ZHIHUA_SERVICE_TOKEN", ""),
        jobs_database_path=os.getenv(
            "ZHIHUA_JOBS_DATABASE_PATH",
            "/root/.local/share/zhihua-service/jobs.sqlite3",
        ),
        minimum_client_version=os.getenv(
            "ZHIHUA_MINIMUM_CLIENT_VERSION",
            "0.1.0",
        ),
        workflow_manifest_version=os.getenv(
            "ZHIHUA_WORKFLOW_MANIFEST_VERSION",
            "h3-workflows-2026.09.08",
        ),
        model_manifest_version=os.getenv(
            "ZHIHUA_MODEL_MANIFEST_VERSION",
            "public-models-2026.09.08",
        ),
        allowed_workflows=workflows,
    )
