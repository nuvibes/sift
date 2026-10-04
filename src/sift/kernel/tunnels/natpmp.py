# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking a VPN provider for a public port, through the tunnel itself (NAT-PMP, RFC 6886).

A swap is hosted on a port the VPN provider opens on ITS server's public address and forwards down
the tunnel to Sift. Nothing on the home router changes and the home address is never in the answer,
which is the point. The asking is NAT-PMP: a twelve-byte UDP question to the provider's gateway
inside the tunnel, carried there by the tunnel client's own `[UDPProxyTunnel]` section from a
loopback port on this device (`process.Listener`). So every question here goes to `127.0.0.1` and
nowhere else.

What the protocol asks and answers, through a real provider:

- **The public address** (opcode 0): two bytes out; twelve back (version, opcode 128, a result
  code, the seconds since the gateway's epoch, and the four bytes of the address).
- **A TCP mapping** (opcode 2): twelve bytes out (the inside port, the public port wanted, the
  lifetime in seconds); sixteen back (the same header, the inside port, the public port GIVEN and
  the lifetime granted). The provider honours the inside port it is asked for and chooses the public
  one itself, so Sift asks for `(its inside port, 0)` and advertises what comes back. The
  documentation's own example, `1 0`, maps the public port to inside port 1, where nothing listens.
- **A lease runs out**, so it is renewed: every 45 seconds against a 60-second lifetime, so one
  lost renewal is not a lapse. Each answer is read afresh (a provider may hand back another port),
  and a renewal that fails ends the hosting.

A reply comes back in tens of milliseconds. A question is asked three times, waiting half a second,
then one, then two (the protocol's doubling, cut short at three tries): a lost datagram costs half a
second, and a provider that never answers (a server that does not forward ports) is known to be
one in three and a half seconds rather than the protocol's full minute.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

#: The protocol's version. There has only ever been one.
_VERSION = 0
_OP_PUBLIC_ADDRESS = 0
_OP_MAP_TCP = 2
#: A reply's opcode is its question's plus this.
_REPLY = 128
_PUBLIC_REPLY_SIZE = 12
_MAP_REPLY_SIZE = 16

#: How long the first try waits for a reply. Each later try waits twice as long as the one before.
FIRST_WAIT_SECONDS = 0.5
#: How many times a question is asked before the provider is taken not to be answering.
TRIES = 3
#: How long a mapping is asked to last.
LEASE_SECONDS = 60
#: How often a mapping is renewed: well inside its lifetime, so one lost renewal is not a lapse.
RENEW_EVERY_SECONDS = 45.0

#: The protocol's result codes, in words for the log line a failed hosting leaves. None of them
#: carries an address.
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
    """What the provider gave: its public address, the public port that reaches the inside port
    Sift asked for, and how many seconds the provider will keep it."""

    public: str
    external_port: int
    lifetime: int

    def same_place(self, other: Mapped) -> bool:
        """Whether a guest would dial the same address and port for both. The lifetime is not
        part of that: a provider may count it down between renewals."""
        return (self.public, self.external_port) == (other.public, other.external_port)


class _Replies(asyncio.DatagramProtocol):
    """Every datagram that comes back, in order, for the one question being asked."""

    def __init__(self) -> None:
        self.received: asyncio.Queue[bytes] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: tuple[str | object, int]) -> None:
        self.received.put_nowait(data)

    def error_received(self, exc: Exception) -> None:
        # Nothing on the port yet, which Windows reports on the NEXT receive as a reset. The same as
        # silence for this purpose: the try waits out its time and the next one asks again.
        return None


class NatPmp:
    """The questions Sift asks one tunnel's provider, through the loopback port its client relays.

    `host` and the waits are parameters for the tests, which answer from a fake on loopback; the
    application always asks `127.0.0.1` on the port the tunnel was started with.
    """

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
        """Ask for a public TCP port reaching `internal_port` inside the tunnel.

        `external_requested` is 0 ("any") when a hosting starts, and the port already held when
        it is renewed, which is how the protocol asks to keep it. Whatever the provider gives back
        is the answer either way; a reply for another inside port, or one granting nothing, is not
        a mapping and is refused rather than advertised.
        """
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
        """The reply to this question within `wait` seconds, or None.

        A datagram that is not an answer to it (too short, another version, another opcode) is
        passed over rather than believed. A refusal IS an answer, and a final one: asking again
        would be asked the same way.
        """
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


#: Told the new answer when a renewal comes back with a different address or port.
Moved = Callable[[Mapped], Awaitable[None]]
#: Told why when a renewal fails. The renewal has stopped by the time it is called.
Failed = Callable[[NatPmpError], Awaitable[None]]


class Renewal:
    """Keeps one mapping alive for as long as a swap is hosted.

    Every `every` seconds it asks for the port it holds again. An answer naming another address or
    port replaces the one held and is passed on (`on_moved`); a failed renewal is passed on
    (`on_failed`) and ends the renewal, because a mapping that is not being renewed lapses on its
    own within the minute and a swap should not find that out from a silent guest.
    """

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
        """Stop renewing. The lease then lapses on its own inside a minute, which is the intent.

        Called from inside the renewal itself when a failure ends the hosting, and that call must
        not cancel the very task it is running in.
        """
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
