# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the track a video is set to off its page's `__NUXT_DATA__` island.

Best effort: a failure here never costs somebody a download that already succeeded."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from sift.slices.download.sources import curl

#: Runs after a download has landed and must never make one feel slow.
_TIME_LIMIT = 12.0

#: Generous: the payload sits near the end of a large document.
_MAX_PAGE_BYTES = 8_000_000

#: Above the real shape's depth (`music` -> list -> entry -> string).
_MAX_DEPTH = 6

_ISLAND = re.compile(
    r"<script[^>]*id=[\"']__NUXT_DATA__[\"'][^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL
)


def music_in_page(html: str, *, url: str) -> str | None:
    """The track this page's video is set to, found by the video's own id, or None."""
    wanted = _video_id(url)
    if not wanted:
        return None
    found = _ISLAND.search(html)
    if found is None:
        return None
    try:
        payload = json.loads(found.group(1))
    except ValueError:
        return None
    if not isinstance(payload, list):
        return None

    for entry in payload:
        if not isinstance(entry, dict) or "music" not in entry:
            continue
        if _at(payload, entry.get("_id")) != wanted:
            continue
        return _written(payload, entry.get("music"))
    return None


def _video_id(url: str) -> str | None:
    """The id at the end of a `/video/some-title_<id>` address, or None."""
    parts = [one for one in urlsplit(url).path.split("/") if one]
    if len(parts) < 2 or parts[0] != "video":
        return None
    _, _, tail = parts[1].rpartition("_")
    return tail or None


def _written(payload: list[Any], reference: Any) -> str | None:
    """The first track of one `music` value as a line to store, or None."""
    tracks = _at(payload, reference)
    if not isinstance(tracks, list) or not tracks:
        return None
    first = _at(payload, tracks[0])
    if not isinstance(first, dict):
        return None
    artist = _text(_at(payload, first.get("artist")))
    song = _text(_at(payload, first.get("song")))
    if artist and song:
        return f"{artist} - {song}"
    # A title alone is still searchable.
    return song or artist or None


def _at(payload: list[Any], reference: Any, depth: int = 0) -> Any:
    """One payload value, following devalue references to a limited depth: a cycle must not hang."""
    if depth >= _MAX_DEPTH:
        return None
    if isinstance(reference, bool) or not isinstance(reference, int):
        return reference
    if not 0 <= reference < len(payload):
        return None
    return _at(payload, payload[reference], depth + 1)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


async def music_of(url: str, *, proxy: str | None = None) -> str | None:
    """Fetch `url` through the guard and read the track off it. Never raises."""
    try:
        fetched = await curl.guarded_get(url, proxy=proxy, time_limit=_TIME_LIMIT)
    except Exception:
        return None
    if fetched.status_code != 200:
        return None
    return music_in_page(fetched.text[:_MAX_PAGE_BYTES], url=url)
