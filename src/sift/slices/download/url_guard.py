# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deciding whether the server is allowed to fetch a URL, before it fetches it.

This slice takes an address a person typed and makes the server go and get it. That is the exact
shape of a server-side request forgery, and it is the most dangerous thing in the backend: without
a check, the address could point the server at its own loopback, at the private network the machine
sits on (a house full of admin panels) or at a cloud metadata endpoint that hands out
credentials. Being admin-only is not a defense, because an admin deliberately pastes untrusted
links from the internet. Routing egress through a VPN is not a defense either: it changes which
network the request leaves from, not whether the address resolves to a private one.

So every address is checked here first, and the check is about where it actually resolves, not what
it looks like. The scheme must be http or https. The host is resolved, and every address it resolves
to must be a public one: not loopback, not a private range, not link-local, not the metadata
address. And because a public URL is free to redirect to a private one, the redirects are walked
here, before the tool runs, and every hop is held to the same rule. A chain that starts public and
ends private is refused, not followed.

A tool follows redirects and fetches segments on its own once it has the URL, so this pre-walk is
the early answer rather than the only one: it refuses a private link before a tool is spawned, with
a sentence a person can read. Everything a tool connects to afterwards goes through the kernel's
tool proxy (`kernel.public_net.ToolProxy`), which holds every connection to the same rule.
"""

from __future__ import annotations

import asyncio
import socket
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit

from sift.kernel.log import get_logger, hashed, security_event
from sift.kernel.paths import PathEscape, confine
from sift.kernel.public_net import address_is_public

log = get_logger(__name__)

#: The only schemes a downloader is ever handed. Everything else (file, ftp, gopher, data) is a
#: way to reach something that is not a web resource, so none of them is allowed.
SAFE_SCHEMES = frozenset({"http", "https"})

#: A cap on how many redirects the pre-walk follows before giving up. A chain longer than this is
#: either broken or deliberately trying to exhaust the check.
MAX_REDIRECT_HOPS = 10

#: How long a single pre-walk request may take.
_REDIRECT_TIMEOUT_SECONDS = 10.0

#: Follows one redirect: given a URL, returns where it points next, or None if it does not redirect.
Follower = Callable[[str], Awaitable[str | None]]


class UrlRejected(Exception):
    """A URL may not be fetched. The message is plain-language and names no address.

    `reason` is a short stable code for the log and the tests; the message is what a person reads.
    """

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


def _check_address(url: str) -> None:
    """Check one URL's scheme and where its host resolves. Raises `UrlRejected` on any problem.

    Resolve-then-check, and check every address the host offers: a host that resolves to one public
    and one private address is refused, because the tool is free to connect to either.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in SAFE_SCHEMES:
        _refuse(url, "scheme", "Only http and https links can be downloaded.")

    host = parts.hostname
    if not host:
        _refuse(url, "no_host", "That does not look like a web address.")

    for address in _resolve(host):
        if not address_is_public(address):
            _refuse(
                url,
                "private_address",
                "That link points to a private or local address, which cannot be downloaded.",
            )


def _refuse(url: str, reason: str, message: str) -> NoReturn:
    """Log the refusal as a redacted security event and raise. The URL never enters the log."""
    security_event("ssrf_blocked", reason=reason, host=hashed(url))
    raise UrlRejected(message, reason=reason)


async def _http_follow(url: str) -> str | None:
    """Ask a URL where it redirects to, without following it. None if it is a final destination.

    Runs a real request in a thread, because the check has to see what the server actually answers.
    A redirect is captured and stopped at rather than chased, so the caller re-validates the target
    before anyone goes there.
    """
    return await asyncio.to_thread(_http_follow_sync, url)


class _Redirected(Exception):
    """Raised internally to hand a redirect's target back out of urllib without following it."""

    def __init__(self, location: str | None) -> None:
        super().__init__(location or "")
        self.location = location


class _StopRedirects(urllib.request.HTTPRedirectHandler):
    """A urllib handler that refuses to follow a redirect, surfacing its target instead."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        # The new URL is the sixth positional argument in urllib's contract. Reaching it this way
        # keeps the signature tolerant of the handler being called either way.
        newurl = args[5] if len(args) > 5 else None
        raise _Redirected(newurl if isinstance(newurl, str) else None)


def _http_follow_sync(url: str) -> str | None:
    opener = urllib.request.build_opener(_StopRedirects)
    request = urllib.request.Request(url, method="HEAD")  # noqa: S310 (scheme already allowlisted)
    try:
        with opener.open(request, timeout=_REDIRECT_TIMEOUT_SECONDS):
            return None
    except _Redirected as redirect:
        return redirect.location
    except (urllib.error.URLError, OSError, ValueError):
        # The pre-walk could not reach the URL. That is not proof it is safe, but it is also not a
        # redirect to somewhere private: the address itself was already checked. Let the tool try
        # and fail with a real download error rather than turning an unreachable host into an SSRF
        # rejection.
        return None


async def guard_url(
    url: str, *, follow: Follower | None = None, max_hops: int = MAX_REDIRECT_HOPS
) -> None:
    """Refuse a URL the server must not fetch. Returns on success, raises `UrlRejected` otherwise.

    Checks the address, then walks its redirects and checks each hop the same way. A chain that
    lands on a private address at any point is refused. `follow` is the redirect resolver, injected
    so a test can script a chain without a network.
    """
    follower = follow if follow is not None else _http_follow

    _check_address(url)

    current = url
    for _ in range(max_hops):
        nxt = await follower(current)
        if nxt is None:
            return
        _check_address(nxt)
        current = nxt

    _refuse(url, "too_many_redirects", "That link redirects too many times to be downloaded.")


def check_url(url: str) -> None:
    """Refuse a URL by scheme and resolved address, without walking its redirects.

    For a caller that fetches the URL with redirects disabled: the curl_cffi resolvers, which cannot
    pin a connection and so refuse rather than chase a redirect to a fresh, unvetted address.
    `guard_url` adds the redirect pre-walk for a caller whose fetch will follow them.
    """
    _check_address(url)


def confine_to(root: Path, candidate: Path) -> Path:
    """Return `candidate` resolved, having proved it is inside `root`. Raises `UrlRejected` if not.

    A downloader names its own output file, and that name came from the same untrusted place the
    bytes did: it can carry `..` or an absolute path that walks out of the directory it was meant
    to land in. Resolving both and checking containment is what makes "inside the download folder" a
    fact rather than a hope; the shared `confine` also fails closed on a path that will not resolve.
    """
    try:
        return confine(root, candidate)
    except PathEscape:
        security_event("path_escape_blocked", reason="outside_dest", handle=hashed(str(candidate)))
        raise UrlRejected(
            "A downloaded file tried to write outside its folder and was stopped.",
            reason="path_escape",
        ) from None


#: `address_is_public` is the kernel's rule; the slice's modules ask for it here.
__all__ = [
    "MAX_REDIRECT_HOPS",
    "SAFE_SCHEMES",
    "Follower",
    "UrlRejected",
    "address_is_public",
    "check_url",
    "confine_to",
    "guard_url",
]
