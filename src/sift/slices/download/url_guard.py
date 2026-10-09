# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deciding whether the server may fetch a URL: the early SSRF answer, before a tool runs.

Every resolved address and every redirect hop must be public; the tool proxy guards the rest."""

from __future__ import annotations

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


def _resolve(host: str) -> list[str]:
    """Every address a host resolves to. Raises `UrlRejected` if it does not resolve at all."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
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
    _check_address(url, here=here)

    current = url
    for _ in range(max_hops):
        nxt = await follow(current)
        if nxt is None:
            return
        _check_address(nxt, here=here)
        current = nxt

    _refuse(url, "too_many_redirects", "That link redirects too many times to be downloaded.")


def check_url(url: str, *, here: bool = True) -> None:
    """Refuse a URL by scheme and resolved address, for a caller that never follows redirects."""
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
    "MAX_REDIRECT_HOPS",
    "SAFE_SCHEMES",
    "Follower",
    "UrlRejected",
    "address_is_public",
    "check_url",
    "confine_to",
    "guard_url",
    "next_hop",
]
