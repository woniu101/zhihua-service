from typing import Any

import httpx

from zhihua_service.schemas import ComfyUIStatusResponse


class ComfyUIClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def get_status(self) -> ComfyUIStatusResponse:
        try:
            response = await self._client.get(f"{self._base_url}/queue")
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            return ComfyUIStatusResponse(
                connected=True,
                base_url=self._base_url,
                queue_running=self._queue_size(payload.get("queue_running")),
                queue_pending=self._queue_size(payload.get("queue_pending")),
            )
        except (httpx.HTTPError, ValueError) as exc:
            return ComfyUIStatusResponse(
                connected=False,
                base_url=self._base_url,
                detail=type(exc).__name__,
            )

    @staticmethod
    def _queue_size(value: Any) -> int | None:
        return len(value) if isinstance(value, list) else None

