# SPDX-License-Identifier: AGPL-3.0-or-later
"""Labelling a pasted RedGIFs link with its uploader, for attribution.

The media still downloads through yt-dlp; this only reads the uploader's username, best-effort. A
`/users/<name>` profile link names the username with no request. Otherwise the gif id is read from
the link and looked up through RedGIFs' anonymous API: a temporary bearer token, then the gif's
`userName`. Any failure leaves the download unlabelled. The API rejects a supplied user agent, so
the guarded requests send none and let the impersonation set it.
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from sift.slices.download.sources import curl
from sift.slices.download.sources.hosts import host_matches, source_host
from sift.slices.download.sources.sites.catalog import hosts_of
from sift.slices.download.sources.tuning import RunPolicy

_API = "https://api.redgifs.com/v2"
_AUTH_URL = f"{_API}/auth/temporary"
_GIF_URL = _API + "/gifs/{gif_id}"
_REDGIFS_HOSTS = hosts_of("redgifs")
# A watch / ifr / embed link carries the gif id as its trailing token.
_WATCH_RE = re.compile(r"redgifs\.(?:com|app)/(?:ifr|watch|embed)/([A-Za-z0-9]+)", re.IGNORECASE)
# A profile link names the username, no API call needed.
_USERS_RE = re.compile(r"redgifs\.(?:com|app)/users/([A-Za-z0-9._-]+)", re.IGNORECASE)
# A bare/CDN id is alphanumeric; the API keys on the lowercase id.
_ID_RE = re.compile(r"^[A-Za-z0-9]{5,}$")
# CDN filenames append a size/variant suffix before the extension.
_VARIANT_SUFFIX_RE = re.compile(
    r"-(?:mobile|silent|large|small|poster|thumb|thumbs|hd|sd|min|max)$", re.IGNORECASE
)
# Path words that are routes, never gif ids.
_RESERVED = frozenset(
    {"watch", "ifr", "embed", "users", "gifs", "i", "gallery", "browse", "tags", "search", "u"}
)


def handles(url: str) -> bool:
    """Whether this is a RedGIFs link this resolver labels (suffix/exact host match)."""
    return host_matches(source_host(url), _REDGIFS_HOSTS)


def _username_on_users_page(url: str) -> str | None:
    match = _USERS_RE.search(url)
    return match.group(1) if match else None


def extract_gif_id(url: str) -> str | None:
    """The gif id from any RedGIFs link form: `/watch|/ifr|/embed/<id>`, `?gif=<id>`, a bare
    `redgifs.com/<id>`, or a CDN filename (`<id>[-variant].<ext>`). Lowercased, or None. Pure."""
    parts = urlsplit(url)
    watch = _WATCH_RE.search(url)
    if watch:
        return watch.group(1).lower()
    query_gif = parse_qs(parts.query).get("gif")
    if query_gif and query_gif[0]:
        return query_gif[0].lower()
    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments or segments[0].lower() == "users":
        return None  # a profile URL carries no gif id
    stem = _VARIANT_SUFFIX_RE.sub("", segments[-1].rsplit(".", 1)[0])
    if stem.lower() in _RESERVED or not _ID_RE.match(stem):
        return None
    return stem.lower()


async def _fetch_uploader(gif_id: str, proxy: str | None, policy: RunPolicy | None) -> str | None:
    """A temporary bearer token, then the gif's `userName`. None on any non-200 or bad shape."""
    auth = await curl.guarded_get(_AUTH_URL, user_agent=None, proxy=proxy, policy=policy)
    if auth.status_code != 200:
        return None
    token = json.loads(auth.text).get("token") if auth.text else None
    if not isinstance(token, str) or not token:
        return None
    fetched = await curl.guarded_get(
        _GIF_URL.format(gif_id=gif_id),
        proxy=proxy,
        policy=policy,
        headers={"Authorization": f"Bearer {token}"},
        user_agent=None,
    )
    if fetched.status_code != 200:
        return None
    data: Any = json.loads(fetched.text)
    gif = data.get("gif") if isinstance(data, dict) else None
    name = gif.get("userName") if isinstance(gif, dict) else None
    return name if isinstance(name, str) and name else None


async def resolve_username(
    url: str, *, proxy: str | None = None, policy: RunPolicy | None = None
) -> str | None:
    """The uploader for a pasted RedGIFs link. A `/users/<name>` link resolves with no request; any
    lookup failure returns None so a download is never blocked by it."""
    users = _username_on_users_page(url)
    if users:
        return users
    gif_id = extract_gif_id(url)
    if gif_id is None:
        return None
    with contextlib.suppress(Exception):
        return await _fetch_uploader(gif_id, proxy, policy)
    return None


__all__ = ["extract_gif_id", "handles", "resolve_username"]
