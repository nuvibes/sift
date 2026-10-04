# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which post a filing came from, for the pass that reads posts out of filenames."""

from __future__ import annotations

import time

from sift.kernel.db import Connection, Database

#: The filing insert, with the post the file was in; `DO NOTHING` as `_LINK_ASSET_USERNAME` has it.
_LINK_ASSET_USERNAME_IN_POST = (
    "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at, post_id) "
    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(asset_id, username_id) DO NOTHING"
)

#: Every file already filed as part of one post: a seek on `ix_asset_usernames_post` that never
#: opens the table. Both halves are bound, because a post id is a post only under its username.
_FILES_IN_POST = (
    "SELECT asset_id FROM asset_usernames WHERE username_id = ? AND post_id = ? ORDER BY asset_id"
)


async def link_username_to_asset_in_post_on(
    connection: Connection,
    *,
    asset_id: str,
    username_id: str,
    post_id: str,
    source: str | None = None,
) -> bool:
    """File one asset under one username AND say which post it was in. False where already filed.

    The post goes only on a row this creates, never onto a filing somebody else made."""
    cursor = await connection.execute(
        _LINK_ASSET_USERNAME_IN_POST, (asset_id, username_id, source, int(time.time()), post_id)
    )
    return bool(cursor.rowcount)


async def files_in_post(db: Database, *, username_id: str, post_id: str) -> list[str]:
    """Every file already filed as part of one post: how a pass knows a post was seen before."""
    rows = await db.fetch_all(_FILES_IN_POST, (username_id, post_id))
    return [str(row["asset_id"]) for row in rows]


#: Files one pass filed whose post is not worked out yet. The same filename through the same reader
#: gives the answer it gave then; `MIN` under GROUP BY as in `_FILES_FILED_UNDER`.
_FILINGS_WITHOUT_A_POST = """
SELECT f.asset_id AS asset_id, f.username_id AS username_id, MIN(l.filename) AS filename
  FROM asset_usernames f
  JOIN asset_locations l ON l.asset_id = f.asset_id AND l.status = 'present'
 WHERE f.source IN (?, ?) AND f.post_id IS NULL
 GROUP BY f.asset_id, f.username_id
"""

#: Which post one filing was part of. `post_id IS NULL` again here: a post set in between stays.
_SET_FILING_POST = (
    "UPDATE asset_usernames SET post_id = ?"
    " WHERE asset_id = ? AND username_id = ? AND post_id IS NULL"
)


async def filings_without_a_post(
    db: Database, sources: tuple[str, str]
) -> list[tuple[str, str, str]]:
    """`(asset_id, username_id, filename)` for what this pass filed before it read posts. Whole:
    the set only shrinks, and the pass reading it already walks every filename."""
    rows = await db.fetch_all(_FILINGS_WITHOUT_A_POST, sources)
    return [(str(row["asset_id"]), str(row["username_id"]), str(row["filename"])) for row in rows]


async def set_filing_post_on(
    connection: Connection, *, asset_id: str, username_id: str, post_id: str
) -> bool:
    """Say which post one already-written filing was part of. False where it already said."""
    cursor = await connection.execute(_SET_FILING_POST, (post_id, asset_id, username_id))
    return bool(cursor.rowcount)
