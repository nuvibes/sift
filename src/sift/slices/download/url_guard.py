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
ends private is refused, not followed. The walk is asked by the route the download holds, so a Site
set to a tunnel is sent nothing from this machine's own address; through a tunnel a name is not
resolved here at all (only a literal address is judged), as the tool proxy does it.

A tool follows redirects and fetches segments on its own once it has the URL, so this pre-walk is
the early answer rather than the only one: it refuses a private link before a tool is spawned, with
a sentence a person can read. Everything a tool connects to afterwards goes through the kernel's
tool proxy (`kernel.public_net.ToolProxy`), which holds every connection to the same rule.
"""

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

#: The only schemes a downloader is ever handed. Everything else (file, ftp, gopher, data) is a
#: way to reach something that is not a web resource, so none of them is allowed.
SAFE_SCHEMES = frozenset({"http", "https"})

#: A cap on how many redirects the pre-walk follows before giving up. A chain longer than this is
#: either broken or deliberately trying to exhaust the check.
MAX_REDIRECT_HOPS = 10

#: How long a single pre-walk request may take.
_REDIRECT_TIMEOUT = aiohttp.ClientTimeout(total=10.0)

#: The answers that send a request somewhere else.
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

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


def _check_address(url: str, *, here: bool = True) -> None:
    """Check one URL's scheme and where its host resolves. Raises `UrlRejected` on any problem.

    Resolve-then-check, and check every address the host offers: a host that resolves to one public
    and one private address is refused, because the tool is free to connect to either. Not `here`
    (through a tunnel), only a literal address or a name for this machine is judged.
    """
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
    """Where a URL redirects to, asked with a HEAD on `session` and never followed; None if not.

    The session is the download's own, so the ask leaves by the download's route. An address that
    cannot be reached is None as well: the tool then fails on it with a real reason.
    """
    try:
        async with session.head(url, allow_redirects=False, timeout=_REDIRECT_TIMEOUT) as answer:
            if answer.status not in _REDIRECT_STATUSES:
                return None
            # aiohttp's own precedence when it follows a redirect.
            location = answer.headers.get("Location") or answer.headers.get("URI")
    except (aiohttp.ClientError, OSError):
        return None
    return urljoin(url, location) if location else None


async def guard_url(
    url: str, *, follow: Follower, here: bool = True, max_hops: int = MAX_REDIRECT_HOPS
) -> None:
    """Refuse a URL the server must not fetch. Returns on success, raises `UrlRejected` otherwise.

    Checks the address, then walks its redirects with `follow` and checks each hop the same way.
    `here` is False when the walk goes through a tunnel (see `_check_address`).
    """
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
    """Refuse a URL by scheme and resolved address, without walking its redirects.

    For a caller that fetches the URL with redirects disabled: the curl_cffi resolvers, which cannot
    pin a connection and so refuse rather than chase a redirect to a fresh, unvetted address.
    `guard_url` adds the redirect pre-walk for a caller whose fetch will follow them. Not `here`,
    nothing is resolved: the check a link gets before its route is known.
    """
    _check_address(url, here=here)


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
    "next_hop",
]
