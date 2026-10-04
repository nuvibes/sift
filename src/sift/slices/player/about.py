# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a file carried when somebody sat down with it: its people, tags, Sites and song.

Read once per sitting, when its first piece is about to be written (see `plays.About` and the
player's schema, "What a sitting was about"), because none of it can be read afterwards: a tag
taken off in May, a person merged away, a song renamed, and last year's sittings would be counted
against what the file carries today.

Two steps, and the second is the one that matters. The links are read for the file the route has
already opened through the access layer; then each kind of thing is asked of the access layer for
THIS viewer, so a person, tag, Site or song they may not be shown (one their own Hidden keeps
from them, say) is not written into their history. The names come back from the same answer, so
a name is the one the viewer would have seen on screen.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from sift.kernel.access import Repository, Viewer
from sift.kernel.db import Database
from sift.slices.player.plays import About, Named

#: The most of each kind one sitting keeps. A file on more than this many people or tags is a
#: file somebody bulk-tagged; the first ones by id are kept, and the rest are let go.
MOST_EACH: Final = 64

#: Every person, tag, Site (through the usernames the file is filed under) and song on one file.
#: `UNION`, so a file filed under two usernames of one Site names that Site once.
_LINKS = """
SELECT 'person' AS kind, person_id AS id FROM asset_people WHERE asset_id = :asset
UNION
SELECT 'tag', tag_id FROM asset_tags WHERE asset_id = :asset
UNION
SELECT 'site', u.site_id
  FROM asset_usernames link
  JOIN usernames u ON u.id = link.username_id
 WHERE link.asset_id = :asset AND u.site_id IS NOT NULL
UNION
SELECT 'song', song_id FROM song_files WHERE asset_id = :asset
"""


async def about_of(database: Database, access: Repository, viewer: Viewer, asset_id: str) -> About:
    """What this file carries now, as this viewer may be shown it, for a sitting beginning now.

    The caller has already opened the file through the access layer, which is what makes reading
    its links here safe: nothing here can name a second file.
    """
    linked: dict[str, list[str]] = {"person": [], "tag": [], "site": [], "song": []}
    for row in await database.fetch_all(_LINKS, {"asset": asset_id}):
        linked[str(row["kind"])].append(str(row["id"]))
    people = await access.visible_people(viewer, sorted(linked["person"]))
    tags = await access.visible_tags(viewer, sorted(linked["tag"]))
    sites = await access.visible_sites(viewer, sorted(linked["site"]))
    song = None
    for song_id in linked["song"][:1]:
        found = await access.visible_song(viewer, song_id)
        song = None if found is None else Named(found.id, found.name)
    return About(
        people=_kept((one.id, one.name) for one in people.values()),
        tags=_kept((one.id, one.name) for one in tags.values()),
        sites=_kept((one.id, one.name) for one in sites.values()),
        song=song,
    )


def _kept(found: Iterable[tuple[str, str]]) -> tuple[Named, ...]:
    """One kind's things, by id so a sitting's list is the same whichever order they were read in,
    and no more than `MOST_EACH` of them."""
    return tuple(Named(id_, name) for id_, name in sorted(found)[:MOST_EACH])
