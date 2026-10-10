# SPDX-License-Identifier: AGPL-3.0-or-later
"""The listener opened again after the loop closed it."""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest
import uvicorn

from sift.kernel import listener


async def _app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def _answers(port: int) -> bool:
    try:
        _, writer = await asyncio.open_connection("127.0.0.1", port)
    except OSError:
        return False
    writer.close()
    return True


@pytest.fixture
async def served() -> Any:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = uvicorn.Config(_app, host="127.0.0.1", port=port, lifespan="off", log_config=None)
    server = uvicorn.Server(config)
    # What `serve` does before `startup`.
    config.load()
    server.lifespan = config.lifespan_class(config)
    await server.startup()
    yield server, port
    await server.shutdown()


async def test_a_closed_listener_is_opened_again_and_answers(served: Any) -> None:
    server, port = served
    assert await _answers(port)
    server.servers[0].close()  # what the Windows loop does on an accept that raised
    await asyncio.sleep(0)
    assert not await _answers(port), "closed, so refused: the fault being guarded against"

    listener.rearm(server)
    loop = asyncio.get_running_loop()
    assert listener.accept_failed(
        loop, {"message": listener.ACCEPT_FAILED, "exception": OSError(64, "gone")}
    )
    for _ in range(50):
        await asyncio.sleep(0.05)
        if await _answers(port):
            break
    assert await _answers(port)
    assert len(server.servers) == 2


async def test_any_other_failure_is_left_to_the_default_handler() -> None:
    listener.rearm(object())
    assert not listener.accept_failed(asyncio.get_running_loop(), {"message": "something else"})
    listener.rearm(None)
    assert not listener.accept_failed(
        asyncio.get_running_loop(), {"message": listener.ACCEPT_FAILED}
    )
