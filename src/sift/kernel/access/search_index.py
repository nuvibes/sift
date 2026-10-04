# SPDX-License-Identifier: AGPL-3.0-or-later
"""Writing the search index: the one path into `assets_fts`.

The table is next door in `schema.py`, in the kernel, because the permission resolver has to join
it: free text filters the scoped read from inside the statement that also decides visibility, and
a table the resolver joins cannot be created by a feature built on top of the resolver.

This is the write path that fills it, kept beside the table for the same reason `catalog.py` keeps
the site and username upsert beside theirs: a feature that needs a row written does not grow its
own copy of the statement. It also has to be here for a harder reason. Assembling the text means
reading `assets` and `asset_locations` directly, unscoped, and nothing outside the kernel is
allowed to do that, rightly, since a query written in a feature returns rows to anyone.

Unscoped is correct HERE, and it is worth saying why rather than leaving it to be assumed. The index
is built on behalf of nobody: there is no viewer, no request, and no page. It has to contain every
asset, because who may SEE a row is decided when the index is read, by the resolver, against the
person actually asking. An index built per viewer would be both wrong and enormous.

The index is a CACHE, and that is a constraint rather than a description. The tables are the truth,
and this can be dropped and rebuilt at any time, which is the property that makes it safe to have
at all. So there is one function that writes a row, and both ways in call it: reindexing one asset
because somebody added a tag, and rebuilding all of them from empty, produce byte-identical rows.
Two code paths here would mean the index depended on the order things happened in, and nobody would
notice, because both would produce an index that looked fine.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sift.kernel.db import Connection, Database
from sift.kernel.forgetting import register_forgetting

#: How many assets one pass writes before going round again. A rebuild of a large library is a long
#: job, and the batch is what lets it be interrupted between passes rather than only at the end.
BATCH = 500


# Everything an asset can be found by, gathered in one statement so that one asset is one read.
#
# The joins are all LEFT: an asset with no tags, nobody in it and no username attached is still
# findable by its filename, and an INNER join anywhere here would quietly drop exactly the files
# that have had the least done to them, which are the ones somebody is most likely searching for
# by name because there is no other way to find them.
#
# Aliases are folded into the People column rather than kept apart. A person is findable by every
# name they go by (that is the same rule the alias resolver applies to a token), and the free-text
# half of search has to agree with the token half or the same query answers differently depending
# on which way it was written.
#
# `group_concat` with an explicit separator, for readability rather than for safety. Be clear about
# what it does NOT do: the trigram tokenizer indexes every run of three characters INCLUDING the
# separator, so `beach party` is matched by the substring `h pa` just as `beach,party` is matched by
# `h,pa`. Adjacent values can be matched across, whichever character sits between them. That is a
# false positive within what the viewer may already see (never a way to reach anything else),
# and removing it would mean one indexed row per value rather than one per asset.
#: The columns of a file's own record the statement below reads as free text. A write to a record
#: reindexes when any of these moved; named here, beside the statement, so the writer and the index
#: cannot disagree about which fields are words somebody can search for.
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

# Delete before insert on every path EXCEPT a rebuild, so writing a row twice leaves one row. The
# index has no primary key of its own (an FTS5 table is a text index, not a table with
# constraints), so nothing would stop a second insert from making the same asset match twice and
# appear twice in a result. A rebuild skips the delete because it emptied the table a moment ago;
# see `_write_page`, where the exception lives.
#
# The delete goes through the rowid recorded beside the asset id: an indexed lookup and a rowid
# delete. `WHERE asset_id = ?` would be a scan of the whole index, because `asset_id` is UNINDEXED
# (it has to be, or the ids would be searchable as text), and nothing about a trigram index
# makes an equality test on an unsearched column fast. The map table exists to buy exactly this.
#
# Not the FTS row's implicit rowid alone, and not `assets.rowid`: VACUUM is free to renumber the
# latter, and a backup being taken is not an acceptable way for search results to start pointing at
# the wrong files.
_FIND_ROW = "SELECT fts_rowid FROM assets_fts_rows WHERE asset_id = ?"
_DELETE_ROW = "DELETE FROM assets_fts WHERE rowid = ?"
_FORGET_ROW = "DELETE FROM assets_fts_rows WHERE asset_id = ?"
# A plain INSERT, never OR REPLACE: the row is always removed first (or the map emptied, on a
# rebuild), and `search_unindexed`'s triggers count on seeing every removal as a DELETE. A replace
# would delete silently and leave the kept count one out; a duplicate now fails loudly instead.
_REMEMBER_ROW = "INSERT INTO assets_fts_rows (asset_id, fts_rowid) VALUES (?, ?)"

_INSERT_ROW = """
INSERT INTO assets_fts
       (asset_id, title, filename, path, tags, people, usernames, collections, sites, music)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

#: Which of a rebuild page's files another write has indexed since the rebuild emptied the index.
_ALREADY_WRITTEN = (
    "SELECT asset_id FROM assets_fts_rows WHERE asset_id IN (SELECT value FROM json_each(?))"
)

_CLEAR = "DELETE FROM assets_fts"
_CLEAR_ROWS = "DELETE FROM assets_fts_rows"

#: Whether any asset has no row in the index yet (a new import, or anything a rebuild has not
#: reached). One row read: the number is KEPT by triggers (catalog v56, `search_unindexed` in
#: `schema.py`), because the anti-join it replaces had to visit every file to answer "none", which
#: is the answer a library at rest gives: tens of milliseconds at boot on a large library, and
#: minutes asked of the FTS table directly.
_ANY_UNINDEXED = "SELECT n FROM search_unindexed WHERE id = 1"

_NOT_YET_INDEXED = """
SELECT a.id FROM assets a
 WHERE NOT EXISTS (SELECT 1 FROM assets_fts_rows m WHERE m.asset_id = a.id)
 LIMIT :limit
"""

#: Rows in the index that name an asset which no longer exists. A delete elsewhere cascades through
#: real foreign keys; an FTS5 virtual table can carry none, so this is the sweep that stands in for
#: one. Without it a removed file stays findable by name. It would match nothing the viewer could
#: open, but it would still answer "yes, something by that name was here", which is precisely the
#: question search must not answer.
#
# `NOT EXISTS` rather than `NOT IN`, and the difference is not style. `assets.id` is TEXT PRIMARY
# KEY, and SQLite lets a TEXT primary key hold NULL: only INTEGER PRIMARY KEY, or an explicit NOT
# NULL, forbids it. One NULL id anywhere in `assets` puts a NULL in the subquery, and `x NOT IN
# (.., NULL)` is NULL rather than true for EVERY row, so the sweep would quietly delete nothing,
# for ever, with no error and no way to notice. `NOT EXISTS` compares row by row and is unaffected.
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
) -> int:
    """Write the index for one asset, a named set of them, or all of them.

    Returns how many rows were written.

    One function for all three, which is what makes the incremental paths and the full rebuild
    produce the same rows, and there is a test that rebuilds from empty and compares, because
    "the cache can always be rebuilt" is a claim that stops being true the moment two code paths
    write it.

    `asset_ids` is not a convenience wrapper around calling this in a loop, and the difference is
    the reason it exists. Every call opens its own write transaction and finishes by sweeping the
    index for rows whose asset no longer exists: two anti-joins over the whole map. Tagging a
    selection of five hundred clips through the single-asset path would be five hundred write-lock
    acquisitions and a thousand whole-table sweeps inside one request, competing with the job pool
    for the same lock. As a set it is one transaction and one pair of sweeps; the per-row work is
    unchanged, because that part really is per row.

    An empty sequence returns before opening a transaction. That is a saving, not a guard: an
    empty list binds as `[]`, `json_each` over it yields no rows, and the filter already matches
    nothing, so the early return buys the write lock not being taken and the orphan sweeps not
    running, for a call that was always going to write nothing. It is NOT what stops an empty
    selection being read as "no filter" and rebuilding the whole library: the filter already
    does that.

    `rebuild` empties the index first. Without it the pass still replaces every row it writes, so
    an incremental run does not disturb the rest.
    """
    if asset_ids is not None and not asset_ids:
        return 0
    if not rebuild:
        async with database.write() as connection:
            written = await _write_pages(connection, asset_id=asset_id, asset_ids=asset_ids)
            await _sweep_orphans(connection)
        return written

    # A rebuild is ONE TRANSACTION PER PAGE. The one write lock is then held for about a tenth of
    # a second at a time, so the boot that enqueues a rebuild after a migration emptied the index
    # can finish its own writes (without them the desktop shell gives up on a backend that never
    # reports ready), and a run stopped part-way leaves an index the catch-up pass
    # (`index_new_assets`) finishes rather than one to start again. The rows within a page are
    # written by `_write_page`, which is where the cost of a rebuild is.
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
    """`index_assets` for a named set, in the caller's own transaction: for a write that changes
    what files are filed under and must say so in the same breath (a catalog step has no other
    connection to hand). How many rows were written."""
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
    """Every page of the named rows, replacing what the index held for each, in the caller's
    transaction."""
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
    """The rows of a rebuild page that no other write has indexed since the rebuild emptied the
    index. A file changed while a rebuild runs (a tag, a merge) is indexed by its own write between
    two pages, with its current text, and a second insert here would refuse the whole page."""
    if not rows:
        return []
    ids = [str(row["asset_id"]) for row in rows]
    found = await connection.execute_fetchall(_ALREADY_WRITTEN, (json.dumps(ids),))
    done = {str(row["asset_id"]) for row in found}
    return [row for row in rows if str(row["asset_id"]) not in done]


async def _sweep_orphans(connection: Connection) -> None:
    # An asset named in the index but not in `assets` is a file that has been removed. Swept on
    # every pass rather than only on a rebuild: the single-asset path is also how a caller
    # reports a deletion, and at that point there is no row left to read text from, so the
    # loop above writes nothing and this is the only thing that acts.
    await connection.execute(_DELETE_ORPHANS)
    await connection.execute(_FORGET_ORPHANS)


async def _write_page(connection: Connection, rows: Sequence[Any], *, replace: bool) -> None:
    """Write one page of rows: every index row first, then every map row.

    GROUPED BY TABLE, NOT ROW BY ROW, and the difference is two orders of magnitude: five
    hundred index inserts and then five hundred map inserts against one index insert followed by
    its map insert, five hundred times. The map table carries a trigger (`search_unindexed`),
    so each map insert is a statement SQLite has to be able to roll back on its own, and opening
    that statement journal flushes the FTS5 table's pending buffer to disk: one segment write
    per row instead of one per page. The rowids come back from the index inserts in order, so the
    map rows are written afterwards from that list.
    """
    if replace:
        for row in rows:
            found = await connection.execute_fetchall(_FIND_ROW, (row["asset_id"],))
            for existing in found:
                await connection.execute(_DELETE_ROW, (existing["fts_rowid"],))
            await connection.execute(_FORGET_ROW, (row["asset_id"],))
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
    for asset_id, rowid in rowids:
        await connection.execute(_REMEMBER_ROW, (asset_id, rowid))


_UNINDEXED_COUNT = """
SELECT COUNT(*) AS waiting FROM assets a
 WHERE NOT EXISTS (SELECT 1 FROM assets_fts_rows m WHERE m.asset_id = a.id)
"""


async def unindexed_count(database: Database) -> int:
    """How many assets have no row in the index yet.

    Beside `anything_unindexed` rather than instead of it, and the two are not the same call made
    twice. That one stops at the first row and is asked on a request; this one counts the whole
    anti-join and is asked once, by the pass that is about to do the work, so it can say how big
    the work is before it starts. A denominator taken after the pass has begun is not one.
    """
    rows = await database.fetch_all(_UNINDEXED_COUNT)
    return int(rows[0]["waiting"]) if rows else 0


async def anything_unindexed(database: Database) -> bool:
    """Whether any asset has no row in the index yet. One indexed lookup, stopping at the first.

    Cheap enough to ask on a request, which is what makes the refresh able to be quiet: work is
    queued when there is some, and never otherwise. A kept number rather than a search; see
    `_ANY_UNINDEXED`.
    """
    rows = await database.fetch_all(_ANY_UNINDEXED)
    return bool(rows) and int(rows[0]["n"]) > 0


async def index_new_assets(database: Database, *, limit: int = 2_000) -> int:
    """Index the assets that have no row yet, and nothing else. Returns how many were written.

    The cheap pass, and the one that runs often. A rebuild costs about eight seconds at fifty
    thousand assets (affordable at boot, not affordable on a loop), whereas this touches only
    what has arrived since, which is normally nothing and costs one indexed anti-join to find out.

    It is deliberately blind to EDITS. An asset whose tags changed already has a row, so this will
    not revisit it; the periodic rebuild is what catches that. Splitting the two is what lets the
    frequent pass be frequent.
    """
    written = 0
    async with database.write() as connection:
        rows = list(await connection.execute_fetchall(_NOT_YET_INDEXED, {"limit": limit}))
        texts: list[Any] = []
        for row in rows:
            texts.extend(
                await connection.execute_fetchall(
                    _TEXT_OF_ASSETS,
                    # Every named parameter has to be bound, including the ones this path does not
                    # use: SQLite refuses a statement with a parameter nobody supplied, and the
                    # refusal surfaces as the catch-up pass quietly writing nothing.
                    {"asset_id": row["id"], "asset_ids": None, "after": None, "limit": 1},
                )
            )
        await _write_page(connection, texts, replace=False)
        written += len(texts)
    return written


# --- what the end of an asset takes out of here ------------------------------------------------


class _ForgetFromSearch:
    """Clearing deleted assets out of the full-text index.

    `assets_fts` is an FTS5 virtual table and takes no foreign key (that is SQLite's rule), and
    `assets_fts_rows` beside it is keyed to the index rather than to the library, so a key there
    would be answering a different question. Neither is reached by the cascade that clears
    everything else, and without this a removed file stays findable by name: it would match nothing
    the viewer could open, but it would still answer "yes, something by that name was here", which
    is precisely the question search must not answer.

    `index_assets` is what does it, unchanged and not a second path. Handed an id whose asset has
    gone it finds no text to write, and the orphan sweep it ends every pass with is the whole of
    the work, which is exactly why that sweep runs on every pass rather than only on a rebuild.
    """

    name = "search-index"

    #: Both of them, because both are keyless and for different reasons. `assets_fts` is virtual
    #: and SQLite hangs no key on one; `assets_fts_rows` is an ordinary table whose key would point
    #: at the index rather than at the library, so it would be answering the wrong question.
    tables = ("assets_fts", "assets_fts_rows")

    def __init__(self, database: Database) -> None:
        self._database = database

    async def forget(self, asset_ids: Sequence[str]) -> int:
        await index_assets(self._database, asset_ids=list(asset_ids))
        # What `index_assets` returns is how many rows it WROTE, which for a deleted asset is
        # always zero. The number worth reporting is how many files were taken out of the index,
        # and that is the set it was asked about.
        return len(asset_ids)


register_forgetting(_ForgetFromSearch.name, _ForgetFromSearch.tables, _ForgetFromSearch)
