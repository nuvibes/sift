# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a pass running as nobody has still to read: unnumbered usernames, unfiled names, unread
pictures. Here because `assets` and `asset_locations` carry permissions; no row reaches a person.
"""

from __future__ import annotations

from collections.abc import Sequence

from sift.kernel.db import Database, in_clause

#: Filenames that might carry the number for a username that has none, driven from the usernames
#: (a set that only shrinks) and returning the username's id: one name on two sites is two rows.
#: A prefix range rather than `LIKE` makes it an index probe on `ix_loc_filename_folded`;
#: `char(1114111)` is the highest character and `lower()` folds as `LIKE` does. Any character after
#: the username matches, so every separator reaches the reader.
_NAMELESS_USERNAME_FILENAMES = """
SELECT a.id AS id, a.name AS name, l.filename AS filename
FROM usernames a
JOIN asset_locations l
  ON lower(l.filename) > lower(a.name)
 AND lower(l.filename) < lower(a.name) || char(1114111)
WHERE a.number IS NULL AND a.name <> ''
LIMIT ?
"""


async def filenames_for_nameless_usernames(db: Database, limit: int) -> list[tuple[str, str, str]]:
    """`(username_id, name, filename)` for usernames still without a number, capped: the set only
    shrinks, so the next pass reaches the rest."""
    rows = await db.fetch_all(_NAMELESS_USERNAME_FILENAMES, (limit,))
    return [(str(row["id"]), str(row["name"]), str(row["filename"])) for row in rows]


#: Present files under no site at all, with their stored names. Whole: most files never match, so a
#: capped read would return the same first page for ever. `NOT EXISTS` because a file filed under
#: two usernames would come back twice from a join.
_UNFILED_FILENAMES = """
SELECT l.asset_id AS asset_id, l.filename AS filename
  FROM asset_locations l
 WHERE l.status = 'present'
   AND NOT EXISTS (SELECT 1 FROM asset_usernames f WHERE f.asset_id = l.asset_id)
"""

#: Present files the picture-reading pass has not read, oldest first. Capped, and the cap converges
#: because reading a file writes the row that takes it out. A present copy to open, a width and
#: height to plan crops from, and no read at this revision of this content; `GROUP BY` because each
#: present copy is a row.
_UNREAD_BY_PICTURE = """
SELECT a.id AS asset_id
  FROM assets a
  JOIN asset_locations l ON l.asset_id = a.id AND l.status = 'present'
 WHERE a.width IS NOT NULL AND a.height IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM watermark_scans s
                    WHERE s.asset_id = a.id AND s.revision = ? AND s.identity = a.identity)
   AND NOT EXISTS (SELECT 1 FROM watermark_refusals r WHERE r.asset_id = a.id)
 GROUP BY a.id
 ORDER BY a.id
 LIMIT ?
"""

#: The same question as a number, capped, written out because concatenated SQL is what the
#: injection rule cannot see through; the test beside them holds the copies to one answer.
_COUNT_UNREAD_BY_PICTURE = """
SELECT COUNT(*) AS total FROM (
SELECT a.id AS asset_id
  FROM assets a
  JOIN asset_locations l ON l.asset_id = a.id AND l.status = 'present'
 WHERE a.width IS NOT NULL AND a.height IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM watermark_scans s
                    WHERE s.asset_id = a.id AND s.revision = ? AND s.identity = a.identity)
   AND NOT EXISTS (SELECT 1 FROM watermark_refusals r WHERE r.asset_id = a.id)
 GROUP BY a.id
 ORDER BY a.id
 LIMIT ?
)
"""

#: The same question asked of a page, for the catch-up pass, which must decide a page by the
#: sweep's own rule. The third copy, held to the others by the same test.
_UNREAD_BY_PICTURE_AMONG = """
SELECT a.id AS asset_id
  FROM assets a
  JOIN asset_locations l ON l.asset_id = a.id AND l.status = 'present'
 WHERE a.id IN (?*)
   AND a.width IS NOT NULL AND a.height IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM watermark_scans s
                    WHERE s.asset_id = a.id AND s.revision = ? AND s.identity = a.identity)
   AND NOT EXISTS (SELECT 1 FROM watermark_refusals r WHERE r.asset_id = a.id)
 GROUP BY a.id
"""

#: Whether the picture reader is installed: a kernel read of a feature's missing table would fail.
_PICTURE_READER_TABLES = (
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
    "('watermark_scans', 'watermark_refusals')"
)


async def filenames_of_unfiled_files(db: Database) -> list[tuple[str, str]]:
    """`(asset_id, filename)` for every present file that is under no site. See above for why whole."""
    rows = await db.sweep_all(_UNFILED_FILENAMES, what="unfiled filenames")
    return [(str(row["asset_id"]), str(row["filename"])) for row in rows]


async def _picture_reader_installed(db: Database) -> bool:
    rows = await db.fetch_all(_PICTURE_READER_TABLES)
    return len(rows) == 2


async def unread_by_picture(db: Database, revision: str, *, limit: int) -> list[str]:
    """Present files the picture pass has not read yet; none where it was never switched on."""
    if not await _picture_reader_installed(db):
        return []
    rows = await db.fetch_all(_UNREAD_BY_PICTURE, (revision, limit))
    return [str(row["asset_id"]) for row in rows]


async def unread_by_picture_among(
    db: Database, revision: str, asset_ids: Sequence[str]
) -> set[str]:
    """Which of these files the picture pass has not read yet, by the sweep's own rule."""
    wanted = list(asset_ids)
    if not wanted or not await _picture_reader_installed(db):
        return set()
    statement, bound = in_clause(_UNREAD_BY_PICTURE_AMONG, wanted)
    rows = await db.fetch_all(statement, [*bound, revision])
    return {str(row["asset_id"]) for row in rows}


async def count_unread_by_picture(db: Database, revision: str, *, limit: int) -> int:
    """How many of those there are, counted no further than `limit`."""
    if not await _picture_reader_installed(db):
        return 0
    row = await db.fetch_one(_COUNT_UNREAD_BY_PICTURE, (revision, limit))
    return 0 if row is None else int(row["total"])
