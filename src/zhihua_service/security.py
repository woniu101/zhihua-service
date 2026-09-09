import re
import secrets
from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from zhihua_service.config import Settings

_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = _VERSION_PATTERN.fullmatch(value)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def client_version_is_supported(client_version: str, minimum: str) -> bool:
    client = _version_tuple(client_version)
    required = _version_tuple(minimum)
    return client is not None and required is not None and client >= required


async def require_service_auth(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    settings: Settings = request.app.state.settings
    if not settings.authentication_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "service_token_not_configured"},
        )
    scheme, _, credential = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(
        credential,
        settings.service_token,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_service_token"},
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_protocol_headers(
    request: Request,
    x_zhihua_api_version: Annotated[str | None, Header()] = None,
    x_zhihua_client_version: Annotated[str | None, Header()] = None,
) -> None:
    await require_service_auth(request, request.headers.get("authorization"))
    settings: Settings = request.app.state.settings
    if x_zhihua_api_version != "v1":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "api_version_incompatible", "required": "v1"},
        )
    if not x_zhihua_client_version or not client_version_is_supported(
        x_zhihua_client_version,
        settings.minimum_client_version,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "client_version_incompatible",
                "minimum": settings.minimum_client_version,
            },
        )
