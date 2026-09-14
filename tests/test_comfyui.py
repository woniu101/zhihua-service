import asyncio

import httpx

from zhihua_service.services.comfyui import ComfyUICancelTarget, ComfyUIClient


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


def test_parse_progress_message_accepts_only_measured_prompt_events() -> None:
    event = ComfyUIClient.parse_progress_message(
        '{"type":"progress","data":{"prompt_id":"prompt-1","value":7,"max":20,"node":"42"}}'
    )
    assert event is not None
    assert event.prompt_id == "prompt-1"
    assert event.current == 7
    assert event.total == 20
    assert event.node_id == "42"

    assert ComfyUIClient.parse_progress_message('{"type":"executing","data":{}}') is None
    assert ComfyUIClient.parse_progress_message(
        '{"type":"progress","data":{"prompt_id":"prompt-1","value":1,"max":0}}'
    ) is None


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


def test_cancel_prompt_deletes_pending_prompt() -> None:
    async def run() -> None:
        requests: list[tuple[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = request.read().decode() if request.method == "POST" else ""
            requests.append((f"{request.method} {request.url.path}", payload))
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={"queue_running": [], "queue_pending": [[7, "prompt-pending", {}]]},
                )
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            result = await ComfyUIClient(http_client, "http://comfy.test").cancel_prompt(
                "prompt-pending"
            )

        assert result == ComfyUICancelTarget.PENDING
        assert requests[1][0] == "POST /queue"
        assert '"delete":["prompt-pending"]' in str(requests[1][1]).replace(" ", "")

    asyncio.run(run())


def test_cancel_prompt_interrupts_running_prompt() -> None:
    async def run() -> None:
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(f"{request.method} {request.url.path}")
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={"queue_running": [[9, "prompt-running", {}]], "queue_pending": []},
                )
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            result = await ComfyUIClient(http_client, "http://comfy.test").cancel_prompt(
                "prompt-running"
            )

        assert result == ComfyUICancelTarget.RUNNING
        assert paths == ["GET /queue", "POST /interrupt"]

    asyncio.run(run())


def test_cancel_prompt_does_not_interrupt_an_unrelated_job() -> None:
    async def run() -> None:
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(f"{request.method} {request.url.path}")
            return httpx.Response(
                200,
                json={"queue_running": [[3, "someone-else", {}]], "queue_pending": []},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            result = await ComfyUIClient(http_client, "http://comfy.test").cancel_prompt("missing")

        assert result == ComfyUICancelTarget.NOT_FOUND
        assert paths == ["GET /queue"]

    asyncio.run(run())
