# SPDX-License-Identifier: AGPL-3.0-or-later
"""The listening socket opened again when an accept fails.

Python's Windows event loop closes a listening socket on the first accept that raises (a peer
that resets while the loop is busy is enough), and nothing opens it again: the process goes on
working while every connection is refused.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sift.kernel.log import get_logger

log = get_logger(__name__)

ACCEPT_FAILED = "Accept failed on a socket"
TRIES = 5

_server: Any = None


def rearm(server: Any) -> None:
    """Keep the uvicorn server whose listener is opened again."""
    global _server
    _server = server


def accept_failed(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> bool:
    """Whether the loop's failure is a closed listener; if so a new one is on its way."""
    if _server is None or not str(context.get("message", "")).startswith(ACCEPT_FAILED):
        return False
    log.warning("listener.accept_failed", error=repr(context.get("exception")))
    loop.create_task(listen_again(_server), name="listener.rearm")
    return True


async def listen_again(server: Any) -> bool:
    """A new listening socket on the server's address, the way uvicorn makes its first."""
    config = server.config

    def create_protocol(_loop: asyncio.AbstractEventLoop | None = None) -> asyncio.Protocol:
        return config.http_protocol_class(  # type: ignore[no-any-return]
            config=config,
            server_state=server.server_state,
            app_state=server.lifespan.state,
            _loop=_loop,
        )

    loop = asyncio.get_running_loop()
    for attempt in range(1, TRIES + 1):
        try:
            made = await loop.create_server(
                create_protocol,
                host=config.host,
                port=config.port,
                ssl=config.ssl,
                backlog=config.backlog,
            )
        except OSError as exc:
            log.warning("listener.rearm_failed", attempt=attempt, error=str(exc))
            await asyncio.sleep(attempt)
            continue
        server.servers.append(made)
        log.info("listener.rearmed", port=config.port, attempt=attempt)
        return True
    log.error("listener.gave_up", port=config.port)
    return False
