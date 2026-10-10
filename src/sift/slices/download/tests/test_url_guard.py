# SPDX-License-Identifier: AGPL-3.0-or-later
"""The SSRF guard. The reject list is the control, and it grows with every evasion found."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import pytest

from sift.slices.download import url_guard
from sift.slices.download.url_guard import UrlRejected, check_url, confine_to, guard_url, next_hop

_REAL_GETADDRINFO = socket.getaddrinfo

# What each named host resolves to, so a test never depends on real DNS. A literal IP resolves to
# itself; a name resolves to whatever this map says.
_DNS = {
    "localhost": ["127.0.0.1"],
    "public.example": ["93.184.216.34"],
    "private.example": ["10.0.0.5"],
    "mixed.example": ["93.184.216.34", "10.0.0.5"],
    "start.example": ["93.184.216.34"],
    "middle.example": ["93.184.216.34"],
}


def _fake_getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        if host not in _DNS:
            raise socket.gaierror(f"no such host: {host}") from None
        addresses = _DNS[host]
    return [(0, 0, 0, "", (address, 0)) for address in addresses]


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)


async def _terminal(_url: str) -> None:
    """A follower that says every URL is a final destination: no redirects in these tests."""
    return None


# Every one of these must be refused. The list is the security control.
_REJECTED = [
    "file:///etc/passwd",
    # A resolvable, public host, so only the scheme check can refuse these. Otherwise an
    # unresolvable host would refuse them first and the scheme guard would go untested.
    "ftp://public.example/x",
    "gopher://public.example/x",
    "data:text/plain,hi",
    "http://127.0.0.1:8080/",
    "http://localhost/",
    "http://10.0.0.42/",
    "http://192.168.1.1/",
    "http://172.16.0.5/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",  # an IPv4 address wearing an IPv6 wrapper
    "http://private.example/",  # a name that resolves to a private address
    "http://mixed.example/",  # resolves to one public and one private address
]


@pytest.mark.parametrize("url", _REJECTED, ids=_REJECTED)
async def test_the_guard_refuses_every_forbidden_url(url: str) -> None:
    with pytest.raises(UrlRejected):
        await guard_url(url, follow=_terminal)


_ACCEPTED = [
    "http://public.example/watch/1",
    "https://public.example/watch/1",
]


@pytest.mark.parametrize("url", _ACCEPTED, ids=_ACCEPTED)
async def test_the_guard_allows_ordinary_public_urls(url: str) -> None:
    await guard_url(url, follow=_terminal)  # does not raise


async def test_an_unresolvable_host_is_refused() -> None:
    with pytest.raises(UrlRejected, match="could not be found"):
        await guard_url("http://nowhere.invalid/", follow=_terminal)


@pytest.mark.parametrize("code", [socket.EAI_AGAIN, 11002])
async def test_a_resolver_that_says_try_later_is_a_wait_not_a_bad_address(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    def later(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        raise socket.gaierror(code, "temporary failure in name resolution")

    monkeypatch.setattr(socket, "getaddrinfo", later)
    with pytest.raises(url_guard.LookupFailedForNow, match="network may be down"):
        await guard_url("http://public.example/", follow=_terminal)


async def test_a_name_not_found_with_no_way_out_is_a_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline, every name is "not found": with no route out, that says nothing of the address."""
    monkeypatch.setattr(url_guard, "_has_a_route", lambda: False)
    with pytest.raises(url_guard.LookupFailedForNow):
        await guard_url("http://nowhere.invalid/", follow=_terminal)


async def test_the_name_is_looked_up_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    asked_on: list[str] = []

    def lookup(host: str, *args: object, **kwargs: object) -> list[tuple[object, ...]]:
        asked_on.append(threading.current_thread().name)
        return _fake_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    await guard_url("http://public.example/", follow=_terminal)

    assert asked_on and threading.main_thread().name not in asked_on


def test_this_machine_with_a_network_has_a_way_out() -> None:
    assert url_guard._has_a_route()


async def test_a_url_with_no_host_is_refused() -> None:
    with pytest.raises(UrlRejected, match="web address"):
        await guard_url("http:///just/a/path", follow=_terminal)


async def test_a_public_url_that_redirects_to_a_private_one_is_refused() -> None:
    async def follow(url: str) -> str | None:
        return "http://127.0.0.1/" if url == "http://public.example/x" else None

    with pytest.raises(UrlRejected, match="private or local"):
        await guard_url("http://public.example/x", follow=follow)


async def test_a_multi_hop_chain_ending_private_is_refused() -> None:
    chain = {
        "http://start.example/": "http://middle.example/",
        "http://middle.example/": "http://10.0.0.9/",
    }

    async def follow(url: str) -> str | None:
        return chain.get(url)

    with pytest.raises(UrlRejected, match="private or local"):
        await guard_url("http://start.example/", follow=follow)


async def test_a_chain_that_never_settles_is_refused() -> None:
    async def follow(_url: str) -> str | None:
        return "http://public.example/loop"

    with pytest.raises(UrlRejected, match="redirects too many times"):
        await guard_url("http://public.example/x", follow=follow, max_hops=3)


async def test_a_refusal_logs_a_redacted_event_with_no_url(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        url_guard, "security_event", lambda kind, **fields: events.append({"kind": kind, **fields})
    )

    with pytest.raises(UrlRejected):
        await guard_url("http://10.0.0.42/secret-panel", follow=_terminal)

    assert events, "an SSRF refusal must record a security event"
    blob = repr(events)
    assert "10.0.0.42" not in blob
    assert "secret-panel" not in blob


def test_confine_allows_a_file_inside_the_folder(tmp_path: Path) -> None:
    inside = tmp_path / "a" / "b.mp4"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"x")
    assert confine_to(tmp_path, inside) == inside.resolve()


def test_confine_refuses_a_file_that_escapes_the_folder(tmp_path: Path) -> None:
    escaping = tmp_path / "sub" / ".." / ".." / "outside.mp4"
    with pytest.raises(UrlRejected, match="outside its folder"):
        confine_to(tmp_path / "sub", escaping)


# --- through a tunnel: nothing is resolved on this machine ---------------------------------------


def test_through_a_tunnel_a_name_is_never_asked_of_this_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *_a, **_k: asked.append(host))
    check_url("https://private.example/x", here=False)
    check_url("https://93.184.216.34/x", here=False)
    assert asked == []


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.42/",
        "http://2130706433/",  # the loopback address spelled as one number
        "http://localhost/",
        "http://admin.localhost./",
        "ftp://public.example/x",
        "http:///just/a/path",
    ],
)
def test_through_a_tunnel_a_literal_or_local_address_is_still_refused(url: str) -> None:
    with pytest.raises(UrlRejected):
        check_url(url, here=False)


async def test_a_walk_through_a_tunnel_judges_each_hop_without_resolving_it() -> None:
    async def follow(url: str) -> str | None:
        return "http://10.0.0.9/" if url == "http://private.example/" else None

    with pytest.raises(UrlRejected, match="private or local"):
        await guard_url("http://private.example/", follow=follow, here=False)


# --- asking where a link goes ------------------------------------------------------------------


@pytest.fixture
async def answering() -> AsyncIterator[tuple[str, dict[str, bytes]]]:
    """A server on loopback answering each path with the head scripted for it."""
    heads: dict[str, bytes] = {}

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        first = (await reader.readuntil(b"\r\n\r\n")).split(b" ", 2)[1].decode()
        writer.write(heads[first] + b"Content-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", heads
    finally:
        server.close()


@pytest.fixture
def real_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loopback only: these tests reach a server on this machine by its literal address."""
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)


@pytest.mark.usefixtures("real_dns")
async def test_next_hop_reads_a_redirect_and_stops_at_an_answer(
    answering: tuple[str, dict[str, bytes]],
) -> None:
    base, heads = answering
    heads["/a"] = b"HTTP/1.1 302 Found\r\nLocation: /b\r\n"
    heads["/u"] = b"HTTP/1.1 301 Moved\r\nURI: http://next.example/\r\n"
    heads["/bare"] = b"HTTP/1.1 302 Found\r\n"
    heads["/b"] = b"HTTP/1.1 200 OK\r\n"
    async with aiohttp.ClientSession() as session:
        assert await next_hop(session, f"{base}/a") == f"{base}/b"
        assert await next_hop(session, f"{base}/u") == "http://next.example/"
        assert await next_hop(session, f"{base}/bare") is None
        assert await next_hop(session, f"{base}/b") is None


@pytest.mark.usefixtures("real_dns")
async def test_next_hop_lets_an_unreachable_address_through_to_the_tool() -> None:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    closed = probe.getsockname()[1]
    probe.close()
    async with aiohttp.ClientSession() as session:
        assert await next_hop(session, f"http://127.0.0.1:{closed}/") is None


async def test_next_hop_never_swallows_a_refusal() -> None:
    """A refusal raised by the guarded session's own check reaches the walk, not a None."""

    class _Refusing:
        def head(self, *_args: object, **_kwargs: object) -> object:
            raise UrlRejected("refused", reason="private_address")

    with pytest.raises(UrlRejected):
        await next_hop(_Refusing(), "http://public.example/")  # type: ignore[arg-type]


def test_address_is_public_is_the_one_policy_the_pin_and_the_prewalk_share() -> None:
    """The shared address rule (`sources.net` pins live connections through it): a public address
    passes, every private/local/metadata one does not, and a string that is not an address at all is
    not public rather than an error."""
    assert url_guard.address_is_public("93.184.216.34") is True
    assert url_guard.address_is_public("127.0.0.1") is False
    assert url_guard.address_is_public("10.0.0.5") is False
    assert url_guard.address_is_public("169.254.169.254") is False
    assert url_guard.address_is_public("::1") is False
    assert url_guard.address_is_public("not-an-ip") is False
