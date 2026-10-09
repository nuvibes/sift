# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which way out a download takes, refusing rather than ever going out directly by mistake."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sift.kernel.tunnels.client import client_fault
from sift.kernel.tunnels.process import TunnelError, TunnelProcess
from sift.kernel.wiring import Part

#: A word, not empty, so an unset setting and a chosen Direct differ on screen.
DIRECT = "direct"

DIRECT_LABEL = "Direct"

#: Reads the route for one site key, or None when that site has no setting of its own.
SiteRouteReader = Callable[[str], Awaitable[str | None]]

#: Reads the route every site follows unless it says otherwise.
DefaultRouteReader = Callable[[], Awaitable[str]]

#: The Site key a URL belongs to, or None; the download slice's catalog answers it.
SiteOf = Callable[[str], str | None]

#: Brings a configured tunnel up, so a restart recovers on the first download that wants it.
TunnelStarter = Callable[[str], Awaitable[None]]

#: Why a configured tunnel is not running, as a refused download says it; None if no tunnel.
TunnelProblem = Callable[[str], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class Route:
    """The way out one download was given, read once so its proxy and its row's words agree."""

    #: What the fetchers are handed: the tunnel's loopback proxy, or None for the machine's own.
    proxy: str | None
    #: The way out in the words a row reads: "Direct", or the name somebody gave the tunnel.
    label: str
    tunnel_id: str | None = None
    #: The tunnel's server when taken, which is not necessarily the address a site sees.
    address: str | None = None


async def _always_direct() -> str:
    return DIRECT


async def _no_override(_key: str) -> None:
    return None


def _no_site(_url: str) -> None:
    return None


async def _already_running(_tunnel_id: str) -> None:
    return None


async def _no_reason(_tunnel_id: str) -> None:
    return None


class EgressRouter:
    """Turns a URL into the proxy a download should use, or refuses."""

    def __init__(
        self,
        tunnels: dict[str, TunnelProcess],
        *,
        read_default: DefaultRouteReader = _always_direct,
        read_site: SiteRouteReader = _no_override,
        ensure_started: TunnelStarter = _already_running,
        site_of: SiteOf = _no_site,
        why_down: TunnelProblem = _no_reason,
    ) -> None:
        self._tunnels = tunnels
        self._why_down = why_down
        self._read_default = read_default
        self._read_site = read_site
        self._ensure_started = ensure_started
        self._site_of = site_of

    async def route_of(self, url: str) -> str:
        """The route this URL's site takes: `DIRECT` or a tunnel id. Read live, per download."""
        site = self._site_of(url)
        if site is not None:
            chosen = await self._read_site(site)
            if chosen:
                return chosen
        return await self._read_default()

    @asynccontextmanager
    async def route_for(self, url: str) -> AsyncIterator[str | None]:
        """Hold the route for one download and yield its proxy, or None for direct; no fallback."""
        async with self.take(url) as taken:
            yield taken.proxy

    @asynccontextmanager
    async def take(self, url: str) -> AsyncIterator[Route]:
        """Hold the route for one download with what its row records, read once."""
        route = await self.route_of(url)
        async with self._hold(route) as (proxy, tunnel):
            if tunnel is None:
                yield Route(proxy=None, label=DIRECT_LABEL)
                return
            yield Route(
                proxy=proxy,
                label=tunnel.name,
                tunnel_id=route,
                address=await tunnel.server_address(),
            )

    @asynccontextmanager
    async def through(self, route: str | None) -> AsyncIterator[str | None]:
        """Hold one named route and yield its proxy, answered as downloads' routes are."""
        async with self._hold(route) as (proxy, _tunnel):
            yield proxy

    @asynccontextmanager
    async def _hold(
        self, route: str | None
    ) -> AsyncIterator[tuple[str | None, TunnelProcess | None]]:
        """The one place a route becomes a held tunnel: one refusal, one lease."""
        if route is None or route == DIRECT:
            yield None, None
            return
        await self._ensure_started(route)
        tunnel = self._tunnels.get(route)
        if tunnel is None or not tunnel.running():
            # A removed or altered program says so in its Settings words.
            lost = await client_fault()
            if lost is not None:
                raise TunnelError(lost)
        if tunnel is None:
            # A configured tunnel says why it is down; only an unknown one no longer exists.
            why = await self._why_down(route)
            raise TunnelError(
                why
                or "This is set to use a tunnel that no longer exists, so nothing was sent. Choose "
                "a tunnel for it, or route it directly."
            )
        async with tunnel.lease() as proxy:
            yield proxy, tunnel


__all__ = [
    "DIRECT",
    "DIRECT_LABEL",
    "DefaultRouteReader",
    "EgressRouter",
    "Route",
    "SiteOf",
    "SiteRouteReader",
    "TunnelProblem",
    "TunnelStarter",
]


#: Which way out each request to a site takes.
EGRESS: Part[EgressRouter] = Part("egress")
