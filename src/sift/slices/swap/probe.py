# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tunnel's own speed, with nothing of the swap in it.

One device hosts a sink behind its tunnel's mapped port; the other pushes bytes to it through its
own tunnel for a few seconds. What the sink counts is the path's figure, to put beside a swap's.
A sink takes only a connection that opens with the probe's secret, and closes itself when its
seconds are up.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sift.kernel.log import get_logger
from sift.slices.swap import lock

if TYPE_CHECKING:
    from sift.slices.swap.live import Egress, Hoster

log = get_logger(__name__)

SECRET_BYTES = 32
#: The longest a sink stays up, and the most connections a send opens.
SECONDS_CAP = 120
STREAMS_CAP = 16
_CHUNK = bytes(1 << 20)
_OPEN_SECONDS = 10.0
_GRACE_SECONDS = 5.0


@dataclass
class Sink:
    """One hosted sink: where it can be reached and what it has counted."""

    tunnel_id: str
    secret: bytes
    seconds: int
    server: asyncio.Server
    port: int
    address: str = ""
    external_port: int = 0
    received: int = 0
    connections: int = 0
    refused: int = 0
    first_byte_at: float | None = None
    last_byte_at: float | None = None
    done: bool = False
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)

    @property
    def rate_bps(self) -> int | None:
        if self.first_byte_at is None or self.last_byte_at is None:
            return None
        seconds = self.last_byte_at - self.first_byte_at
        return None if seconds <= 0 else int(self.received * 8 / seconds)


@dataclass(frozen=True, slots=True)
class Sent:
    """What a send pushed through this device's tunnel."""

    sent: int
    seconds: float
    streams: int
    failed: int

    @property
    def rate_bps(self) -> int:
        return 0 if self.seconds <= 0 else int(self.sent * 8 / self.seconds)


class Probes:
    """The sinks this device hosts, one per tunnel at a time, and the sends it makes."""

    def __init__(
        self, hoster: Hoster, egress: Egress, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.hoster = hoster
        self.egress = egress
        self.clock = clock
        self.sinks: dict[str, Sink] = {}

    async def host(self, tunnel_id: str, master_key: bytes, seconds: int) -> Sink:
        """Host a sink behind `tunnel_id` for `seconds`. Raises what the tunnel manager raises."""
        await self.stop(tunnel_id)
        seconds = max(1, min(seconds, SECONDS_CAP))
        secret = os.urandom(SECRET_BYTES)
        server: asyncio.Server | None = None

        async def taken(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await self._drain(sink, reader, writer)

        server = await asyncio.start_server(taken, "127.0.0.1", 0)
        port = int(server.sockets[0].getsockname()[1])
        sink = Sink(tunnel_id, secret, seconds, server, port)
        try:
            hosting = await self.hoster.host_on(tunnel_id, target_port=port, master_key=master_key)
        except BaseException:
            server.close()
            raise
        sink.address, sink.external_port = hosting.public_ipv4, hosting.external_port
        self.sinks[tunnel_id] = sink
        sink.tasks.add(asyncio.ensure_future(self._close_when_up(sink)))
        log.info("swap.probe_hosted", tunnel=tunnel_id, seconds=seconds)
        return sink

    def reading(self, tunnel_id: str) -> Sink | None:
        return self.sinks.get(tunnel_id)

    async def stop(self, tunnel_id: str) -> None:
        sink = self.sinks.pop(tunnel_id, None)
        if sink is None:
            return
        sink.done = True
        sink.server.close()
        for task in list(sink.tasks):
            task.cancel()
        with contextlib.suppress(Exception):
            await self.hoster.stop_hosting(tunnel_id)
        log.info("swap.probe_stopped", tunnel=tunnel_id, received=sink.received)

    async def _close_when_up(self, sink: Sink) -> None:
        await asyncio.sleep(sink.seconds + _GRACE_SECONDS)
        sink.done = True
        sink.server.close()
        with contextlib.suppress(Exception):
            await self.hoster.stop_hosting(sink.tunnel_id)
        log.info("swap.probe_done", tunnel=sink.tunnel_id, received=sink.received)

    async def _drain(
        self, sink: Sink, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            try:
                opening = await asyncio.wait_for(reader.readexactly(SECRET_BYTES), _OPEN_SECONDS)
            except (TimeoutError, asyncio.IncompleteReadError, OSError):
                sink.refused += 1
                return
            if opening != sink.secret:
                sink.refused += 1
                return
            sink.connections += 1
            while not sink.done:
                data = await reader.read(len(_CHUNK))
                if not data:
                    return
                now = self.clock()
                if sink.first_byte_at is None:
                    sink.first_byte_at = now
                sink.last_byte_at = now
                sink.received += len(data)
        finally:
            writer.close()

    async def send(
        self,
        tunnel_id: str,
        address: str,
        port: int,
        secret: bytes,
        seconds: int,
        streams: int,
    ) -> Sent:
        """Push bytes to a sink at `address:port` through `tunnel_id` on `streams` connections."""
        seconds = max(1, min(seconds, SECONDS_CAP))
        streams = max(1, min(streams, STREAMS_CAP))
        async with self.egress.through(tunnel_id) as proxy:
            started = self.clock()
            results = await asyncio.gather(
                *(self._push(proxy, address, port, secret, seconds) for _ in range(streams)),
                return_exceptions=True,
            )
            elapsed = self.clock() - started
        sent = sum(one for one in results if isinstance(one, int))
        failed = 0
        for one in results:
            if not isinstance(one, int):
                failed += 1
                log.info("swap.probe_stream_failed", tunnel=tunnel_id, why=type(one).__name__)
        log.info("swap.probe_sent", tunnel=tunnel_id, sent=sent, streams=streams, failed=failed)
        return Sent(sent, elapsed, streams, failed)

    async def _push(
        self, proxy: str | None, address: str, port: int, secret: bytes, seconds: int
    ) -> int:
        _reader, writer = await lock.open_through(proxy, address, port, _OPEN_SECONDS)
        sent = 0
        try:
            writer.write(secret)
            await writer.drain()
            until = self.clock() + seconds
            while self.clock() < until:
                writer.write(_CHUNK)
                await writer.drain()
                sent += len(_CHUNK)
        except (ConnectionError, OSError):
            return sent
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        return sent
