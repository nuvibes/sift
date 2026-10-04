# SPDX-License-Identifier: AGPL-3.0-or-later
"""The guarded egress path: a resolver that pins a connection to a vetted public address and
refuses a private one at connect time. The name lookup is faked, so the vetting is what is tested.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable
from types import SimpleNamespace

import aiohttp
import pytest

from sift.slices.download.sources.net import (
    DEFAULT_USER_AGENT,
    GuardedResolver,
    _guard_trace,
    _pace_trace,
    _vet_literal_host,
    _vet_redirect,
    _vet_request_start,
    guarded_session,
)
from sift.slices.download.sources.ratelimit import Pacer
from sift.slices.download.url_guard import UrlRejected

Gai = Callable[..., Awaitable[list[tuple[int, int, int, str, tuple[str, int]]]]]


def _fake_getaddrinfo(address: str) -> Gai:
    async def gai(
        host: str, port: int, **_kw: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    return gai


async def test_a_public_address_is_pinned_and_the_name_is_kept_for_tls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))

    results = await GuardedResolver().resolve("example.com", 443)

    assert results[0]["host"] == "93.184.216.34"  # the socket goes to the checked address
    assert results[0]["hostname"] == "example.com"  # the name is still what TLS presents
    assert results[0]["port"] == 443


async def test_a_private_address_refuses_the_whole_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo("127.0.0.1"))

    with pytest.raises(UrlRejected, match="private or local"):
        await GuardedResolver().resolve("rebind.example", 80)


async def test_a_metadata_address_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo("169.254.169.254"))

    with pytest.raises(UrlRejected):
        await GuardedResolver().resolve("metadata.example", 80)


async def test_an_unresolvable_name_is_a_connection_error_not_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()

    async def failing(host: str, port: int, **_kw: object) -> list[object]:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(loop, "getaddrinfo", failing)

    with pytest.raises(OSError) as exc:
        await GuardedResolver().resolve("nx.example", 80)
    assert not isinstance(exc.value, UrlRejected)


async def test_close_is_a_no_op() -> None:
    await GuardedResolver().close()  # nothing to release; it must not raise


async def test_the_session_presents_the_default_agent_and_a_caller_can_override_it() -> None:
    async with guarded_session() as session:
        assert session.headers["User-Agent"] == DEFAULT_USER_AGENT
    assert session.closed  # leaving the context closes the session and its connector

    async with guarded_session(user_agent="sift-test/1.0") as session:
        assert session.headers["User-Agent"] == "sift-test/1.0"


async def test_the_guarded_session_blocks_a_real_connection_to_a_private_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name resolving to loopback never opens a socket through the guarded session."""
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo("127.0.0.1"))

    async with guarded_session() as session:
        with pytest.raises((UrlRejected, aiohttp.ClientError)):
            await session.get("http://rebind.example/x")


# --- literal-IP targets: aiohttp connects to these without asking the resolver ------------------


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",  # loopback, v4
        "::1",  # loopback, v6
        "169.254.169.254",  # the cloud metadata address
        "10.0.0.5",  # a private range
        "::ffff:127.0.0.1",  # an IPv4 loopback wearing an IPv6 wrapper
    ],
)
def test_a_literal_private_address_is_refused(host: str) -> None:
    with pytest.raises(UrlRejected, match="private or local"):
        _vet_literal_host(host)


@pytest.mark.parametrize(
    "host",
    [
        "2130706433",  # 127.0.0.1 as a single decimal
        "0177.0.0.1",  # 127.0.0.1 with an octal first part
        "127.1",  # 127.0.0.1 in short form
    ],
)
def test_a_legacy_numeric_form_of_a_private_address_is_refused(host: str) -> None:
    """Legacy numeric forms of a private address are normalized and refused here."""
    with pytest.raises(UrlRejected, match="private or local"):
        _vet_literal_host(host)


@pytest.mark.parametrize("host", ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"])
def test_a_literal_public_address_is_allowed(host: str) -> None:
    """The rule is public-only, not name-only: a real CDN reached by its IP is fine."""
    _vet_literal_host(host)  # does not raise


def test_a_malformed_numeric_host_is_not_a_literal_to_judge() -> None:
    """A numeric host that normalizes to no address is left alone."""
    _vet_literal_host("999.999.999.999")  # does not raise


def test_a_name_is_left_to_the_resolver() -> None:
    """A name is left to the resolver, which pins it at connect time."""
    _vet_literal_host("example.com")  # does not raise
    _vet_literal_host(None)  # a host aiohttp could not parse is nothing to judge either


async def test_the_session_refuses_a_literal_private_address_before_connecting() -> None:
    """A literal loopback URL never opens a socket through the whole session."""
    async with guarded_session() as session:
        with pytest.raises((UrlRejected, aiohttp.ClientError)):
            # Port 9 (discard) so a regression that let this through would still not hang.
            await session.get("http://127.0.0.1:9/x")


def _redirect_params(location: str) -> SimpleNamespace:
    """What aiohttp hands the redirect hook: the request and the response carrying `Location`."""
    return SimpleNamespace(
        url="https://cdn.example/file",
        response=SimpleNamespace(headers={"Location": location}),
    )


async def test_a_redirect_to_a_literal_private_address_is_refused() -> None:
    """A redirect to a literal private address is refused before the follow."""
    with pytest.raises(UrlRejected, match="private or local"):
        await _vet_redirect(None, None, _redirect_params("http://169.254.169.254/latest/"))  # type: ignore[arg-type]


async def test_a_redirect_named_only_by_the_uri_header_is_still_vetted() -> None:
    """A reply naming its target only by `URI`, which aiohttp follows, is vetted the same way."""
    params = SimpleNamespace(
        url="https://cdn.example/file",
        response=SimpleNamespace(headers={"URI": "http://169.254.169.254/latest/"}),
    )
    with pytest.raises(UrlRejected, match="private or local"):
        await _vet_redirect(None, None, params)  # type: ignore[arg-type]


async def test_a_redirect_to_a_name_is_left_to_the_resolver() -> None:
    """A redirect to a name, or a relative one, is left to the resolver."""
    await _vet_redirect(None, None, _redirect_params("https://other.example/b"))  # type: ignore[arg-type]
    await _vet_redirect(None, None, _redirect_params("/relative/path"))  # type: ignore[arg-type]


async def test_a_redirect_with_no_location_is_ignored() -> None:
    """A 3xx with no `Location` is not a target to vet."""
    await _vet_redirect(None, None, _redirect_params(""))  # type: ignore[arg-type]


def test_both_guards_are_wired_into_the_session_trace() -> None:
    """Both guards are wired into the session trace: the first request and each redirect."""
    trace = _guard_trace()
    assert list(trace.on_request_start) == [_vet_request_start]
    assert list(trace.on_request_redirect) == [_vet_redirect]


async def test_every_request_a_paced_session_starts_waits_its_turn_behind_the_last() -> None:
    """A paced session's second request waits the whole gap behind the first."""
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    trace = _pace_trace(Pacer(2.0, clock=lambda: 10.0, sleep=sleep))
    [on_start] = trace.on_request_start

    await on_start(None, None, None)  # type: ignore[arg-type]
    assert slept == []
    await on_start(None, None, None)  # type: ignore[arg-type]
    assert slept == [2.0]


# --- no exempt host: a stash-box endpoint is held to the rule like any other address ----------


async def test_a_stash_box_endpoint_on_a_loopback_or_private_address_is_refused_like_any_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stash-box endpoint on loopback or a private network is refused like any other address;
    reached as the box's route, the same listener answers."""
    from aiohttp import web

    heard: list[str] = []

    async def answer(request: web.Request) -> web.Response:
        heard.append(request.path)
        return web.Response(text="box")

    app = web.Application()
    app.router.add_post("/graphql", answer)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        port = runner.addresses[0][1]
        async with guarded_session() as session:
            with pytest.raises(UrlRejected, match="private or local"):
                await session.post(f"http://127.0.0.1:{port}/graphql", json={"query": "{}"})
        assert heard == []

        async with (
            guarded_session(proxy=f"http://127.0.0.1:{port}") as session,
            session.post("http://northbox.invalid/graphql", json={"query": "{}"}) as got,
        ):
            assert await got.text() == "box"
        assert heard == ["/graphql"]
    finally:
        await runner.cleanup()

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo("192.168.1.20"))
    with pytest.raises(UrlRejected, match="private or local"):
        await GuardedResolver().resolve("box.lan", 80)
