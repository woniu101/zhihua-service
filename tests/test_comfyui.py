import asyncio

import httpx

from zhihua_service.services.comfyui import ComfyUIClient


def test_comfyui_status_reports_queue_sizes() -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/queue"
            return httpx.Response(
                200,
                json={"queue_running": [[1, "running"]], "queue_pending": []},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            status = await ComfyUIClient(http_client, "http://comfy.test").get_status()

        assert status.connected is True
        assert status.queue_running == 1
        assert status.queue_pending == 0

    asyncio.run(run())


def test_comfyui_status_handles_connection_failure() -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            status = await ComfyUIClient(http_client, "http://comfy.test").get_status()

        assert status.connected is False
        assert status.detail == "ConnectError"

    asyncio.run(run())
