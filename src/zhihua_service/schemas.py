from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "zhihua-service"
    version: str


class VersionResponse(BaseModel):
    service_version: str
    api_version: Literal["v1"] = "v1"


class CapabilitiesResponse(BaseModel):
    api_version: Literal["v1"] = "v1"
    features: list[str]


class EnvironmentStatusResponse(BaseModel):
    python_version: str
    python_executable: str
    torch_version: str | None
    cuda_available: bool
    cuda_device_count: int
    comfyui_path: str
    comfyui_path_exists: bool


class ComfyUIStatusResponse(BaseModel):
    connected: bool
    base_url: str
    queue_running: int | None = None
    queue_pending: int | None = None
    detail: str | None = None


class ReadyResponse(BaseModel):
    ready: bool
    comfyui: ComfyUIStatusResponse

