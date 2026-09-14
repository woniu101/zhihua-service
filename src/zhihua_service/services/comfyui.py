import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
import websockets

from zhihua_service.schemas import ComfyUIStatusResponse


class ComfyUIError(RuntimeError):
    code = "comfyui_error"


class ComfyUIUnavailableError(ComfyUIError):
    code = "comfyui_unavailable"


class ComfyUIPromptRejectedError(ComfyUIError):
    code = "comfyui_prompt_rejected"


class ComfyUICancelTarget(str, Enum):
    RUNNING = "running"
    PENDING = "pending"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class ComfyUIOutput:
    node_id: str
    filename: str
    subfolder: str
    storage_type: str


@dataclass(frozen=True, slots=True)
class ComfyUIHistory:
    state: str
    outputs: tuple[ComfyUIOutput, ...] = ()
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ComfyUIProgressEvent:
    prompt_id: str
    current: int
    total: int
    node_id: str | None = None


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

    async def find_node_types(self, node_types: set[str]) -> set[str] | None:
        """Return installed ComfyUI node types, or None while ComfyUI is offline."""
        if not node_types:
            return set()

        async def probe(node_type: str) -> tuple[str, bool]:
            response = await self._client.get(f"{self._base_url}/object_info/{node_type}")
            if response.status_code >= 500:
                response.raise_for_status()
            if response.status_code >= 400:
                return node_type, False
            payload = response.json()
            return node_type, isinstance(payload, dict) and node_type in payload

        try:
            results = await asyncio.gather(*(probe(node_type) for node_type in node_types))
        except (httpx.HTTPError, ValueError):
            return None
        return {node_type for node_type, installed in results if installed}

    async def submit_prompt(
        self,
        prompt: dict[str, Any],
        *,
        client_id: str,
    ) -> str:
        try:
            response = await self._client.post(
                f"{self._base_url}/prompt",
                json={"prompt": prompt, "client_id": client_id},
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ComfyUIUnavailableError(type(exc).__name__) from exc
        if response.status_code >= 500:
            raise ComfyUIUnavailableError(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ComfyUIPromptRejectedError(self._response_detail(response)) from None
        try:
            payload = response.json()
            prompt_id = payload["prompt_id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ComfyUIPromptRejectedError("response has no prompt_id") from exc
        if not isinstance(prompt_id, str) or not prompt_id:
            raise ComfyUIPromptRejectedError("response has an invalid prompt_id")
        return prompt_id

    async def get_history(self, prompt_id: str) -> ComfyUIHistory:
        try:
            response = await self._client.get(f"{self._base_url}/history/{prompt_id}")
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ComfyUIUnavailableError(type(exc).__name__) from exc
        if response.status_code >= 500:
            raise ComfyUIUnavailableError(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ComfyUIError(f"history returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ComfyUIError("history returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ComfyUIError("history response must be an object")
        entry = payload.get(prompt_id)
        if entry is None:
            return ComfyUIHistory(state="pending")
        if not isinstance(entry, dict):
            raise ComfyUIError("history entry must be an object")

        status = entry.get("status", {})
        if not isinstance(status, dict):
            status = {}
        status_text = str(status.get("status_str", "")).lower()
        completed = status.get("completed") is True
        if status_text in {"error", "failed"}:
            return ComfyUIHistory(
                state="failed",
                detail=self._history_error_detail(status),
            )
        if not completed and status_text not in {"success", "completed"}:
            return ComfyUIHistory(state="pending")

        outputs = self._parse_outputs(entry.get("outputs"))
        return ComfyUIHistory(state="completed", outputs=tuple(outputs))

    async def stream_progress(
        self,
        *,
        client_id: str,
        prompt_id: str,
    ) -> AsyncIterator[ComfyUIProgressEvent]:
        """Yield measured ComfyUI sampler progress for a submitted prompt."""
        websocket_url = self._websocket_url(client_id)
        try:
            async with websockets.connect(
                websocket_url,
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                max_size=2 * 1024 * 1024,
            ) as socket:
                async for message in socket:
                    event = self.parse_progress_message(message)
                    if event is not None and event.prompt_id == prompt_id:
                        yield event
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ComfyUIUnavailableError(type(exc).__name__) from exc

    async def cancel_prompt(self, prompt_id: str) -> ComfyUICancelTarget:
        """Remove a queued prompt or interrupt the active ComfyUI execution."""
        try:
            response = await self._client.get(f"{self._base_url}/queue")
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ComfyUIUnavailableError(type(exc).__name__) from exc
        if response.status_code >= 500:
            raise ComfyUIUnavailableError(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ComfyUIError(f"queue returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ComfyUIError("queue returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ComfyUIError("queue response must be an object")

        if prompt_id in self._queue_prompt_ids(payload.get("queue_pending")):
            await self._post_control("/queue", {"delete": [prompt_id]})
            return ComfyUICancelTarget.PENDING
        if prompt_id in self._queue_prompt_ids(payload.get("queue_running")):
            await self._post_control("/interrupt")
            return ComfyUICancelTarget.RUNNING
        return ComfyUICancelTarget.NOT_FOUND

    async def _post_control(self, path: str, payload: dict[str, Any] | None = None) -> None:
        try:
            response = await self._client.post(f"{self._base_url}{path}", json=payload)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ComfyUIUnavailableError(type(exc).__name__) from exc
        if response.status_code >= 500:
            raise ComfyUIUnavailableError(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ComfyUIError(f"control request returned HTTP {response.status_code}")

    @staticmethod
    def _parse_outputs(value: Any) -> list[ComfyUIOutput]:
        if not isinstance(value, dict):
            return []
        found: list[ComfyUIOutput] = []
        for node_id, node_output in value.items():
            if not isinstance(node_output, dict):
                continue
            for items in node_output.values():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
                        continue
                    found.append(
                        ComfyUIOutput(
                            node_id=str(node_id),
                            filename=item["filename"],
                            subfolder=(
                                item.get("subfolder")
                                if isinstance(item.get("subfolder"), str)
                                else ""
                            ),
                            storage_type=(
                                item.get("type")
                                if isinstance(item.get("type"), str)
                                else "output"
                            ),
                        )
                    )
        return found

    @staticmethod
    def _history_error_detail(status: dict[str, Any]) -> str:
        messages = status.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                if isinstance(message, list) and len(message) >= 2:
                    details = message[1]
                    if isinstance(details, dict):
                        text = details.get("exception_message")
                        if isinstance(text, str) and text:
                            return text[:500]
        return "ComfyUI execution failed"

    @staticmethod
    def _response_detail(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"HTTP {response.status_code}"
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return error["message"][:500]
        return f"HTTP {response.status_code}"

    @staticmethod
    def _queue_size(value: Any) -> int | None:
        return len(value) if isinstance(value, list) else None

    def _websocket_url(self, client_id: str) -> str:
        parts = urlsplit(self._base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        query = f"clientId={quote(client_id, safe='')}"
        return urlunsplit((scheme, parts.netloc, f"{parts.path.rstrip('/')}/ws", query, ""))

    @staticmethod
    def parse_progress_message(message: str | bytes) -> ComfyUIProgressEvent | None:
        if isinstance(message, bytes):
            try:
                message = message.decode("utf-8")
            except UnicodeDecodeError:
                return None
        try:
            payload = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("type") != "progress":
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        prompt_id = data.get("prompt_id")
        current = data.get("value")
        total = data.get("max")
        if (
            not isinstance(prompt_id, str)
            or not prompt_id
            or not isinstance(current, int)
            or isinstance(current, bool)
            or not isinstance(total, int)
            or isinstance(total, bool)
            or current < 0
            or total <= 0
        ):
            return None
        node_id = data.get("node")
        return ComfyUIProgressEvent(
            prompt_id=prompt_id,
            current=min(current, total),
            total=total,
            node_id=str(node_id) if node_id is not None else None,
        )

    @staticmethod
    def _queue_prompt_ids(value: Any) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {
            entry[1]
            for entry in value
            if isinstance(entry, list)
            and len(entry) > 1
            and isinstance(entry[1], str)
            and entry[1]
        }
