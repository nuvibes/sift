# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the track a video is set to off the page it came from.

For a large part of this kind of library the music IS what the file is: two edits of the same
footage to two different songs are two different things, and the only place that fact exists is the
page it was fetched from. Nothing else in Sift can work it out afterwards (there is no tag in the
file and no stash-box to ask), so if it is not read here it is not read at all.

**Read from the page's own data island, not from what is on screen.** PMVHaven is a Nuxt
application: the anchor a person sees ("Kestrel Media - Hollowgrain", linking to a search) is
built by the browser after the page loads, so it does not exist in the HTML a downloader fetches.
What does exist is `__NUXT_DATA__`, the payload the page hydrates itself from, and the track is in
there as structured data rather than as markup to scrape.

**The payload is a flat array and every value is an index into it.** That is the devalue format
Nuxt serialises with, and it is why nothing here can simply read a key: the video's `music` is a
number, which points at a list, which holds a number, which points at `{artist, song}`, and each of
those is a number pointing at a string. Resolving is what `_at` does, and it is depth-limited
because a payload is free to contain a cycle and this runs inside somebody's download.

**Best effort, always.** A page that will not load, a payload that will not parse, a shape that is
not what was expected: all of them return nothing. This decorates a download that has already
succeeded, and no failure here may cost somebody their file.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from sift.slices.download.sources import curl

#: How long the page fetch may take. The same figure the creator reader uses, and for the same
#: reason: this runs after a download has landed and must never be what makes one feel slow.
_TIME_LIMIT = 12.0

#: How much of a page is read. A Nuxt payload sits near the end of a large document, so this is
#: deliberately generous: the creator reader's four megabytes would truncate the island itself.
_MAX_PAGE_BYTES = 8_000_000

#: How deep a reference chain may be followed before the payload is called nonsense. Four is one
#: more than the real shape needs (`music` -> list -> entry -> string).
_MAX_DEPTH = 6

_ISLAND = re.compile(
    r"<script[^>]*id=[\"']__NUXT_DATA__[\"'][^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL
)


def music_in_page(html: str, *, url: str) -> str | None:
    """The track this page's video is set to, as one line, or None.

    The video is found by the id in its own address rather than by taking the first entry that has
    a `music` key. A PMVHaven page carries the payload for everything it draws (the video, and
    fifteen more down the side), and every one of those is a dict with a `music` key. Taking the
    first would file this video under a suggestion's music, which is wrong in a way nobody would
    ever catch: it is a real track name on a real record.
    """
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
    """The id at the end of a `/video/some-title_<id>` address.

    None for anything else, which is what makes this safe to call on every page: a site whose
    addresses do not look like this reads as "no music here" rather than as an error.
    """
    parts = [one for one in urlsplit(url).path.split("/") if one]
    if len(parts) < 2 or parts[0] != "video":
        return None
    _, _, tail = parts[1].rpartition("_")
    return tail or None


def _written(payload: list[Any], reference: Any) -> str | None:
    """One `music` value as the line to store, or None when there is no track on it.

    An empty list is the ordinary case and is not a failure: most videos have no music recorded,
    and `music: []` is the page saying so.

    Only the FIRST track. A video can carry several and the record holds one line; joining them
    would produce a value that matches nothing when somebody searches for either.
    """
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
    # One half is still worth keeping. A track with only a title is a track somebody can search for;
    # refusing it because the artist is blank would throw away the more useful of the two.
    return song or artist or None


def _at(payload: list[Any], reference: Any, depth: int = 0) -> Any:
    """One value out of the payload, following references until something concrete is reached.

    Depth-limited rather than trusting the document: the payload arrives from somebody else's
    server, a reference is free to point back at its own entry, and an unbounded walk would be a
    page that hangs a download worker rather than a page that fails to name a song.
    """
    if depth >= _MAX_DEPTH:
        return None
    if isinstance(reference, bool) or not isinstance(reference, int):
        return reference
    if not 0 <= reference < len(payload):
        return None
    return _at(payload, payload[reference], depth + 1)


def _text(value: Any) -> str:
    """A payload value as a trimmed string, or empty for anything that is not one."""
    return value.strip() if isinstance(value, str) else ""


async def music_of(url: str, *, proxy: str | None = None) -> str | None:
    """Fetch `url` and read the track off it. Never raises.

    The address goes through the same guard as every other fetch this slice makes, so a page that
    resolves somewhere the server may not be pointed at is refused here exactly as it would be
    anywhere else. Everything after that is swallowed, for the reason at the top of this file.
    """
    try:
        fetched = await curl.guarded_get(url, proxy=proxy, time_limit=_TIME_LIMIT)
    except Exception:
        return None
    if fetched.status_code != 200:
        return None
    return music_in_page(fetched.text[:_MAX_PAGE_BYTES], url=url)
