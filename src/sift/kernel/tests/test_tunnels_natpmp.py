# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking a provider for a public port, against a fake that answers in the provider's own shapes.

Nothing here reaches a provider or leaves this device. The answerer is a UDP endpoint on loopback
that replies the way a real one was measured replying: twelve bytes for the public address,
sixteen for a mapping, the inside port echoed and a public port of its own choosing.
"""

from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncIterator, MutableMapping
from typing import Any

import pytest
from structlog.testing import capture_logs

from sift.kernel.tunnels import natpmp
from sift.kernel.tunnels.natpmp import Mapped, NatPmp, NatPmpError, Renewal

#: The provider's address in the fake's replies: a documentation address, never a real one.
_PUBLIC = "198.51.100.20"
_INSIDE = 41234
#: Short, so a question nobody answers costs the suite a fraction of a second.
_QUICK = 0.05


class _Provider(asyncio.DatagramProtocol):
    """Answers NAT-PMP the way the provider did: opcode 0 with the address, opcode 2 with the inside
    port echoed and a public port it chose. Told to drop, refuse, mumble or map elsewhere."""

    def __init__(self) -> None:
        self.questions: list[bytes] = []
        self.external_ports = [61000]
        self.drop = 0
        self.silent = False
        self.refuse = 0
        self.mumble_first = False
        self.mumble_only = False
        self.echo_inside: int | None = None
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, asyncio.DatagramTransport)
        self.transport = transport

    def datagram_received(self, data: bytes, addr: Any) -> None:
        self.questions.append(data)
        if self.silent:
            return
        if self.drop:
            self.drop -= 1
            return
        assert self.transport is not None
        if self.mumble_first or self.mumble_only:
            # Something that is not an answer: another opcode entirely.
            self.transport.sendto(struct.pack("!BBHI", 0, 129, 0, 7), addr)
            if self.mumble_only:
                return
        opcode = data[1]
        if opcode == 0:
            reply = struct.pack("!BBHI", 0, 128, self.refuse, 7) + bytes(
                int(part) for part in _PUBLIC.split(".")
            )
        else:
            _v, _o, _r, inside, _wanted, lifetime = struct.unpack("!BBHHHI", data)
            external = (
                self.external_ports.pop(0)
                if len(self.external_ports) > 1
                else (self.external_ports[0])
            )
            echoed = inside if self.echo_inside is None else self.echo_inside
            reply = struct.pack("!BBHIHHI", 0, 130, self.refuse, 7, echoed, external, lifetime)
        self.transport.sendto(reply, addr)


@pytest.fixture
async def provider() -> AsyncIterator[tuple[_Provider, int]]:
    loop = asyncio.get_running_loop()
    answerer = _Provider()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: answerer, local_addr=("127.0.0.1", 0)
    )
    try:
        yield answerer, int(transport.get_extra_info("sockname")[1])
    finally:
        transport.close()


def _client(port: int) -> NatPmp:
    return NatPmp(port, first_wait=_QUICK)


# --- the two questions ---------------------------------------------------------------------------


async def test_the_public_address_is_read_from_the_twelve_byte_reply(
    provider: tuple[_Provider, int],
) -> None:
    answerer, port = provider
    assert await _client(port).public_address() == _PUBLIC
    assert answerer.questions == [b"\x00\x00"]


async def test_a_mapping_asks_for_the_inside_port_and_no_public_port_in_particular(
    provider: tuple[_Provider, int],
) -> None:
    """(its inside port, 0), never the documented `1 0`, which maps the public port to inside
    port 1 where nothing listens. The provider chooses the public port, and that is what is
    advertised."""
    answerer, port = provider
    mapped = await _client(port).map_tcp(_INSIDE)

    asked = answerer.questions[-1]
    assert asked == struct.pack("!BBHHHI", 0, 2, 0, _INSIDE, 0, 60)
    assert len(asked) == 12
    assert mapped == Mapped(public=_PUBLIC, external_port=61000, lifetime=60)


async def test_a_lost_reply_is_asked_again(provider: tuple[_Provider, int]) -> None:
    answerer, port = provider
    answerer.drop = 1
    assert await _client(port).public_address() == _PUBLIC
    assert len(answerer.questions) == 2


async def test_a_provider_that_never_answers_is_asked_three_times_then_given_up_on(
    provider: tuple[_Provider, int],
) -> None:
    """A server that does not forward ports: known in three tries, not in the protocol's minute."""
    answerer, port = provider
    answerer.silent = True
    with pytest.raises(NatPmpError, match="no answer"):
        await _client(port).public_address()
    assert len(answerer.questions) == natpmp.TRIES == 3


async def test_a_refusal_is_final_and_not_asked_again(provider: tuple[_Provider, int]) -> None:
    answerer, port = provider
    answerer.refuse = 2
    with pytest.raises(NatPmpError, match="not authorized"):
        await _client(port).map_tcp(_INSIDE)
    assert len(answerer.questions) == 1


async def test_a_datagram_that_is_not_the_answer_is_passed_over(
    provider: tuple[_Provider, int],
) -> None:
    answerer, port = provider
    answerer.mumble_first = True
    assert await _client(port).public_address() == _PUBLIC


async def test_nothing_but_datagrams_that_are_not_the_answer_is_no_answer(
    provider: tuple[_Provider, int],
) -> None:
    """Believed would be worse than silence: a stray reply to another question is not an address."""
    answerer, port = provider
    answerer.mumble_only = True
    with pytest.raises(NatPmpError, match="no answer"):
        await NatPmp(port, first_wait=_QUICK, tries=1).public_address()


async def test_a_mapping_for_another_inside_port_is_not_advertised(
    provider: tuple[_Provider, int],
) -> None:
    """The provider honours the inside port it is asked for. One that answered for another would
    forward the public port to where nothing listens, and a token naming it would be a lie."""
    answerer, port = provider
    answerer.echo_inside = _INSIDE + 1
    with pytest.raises(NatPmpError, match="maps nothing"):
        await _client(port).map_tcp(_INSIDE)


async def test_a_port_that_cannot_be_asked_from_is_no_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()

    async def refuses(*_args: object, **_kwargs: object) -> None:
        raise OSError("no")

    monkeypatch.setattr(loop, "create_datagram_endpoint", refuses)
    with pytest.raises(NatPmpError, match="no port to ask"):
        await NatPmp(5351).public_address()


# --- keeping it ----------------------------------------------------------------------------------


class _Told:
    def __init__(self) -> None:
        self.moved: list[Mapped] = []
        self.failed: list[NatPmpError] = []
        self.slept: list[float] = []
        self.done = asyncio.Event()

    async def on_moved(self, mapped: Mapped) -> None:
        self.moved.append(mapped)

    async def on_failed(self, error: NatPmpError) -> None:
        self.failed.append(error)
        self.done.set()

    def sleeper(self, rounds: int) -> Any:
        """Records how long each wait asked for, and returns immediately, `rounds` times."""

        async def sleep(seconds: float) -> None:
            self.slept.append(seconds)
            if len(self.slept) > rounds:
                self.done.set()
                await asyncio.Event().wait()

        return sleep


async def test_a_mapping_is_renewed_every_45_seconds_asking_to_keep_its_port(
    provider: tuple[_Provider, int],
) -> None:
    """45 seconds against a 60-second lease, so one lost renewal is not a lapse. A renewal asks
    for the public port already held, which is how the protocol asks to keep it."""
    answerer, port = provider
    client = _client(port)
    first = await client.map_tcp(_INSIDE)
    told = _Told()
    renewal = Renewal(
        client,
        _INSIDE,
        first,
        on_moved=told.on_moved,
        on_failed=told.on_failed,
        sleep=told.sleeper(2),
    )
    renewal.start()
    await asyncio.wait_for(told.done.wait(), 5)
    await renewal.stop()

    assert told.slept[:2] == [45.0, 45.0]
    maps = [one for one in answerer.questions if one[1] == 2]
    assert maps[1:] == [struct.pack("!BBHHHI", 0, 2, 0, _INSIDE, 61000, 60)] * 2
    assert told.moved == [] and told.failed == []
    assert renewal.current.external_port == 61000


async def test_a_provider_that_moves_the_port_at_a_renewal_is_passed_on(
    provider: tuple[_Provider, int],
) -> None:
    """The answer is read afresh each time. A token already handed out names the old port, so the
    session is the thing that has to hear about the new one."""
    answerer, port = provider
    answerer.external_ports = [61000, 62000]
    client = _client(port)
    first = await client.map_tcp(_INSIDE)
    told = _Told()
    renewal = Renewal(
        client,
        _INSIDE,
        first,
        on_moved=told.on_moved,
        on_failed=told.on_failed,
        sleep=told.sleeper(1),
    )
    renewal.start()
    await asyncio.wait_for(told.done.wait(), 5)
    await renewal.stop()

    assert [one.external_port for one in told.moved] == [62000]
    assert renewal.current.external_port == 62000


async def test_a_failed_renewal_is_passed_on_and_the_renewing_stops(
    provider: tuple[_Provider, int],
) -> None:
    answerer, port = provider
    client = NatPmp(port, first_wait=_QUICK, tries=1)
    first = await client.map_tcp(_INSIDE)
    answerer.silent = True
    told = _Told()
    renewal = Renewal(
        client,
        _INSIDE,
        first,
        on_moved=told.on_moved,
        on_failed=told.on_failed,
        sleep=told.sleeper(5),
    )
    renewal.start()
    await asyncio.wait_for(told.done.wait(), 5)

    assert len(told.failed) == 1
    assert told.slept == [45.0], "it went on renewing after the provider stopped answering"
    await renewal.stop()


def test_the_cadence_sits_inside_the_lease() -> None:
    assert natpmp.RENEW_EVERY_SECONDS == 45.0
    assert natpmp.LEASE_SECONDS == 60
    assert natpmp.RENEW_EVERY_SECONDS < natpmp.LEASE_SECONDS


async def test_stopping_a_renewal_that_never_started_is_nothing() -> None:
    told = _Told()
    renewal = Renewal(
        NatPmp(5351),
        _INSIDE,
        Mapped(_PUBLIC, 61000, 60),
        on_moved=told.on_moved,
        on_failed=told.on_failed,
    )
    await renewal.stop()


def test_the_lifetime_is_not_part_of_where_a_guest_dials() -> None:
    assert Mapped(_PUBLIC, 61000, 60).same_place(Mapped(_PUBLIC, 61000, 42))
    assert not Mapped(_PUBLIC, 61000, 60).same_place(Mapped(_PUBLIC, 62000, 60))


def test_a_reset_from_a_port_with_nobody_on_it_is_silence_not_a_failure() -> None:
    """Windows reports an unreachable loopback port on the next receive, as an error. The try waits
    out its time and the next one asks again, exactly as for a lost datagram."""
    replies = natpmp._Replies()
    replies.error_received(ConnectionResetError())
    assert replies.received.empty()
    assert (replies.heard, replies.unreachable) == ("ConnectionResetError", True)


def test_windows_port_unreachable_is_nobody_there_and_another_error_is_not() -> None:
    """What the proactor hands a connected socket for an empty loopback port: a bare OSError
    carrying Windows' code 1234."""

    class Unreachable(OSError):
        winerror = 1234

    replies = natpmp._Replies()
    replies.error_received(Unreachable())
    assert (replies.heard, replies.unreachable) == ("Unreachable 1234", True)

    other = natpmp._Replies()
    other.error_received(PermissionError())
    assert (other.heard, other.unreachable) == ("PermissionError", False)


async def test_starting_a_renewal_twice_is_one_renewal() -> None:
    told = _Told()
    renewal = Renewal(
        NatPmp(5351),
        _INSIDE,
        Mapped(_PUBLIC, 61000, 60),
        on_moved=told.on_moved,
        on_failed=told.on_failed,
        sleep=told.sleeper(0),
    )
    renewal.start()
    first = renewal._task
    renewal.start()
    assert renewal._task is first
    await renewal.stop()


# --- the line each request writes ----------------------------------------------------------------


def _port_lines(logs: list[MutableMapping[str, Any]]) -> list[MutableMapping[str, Any]]:
    return [entry for entry in logs if entry["event"] == "tunnel.port"]


def _never_where(logs: list[MutableMapping[str, Any]]) -> None:
    """A line says what happened, never where a swap's host is reached."""
    assert not [entry for entry in logs if _PUBLIC in str(entry) or "61000" in str(entry)]


async def test_a_port_given_is_one_line_with_when_and_for_how_long(
    provider: tuple[_Provider, int],
) -> None:
    _answerer, port = provider
    with capture_logs() as logs:
        await NatPmp(port, first_wait=_QUICK, tunnel="Lisbon").map_tcp(_INSIDE)

    (line,) = _port_lines(logs)
    assert line["log_level"] == "info"
    assert line["tunnel"] == "Lisbon"
    assert line["renewal"] is False
    assert line["outcome"] == "given"
    assert line["lifetime"] == 60
    assert line["tries"] == 1
    assert line["asked_at"].endswith("+00:00") and "T" in line["asked_at"]
    assert isinstance(line["took_ms"], int) and line["took_ms"] >= 0
    _never_where(logs)


async def test_a_provider_that_never_answers_is_logged_as_the_gateway_after_its_tries(
    provider: tuple[_Provider, int],
) -> None:
    answerer, port = provider
    answerer.silent = True
    with capture_logs() as logs, pytest.raises(NatPmpError) as refused:
        await NatPmp(port, first_wait=_QUICK, tunnel="Lisbon").map_tcp(_INSIDE)

    assert (refused.value.hop, refused.value.kind) == (natpmp.GATEWAY, "no_answer")
    (line,) = _port_lines(logs)
    assert line["log_level"] == "warning"
    assert (line["outcome"], line["hop"], line["kind"]) == ("refused", "gateway", "no_answer")
    assert (line["step"], line["tries"], line["stray"]) == ("address", natpmp.TRIES, 0)
    assert line["result"] is None and line["result_name"] is None


async def test_a_refusal_is_logged_with_the_providers_code_and_its_name(
    provider: tuple[_Provider, int],
) -> None:
    answerer, port = provider
    answerer.refuse = 2
    with capture_logs() as logs, pytest.raises(NatPmpError):
        await _client(port).map_tcp(_INSIDE)

    (line,) = _port_lines(logs)
    assert (line["hop"], line["kind"]) == ("answer", "refused")
    assert (line["result"], line["result_name"]) == (2, "not authorized")
    assert line["tries"] == 1


async def test_a_code_the_protocol_does_not_name_is_logged_as_unknown(
    provider: tuple[_Provider, int],
) -> None:
    answerer, port = provider
    answerer.refuse = 9
    with capture_logs() as logs, pytest.raises(NatPmpError):
        await _client(port).map_tcp(_INSIDE)

    (line,) = _port_lines(logs)
    assert (line["result"], line["result_name"]) == (9, "unknown")


async def test_an_answer_mapping_nothing_is_logged_at_the_mapping_step(
    provider: tuple[_Provider, int],
) -> None:
    answerer, port = provider
    answerer.echo_inside = _INSIDE + 1
    with capture_logs() as logs, pytest.raises(NatPmpError):
        await _client(port).map_tcp(_INSIDE)

    (line,) = _port_lines(logs)
    assert (line["step"], line["hop"], line["kind"]) == ("map", "answer", "maps_nothing")
    _never_where(logs)


async def test_only_replies_to_other_questions_is_logged_as_the_answer_not_silence(
    provider: tuple[_Provider, int],
) -> None:
    """Something inside the tunnel replied, so the relay and the way to the gateway both work."""
    answerer, port = provider
    answerer.mumble_only = True
    with capture_logs() as logs, pytest.raises(NatPmpError):
        await NatPmp(port, first_wait=_QUICK, tries=2).map_tcp(_INSIDE)

    (line,) = _port_lines(logs)
    assert (line["hop"], line["kind"], line["stray"]) == ("answer", "not_an_answer", 2)


async def test_a_socket_this_device_refuses_is_logged_as_the_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()

    async def refuses(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("no")

    monkeypatch.setattr(loop, "create_datagram_endpoint", refuses)
    with capture_logs() as logs, pytest.raises(NatPmpError):
        await NatPmp(5351).map_tcp(_INSIDE)

    (line,) = _port_lines(logs)
    assert (line["hop"], line["kind"], line["heard"]) == ("socket", "no_socket", "PermissionError")
    assert line["tries"] == 0


async def test_a_relay_nobody_listens_on_is_logged_as_the_proxy() -> None:
    """A loopback port with nothing on it: the system says so, which silence from inside the
    tunnel never does, so a client that never bound its relay is told from a silent gateway."""
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
    )
    empty = int(transport.get_extra_info("sockname")[1])
    transport.close()

    with capture_logs() as logs, pytest.raises(NatPmpError) as refused:
        await NatPmp(empty, first_wait=_QUICK, tries=2).map_tcp(_INSIDE)

    assert (refused.value.hop, refused.value.kind) == (natpmp.PROXY, "relay_closed")
    (line,) = _port_lines(logs)
    assert line["heard"] in {"OSError 1234", "ConnectionResetError", "ConnectionRefusedError"}


async def test_a_relay_that_binds_after_the_first_try_is_heard_on_the_next() -> None:
    """On Windows a socket that heard "port unreachable" never delivers another datagram, so a try
    after one is asked from a fresh socket."""
    loop = asyncio.get_running_loop()
    probe, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
    )
    late = int(probe.get_extra_info("sockname")[1])
    probe.close()
    answerer = _Provider()
    bound: list[asyncio.DatagramTransport] = []

    async def bind_late() -> None:
        await asyncio.sleep(0.1)
        transport, _ = await loop.create_datagram_endpoint(
            lambda: answerer, local_addr=("127.0.0.1", late)
        )
        bound.append(transport)

    binding = asyncio.create_task(bind_late())
    try:
        address = await NatPmp(late, first_wait=0.2).public_address()
    finally:
        await binding
        for transport in bound:
            transport.close()

    assert address == _PUBLIC
    assert len(answerer.questions) == 1


async def test_every_renewal_writes_its_own_line_marked_as_one(
    provider: tuple[_Provider, int],
) -> None:
    _answerer, port = provider
    client = NatPmp(port, first_wait=_QUICK, tunnel="Lisbon")
    first = await client.map_tcp(_INSIDE)
    told = _Told()
    renewal = Renewal(
        client,
        _INSIDE,
        first,
        on_moved=told.on_moved,
        on_failed=told.on_failed,
        sleep=told.sleeper(2),
    )
    with capture_logs() as logs:
        renewal.start()
        await asyncio.wait_for(told.done.wait(), 5)
        await renewal.stop()

    lines = _port_lines(logs)
    assert len(lines) == 2
    assert all(line["renewal"] is True and line["outcome"] == "given" for line in lines)
    _never_where(logs)
