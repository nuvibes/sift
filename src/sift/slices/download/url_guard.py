# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deciding whether the server may fetch a URL: the early SSRF answer, before a tool runs.

Every resolved address and every redirect hop must be public; the tool proxy guards the rest."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NoReturn
from urllib.parse import urljoin, urlsplit

import aiohttp

from sift.kernel.log import get_logger, hashed, security_event
from sift.kernel.paths import PathEscape, confine
from sift.kernel.public_net import address_is_public, literal_address

log = get_logger(__name__)

SAFE_SCHEMES = frozenset({"http", "https"})

#: A longer chain is broken or trying to exhaust the check.
MAX_REDIRECT_HOPS = 10

_REDIRECT_TIMEOUT = aiohttp.ClientTimeout(total=10.0)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

Follower = Callable[[str], Awaitable[str | None]]


class UrlRejected(Exception):
    """A URL may not be fetched; the message names no address, `reason` is a stable code."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class LookupFailedForNow(Exception):
    """The name could not be looked up because the network or its resolver is not answering: a
    wait for the network, never a verdict on the address."""


LOOKUP_FAILED_FOR_NOW = (
    "The address couldn't be looked up just now, so the network may be down. Sift tries again "
    "in a little while."
)

#: A resolver's "try later" answers: POSIX's, then Windows' own numbers for the same two.
_FOR_NOW = frozenset(
    code
    for code in (
        getattr(socket, "EAI_AGAIN", None),
        getattr(socket, "EAI_FAIL", None),
        11002,
        11003,
    )
    if code is not None
)

#: Documentation addresses: a datagram socket's connect to one asks the routing table only and
#: sends nothing.
_NO_ONE = (("192.0.2.1", socket.AF_INET), ("2001:db8::1", socket.AF_INET6))


def _has_a_route() -> bool:
    """Whether this machine has any way out to the internet at all; nothing leaves it."""
    for address, family in _NO_ONE:
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as probe:
                probe.connect((address, 9))
        except OSError:
            continue
        return True
    return False


def _resolve(host: str) -> list[str]:
    """Every address a host resolves to. Raises `UrlRejected` if it does not resolve at all, and
    `LookupFailedForNow` where the resolver or the network is what failed. Blocking."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        if exc.errno in _FOR_NOW or not _has_a_route():
            raise LookupFailedForNow(LOOKUP_FAILED_FOR_NOW) from exc
        raise UrlRejected(
            "That address could not be found. Check it and try again.", reason="unresolvable"
        ) from exc
    return [str(info[4][0]) for info in infos]


def _check_address(url: str, *, here: bool = True) -> None:
    """Check a URL's scheme and every address its host offers; not `here`, only literal ones."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in SAFE_SCHEMES:
        _refuse(url, "scheme", "Only http and https links can be downloaded.")

    host = parts.hostname
    if not host:
        _refuse(url, "no_host", "That does not look like a web address.")

    for address in _resolve(host) if here else _named_here(host):
        if not address_is_public(address):
            _refuse(
                url,
                "private_address",
                "That link points to a private or local address, which cannot be downloaded.",
            )


def _named_here(host: str) -> list[str]:
    """The address a host names without asking a resolver, the way the tool proxy reads it."""
    literal = literal_address(host)
    if literal is not None:
        return [str(literal)]
    name = host.rstrip(".").lower()
    return ["127.0.0.1"] if name == "localhost" or name.endswith(".localhost") else []


def _refuse(url: str, reason: str, message: str) -> NoReturn:
    """Log the refusal as a redacted security event and raise. The URL never enters the log."""
    security_event("ssrf_blocked", reason=reason, host=hashed(url))
    raise UrlRejected(message, reason=reason)


async def next_hop(session: aiohttp.ClientSession, url: str) -> str | None:
    """Where a URL redirects, asked with a HEAD on the download's own session; None if not."""
    try:
        async with session.head(url, allow_redirects=False, timeout=_REDIRECT_TIMEOUT) as answer:
            if answer.status not in _REDIRECT_STATUSES:
                return None
            location = answer.headers.get("Location") or answer.headers.get("URI")
    except (aiohttp.ClientError, OSError):
        return None
    return urljoin(url, location) if location else None


async def guard_url(
    url: str, *, follow: Follower, here: bool = True, max_hops: int = MAX_REDIRECT_HOPS
) -> None:
    """Refuse a URL the server must not fetch, checking every redirect hop the same way."""
    await vet_url(url, here=here)

    current = url
    for _ in range(max_hops):
        nxt = await follow(current)
        if nxt is None:
            return
        await vet_url(nxt, here=here)
        current = nxt

    _refuse(url, "too_many_redirects", "That link redirects too many times to be downloaded.")


def check_url(url: str, *, here: bool = True) -> None:
    """Refuse a URL by scheme and resolved address, for a caller that never follows redirects.
    Blocking where it resolves: from the event loop, `vet_url`."""
    _check_address(url, here=here)


async def vet_url(url: str, *, here: bool = True) -> None:
    """`check_url` on a thread, so a resolver that is not answering never holds the event loop."""
    if here:
        await asyncio.to_thread(_check_address, url, here=here)
    else:
        _check_address(url, here=here)


def confine_to(root: Path, candidate: Path) -> Path:
    """Return `candidate` resolved, proved inside `root`: a tool's file name is untrusted."""
    try:
        return confine(root, candidate)
    except PathEscape:
        security_event("path_escape_blocked", reason="outside_dest", handle=hashed(str(candidate)))
        raise UrlRejected(
            "A downloaded file tried to write outside its folder and was stopped.",
            reason="path_escape",
        ) from None


__all__ = [
    "LOOKUP_FAILED_FOR_NOW",
    "MAX_REDIRECT_HOPS",
    "SAFE_SCHEMES",
    "Follower",
    "LookupFailedForNow",
    "UrlRejected",
    "address_is_public",
    "check_url",
    "confine_to",
    "guard_url",
    "next_hop",
    "vet_url",
]
