# SPDX-License-Identifier: AGPL-3.0-or-later
"""Writing the search index, the one path into `assets_fts`: unscoped, since who may see a row is
decided when it is read."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sift.kernel.db import Connection, Database
from sift.kernel.forgetting import register_forgetting

#: Assets per pass, so a rebuild can stop between passes.
BATCH = 500


# Everything an asset can be found by, in one statement. All LEFT joins, so a file with nothing on
# it is still found by name; aliases fold into People so free text agrees with tokens.
#: The record fields read as free text: a write moving one reindexes.
INDEXED_RECORD_FIELDS: frozenset[str] = frozenset({"title", "music"})

_TEXT_OF_ASSETS = """
SELECT a.id AS asset_id,
       COALESCE(a.title, '') AS title,
       -- The track a file is set to, which for a large part of this kind of library IS what the
       -- file is. It has a token of its own (`music:`), and without it here typing a song title
       -- into the search box would find nothing while the same words in the token found it: two
       -- halves of one search disagreeing, which is the thing this index exists to stop.
       COALESCE(a.music, '') AS music,
       COALESCE(a.original_filename, '') AS filename,
       COALESCE((SELECT group_concat(l.rel_path, ' ')
                   FROM asset_locations l WHERE l.asset_id = a.id), '') AS path,
       -- A tag's other names go in with it, for the reason a person's aliases do: a tag imported
       -- from a stash-box carries the words other people use for the same thing, and a search that
       -- only matches the one spelling somebody happened to file it under is a search that misses.
       --
       -- A UNION subquery rather than two `group_concat`s, so the DISTINCT is the union's and the
       -- separator stays the space every other column here uses.
       COALESCE((SELECT group_concat(words.name, ' ') FROM (
                   SELECT DISTINCT t.name AS name
                     FROM asset_tags at JOIN tags t ON t.id = at.tag_id
                    WHERE at.asset_id = a.id
                   UNION
                   SELECT DISTINCT ta.alias AS name
                     FROM asset_tags at JOIN tag_aliases ta ON ta.tag_id = at.tag_id
                    WHERE at.asset_id = a.id) AS words), '') AS tags,
       COALESCE((SELECT group_concat(names.name, ' ') FROM (
                   SELECT p.name AS name
                     FROM asset_people ap JOIN people p ON p.id = ap.person_id
                    WHERE ap.asset_id = a.id
                    UNION
                   SELECT al.alias
                     FROM asset_people ap JOIN people_aliases al ON al.person_id = ap.person_id
                    WHERE ap.asset_id = a.id) AS names), '') AS people,
       COALESCE((SELECT group_concat(named.name, ' ') FROM (
                   SELECT u.name AS name
                     FROM asset_usernames au JOIN usernames u ON u.id = au.username_id
                    WHERE au.asset_id = a.id
                    UNION
                   SELECT u.display_name
                     FROM asset_usernames au JOIN usernames u ON u.id = au.username_id
                    WHERE au.asset_id = a.id AND u.display_name IS NOT NULL) AS named), '')
         AS usernames,
       -- The names somebody gave their own groupings. A vaulted collection's name is left in, the
       -- same as a vaulted person's, because concealment here is decided when the search RUNS:
       -- the scoped asset query never offers a concealed asset for the index to match against.
       -- Filtering at write time instead would mean the index disagreed with the tables it is a
       -- cache of, and would go stale the moment something was un-vaulted.
       COALESCE((SELECT group_concat(c.name, ' ')
                   FROM collection_items ci JOIN collections c ON c.id = ci.collection_id
                  WHERE ci.asset_id = a.id), '') AS collections,
       -- Written as a UNION subquery rather than `group_concat(DISTINCT pl.name)`, which is legal
       -- but cannot also take a separator: it would join with a comma where every other column
       -- here joins with a space, and adding the separator later is a syntax error rather than an
       -- edit. UNION supplies the DISTINCT, and the separator stays explicit like the rest.
       -- A site's OTHER names go in beside its own, exactly as a person's aliases do above. A site
       -- that has been linked to a stash-box usually carries three or four of them, and a library
       -- where searching `NORTHLIGHT` finds nothing because the site is filed as `Northlight Raw` is a
       -- search that is wrong in the way people notice.
       COALESCE((SELECT group_concat(sites.name, ' ') FROM (
                   SELECT DISTINCT pl.name AS name
                     FROM asset_usernames aa
                     JOIN usernames ac ON ac.id = aa.username_id
                     JOIN sites pl ON pl.id = ac.site_id
                    WHERE aa.asset_id = a.id
                   UNION
                   SELECT DISTINCT pa.alias AS name
                     FROM asset_usernames aa
                     JOIN usernames ac ON ac.id = aa.username_id
                     JOIN site_aliases pa ON pa.site_id = ac.site_id
                    WHERE aa.asset_id = a.id) AS sites), '') AS sites
  FROM assets a
 WHERE (:asset_id IS NULL OR a.id = :asset_id)
   -- A named set, for a selection written in one go. Uncorrelated and indexed on the primary key,
   -- and NULL means "not filtering by a set" the same way `:asset_id` does. Both are bound on
   -- every call: SQLite refuses a statement with a named parameter nobody supplied.
   AND (:asset_ids IS NULL OR a.id IN (SELECT value FROM json_each(:asset_ids)))
   -- Paged by the last id read, never by OFFSET: a rebuild commits between pages (below), and an
   -- asset landing between two pages would shift an OFFSET so that one row is read twice or not at
   -- all. The id is the primary key, so this is one range per page whatever the offset would be.
   AND (:after IS NULL OR a.id > :after)
 ORDER BY a.id
 LIMIT :limit
"""

# Delete before insert on every path but a rebuild, so a row is never written twice; through the
# map's rowid, since `asset_id` is UNINDEXED and VACUUM may renumber `assets.rowid`.
_DELETE_ROWS = """
DELETE FROM assets_fts
 WHERE rowid IN (SELECT fts_rowid FROM assets_fts_rows
                  WHERE asset_id IN (SELECT value FROM json_each(?)))
"""
_FORGET_ROWS = "DELETE FROM assets_fts_rows WHERE asset_id IN (SELECT value FROM json_each(?))"
# A plain INSERT: `search_unindexed`'s triggers must see every removal as a DELETE.
_REMEMBER_ROWS = """
INSERT INTO assets_fts_rows (asset_id, fts_rowid)
SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]') FROM json_each(?)
"""

_INSERT_ROW = """
INSERT INTO assets_fts
       (asset_id, title, filename, path, tags, people, usernames, collections, sites, music)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

#: Which of a rebuild page's files another write has indexed since the rebuild emptied it.
_ALREADY_WRITTEN = (
    "SELECT asset_id FROM assets_fts_rows WHERE asset_id IN (SELECT value FROM json_each(?))"
)

_CLEAR = "DELETE FROM assets_fts"
_CLEAR_ROWS = "DELETE FROM assets_fts_rows"

#: Whether any asset has no row yet: one read of the number triggers keep (`search_unindexed`).
_ANY_UNINDEXED = "SELECT n FROM search_unindexed WHERE id = 1"

_NOT_YET_INDEXED = """
SELECT a.id FROM assets a
 WHERE NOT EXISTS (SELECT 1 FROM assets_fts_rows m WHERE m.asset_id = a.id)
 LIMIT :limit
"""

#: Index rows naming an asset that no longer exists: an FTS5 table carries no keys, and a removed
#: file must not stay findable.
# NOT EXISTS, not NOT IN: one NULL id would make NOT IN delete nothing.
_DELETE_ORPHANS = """
DELETE FROM assets_fts
 WHERE rowid IN (SELECT m.fts_rowid FROM assets_fts_rows m
                  WHERE NOT EXISTS (SELECT 1 FROM assets a WHERE a.id = m.asset_id))
"""

_FORGET_ORPHANS = """
DELETE FROM assets_fts_rows
 WHERE NOT EXISTS (SELECT 1 FROM assets a WHERE a.id = assets_fts_rows.asset_id)
"""


async def index_assets(
    database: Database,
    *,
    asset_id: str | None = None,
    asset_ids: Sequence[str] | None = None,
    rebuild: bool = False,
    sweep: bool = True,
) -> int:
    """Write the index for one asset, a named set, or all of them, the same rows by every path;
    returns how many."""
    if asset_ids is not None and not asset_ids:
        return 0
    if not rebuild:
        async with database.write() as connection:
            written = await _write_pages(connection, asset_id=asset_id, asset_ids=asset_ids)
            if sweep:
                await _sweep_orphans(connection)
        return written

    # One transaction per page, so the write lock is held briefly and a stopped run is finished by
    # `index_new_assets`.
    async with database.write() as connection:
        await connection.execute(_CLEAR)
        await connection.execute(_CLEAR_ROWS)
    written = 0
    after: str | None = None
    while True:
        async with database.write() as connection:
            rows = await _read_page(connection, asset_id=None, asset_ids=None, after=after)
            await _write_page(connection, await _not_written_since(connection, rows), replace=False)
        written += len(rows)
        if len(rows) < BATCH:
            break
        after = str(rows[-1]["asset_id"])
    async with database.write() as connection:
        await _sweep_orphans(connection)
    return written


async def index_on(connection: Connection, asset_ids: Sequence[str]) -> int:
    """`index_assets` for a named set in the caller's own transaction; returns how many rows."""
    if not asset_ids:
        return 0
    written = await _write_pages(connection, asset_id=None, asset_ids=asset_ids)
    await _sweep_orphans(connection)
    return written


async def _read_page(
    connection: Connection,
    *,
    asset_id: str | None,
    asset_ids: Sequence[str] | None,
    after: str | None,
) -> list[Any]:
    return list(
        await connection.execute_fetchall(
            _TEXT_OF_ASSETS,
            {
                "asset_id": asset_id,
                "asset_ids": json.dumps(list(asset_ids)) if asset_ids is not None else None,
                "after": after,
                "limit": BATCH,
            },
        )
    )


async def _write_pages(
    connection: Connection, *, asset_id: str | None, asset_ids: Sequence[str] | None
) -> int:
    """Every page of the named rows, in the caller's transaction."""
    written = 0
    after: str | None = None
    while True:
        rows = await _read_page(connection, asset_id=asset_id, asset_ids=asset_ids, after=after)
        await _write_page(connection, rows, replace=True)
        written += len(rows)
        if len(rows) < BATCH:
            return written
        after = str(rows[-1]["asset_id"])


async def _not_written_since(connection: Connection, rows: Sequence[Any]) -> list[Any]:
    """The rows of a rebuild page no other write has indexed since, or a second insert would refuse
    the page."""
    if not rows:
        return []
    ids = [str(row["asset_id"]) for row in rows]
    found = await connection.execute_fetchall(_ALREADY_WRITTEN, (json.dumps(ids),))
    done = {str(row["asset_id"]) for row in found}
    return [row for row in rows if str(row["asset_id"]) not in done]


async def sweep_orphans(database: Database) -> None:
    """The orphan sweep on its own, for a caller that wrote its sets with `sweep=False`."""
    async with database.write() as connection:
        await _sweep_orphans(connection)


async def _sweep_orphans(connection: Connection) -> None:
    # Swept on every pass: a deletion arrives here with no text left to write.
    await connection.execute(_DELETE_ORPHANS)
    await connection.execute(_FORGET_ORPHANS)


async def _write_page(connection: Connection, rows: Sequence[Any], *, replace: bool) -> None:
    """Write one page of rows, every index row and then every map row: per row, the map's trigger
    would flush FTS5 each time."""
    if replace and rows:
        named = json.dumps([str(row["asset_id"]) for row in rows])
        await connection.execute(_DELETE_ROWS, (named,))
        await connection.execute(_FORGET_ROWS, (named,))
    rowids: list[tuple[str, int]] = []
    for row in rows:
        cursor = await connection.execute(
            _INSERT_ROW,
            (
                row["asset_id"],
                row["title"],
                row["filename"],
                row["path"],
                row["tags"],
                row["people"],
                row["usernames"],
                row["collections"],
                row["sites"],
                row["music"],
            ),
        )
        rowids.append((str(row["asset_id"]), int(cursor.lastrowid or 0)))
    if rowids:
        await connection.execute(_REMEMBER_ROWS, (json.dumps(rowids),))


_UNINDEXED_COUNT = """
SELECT COUNT(*) AS waiting FROM assets a
 WHERE NOT EXISTS (SELECT 1 FROM assets_fts_rows m WHERE m.asset_id = a.id)
"""


async def unindexed_count(database: Database) -> int:
    """How many assets have no row in the index yet, counted whole, for the pass about to do the
    work."""
    rows = await database.fetch_all(_UNINDEXED_COUNT)
    return int(rows[0]["waiting"]) if rows else 0


async def anything_unindexed(database: Database) -> bool:
    """Whether any asset has no row in the index yet, read off the kept number."""
    rows = await database.fetch_all(_ANY_UNINDEXED)
    return bool(rows) and int(rows[0]["n"]) > 0


async def index_new_assets(database: Database, *, limit: int = 2_000) -> int:
    """Index the assets with no row yet, and nothing else; edits are the periodic rebuild's."""
    written = 0
    async with database.write() as connection:
        rows = list(await connection.execute_fetchall(_NOT_YET_INDEXED, {"limit": limit}))
        texts: list[Any] = []
        for row in rows:
            texts.extend(
                await connection.execute_fetchall(
                    _TEXT_OF_ASSETS,
                    # Every named parameter must be bound, or the statement is refused.
                    {"asset_id": row["id"], "asset_ids": None, "after": None, "limit": 1},
                )
            )
        await _write_page(connection, texts, replace=False)
        written += len(texts)
    return written


# --- what the end of an asset takes out of here ------------------------------------------------


class _ForgetFromSearch:
    """Clearing deleted assets out of the keyless full-text index, through `index_assets`."""

    name = "search-index"

    tables = ("assets_fts", "assets_fts_rows")

    def __init__(self, database: Database) -> None:
        self._database = database

    async def forget(self, asset_ids: Sequence[str]) -> int:
        await index_assets(self._database, asset_ids=list(asset_ids))
        # The number is the files taken out, not the rows written.
        return len(asset_ids)


register_forgetting(_ForgetFromSearch.name, _ForgetFromSearch.tables, _ForgetFromSearch)
