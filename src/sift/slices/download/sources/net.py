# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one way this slice reaches the network: every connection vetted as it opens.

A name is resolved, checked and pinned (no DNS rebind); a literal IP is checked on the request."""

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

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class GuardedResolver(AbstractResolver):
    """Refuse a host if any address it offers is private; pin the connection to a checked one."""

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[ResolveResult]:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            # Not an SSRF attempt: an ordinary unreachable download.
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
    """Refuse a literal, non-public IP host, the one the resolver never sees."""
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
    """Each redirect target (`Location`, else `URI`, as aiohttp follows), checked before it is."""
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
    """The connection timeout as a session's budget to connect and between reads; no total."""
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
    """An aiohttp session whose every connection is vetted before it opens; no exempt host."""
    paced = (_pace_trace(Pacer.for_policy(policy)),) if policy is not None else ()
    # Under `proxy` the name resolves at the proxy: a check, not a pin. HTTP proxies only.
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
