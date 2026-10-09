# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning a pasted link into the media behind it: the first of the two download stages."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from sift.slices.download.sources import (
    discord,
    goonbox,
    instagram,
    reddit,
    redgifs,
    sites,
    tiktok,
    twitter,
    youtube,
)
from sift.slices.download.sources.progress import Report, nowhere
from sift.slices.download.sources.registry import (
    Backend,
    backend_for,
    classify,
    fallback_backend_for,
)
from sift.slices.download.sources.resolved import FetchRoute, ResolvedItem, ResolvedMedia
from sift.slices.download.sources.sites import catalog
from sift.slices.download.sources.tuning import RunPolicy

#: Takes the proxy: a lookup off the route would leave by the machine's own address.
UsernameLookup = Callable[..., Awaitable[str | None]]

_SUBPROCESS_ROUTE: dict[Backend, FetchRoute] = {
    Backend.YTDLP: "ytdlp",
    Backend.GALLERYDL: "gallerydl",
}


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_mutable(url: str) -> bool:
    """Whether a link's contents change (a story tray), so it is re-resolved on every paste."""
    return catalog.is_mutable(url)


async def resolve(
    url: str,
    *,
    downloader: Backend | None = None,
    cookies_file: Path | None = None,
    proxy: str | None = None,
    report: Report = nowhere,
    policy: RunPolicy | None = None,
) -> ResolvedMedia:
    """Resolve `url` to the media behind it; a hand-chosen `downloader` overrides every reader."""
    if downloader is not None:
        return _subprocess_media(url, forced=downloader)
    if tiktok.handles(url):
        return await tiktok.resolve_tiktok(url, proxy=proxy, policy=policy)
    if instagram.handles(url):
        return await instagram.resolve_instagram(url, proxy=proxy, policy=policy)
    if twitter.handles(url):
        return twitter.resolve_x(url)
    if youtube.handles(url):
        return await _labelled(url, youtube.resolve_username, proxy, policy)
    if redgifs.handles(url):
        return await _labelled(url, redgifs.resolve_username, proxy, policy)
    if reddit.handles(url):
        return await reddit.resolve_reddit(
            url, cookies_file=cookies_file, proxy=proxy, policy=policy
        )
    if goonbox.handles(url):
        return await goonbox.resolve_goonbox(url, proxy=proxy, policy=policy)
    if sites.is_site(url):
        return await sites.resolve_site(
            url, cookies_file=cookies_file, proxy=proxy, report=report, policy=policy
        )
    return _subprocess_media(url)


async def _labelled(
    url: str, resolve_username: UsernameLookup, proxy: str | None, policy: RunPolicy | None
) -> ResolvedMedia:
    """A subprocess item with a best-effort username from the site's own API."""
    media = _subprocess_media(url)
    username = await resolve_username(url, proxy=proxy, policy=policy)
    return replace(media, username=username) if username else media


def _other_tool(url: str, forced: Backend) -> Backend | None:
    """The catalog's other tool for this site, not the one chosen, or None."""
    named = [backend_for(url), fallback_backend_for(url)]
    return next((one for one in named if one is not None and one is not forced), None)


def _subprocess_media(url: str, *, forced: Backend | None = None) -> ResolvedMedia:
    """One subprocess item on the source URL, `forced` leading and the catalog's fallback kept."""
    attribution = classify(url)
    host = _host_of(url)
    fallback = _other_tool(url, forced) if forced is not None else fallback_backend_for(url)
    item = ResolvedItem.subprocess(
        source_url=url,
        backend=_SUBPROCESS_ROUTE[forced if forced is not None else backend_for(url)],
        fallback_backend=_SUBPROCESS_ROUTE[fallback] if fallback is not None else None,
    )
    return ResolvedMedia(
        site=attribution.site or host,
        source_url=url,
        source_host=host,
        items=[item],
        username=attribution.username,
        # Only a Discord attachment's address says when it was posted.
        posted=discord.posted_from_link(url),
    )


__all__ = ["is_mutable", "resolve"]
