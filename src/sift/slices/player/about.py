# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a file carried when somebody sat down with it: its people, tags, Sites and song.

Read once per sitting and only as this viewer may be shown it, since none of it can be read later.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from sift.kernel.access import Repository, Viewer
from sift.kernel.db import Database
from sift.slices.player.plays import About, Named

#: The most of each kind one sitting keeps, the first by id.
MOST_EACH: Final = 64

#: Every person, tag, Site and song on one file; `UNION` names a Site once.
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
    """What this file carries now, as this viewer may be shown it; the caller opened the file."""
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
    """One kind's things, by id and at most `MOST_EACH`."""
    return tuple(Named(id_, name) for id_, name in sorted(found)[:MOST_EACH])
