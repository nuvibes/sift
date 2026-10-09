# SPDX-License-Identifier: AGPL-3.0-or-later
"""Small HTTP helpers shared across the app, kept free of any web framework."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from urllib.parse import urlsplit

SESSION_COOKIE_NAME = "sift_session"
"""The cookie the session travels in.

Here rather than with the rest of sign-in's numbers because it is not one: it is the name the
browser and the server have to agree on, and things that are neither sign-in nor the browser need
it. The job feed reads it off a socket that never passes through a route, so it would otherwise
have to reach into another feature for a string.
"""

CSRF_HEADER_NAME = "x-csrf-token"
"""The header a state-changing request carries its CSRF token in.

The token is bound to the session and delivered in the response body, not a second cookie. A
cross-site page cannot set a header, which is the whole of why it goes in one. Beside the cookie
name for the same reason: the pair is what the browser and the server agree on, and splitting them
across two homes is how one of them comes to be spelled twice.
"""


def origin_is_allowed(origin: str | None, host: str | None, allowed: tuple[str, ...]) -> bool:
    """Whether a socket handshake from this Origin may proceed; its only cross-site defence."""
    # No Origin: scripts and tools carry no cookie of yours, and could forge one anyway.
    if origin is None:
        return True
    if origin in allowed:
        return True
    # Same origin, compared on the host the request carries, so any name or port works.
    return bool(host) and urlsplit(origin).netloc == host


def is_https(scheme: str, forwarded_proto: str) -> bool:
    """Whether the browser reached Sift over HTTPS; a lying header downgrades nobody."""
    if scheme == "https":
        return True
    return forwarded_proto.split(",")[0].strip().lower() == "https"


#: Both families, as `localhost` may resolve to either.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def is_local_request(client_host: str | None, forwarded_for: str) -> bool:
    """Whether the caller is on Sift's machine; behind a proxy the loopback proves nothing."""
    if forwarded_for.strip():
        return False
    return client_host in _LOOPBACK_HOSTS


#: A direct browser sends none, so any one means the connection is not the person's.
FORWARDING_HEADERS = frozenset(
    {
        "forwarded",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
        "cf-connecting-ip",
        "true-client-ip",
        "via",
    }
)


#: Named rather than `is_private`, which also accepts reserved ranges no network uses.
_LOCAL_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


def reached_over_the_local_network(client_host: str | None, header_names: Iterable[str]) -> bool:
    """Whether the caller reached Sift directly from this machine or its private network."""
    # A header only turns a yes into a no, so a wrong answer costs a password, never a PIN.
    if any(name.lower() in FORWARDING_HEADERS for name in header_names):
        return False
    if client_host is None:
        return False
    try:
        ip = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return any(ip in network for network in _LOCAL_NETWORKS)


#: A picture in a few reads, while no hostile answer makes one allocation enormous.
_CHUNK_BYTES = 64 * 1024


async def read_capped(stream: object, limit: int) -> bytes:
    """Read a body until it ends or `limit` bytes arrive; `read(n)` returns only what came."""
    buffer = bytearray()
    async for chunk in stream.iter_chunked(_CHUNK_BYTES):  # type: ignore[attr-defined]
        buffer.extend(chunk)
        if len(buffer) >= limit:
            break
    return bytes(buffer[:limit])


def chunk_bytes() -> int:
    """How much one read takes off the socket, for a test that needs more than one read."""
    return _CHUNK_BYTES
