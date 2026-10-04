# SPDX-License-Identifier: AGPL-3.0-or-later
"""Resolving an X (Twitter) link: the media, and the author's username read from the URL.

An X post can hold images or a video, and no single tool does both well: gallery-dl gets the
images, yt-dlp gets the video. So a status link resolves to one subprocess item that tries
gallery-dl first and falls back to yt-dlp when the first finds nothing. The author's @username is
taken straight from the path (`/<user>/status/<id>`), which is pure string work (no request that
could fail or slow the download), so a drop is attributed for free. The anonymised `/i/status/<id>`
form carries no username.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sift.slices.download.sources.hosts import host_matches, source_host
from sift.slices.download.sources.resolved import ResolvedItem, ResolvedMedia
from sift.slices.download.sources.sites.catalog import hosts_of

SITE_X = "X"

_X_HOSTS = hosts_of("x")
# An X username is 1-15 of [A-Za-z0-9_]; casing is preserved here and normalised downstream.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
# First path segments that are routes, never usernames: chiefly `i` (`/i/status/<id>`) and `web`.
_RESERVED = frozenset(
    {
        "i",
        "web",
        "home",
        "explore",
        "notifications",
        "messages",
        "settings",
        "search",
        "hashtag",
        "compose",
        "intent",
        "share",
        "login",
        "signup",
        "account",
        "tos",
        "privacy",
        "about",
    }
)


def handles(url: str) -> bool:
    """Whether this is an X/Twitter link this resolver owns (suffix/exact host match)."""
    return host_matches(source_host(url), _X_HOSTS)


def extract_username(url: str) -> str | None:
    """The author @username from a status link (`/<user>/status/<id>`), or None when the link carries
    no username (the anonymised `/i/status/` form) or is not a status link. Pure: no network."""
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if len(segments) >= 3 and segments[1].lower() == "status":
        user = segments[0]
        if user.lower() in _RESERVED:
            return None
        return user if _USERNAME_RE.match(user) else None
    return None


def extract_post_id(url: str) -> str | None:
    """The post's number from a status link (`/<user>/status/<number>`, `/i/status/<number>`), or
    None. The anonymised form names no username, but it does name the post. Pure: no network."""
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if len(segments) >= 3 and segments[1].lower() == "status" and segments[2].isdigit():
        return segments[2]
    return None


#: A post number is a snowflake: milliseconds since this moment, above 22 bits of sequence.
_X_EPOCH_MS = 1288834974657
_TIME_SHIFT = 22
#: Numbers below this were handed out before snowflakes and carry no moment.
_FIRST_SNOWFLAKE = 29700859247


def posted_from_post_id(post_id: str | None) -> datetime | None:
    """When the post was made, read from its number, or None where the number says nothing."""
    if post_id is None or not post_id.isdigit():
        return None
    try:
        number = int(post_id)
        if number < _FIRST_SNOWFLAKE:
            return None
        return datetime.fromtimestamp(((number >> _TIME_SHIFT) + _X_EPOCH_MS) / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def resolve_x(url: str) -> ResolvedMedia:
    """Resolve an X link to one subprocess item (gallery-dl first, yt-dlp on nothing), plus the
    author's username. The media route is the catch-all's; this adds the fallback and the
    attribution."""
    host = source_host(url)
    item = ResolvedItem.subprocess(source_url=url, backend="gallerydl", fallback_backend="ytdlp")
    return ResolvedMedia(
        site=SITE_X,
        source_url=url,
        source_host=host,
        items=[item],
        username=extract_username(url),
        # The post's number is `{id}`; the picture number beside it in each of gallery-dl's file
        # names (`<number>_1.jpg`) is read as `{n}` from that name by `tool_file_name_facts`.
        post_id=extract_post_id(url),
        posted=posted_from_post_id(extract_post_id(url)),
    )


__all__ = [
    "SITE_X",
    "extract_post_id",
    "extract_username",
    "handles",
    "posted_from_post_id",
    "resolve_x",
]
