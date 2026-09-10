import hashlib
import mimetypes
import os
import re
import uuid
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from zhihua_service.schemas import InputUploadResponse
from zhihua_service.security import require_protocol_headers

router = APIRouter(
    prefix="/inputs",
    tags=["inputs"],
    dependencies=[Depends(require_protocol_headers)],
)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".webm"}
INPUT_ID_PATTERN = re.compile(r"^[a-f0-9]{32}\.(?:jpg|jpeg|png|webp|mp4|mov|webm)$")


def _valid_signature(path: Path, extension: str) -> bool:
    with path.open("rb") as stream:
        header = stream.read(16)
    if extension in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if extension == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == ".webp":
        return header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    if extension in {".mp4", ".mov"}:
        return len(header) >= 12 and header[4:8] == b"ftyp"
    if extension == ".webm":
        return header.startswith(b"\x1a\x45\xdf\xa3")
    return False


@router.post("", response_model=InputUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_input(
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
) -> InputUploadResponse:
    original_filename = Path(unquote(filename)).name
    extension = Path(original_filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"code": "input_type_not_supported"},
        )
    maximum = request.app.state.settings.maximum_input_bytes
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > maximum:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "input_too_large", "maximum_bytes": maximum},
        )

    root = Path(request.app.state.settings.comfyui_input_path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    input_id = f"{uuid.uuid4().hex}{extension}"
    final_path = root / input_id
    temporary_path = root / f".{input_id}.upload"
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with temporary_path.open("xb") as stream:
            async for chunk in request.stream():
                size_bytes += len(chunk)
                if size_bytes > maximum:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail={"code": "input_too_large", "maximum_bytes": maximum},
                    )
                stream.write(chunk)
                digest.update(chunk)
        if size_bytes == 0 or not _valid_signature(temporary_path, extension):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail={"code": "input_content_invalid"},
            )
        os.replace(temporary_path, final_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    media_type = mimetypes.guess_type(original_filename)[0] or "application/octet-stream"
    return InputUploadResponse(
        input_id=input_id,
        remote_file=f"zhihua-inputs/{input_id}",
        original_filename=original_filename,
        media_type=media_type,
        size_bytes=size_bytes,
        sha256=digest.hexdigest(),
    )


@router.delete("/{input_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_input(request: Request, input_id: str) -> None:
    if not INPUT_ID_PATTERN.fullmatch(input_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "input_not_found"},
        )
    root = Path(request.app.state.settings.comfyui_input_path).resolve()
    candidate = (root / input_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "input_not_found"},
        ) from exc
    candidate.unlink(missing_ok=True)
