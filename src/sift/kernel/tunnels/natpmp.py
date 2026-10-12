# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking a VPN provider for a public port through the tunnel itself (NAT-PMP, RFC 6886).

Asked of `127.0.0.1`, relayed by the client; three tries over three and a half seconds, renewed.
Each request is one `tunnel.port` line: when, how long, and the answer's lifetime or the hop where
it died; never the address or the port, which say where a swap's host is reached."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import struct
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sift.kernel.log import get_logger

log = get_logger(__name__)

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

#: Where a question died, nearest first: Sift's own socket, the client's loopback relay, the
#: provider's gateway inside the tunnel, or the answer that came back.
SOCKET = "socket"
PROXY = "proxy"
GATEWAY = "gateway"
ANSWER = "answer"

#: Windows' code for "port unreachable", which a loopback port with nothing on it answers with.
_PORT_UNREACHABLE = 1234


def _nobody_there(exc: Exception) -> bool:
    """Whether the system says nothing listens where the datagram went."""
    if isinstance(exc, ConnectionRefusedError | ConnectionResetError):
        return True
    return getattr(exc, "winerror", None) == _PORT_UNREACHABLE


def _named(exc: Exception) -> str:
    """An error as the log line names it: its class, and Windows' own code where it has one."""
    code = getattr(exc, "winerror", None)
    return type(exc).__name__ if code is None else f"{type(exc).__name__} {code}"


class NatPmpError(Exception):
    """The provider did not give a port: no reply, a refusal, or a reply that was not an answer.

    `kind` and `hop` say how and where, for the log; `result` is the provider's code, if any."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "no_answer",
        hop: str = GATEWAY,
        result: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.hop = hop
        self.result = result


@dataclass(slots=True)
class _Trace:
    """What one request met on its way, for its log line."""

    step: str = "address"
    tries: int = 0
    stray: int = 0
    heard: str | None = None


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
        self.stray = 0
        #: The last error the system reported on the socket, named for the log.
        self.heard: str | None = None
        self.unreachable = False

    def datagram_received(self, data: bytes, addr: tuple[str | object, int]) -> None:
        self.received.put_nowait(data)

    def error_received(self, exc: Exception) -> None:
        # Windows reports an empty port on the next receive: silence for the try, but remembered.
        self.heard = _named(exc)
        self.unreachable = self.unreachable or _nobody_there(exc)


class NatPmp:
    """The questions Sift asks one tunnel's provider, through its client's loopback relay."""

    def __init__(
        self,
        port: int,
        *,
        host: str = "127.0.0.1",
        first_wait: float = FIRST_WAIT_SECONDS,
        tries: int = TRIES,
        tunnel: str = "",
    ) -> None:
        self._where = (host, port)
        self._first_wait = first_wait
        self._tries = tries
        #: The tunnel's name, for the log line; never its configuration.
        self._tunnel = tunnel

    async def public_address(self) -> str:
        """The provider's public IPv4 address: its server's, which is what a guest dials."""
        return await self._public_address(_Trace())

    async def _public_address(self, trace: _Trace) -> str:
        reply = await self._ask(
            struct.pack("!BB", _VERSION, _OP_PUBLIC_ADDRESS),
            _OP_PUBLIC_ADDRESS,
            _PUBLIC_REPLY_SIZE,
            trace,
        )
        return str(ipaddress.IPv4Address(reply[8:12]))

    async def map_tcp(
        self,
        internal_port: int,
        external_requested: int = 0,
        lifetime: int = LEASE_SECONDS,
        *,
        renewing: bool = False,
    ) -> Mapped:
        """Ask for a public TCP port reaching `internal_port`; refusing a reply mapping nothing.

        Logged as one `tunnel.port` line, answered or refused."""
        asked_at = time.time()
        started = time.monotonic()
        trace = _Trace()
        try:
            answer = await self._map(internal_port, external_requested, lifetime, trace)
        except NatPmpError as exc:
            log.warning(
                "tunnel.port",
                tunnel=self._tunnel,
                renewal=renewing,
                asked_at=_when(asked_at),
                took_ms=_since(started),
                outcome="refused",
                hop=exc.hop,
                kind=exc.kind,
                step=trace.step,
                tries=trace.tries,
                result=exc.result,
                result_name=None if exc.result is None else _RESULTS.get(exc.result, "unknown"),
                heard=trace.heard,
                stray=trace.stray,
            )
            raise
        log.info(
            "tunnel.port",
            tunnel=self._tunnel,
            renewal=renewing,
            asked_at=_when(asked_at),
            took_ms=_since(started),
            outcome="given",
            tries=trace.tries,
            lifetime=answer.lifetime,
        )
        return answer

    async def _map(
        self, internal_port: int, external_requested: int, lifetime: int, trace: _Trace
    ) -> Mapped:
        public = await self._public_address(trace)
        trace.step = "map"
        question = struct.pack(
            "!BBHHHI", _VERSION, _OP_MAP_TCP, 0, internal_port, external_requested, lifetime
        )
        reply = await self._ask(question, _OP_MAP_TCP, _MAP_REPLY_SIZE, trace)
        inside, external, granted = struct.unpack("!HHI", reply[8:16])
        if inside != internal_port or external == 0 or granted == 0:
            raise NatPmpError(
                "the provider's answer maps nothing to the port that was asked for",
                kind="maps_nothing",
                hop=ANSWER,
            )
        return Mapped(public=public, external_port=external, lifetime=granted)

    async def _ask(self, question: bytes, opcode: int, size: int, trace: _Trace) -> bytes:
        trace.tries = 0
        trace.stray = 0
        transport, replies = await self._open(trace)
        try:
            wait = self._first_wait
            for _try in range(self._tries):
                if replies.unreachable:
                    # A Windows socket that has heard "unreachable" never reads again.
                    _close(transport, replies, trace)
                    transport, replies = await self._open(trace)
                transport.sendto(question)
                trace.tries += 1
                reply = await self._answer(replies, opcode, size, wait)
                if reply is not None:
                    return reply
                wait *= 2
        finally:
            _close(transport, replies, trace)
        if replies.unreachable:
            # The relay is on this device, so the system can say nothing listens there.
            raise NatPmpError(
                "no answer from the provider: nothing listens on the tunnel's relay",
                kind="relay_closed",
                hop=PROXY,
            )
        if trace.stray:
            raise NatPmpError("no answer from the provider", kind="not_an_answer", hop=ANSWER)
        raise NatPmpError("no answer from the provider", kind="no_answer", hop=GATEWAY)

    async def _open(self, trace: _Trace) -> tuple[asyncio.DatagramTransport, _Replies]:
        loop = asyncio.get_running_loop()
        try:
            return await loop.create_datagram_endpoint(_Replies, remote_addr=self._where)
        except OSError as exc:
            trace.heard = _named(exc)
            raise NatPmpError(
                "no port to ask the provider from: this device refused one",
                kind="no_socket",
                hop=SOCKET,
            ) from exc

    @staticmethod
    async def _answer(replies: _Replies, opcode: int, size: int, wait: float) -> bytes | None:
        """The reply within `wait`, or None; a non-answer is skipped, a refusal is final."""
        try:
            async with asyncio.timeout(wait):
                while True:
                    data = await replies.received.get()
                    if len(data) < size or data[0] != _VERSION or data[1] != _REPLY + opcode:
                        replies.stray += 1
                        continue
                    (result,) = struct.unpack("!H", data[2:4])
                    if result != 0:
                        raise NatPmpError(
                            f"the provider refused: {_RESULTS.get(result, result)}",
                            kind="refused",
                            hop=ANSWER,
                            result=result,
                        )
                    return data
        except TimeoutError:
            return None


def _close(transport: asyncio.DatagramTransport, replies: _Replies, trace: _Trace) -> None:
    """Close one socket, keeping what it met for the request's line; twice is once."""
    trace.stray += replies.stray
    replies.stray = 0
    trace.heard = replies.heard or trace.heard
    transport.close()


def _when(epoch: float) -> str:
    """A moment as the log's own timestamps write it, to the millisecond."""
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="milliseconds")


def _since(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


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
                    self._internal_port,
                    self._current.external_port,
                    self._lifetime,
                    renewing=True,
                )
            except NatPmpError as exc:
                await self._on_failed(exc)
                return
            moved = not fresh.same_place(self._current)
            self._current = fresh
            if moved:
                await self._on_moved(fresh)


__all__ = [
    "ANSWER",
    "FIRST_WAIT_SECONDS",
    "GATEWAY",
    "LEASE_SECONDS",
    "PROXY",
    "RENEW_EVERY_SECONDS",
    "SOCKET",
    "TRIES",
    "Mapped",
    "NatPmp",
    "NatPmpError",
    "Renewal",
]
