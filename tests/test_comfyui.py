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


def test_find_node_types_reports_installed_and_missing_nodes() -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            node_type = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={node_type: {}} if node_type == "Installed" else {})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            installed = await ComfyUIClient(http_client, "http://comfy.test").find_node_types(
                {"Installed", "Missing"}
            )

        assert installed == {"Installed"}

    asyncio.run(run())


def test_find_node_types_returns_none_when_comfyui_is_offline() -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            installed = await ComfyUIClient(http_client, "http://comfy.test").find_node_types(
                {"Required"}
            )

        assert installed is None

    asyncio.run(run())
