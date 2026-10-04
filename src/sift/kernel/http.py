# SPDX-License-Identifier: AGPL-3.0-or-later
"""Small HTTP helpers shared across the app, kept free of any web framework.

One function decides "did the browser reach Sift over HTTPS", because two things depend on the
answer (the Secure flag on the session cookie and whether HSTS is sent) and a second copy of
this logic would eventually disagree with the first about a security-relevant question. It takes
the two values it needs rather than a request object, so the kernel stays independent of the
framework the request came from.
"""

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
    """Whether a WebSocket handshake claiming this Origin may proceed.

    This is the whole of the cross-site defence for a socket, and it has to be, because nothing
    else applies. A WebSocket is not covered by the browser's same-origin policy: any page on the
    internet may open one to Sift, and **the browser attaches the session cookie to it**. There is
    no preflight for the CORS rules to answer, and the middleware that enforces them never sees a
    handshake. Without this, somebody who is signed in and visits a hostile page streams to it.

    Same origin, or an origin the operator has explicitly allowed: the same list the HTTP side
    uses, read from the same setting, so there is one answer to "who may call this app from
    elsewhere" rather than two that can drift apart.

    No Origin header at all is allowed, and that is a decision rather than an oversight. Browsers
    always send one; the callers that do not are scripts and command-line tools, which are not the
    threat this defends against: they have no cookie of yours to be tricked into sending, and
    anyone able to hand-craft a request can set any Origin they like anyway. Refusing them would
    break every non-browser client to inconvenience nobody.

    It lives in the kernel because two features open sockets now (the job feed and the live
    change feed) and neither may import the other. A second copy of a cross-site check is a
    second place for one of them to be wrong, and the one that is wrong is the one nobody looks at.
    """
    if origin is None:
        return True
    if origin in allowed:
        return True
    # Same-origin: the page that opened the socket lives at the address the socket was sent to.
    # Compared on the host as the request itself carries it, not on anything configured, so this
    # keeps working behind whatever name or port Sift is reached by.
    return bool(host) and urlsplit(origin).netloc == host


def is_https(scheme: str, forwarded_proto: str) -> bool:
    """Whether the browser reached Sift over HTTPS.

    Direct on a LAN it is the request's own scheme; behind a tunnel or proxy that terminates TLS,
    the origin connection is plain HTTP and the proxy records the real scheme in the first hop of
    the X-Forwarded-Proto header (later hops are the proxies between it and Sift, not the browser).

    Only two things read this (the Secure flag on a cookie and the HSTS header) and both fail
    safe if the header lies: a client that claims HTTPS gets a Secure cookie its own browser then
    refuses to send back over HTTP, and an HSTS header a plain-HTTP browser ignores. It downgrades
    nobody, so the header is honoured without a trusted-proxy list.
    """
    if scheme == "https":
        return True
    return forwarded_proto.split(",")[0].strip().lower() == "https"


#: The addresses that mean "this request came from the machine Sift is running on". Both families,
#: because a browser resolving `localhost` may pick either and neither is more local than the other.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def is_local_request(client_host: str | None, forwarded_for: str) -> bool:
    """Whether the caller is on the same machine as Sift.

    Asked in exactly one place: whether a filesystem path is worth answering with. A path is not a
    secret here (an admin already sees absolute paths in the folder picker and in every granted
    root), it is simply MEANINGLESS to anybody else. Handing `D:\\Media\\clip.mp4` to a machine
    that has no D: drive produces a drag of a file that is not there, which is a worse failure than
    saying no.

    `X-Forwarded-For` present means a proxy is in front, and then the connection Sift sees is the
    proxy's rather than the person's, so the loopback address is the proxy's own and proves
    nothing about who is really calling. The answer there is no, which costs a reverse-proxy user a
    path they could not have used anyway.
    """
    if forwarded_for.strip():
        return False
    return client_host in _LOOPBACK_HOSTS


#: Headers a proxy or a tunnel adds on the way in. A browser talking to Sift directly sends none of
#: them, so any one present means the connection Sift sees is not the person's own.
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


#: The ranges a household or office network hands out, and this machine's own. Named rather than
#: read from `is_private`, which also answers yes for documentation, benchmark and reserved ranges
#: that no local network uses.
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
    """Whether the caller reached Sift directly from this machine or its private network.

    Asked before a PIN may reopen a locked session: six digits are a fair guard on the local
    network and not over the internet, where the password is required instead.

    Anything that forwarded the request (a tunnel, a reverse proxy) leaves a header behind, and
    then the address Sift sees is the forwarder's rather than the person's, so the answer is no.
    Otherwise the socket's own address decides: loopback, a private range or link-local is the
    local network (`_LOCAL_NETWORKS`), and every other address, a shared carrier range an overlay
    VPN hands out included, is not. Adding a header can only turn a yes into a no, and a remote caller cannot remove
    one its tunnel adds, so a wrong answer costs a password and never lets a PIN through.

    A forwarder that adds nothing and connects from loopback (a bare TCP relay) looks like this
    machine, and no request carries anything that could tell the two apart.
    """
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


#: How much of a body is taken off the socket at a time. Big enough that a picture is a handful of
#: reads, small enough that a hostile answer cannot make one allocation enormous.
_CHUNK_BYTES = 64 * 1024


async def read_capped(stream: object, limit: int) -> bytes:
    """Read a response body until it ends or `limit` bytes have arrived, whichever comes first.

    **This exists because `read(n)` on a response body does NOT return n bytes.** It returns
    whatever has arrived so far, which over a network is usually the first packet. Nothing errors:
    a truncated picture is still a file, and a browser draws as much of one as it was given (a
    site logo or a stash-box portrait stored as its first packet). So the reader lives in the
    kernel rather than in any feature that needs it, and every caller gets it right by not writing
    it.

    The limit is a limit rather than a length: everything read through this comes from a machine
    Sift does not control, so the read stops at the cap instead of at whatever the far end feels
    like sending. A caller that has to tell "exactly the cap" from "over it" asks for `cap + 1` and
    compares against `cap`, which is the one extra byte that makes the difference observable.
    """
    buffer = bytearray()
    async for chunk in stream.iter_chunked(_CHUNK_BYTES):  # type: ignore[attr-defined]
        buffer.extend(chunk)
        if len(buffer) >= limit:
            break
    return bytes(buffer[:limit])


def chunk_bytes() -> int:
    """How much one read takes off the socket.

    Exposed so a test can build a body that is deliberately more than one read long, which is the
    only way to tell a reader that loops from one that returns its first piece. Reaching for the
    private name from a test would make the constant impossible to move.
    """
    return _CHUNK_BYTES
