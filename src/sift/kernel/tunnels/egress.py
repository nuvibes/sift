# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which way out a download takes, and the refusal that keeps that promise.

A site is set to go out directly (the machine's own address) or through one named tunnel, and
sites with no setting of their own follow the global default. Both are read at the moment a download
starts rather than captured when Sift booted, so changing one takes effect on the next download with
nothing to restart. A transfer already running keeps the route it began on, because a transfer
cannot be re-routed mid-stream.

**The refusal is the feature.** If a site is set to a tunnel and that tunnel is not up, the download
fails and says which tunnel. It never falls back to going out directly. Quietly using the machine's
own address on the one site somebody explicitly said not to is the worst thing this can do: it is
undetectable from the outside, it happens exactly when the tunnel is broken, and the whole reason
the setting exists is the site being able to tell.

Who owns what: Sift owns the choice of route and holds it for the length of a download. The tunnel
owns whether it is usable, and says so; nothing here second-guesses that by trying anyway.

Which SITE a URL belongs to is the download slice's knowledge (its catalog of Sites), and the
kernel may not import a slice, so it is handed in (`site_of`) by the composition root like the two
readers beside it. A router built without one routes every URL by the default alone.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sift.kernel.tunnels.client import client_fault
from sift.kernel.tunnels.process import TunnelError, TunnelProcess
from sift.kernel.wiring import Part

#: The setting value meaning "the machine's own address". Not a tunnel id, and deliberately a word
#: rather than an empty value: an unset setting and a chosen Direct must not be the same thing on a
#: screen, even though they route the same way.
DIRECT = "direct"

#: What a row says when a download went out of the machine's own address. Capitalised because it is
#: read as a word on a screen, beside tunnel names people chose themselves.
DIRECT_LABEL = "Direct"

#: Reads the route for one site key, or None when that site has no setting of its own.
SiteRouteReader = Callable[[str], Awaitable[str | None]]

#: Reads the route every site follows unless it says otherwise.
DefaultRouteReader = Callable[[], Awaitable[str]]

#: The key of the Site a URL belongs to, or None when it is none Sift knows. The download slice's
#: catalog answers it; see the module's last paragraph.
SiteOf = Callable[[str], str | None]

#: Brings a tunnel up if it is meant to be running and is not. Called before a route is taken, so a
#: restart recovers on the first download that wants the tunnel rather than needing a visit to a
#: settings screen.
TunnelStarter = Callable[[str], Awaitable[None]]

#: Why a tunnel that is configured is not running (turned off, a start that failed and why, no key
#: yet), as the sentence a download refused over it says; None when there is no such tunnel.
TunnelProblem = Callable[[str], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class Route:
    """The way out one download was given, as it stood at the moment it was taken.

    One record rather than the proxy alone, because three things are asked of the same moment and
    they must not be asked of three different ones. The proxy is what the tools are handed; the
    label and the address are what the row writes down; the tunnel id is what the log carries. Read
    separately (the route once for the proxy and again for the name),
    a setting changed between the two reads would have a row name a tunnel its download never used.
    """

    #: What the fetchers are handed: the tunnel's loopback proxy, or None for the machine's own.
    proxy: str | None
    #: The way out in the words a row reads: "Direct", or the name somebody gave the tunnel.
    label: str
    #: The tunnel's id, or None when the download went out directly.
    tunnel_id: str | None = None
    #: The address of the server the tunnel was connected to when the route was taken: the same
    #: value Settings shows beside the tunnel. None when direct, or when the client had not said.
    #:
    #: NOT necessarily the address a site sees. A provider may send traffic out of a different
    #: machine from the one the tunnel connects to, so the tunnel's server and the address a site is
    #: shown can differ entirely. What this records is
    #: which server carried the download, which is the fact Sift can know without asking anybody.
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
    """Turns a URL into the proxy a download should use, or refuses.

    The tunnels are looked up by id in a mapping owned elsewhere, so a tunnel that has been stopped
    since the setting was made is simply absent, which is a refusal, not a silent direct fetch.
    """

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
        """Hold the route for one download and yield the proxy to use, or None for direct.

        Held rather than merely read: a tunnel being turned off waits for the downloads already on
        it, and that is what this is counted by. Raises when the route names a tunnel that is not
        there or not up: the download then fails naming it, rather than going out directly.
        """
        async with self.take(url) as taken:
            yield taken.proxy

    @asynccontextmanager
    async def take(self, url: str) -> AsyncIterator[Route]:
        """Hold the route for one download and say everything about it a row has to record.

        `route_for` with the rest of the answer. The route is read ONCE, and the label and the
        address come from the tunnel actually being held rather than from a second read of the
        settings, so what a row says it went out through is what it went out through, even if
        the setting moved while it was being taken. Refuses exactly as `route_for` does.
        """
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
        """Hold one named route (`DIRECT`, None, or a tunnel id) and yield its proxy address.

        The half of `route_for` that does not care where the route came from. A download reads its
        route off the site it is fetching; a stash-box stores its own on its row. Both are the same
        question (which tunnel, is it up, what address does it listen on), and both are answered
        here, so the two cannot come to disagree about what a tunnel id means or when a tunnel that
        is down refuses.
        """
        async with self._hold(route) as (proxy, _tunnel):
            yield proxy

    @asynccontextmanager
    async def _hold(
        self, route: str | None
    ) -> AsyncIterator[tuple[str | None, TunnelProcess | None]]:
        """The one place a route becomes a held tunnel: its proxy, and the tunnel holding it.

        Both `through` and `take` come here, so there is one refusal and one lease. The tunnel is
        handed back as well as its proxy because a row records WHICH tunnel it was, and looking it
        up again afterwards would be a second read of a mapping that can change in between.
        """
        if route is None or route == DIRECT:
            yield None, None
            return
        await self._ensure_started(route)
        tunnel = self._tunnels.get(route)
        if tunnel is None or not tunnel.running():
            # A tunnel that could not start because its program was removed or altered says so,
            # in the words its row on Settings says it in, rather than as a tunnel that has gone.
            lost = await client_fault()
            if lost is not None:
                raise TunnelError(lost)
        if tunnel is None:
            # A tunnel that is configured and not running says why (turned off, a start that
            # failed in its own words, no key yet); only a tunnel that is not configured at all
            # is one that no longer exists.
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
