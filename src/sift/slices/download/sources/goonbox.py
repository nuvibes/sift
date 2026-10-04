# SPDX-License-Identifier: AGPL-3.0-or-later
"""Resolving a goonbox.cr link to the direct media behind it.

goonbox.cr is a Chevereto-style image host that exposes a small JSON API. An image page `/img/<id>`
is backed by `/api/images/<id>`, and an album `/a/<id>` by `/api/albums/<id>` paged with a `page`
query. Each image the API returns names its own original file URL on the CDN, so a link resolves
straight to direct addresses (no page scraping and no tool), the way the Instagram and Reddit
resolvers do for their own sites.

Best-effort: any failure (a challenge page, a moved endpoint, a private album) returns nothing and
lets the caller fall through to a subprocess tool, rather than raising and hanging the resolve.

Scope is goonbox.cr and its file CDN cuckcapital.cr, whose API shape is known. The older jpg.church
family this host is descended from serves a different, HTML page structure; those domains are left to
the subprocess tools until each is verified on its own, rather than assumed to share this API.

The API's shape below (the two endpoints, the `original_url` on each image, the `pagination` block
on an album) is the public behaviour of the site, learned rather than copied from any other tool.
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sift.slices.download.sources import curl
from sift.slices.download.sources.hosts import host_matches, source_host
from sift.slices.download.sources.ratelimit import Pacer
from sift.slices.download.sources.resolved import ResolvedItem, ResolvedMedia
from sift.slices.download.sources.sites.catalog import hosts_of
from sift.slices.download.sources.sites.common import guess_type_and_ext
from sift.slices.download.sources.tuning import RunPolicy

_SITE = "GoonBox"

# The page host and the CDN host its files sit on (e.g. simp6.cuckcapital.cr), read from the site
# catalog rather than written out again here. A bare link on the CDN is already the media, with no
# page to read, so the two are told apart, but which domains the site owns is one fact.
_HOSTS = hosts_of("goonbox")
_CDN_HOSTS = ("cuckcapital.cr",)

# The id in an image or album page path. goonbox's own links are bare (`/img/<id>`); the whole
# segment is the id the API takes.
_IMAGE_ID_RE = re.compile(r"^/(?:img|image)/([^/?#]+)", re.IGNORECASE)
_ALBUM_ID_RE = re.compile(r"^/(?:a|album)/([^/?#]+)", re.IGNORECASE)

# A ceiling on album paging: large albums are real, an endless one is a fault or a trap.
_MAX_ALBUM_PAGES = 200


def handles(url: str) -> bool:
    """Whether this is a goonbox.cr link: one of its pages, or a file on its CDN."""
    return host_matches(source_host(url), _HOSTS)


def _origin(url: str) -> str:
    """The `scheme://host/` root of a URL: where the API lives, and the referer for a CDN file."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


def _item(index: int, image: Any, page_url: str) -> ResolvedItem | None:
    """A direct item from one API image object, or None when it names no original file."""
    if not isinstance(image, dict):
        return None
    url = image.get("original_url")
    if not isinstance(url, str) or not url:
        return None
    name = image.get("original_filename")
    filename = name if isinstance(name, str) and name else None
    media_type, guessed_ext = guess_type_and_ext(url, filename)
    ext = image.get("extension")
    size = image.get("size_bytes")
    return ResolvedItem(
        index=index,
        url=url,
        media_type=media_type,
        ext=(f".{ext}" if isinstance(ext, str) and ext else "") or guessed_ext or ".jpg",
        referer=_origin(page_url),
        filename=filename,
        expected_bytes=size if isinstance(size, int) else None,
    )


async def _get_json(url: str, proxy: str | None, policy: RunPolicy | None) -> Any:
    """A JSON body from an API endpoint, or None on a non-200 or an unparseable body."""
    fetched = await curl.guarded_get(url, proxy=proxy, policy=policy)
    if fetched.status_code != 200:
        return None
    try:
        return json.loads(fetched.text)
    except ValueError:
        return None


async def _resolve_image(
    image_id: str, page_url: str, proxy: str | None, policy: RunPolicy | None
) -> list[ResolvedItem]:
    data = await _get_json(f"{_origin(page_url)}api/images/{image_id}", proxy, policy)
    if isinstance(data, dict):
        item = _item(0, data.get("image"), page_url)
        if item is not None:
            return [item]
    return []


async def _resolve_album(
    album_id: str, page_url: str, proxy: str | None, policy: RunPolicy | None
) -> list[ResolvedItem]:
    """Every image in an album, across its numbered pages. Stops at the last page the API reports, or
    at the ceiling, and keeps whatever it gathered if a page fails partway. A download's pages are
    asked the wait between requests apart, like any other of its requests."""
    origin = _origin(page_url)
    images: list[Any] = []
    pacer = Pacer.for_policy(policy) if policy is not None else None
    page = 1
    while page <= _MAX_ALBUM_PAGES:
        if pacer is not None:
            await pacer.wait()
        data = await _get_json(f"{origin}api/albums/{album_id}?page={page}", proxy, policy)
        if not isinstance(data, dict):
            break
        batch = data.get("images")
        if isinstance(batch, list):
            images.extend(batch)
        pagination = data.get("pagination")
        if not isinstance(pagination, dict):
            break
        current = pagination.get("current_page")
        last = pagination.get("last_page")
        if not isinstance(current, int) or not isinstance(last, int) or current >= last:
            break
        page += 1

    items: list[ResolvedItem] = []
    for image in images:
        item = _item(len(items), image, page_url)
        if item is not None:
            items.append(item)
    return items


async def resolve_goonbox(
    url: str, *, proxy: str | None = None, policy: RunPolicy | None = None
) -> ResolvedMedia:
    """Resolve a goonbox.cr link to its direct media via the site's JSON API.

    A bare CDN file link is already the media; an album is walked into its images; an image page is
    read through the API. Nothing resolved means the fall-through: one subprocess item, so a tool
    gets the last attempt rather than the resolve failing outright here.
    """
    host = source_host(url)
    path = urlsplit(url).path

    items: list[ResolvedItem] = []
    with contextlib.suppress(Exception):
        if host_matches(host, _CDN_HOSTS):
            item = _item(0, {"original_url": url}, url)
            items = [item] if item is not None else []
        elif album := _ALBUM_ID_RE.match(path):
            items = await _resolve_album(album.group(1), url, proxy, policy)
        elif image := _IMAGE_ID_RE.match(path):
            items = await _resolve_image(image.group(1), url, proxy, policy)

    if not items:
        items = [
            ResolvedItem.subprocess(source_url=url, backend="gallerydl", fallback_backend="ytdlp")
        ]

    return ResolvedMedia(site=_SITE, source_url=url, source_host=host, items=items)


__all__ = ["handles", "resolve_goonbox"]
