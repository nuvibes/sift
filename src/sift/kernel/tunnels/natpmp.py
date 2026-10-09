# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking a VPN provider for a public port through the tunnel itself (NAT-PMP, RFC 6886).

Asked of `127.0.0.1`, relayed by the client; three tries over three and a half seconds, renewed."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

_VERSION = 0
_OP_PUBLIC_ADDRESS = 0
_OP_MAP_TCP = 2
#: A reply's opcode is its question's plus this.
_REPLY = 128
_PUBLIC_REPLY_SIZE = 12
_MAP_REPLY_SIZE = 16

#: The first try's wait, doubling on each later try.
FIRST_WAIT_SECONDS = 0.5
#: Tries before the provider is taken not to be answering.
TRIES = 3
LEASE_SECONDS = 60
#: Well inside the lease, so one lost renewal is not a lapse.
RENEW_EVERY_SECONDS = 45.0

#: The protocol's result codes in words for the log; none carries an address.
_RESULTS = {
    1: "unsupported version",
    2: "not authorized",
    3: "network failure",
    4: "out of resources",
    5: "unsupported opcode",
}


class NatPmpError(Exception):
    """The provider did not give a port: no reply, a refusal, or a reply that was not an answer."""


@dataclass(frozen=True, slots=True)
class Mapped:
    """What the provider gave: its public address, the public port and the seconds it keeps it."""

    public: str
    external_port: int
    lifetime: int

    def same_place(self, other: Mapped) -> bool:
        """Whether a guest would dial the same address and port for both; lifetime aside."""
        return (self.public, self.external_port) == (other.public, other.external_port)


class _Replies(asyncio.DatagramProtocol):
    """Every datagram that comes back, in order, for the one question being asked."""

    def __init__(self) -> None:
        self.received: asyncio.Queue[bytes] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: tuple[str | object, int]) -> None:
        self.received.put_nowait(data)

    def error_received(self, exc: Exception) -> None:
        # Windows reports an empty port as a reset on the next receive; treated as silence.
        return None


class NatPmp:
    """The questions Sift asks one tunnel's provider, through its client's loopback relay."""

    def __init__(
        self,
        port: int,
        *,
        host: str = "127.0.0.1",
        first_wait: float = FIRST_WAIT_SECONDS,
        tries: int = TRIES,
    ) -> None:
        self._where = (host, port)
        self._first_wait = first_wait
        self._tries = tries

    async def public_address(self) -> str:
        """The provider's public IPv4 address: its server's, which is what a guest dials."""
        reply = await self._ask(
            struct.pack("!BB", _VERSION, _OP_PUBLIC_ADDRESS), _OP_PUBLIC_ADDRESS, _PUBLIC_REPLY_SIZE
        )
        return str(ipaddress.IPv4Address(reply[8:12]))

    async def map_tcp(
        self, internal_port: int, external_requested: int = 0, lifetime: int = LEASE_SECONDS
    ) -> Mapped:
        """Ask for a public TCP port reaching `internal_port`; refusing a reply mapping nothing."""
        public = await self.public_address()
        question = struct.pack(
            "!BBHHHI", _VERSION, _OP_MAP_TCP, 0, internal_port, external_requested, lifetime
        )
        reply = await self._ask(question, _OP_MAP_TCP, _MAP_REPLY_SIZE)
        inside, external, granted = struct.unpack("!HHI", reply[8:16])
        if inside != internal_port or external == 0 or granted == 0:
            raise NatPmpError("the provider's answer maps nothing to the port that was asked for")
        return Mapped(public=public, external_port=external, lifetime=granted)

    async def _ask(self, question: bytes, opcode: int, size: int) -> bytes:
        loop = asyncio.get_running_loop()
        try:
            transport, replies = await loop.create_datagram_endpoint(
                _Replies, remote_addr=self._where
            )
        except OSError as exc:
            raise NatPmpError("no port to ask the provider from: this device refused one") from exc
        try:
            wait = self._first_wait
            for _try in range(self._tries):
                transport.sendto(question)
                reply = await self._answer(replies, opcode, size, wait)
                if reply is not None:
                    return reply
                wait *= 2
        finally:
            transport.close()
        raise NatPmpError("no answer from the provider")

    @staticmethod
    async def _answer(replies: _Replies, opcode: int, size: int, wait: float) -> bytes | None:
        """The reply within `wait`, or None; a non-answer is skipped, a refusal is final."""
        try:
            async with asyncio.timeout(wait):
                while True:
                    data = await replies.received.get()
                    if len(data) < size or data[0] != _VERSION or data[1] != _REPLY + opcode:
                        continue
                    (result,) = struct.unpack("!H", data[2:4])
                    if result != 0:
                        raise NatPmpError(f"the provider refused: {_RESULTS.get(result, result)}")
                    return data
        except TimeoutError:
            return None


#: Told the new answer when a renewal returns another address or port.
Moved = Callable[[Mapped], Awaitable[None]]
#: Told why a renewal failed, after it has stopped.
Failed = Callable[[NatPmpError], Awaitable[None]]


class Renewal:
    """Keeps one mapping alive while a swap is hosted, passing on moves and ending on a failure."""

    def __init__(
        self,
        client: NatPmp,
        internal_port: int,
        mapped: Mapped,
        *,
        on_moved: Moved,
        on_failed: Failed,
        every: float = RENEW_EVERY_SECONDS,
        lifetime: int = LEASE_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._internal_port = internal_port
        self._current = mapped
        self._on_moved = on_moved
        self._on_failed = on_failed
        self._every = every
        self._lifetime = lifetime
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None

    @property
    def current(self) -> Mapped:
        """What the provider gave most recently."""
        return self._current

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="natpmp-renewal")

    async def stop(self) -> None:
        """Stop renewing and let the lease lapse; never cancels the task it is called from."""
        task, self._task = self._task, None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            await self._sleep(self._every)
            try:
                fresh = await self._client.map_tcp(
                    self._internal_port, self._current.external_port, self._lifetime
                )
            except NatPmpError as exc:
                await self._on_failed(exc)
                return
            moved = not fresh.same_place(self._current)
            self._current = fresh
            if moved:
                await self._on_moved(fresh)


__all__ = [
    "FIRST_WAIT_SECONDS",
    "LEASE_SECONDS",
    "RENEW_EVERY_SECONDS",
    "TRIES",
    "Mapped",
    "NatPmp",
    "NatPmpError",
    "Renewal",
]
