# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one way this slice is allowed to reach out onto the network.

The resolvers and the fetcher make many requests a person never sees: an API call to learn a media
address, the fetch of that address, a redirect chased to a CDN. Every one of those is the server
being told to go and get something, and the address always traces back to a link someone pasted,
so every one is a chance to point the server at its own private network. Guarding only the pasted
link is not enough: the danger is just as real in the CDN address an API handed back, or in a
redirect, and those are decided after the pasted link was checked.

So the check moves to the only place that sees all of them: the moment a connection is opened. This
resolver sits under an aiohttp session: for every host the session is about to connect to by name, it
resolves the name, holds every address it resolves to against the same public-only rule the pasted
link is held to, and then hands the connection the vetted address itself. Because the connection is
pinned to the address that was checked, a name that answers "public" to the check and "private" a
moment later at connect time (a DNS rebind) cannot slip through: the socket goes to the address the
check saw, not to a fresh lookup. The name is still what the server presents for TLS, so certificates
match.

A resolver only sees names. A URL whose host is already a literal IP is never resolved (aiohttp
connects to it straight), so the resolver above never gets a say on `http://127.0.0.1/` or
`http://169.254.169.254/`, and a caller that trusted the session to vet "every host" would be handing
those through unchecked. That gap is closed on the request itself: before the first connection and
before each redirect the session is about to follow, a literal-IP target is held to the same
public-only rule, and a private one is refused before a socket opens. A redirect to a name is still
vetted by the resolver when the connection is made, so between the two nothing reaches the network
unchecked.

The rule lives once, in the kernel (`public_net.address_is_public`); this only decides how it is
applied to a live connection. The subprocess tools are held to the same rule by the proxy every one
of their runs goes out through (`public_net.ToolProxy`), which judges each connection they open.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver
from aiohttp.client import DEFAULT_TIMEOUT

from sift.kernel.fetch import outbound_session
from sift.kernel.log import get_logger, hashed, security_event
from sift.kernel.public_net import address_is_public, literal_address
from sift.slices.download.sources.ratelimit import Pacer
from sift.slices.download.sources.tuning import RunPolicy
from sift.slices.download.url_guard import UrlRejected

if TYPE_CHECKING:
    from aiohttp.abc import ResolveResult

log = get_logger(__name__)

#: The user-agent the guarded session presents unless a caller overrides it. A plain library agent is
#: turned away by some hosts; this is an ordinary desktop browser string, nothing site-specific.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class GuardedResolver(AbstractResolver):
    """An aiohttp resolver that refuses a private address and pins the connection to a vetted one.

    aiohttp asks this to turn a host into addresses right before it connects. Every address the host
    offers is checked; if any is not public the whole connection is refused, because the connector is
    free to try any of them. What is returned is the checked address, so the socket goes exactly where
    the check looked.
    """

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[ResolveResult]:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            # A name that does not resolve is not an SSRF attempt; let it surface as an ordinary
            # connection failure the caller reports as a download that could not be reached.
            raise OSError(f"could not resolve {host!r}") from exc

        results: list[ResolveResult] = []
        for res_family, _type, _proto, _canon, sockaddr in infos:
            address = str(sockaddr[0])
            if not address_is_public(address):
                security_event("ssrf_blocked", reason="private_address", host=hashed(host))
                raise UrlRejected(
                    "That link points to a private or local address, which cannot be downloaded.",
                    reason="private_address",
                )
            results.append(
                {
                    "hostname": host,
                    "host": address,
                    "port": sockaddr[1],
                    "family": res_family,
                    "proto": 0,
                    "flags": 0,
                }
            )
        return results

    async def close(self) -> None:
        return None


def _vet_literal_host(host: str | None) -> None:
    """Refuse a target whose host is a literal, non-public IP. Names are left to the resolver.

    A name is not judged here: it is resolved and pinned when the connection is made. Only an
    address that is already an IP, in any form aiohttp would connect to without asking the resolver,
    is judged, because that is the one the resolver never sees.
    """
    if host is None:
        return
    ip = literal_address(host)
    if ip is None:
        return  # a name, not a literal address: the resolver handles it at connect time
    if not address_is_public(str(ip)):
        security_event("ssrf_blocked", reason="private_address", host=hashed(host))
        raise UrlRejected(
            "That link points to a private or local address, which cannot be downloaded.",
            reason="private_address",
        )


async def _vet_request_start(
    _session: aiohttp.ClientSession, _ctx: SimpleNamespace, params: aiohttp.TraceRequestStartParams
) -> None:
    """The first address the session is about to open, checked before the socket."""
    _vet_literal_host(params.url.host)


async def _vet_redirect(
    _session: aiohttp.ClientSession,
    _ctx: SimpleNamespace,
    params: aiohttp.TraceRequestRedirectParams,
) -> None:
    """Each redirect the session is about to follow, checked before it is followed.

    The signal names the request that was redirected, not where it points; the target is the
    location header resolved against it. aiohttp follows `Location`, or `URI` when there is no
    `Location`, so both are read here with the same precedence: reading only `Location` would let a
    reply that carries just `URI` reach its target unchecked. Raising here aborts before the next
    connection opens.
    """
    location = params.response.headers.get("Location") or params.response.headers.get("URI")
    if location:
        _vet_literal_host(urlsplit(urljoin(str(params.url), location)).hostname)


def _guard_trace() -> aiohttp.TraceConfig:
    trace = aiohttp.TraceConfig()
    trace.on_request_start.append(_vet_request_start)
    trace.on_request_redirect.append(_vet_redirect)
    return trace


def _pace_trace(pacer: Pacer) -> aiohttp.TraceConfig:
    """Every request the session starts waits its turn behind the one before it."""

    async def _wait_its_turn(
        _session: aiohttp.ClientSession,
        _ctx: SimpleNamespace,
        _params: aiohttp.TraceRequestStartParams,
    ) -> None:
        await pacer.wait()

    trace = aiohttp.TraceConfig()
    trace.on_request_start.append(_wait_its_turn)
    return trace


def policy_timeout(policy: RunPolicy) -> aiohttp.ClientTimeout:
    """The connection timeout setting as a session's budget: to connect, and between reads.

    No ceiling on the whole: a large download that is moving is not a stuck one. A caller that
    wants a ceiling on a small read (a page, an API answer) sets one per request, and keeps these
    two. See `sites.extractors._within`.
    """
    stuck = policy.pacing.timeout_seconds
    return aiohttp.ClientTimeout(total=None, sock_connect=stuck, sock_read=stuck)


@asynccontextmanager
async def guarded_session(
    *,
    user_agent: str | None = None,
    proxy: str | None = None,
    observe: tuple[aiohttp.TraceConfig, ...] = (),
    policy: RunPolicy | None = None,
) -> AsyncIterator[aiohttp.ClientSession]:
    """An aiohttp session whose every connection is vetted before it opens.

    A host given by name is resolved and pinned by `GuardedResolver`; a host given as a literal IP,
    which the resolver never sees, is vetted on the request and on each redirect. DNS caching is off
    so each connection re-vets rather than trusting a lookup made earlier. Closing the session closes
    its connector.

    `proxy` sends every request through a tunnel, and one property of this session does not survive
    it: the socket is opened to the PROXY, so the name is resolved there rather than here and the
    address the check looked at is not the address the connection went to. Under a proxy this is a
    check on the address, not a pin to it, which is why the proxy is a listener on this machine
    rather than one somewhere on the network.
    Only HTTP proxies work: aiohttp does not speak SOCKS, and does not say so: it opens a
    connection to the SOCKS port and talks HTTP at it, which reads as a hang.

    There is no exempt host, on purpose, and that includes a stash-box's endpoint. The boxes Sift
    asks are the official public ones, so an endpoint on a private or loopback address is refused
    exactly like a pasted link: every caller gets the one guard. A test standing in for a box on this
    machine reaches it the way a tunnel is reached (the box's route names a proxy on loopback and
    the endpoint's host is a name the proxy answers for), which leaves this rule untouched.

    `observe` adds listeners that WATCH the session and decide nothing: a site reader keeps what
    the site answered, so a reading that found nothing can say what the site said. They run after
    the guard's own, which stays first and cannot be replaced from here.

    `policy` is the Downloads settings, given by a DOWNLOAD's sessions and by nothing else. With it,
    the connection timeout is the session's budget to connect and between reads, and every request
    the session makes waits the wait between requests behind the one before: a Site's pages, each
    file of an album, each redirect and each retry, which is what the setting means to the Site on
    the other end and what the tools' `--sleep-requests` / `--sleep-request` do inside their runs.
    Without it the session is unpaced: a cover image or a stash-box answer is not a download and is
    not paced like one.
    """
    paced = (_pace_trace(Pacer.for_policy(policy)),) if policy is not None else ()
    session = await outbound_session(
        resolver=GuardedResolver(),
        headers={"User-Agent": user_agent or DEFAULT_USER_AGENT},
        trace_configs=[_guard_trace(), *paced, *observe],
        proxy=proxy,
        timeout=policy_timeout(policy) if policy is not None else DEFAULT_TIMEOUT,
    )
    try:
        yield session
    finally:
        await session.close()


__all__ = ["DEFAULT_USER_AGENT", "GuardedResolver", "guarded_session", "policy_timeout"]
