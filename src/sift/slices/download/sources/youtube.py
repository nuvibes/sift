# SPDX-License-Identifier: AGPL-3.0-or-later
"""Labelling a pasted YouTube link with its channel's username, for attribution.

The media still downloads through yt-dlp; this only reads the channel's @username, best-effort, from
YouTube's anonymous oEmbed endpoint: the same public metadata yt-dlp sees, with no key or login.
Any failure leaves the download unlabelled rather than blocking it. The oEmbed API rejects a supplied
user agent, so the guarded request sends none and lets the impersonation set it.
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

_OEMBED_URL = "https://www.youtube.com/oembed"
_WATCH_URL = "https://www.youtube.com/watch?v={vid}"
_YOUTUBE_HOSTS = hosts_of("youtube")
# A YouTube video id is exactly 11 chars of [A-Za-z0-9_-].
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
# First path segments whose next segment is the video id (`/shorts/<id>` etc.).
_ID_PATH_MARKERS = frozenset({"shorts", "embed", "v", "live"})
# The channel's @username inside oEmbed's `author_url` (`.../@Name`).
_AUTHOR_USERNAME_RE = re.compile(r"/@([A-Za-z0-9._-]+)")


def handles(url: str) -> bool:
    """Whether this is a YouTube link this resolver labels (suffix/exact host match)."""
    return host_matches(source_host(url), _YOUTUBE_HOSTS)


def extract_video_id(url: str) -> str | None:
    """The 11-char video id from any YouTube link form: `/shorts/<id>`, `youtu.be/<id>`,
    `watch?v=<id>`, `/embed|/v|/live/<id>`. None for a channel or playlist URL. Pure."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    segments = [segment for segment in parts.path.split("/") if segment]
    if host_matches(host, ("youtu.be",)):
        return segments[0] if segments and _VIDEO_ID_RE.match(segments[0]) else None
    if len(segments) >= 2 and segments[0].lower() in _ID_PATH_MARKERS:
        return segments[1] if _VIDEO_ID_RE.match(segments[1]) else None
    query_v = parse_qs(parts.query).get("v")
    if query_v and _VIDEO_ID_RE.match(query_v[0]):
        return query_v[0]
    return None


def channel_from_oembed(data: Any) -> str | None:
    """The channel's username from an oEmbed body: the `@name` in `author_url` (canonical), else the
    `author_name` display name for a legacy channel whose oEmbed has no username."""
    if not isinstance(data, dict):
        return None
    author_url = data.get("author_url")
    if isinstance(author_url, str):
        match = _AUTHOR_USERNAME_RE.search(author_url)
        if match:
            return match.group(1)
    name = data.get("author_name")
    return name.strip() if isinstance(name, str) and name.strip() else None


async def resolve_username(
    url: str, *, proxy: str | None = None, policy: RunPolicy | None = None
) -> str | None:
    """The channel's username for a pasted YouTube link, or None when the URL carries no video id or
    the
    lookup fails. Best-effort: any error is swallowed so a download is never blocked by it."""
    video_id = extract_video_id(url)
    if video_id is None:
        return None
    with contextlib.suppress(Exception):
        fetched = await curl.guarded_get(
            _OEMBED_URL,
            proxy=proxy,
            policy=policy,
            params={"url": _WATCH_URL.format(vid=video_id), "format": "json"},
            user_agent=None,
        )
        if fetched.status_code == 200:
            return channel_from_oembed(json.loads(fetched.text))
    return None


__all__ = ["channel_from_oembed", "extract_video_id", "handles", "resolve_username"]
