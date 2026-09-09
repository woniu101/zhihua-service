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
            running = self._queue_size(payload.get("queue_running"))
            pending = self._queue_size(payload.get("queue_pending"))
            if running is None or pending is None:
                raise ValueError("ComfyUI queue response has an unsupported shape")
            return ComfyUIStatusResponse(
                connected=True,
                ready=True,
                base_url=self._base_url,
                queue_running=running,
                queue_pending=pending,
            )
        except (httpx.HTTPError, ValueError) as exc:
            return ComfyUIStatusResponse(
                connected=False,
                ready=False,
                base_url=self._base_url,
                detail=type(exc).__name__,
            )

    @staticmethod
    def _queue_size(value: Any) -> int | None:
        return len(value) if isinstance(value, list) else None
