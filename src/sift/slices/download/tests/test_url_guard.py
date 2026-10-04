# SPDX-License-Identifier: AGPL-3.0-or-later
"""The SSRF guard. The reject list is the control, and it grows with every evasion found."""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal

import pytest

from sift.slices.download import url_guard
from sift.slices.download.url_guard import UrlRejected, confine_to, guard_url

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


# --- the default redirect follower (the real one, exercised without a network) ----------------


class _FakeOpener:
    def __init__(self, behaviour: object) -> None:
        self._behaviour = behaviour

    def open(self, _request: object, timeout: float | None = None) -> object:
        return self._behaviour()  # type: ignore[operator]


class _NullContext:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *_args: object) -> Literal[False]:
        return False


async def test_default_follower_returns_none_when_a_url_does_not_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_a: _FakeOpener(_NullContext))
    assert await url_guard._http_follow("http://public.example/") is None


async def test_default_follower_hands_back_a_redirect_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def redirect() -> object:
        raise url_guard._Redirected("http://next.example/")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_a: _FakeOpener(redirect))
    assert await url_guard._http_follow("http://public.example/") == "http://next.example/"


async def test_default_follower_swallows_an_unreachable_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreachable() -> object:
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_a: _FakeOpener(unreachable))
    assert await url_guard._http_follow("http://public.example/") is None


def test_the_no_follow_handler_surfaces_the_target_and_tolerates_a_missing_one() -> None:
    handler = url_guard._StopRedirects()
    with pytest.raises(url_guard._Redirected) as redirect:
        handler.redirect_request(None, None, 302, "moved", {}, "http://next/")
    assert redirect.value.location == "http://next/"

    with pytest.raises(url_guard._Redirected) as no_target:
        handler.redirect_request(None, None, 302, "moved", {})
    assert no_target.value.location is None


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
