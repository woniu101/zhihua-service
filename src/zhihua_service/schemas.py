import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(StrictModel):
    status: Literal["ok"] = "ok"
    service: str = "zhihua-service"
    version: str


class VersionResponse(StrictModel):
    service_version: str
    api_version: Literal["v1"] = "v1"
    minimum_client_version: str
    workflow_manifest_version: str
    model_manifest_version: str


class CapabilitiesResponse(StrictModel):
    api_version: Literal["v1"] = "v1"
    authentication_required: bool = True
    authentication_configured: bool
    features: list[str]
    workflows: list[str]
    available_workflows: list[str]
    job_kinds: list[str]
    job_statuses: list[str]
    result_manifest_version: Literal["1"] = "1"


class EnvironmentStatusResponse(StrictModel):
    python_version: str
    python_executable: str
    torch_version: str | None
    cuda_available: bool
    cuda_device_count: int
    comfyui_path: str
    comfyui_path_exists: bool
    required_models: int
    available_models: int
    public_model_links: int
    missing_models: list[str]


class ComfyUIStatusResponse(StrictModel):
    connected: bool
    ready: bool = False
    base_url: str
    queue_running: int | None = None
    queue_pending: int | None = None
    detail: str | None = None


class ReadyResponse(StrictModel):
    ready: bool
    comfyui: ComfyUIStatusResponse


class HandshakeRequest(StrictModel):
    client_version: str = Field(min_length=1, max_length=40)
    supported_api_versions: list[str] = Field(min_length=1, max_length=8)
    workflow_manifest_versions: list[str] = Field(default_factory=list, max_length=16)
    model_manifest_versions: list[str] = Field(default_factory=list, max_length=16)


class HandshakeResponse(StrictModel):
    compatible: bool
    reasons: list[str]
    service_version: str
    selected_api_version: str | None
    minimum_client_version: str
    workflow_manifest_version: str
    model_manifest_version: str
    capabilities: list[str]


class JobKind(str, Enum):
    IMAGE_GENERATION = "image_generation"
    IMAGE_EDIT = "image_edit"
    VIDEO_CANDIDATE = "video_candidate"
    VIDEO_REFERENCE_REMAKE = "video_reference_remake"
    VIDEO_UPSCALE = "video_upscale"


class JobStatus(str, Enum):
    QUEUED = "queued"
    WAITING_FOR_COMPUTE = "waiting_for_compute"
    PREPARING = "preparing"
    UPLOADING = "uploading"
    RUNNING = "running"
    UPSCALING = "upscaling"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class JobCreateRequest(StrictModel):
    client_request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    project_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    scene_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    kind: JobKind
    workflow_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 16 * 1024:
            raise ValueError("parameters must be 16 KiB or smaller")
        sensitive_fragments = ("token", "secret", "password", "private_key", "api_key")
        for key in value:
            lowered = key.lower()
            if any(fragment in lowered for fragment in sensitive_fragments):
                raise ValueError("parameters must not contain credentials")
        return value


class ArtifactManifest(StrictModel):
    artifact_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    kind: Literal["video", "image", "audio", "metadata"]
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=127)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    download_path: str = Field(
        pattern=r"^/api/v1/jobs/[A-Za-z0-9._:-]+/artifacts/[A-Za-z0-9._:-]+$"
    )


class ResultManifest(StrictModel):
    schema_version: Literal["1"] = "1"
    job_id: str
    workflow_id: str
    prompt_id: str | None = None
    created_at: datetime
    artifacts: list[ArtifactManifest] = Field(default_factory=list)


class JobResponse(StrictModel):
    id: str
    client_request_id: str
    project_id: str
    scene_id: str
    kind: JobKind
    workflow_id: str
    status: JobStatus
    prompt_id: str | None = None
    progress: float = Field(ge=0, le=1)
    error_code: str | None = None
    error_message: str | None = None
    status_detail: str | None = None
    created_at: datetime
    updated_at: datetime
    result_manifest: ResultManifest | None = None


class JobListResponse(StrictModel):
    jobs: list[JobResponse]
    total: int


class QueueStatusResponse(StrictModel):
    active: int
    queued: int
    terminal: int
    by_status: dict[str, int]


class JobCancelResponse(StrictModel):
    id: str
    status: JobStatus


class InputUploadResponse(StrictModel):
    input_id: str
    remote_file: str
    original_filename: str
    media_type: str
    size_bytes: int
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
