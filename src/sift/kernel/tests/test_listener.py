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


async def test_a_port_still_held_is_tried_again_and_then_given_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The closed socket may not have released its port yet: a short wait and another try."""
    tries: list[int] = []

    async def refuses(*_: Any, **__: Any) -> Any:
        tries.append(1)
        raise OSError(98, "address in use")

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "create_server", refuses)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _seconds: real_sleep(0))
    monkeypatch.setattr(listener, "TRIES", 2)
    server = uvicorn.Server(
        uvicorn.Config(_app, host="127.0.0.1", port=1, lifespan="off", log_config=None)
    )
    server.config.load()
    server.lifespan = server.config.lifespan_class(server.config)
    server.servers = []
    assert await listener.listen_again(server) is False
    assert len(tries) == 2
