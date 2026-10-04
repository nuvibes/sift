# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning a pasted link into the media behind it: the first of the two download stages.

A person pastes something they can read: a post, an album, a video page. Resolving it means working
out what media that page actually holds and how each piece is fetched: a direct address the
streaming fetcher can pull, or a job for one of the subprocess tools. The second stage, the fetch,
never has to care which, because this stage has already decided.

A host with no resolver of its own resolves to one subprocess item pointed at the source URL, for
yt-dlp or gallery-dl to fetch. The per-site resolvers (the ones that call a service, read a page,
and hand back direct media addresses) sit on top of this dispatch, and nothing downstream changes
for them. The site and the username come from the URL, so a drop is attributed for free whichever
way it resolves.
"""

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

#: Reads a username off a site's own API. Takes the proxy because it is a request like any
#: other: a lookup that ignored the route would go out of the machine's own address on exactly the
#: sites a tunnel was chosen for.
UsernameLookup = Callable[..., Awaitable[str | None]]

#: The subprocess tools, keyed to the fetch route an item carries. The enum's values already spell the
#: routes, but the map keeps the types honest rather than casting a bare string.
_SUBPROCESS_ROUTE: dict[Backend, FetchRoute] = {
    Backend.YTDLP: "ytdlp",
    Backend.GALLERYDL: "gallerydl",
}

# The tool is chosen per Site, beside that Site's folder and its naming, rather than by one
# instance-wide switch. Schema v13 carries the older `downloads.service_method` value over as rows
# for TikTok and Instagram. See `site_options`.


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_mutable(url: str) -> bool:
    """Whether a link stands for a set of media whose contents change, rather than one fixed post.

    A permalink always resolves to the same media, so once it has been fetched a re-paste is skipped
    by its address and that saves a pointless second download. These do not: today's story tray is
    not yesterday's, and a highlight is added to for years under one address. Skipping one of those
    because the address is familiar loses everything posted since, silently, with the download
    reported as already in the library.

    So such a link is re-resolved every time it is pasted, and what stops it re-fetching the whole
    set is the record of the individual items already fetched.

    Which shapes those are is a fact about the site, and it is kept with the rest of the site's facts
    rather than asked of a resolver: the two answers drifting apart is the failure this catalog
    exists to prevent.
    """
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
    """Resolve `url` to the media behind it.

    A host with its own service resolver (TikTok and Instagram) calls that service and returns
    direct addresses. A file-host site is read by its extractor, which may need a saved login
    (`cookies_file`) for a gated post. Every other host yields a single subprocess item on the source
    URL: the tool a matched site names, or yt-dlp for the catch-all. The display site falls back
    to the host when the URL names no known one, so a resolved item always has a site to show.

    ## `downloader` overrides ALL of that, and that is what it is for

    A Site given a tool by hand skips the service resolvers and the per-site extractors and goes
    straight to that tool on the source address. Somebody reaches for this when Sift's own way of
    reading a Site is what is failing them, so honouring the choice for the tool and then
    quietly using Sift's reader anyway would leave the setting looking ignored.

    Nothing else changes: the attribution, the fallback tool and the display site are read the
    same way, because those are facts about the address rather than about how it is fetched.

    `policy` is the download's settings, handed to every reader Sift runs itself so its requests are
    timed, retried and paced like the file's own (`sites.resolve_site`, and `curl` for the service
    lookups).
    """
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
    """A subprocess item (media stays on yt-dlp) with a username read from the site's own
    API: YouTube's channel, RedGIFs' uploader. The lookup is best-effort; a miss leaves it as the
    URL-derived username, which for these hosts is none."""
    media = _subprocess_media(url)
    username = await resolve_username(url, proxy=proxy, policy=policy)
    return replace(media, username=username) if username else media


def _other_tool(url: str, forced: Backend) -> Backend | None:
    """The tool this site names that is NOT the one chosen, or None when there is no other.

    Both of the catalog's picks are considered, not just its second: a site whose only tool is
    gallery-dl still has gallery-dl worth trying behind a hand-chosen yt-dlp, and reading only the
    `fallback_backend` field would leave that site with no second attempt at all.
    """
    named = [backend_for(url), fallback_backend_for(url)]
    return next((one for one in named if one is not None and one is not forced), None)


def _subprocess_media(url: str, *, forced: Backend | None = None) -> ResolvedMedia:
    """One subprocess item on the source URL, for a host with no direct resolver of its own.

    The item carries the catalog's second tool as well as its first. A site that serves images and
    video needs one (neither downloader reads both), and without it a site added the ordinary way
    would run a single tool and come back empty for the kind that tool cannot see, which is
    indistinguishable from a post that held nothing.

    `forced` is a tool chosen by hand for this Site. It LEADS, and the other tool the catalog
    names for the site still follows behind it.

    The fallback is kept even when a tool was named: it is not there as a preference, it is there
    because neither downloader reads both pictures and video, so an Instagram post of photographs
    comes back empty from yt-dlp alone
    (`test_instagram_uses_the_tool_when_a_Site_is_pointed_at_one`). Dropping it would turn a chosen
    tool into a silent loss of half a Site's media.

    So the choice decides which tool goes first, and the site's known coverage is not spent on it.
    """
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
        # Only a Discord attachment's address says when it was posted; for every other one this is
        # None and the tool's own record of the file is what `{posted}` reads.
        posted=discord.posted_from_link(url),
    )


__all__ = ["is_mutable", "resolve"]
