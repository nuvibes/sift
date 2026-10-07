# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shape of the library, unscoped: which folders hold what, and what they are called.

A background pass that reads the library as a whole needs this and cannot get it from the access
layer, which answers "what may THIS user see", and a pass runs for nobody. It is the same need
duplicate-finding has, answered the same way and in the same place: the tables live in the kernel
because the permission resolver joins them, so the unscoped reads of them live here too.

Nothing here reaches a screen. A feature that puts any of this in front of somebody resolves it
through the access layer first, against whoever is asking.

The chain of names is assembled by the query rather than walked afterwards. A walk per folder over
five thousand folders is five thousand round trips for a fact one recursive statement produces in
one, and the pass that wants it runs every time a batch of imports settles.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from sift.kernel.db import Connection, Database, in_clause

#: What separates one folder name from the next inside the assembled chain.
#:
#: A newline, because there is no character a filesystem refuses in a name and this is the least
#: likely of them. A folder whose name contains one produces a chain with an extra element: a
#: reading that is wrong for that one folder, rather than a query that fails or a name silently
#: cut in half.
SEPARATOR = "\n"

_FOLDER_TREE = """
WITH RECURSIVE
chain(id, root_id, rel_path, name, names) AS (
  SELECT f.id, f.root_id, f.rel_path, f.name, f.name
    FROM folders f
   WHERE f.parent_id IS NULL
  UNION ALL
  SELECT f.id, f.root_id, f.rel_path, f.name, c.names || char(10) || f.name
    FROM chain c
    JOIN folders f ON f.parent_id = c.id AND f.root_id = c.root_id
),
held(folder_id, files, newest) AS (
  SELECT l.folder_id, COUNT(DISTINCT l.asset_id), MAX(a.added_at)
    FROM asset_locations l
    JOIN assets a ON a.id = l.asset_id
   WHERE l.folder_id IS NOT NULL
   GROUP BY l.folder_id
)
SELECT c.id, c.root_id, c.rel_path, c.names,
       h.files AS files, COALESCE(h.newest, 0) AS newest
  FROM chain c
  JOIN held h ON h.folder_id = c.id
"""

# Everything under one folder, however deep. Read off the folder's ancestry (every folder that
# lists it as an ancestor, itself included), because what a caller means by "this folder" includes
# what is filed inside it: a person's folder holding `Videos` and `Pics` is one subject, not three.
# Not a prefix test on the stored path, which no index can answer.
_ASSETS_UNDER = """
SELECT DISTINCT l.asset_id AS id
  FROM folder_ancestry an
  JOIN asset_locations l ON l.folder_id = an.folder_id
 WHERE an.ancestor_id = :folder_id
"""

# The same for several folders together, each file named with the folder it was asked under.
_ASSETS_UNDER_MANY = """
SELECT DISTINCT an.ancestor_id AS folder, l.asset_id AS id
  FROM folder_ancestry an
  JOIN asset_locations l ON l.folder_id = an.folder_id
 WHERE an.ancestor_id IN (?*)
"""

_FOLDERS_PER_ASK = 500

# The names of the files sitting DIRECTLY in one folder, which is a different question from the one
# above: a naming convention shared by a parent and a child is two facts, not one.
_FILENAMES_IN = """
SELECT a.original_filename AS name
  FROM asset_locations l
  JOIN assets a ON a.id = l.asset_id
 WHERE l.folder_id = ?
   AND a.original_filename IS NOT NULL
"""

# The same question asked about several folders together, grouped by the folder each name sits in.
#
# The per-folder version above, asked once per folder inside a pass over every folder that has
# moved, is one round trip per folder on a full rebuild and the whole library's filenames read a
# folder at a time. This asks once for the set the caller actually needs.
_FILENAMES_IN_MANY = """
SELECT l.folder_id AS folder_id, a.original_filename AS name
  FROM asset_locations l
  JOIN assets a ON a.id = l.asset_id
 WHERE l.folder_id IN (?*)
   AND a.original_filename IS NOT NULL
"""

_FILES_IN = """
SELECT a.id AS id, a.original_filename AS name
  FROM asset_locations l
  JOIN assets a ON a.id = l.asset_id
 WHERE l.folder_id = ?
   AND a.original_filename IS NOT NULL
"""

_FOLDER_PLACE = "SELECT root_id, rel_path FROM folders WHERE id = ?"

#: A folder as a line says it: its path inside the library, or its name for the top one.
_FOLDER_SAID = "SELECT rel_path, name FROM folders WHERE id = ?"

#: The folder of this name directly inside another, by the index on the parent and the name.
_FOLDER_NAMED = "SELECT id, root_id, rel_path FROM folders WHERE parent_id = ? AND name = ?"

#: Every folder below one, as a range over the paths that begin with its path and a slash. `0` is
#: the character after `/`, so the range is exactly the paths inside, read off the folder index.
_FOLDERS_INSIDE = (
    "SELECT id, rel_path FROM folders WHERE root_id = ? AND rel_path > ? AND rel_path < ?"
)

# Somewhere to put the per-file numbers a caller wants folded, for the length of one fold.
#
# TEMPORARY, so it belongs to the connection rather than to the database: nothing is written to the
# library file, no lock is taken against the writer, and it disappears when the connection does.
# Two folds cannot collide over it because a fold runs on the sweep lane and the lane admits one
# pass at a time (see `Database.sweep`).
_FOLD_INPUT = """
CREATE TEMP TABLE IF NOT EXISTS fold_input (
  asset_id TEXT PRIMARY KEY,
  value    INTEGER NOT NULL,
  stamp    INTEGER NOT NULL,
  counted  INTEGER NOT NULL
)
"""

_EMPTY_FOLD_INPUT = "DELETE FROM fold_input"

_ADD_TO_FOLD = (
    "INSERT OR REPLACE INTO fold_input (asset_id, value, stamp, counted) VALUES (?,?,?,?)"
)

# The fold itself: per-file numbers turned into per-folder ones, by the database.
#
# A LEFT JOIN so a folder whose files are all absent from the input still comes back, with zeroes.
# That is load-bearing: a folder nothing has looked at
# yet is a different answer from a folder that is not there.
_FOLD_BY_FOLDER = """
SELECT l.folder_id                     AS folder_id,
       COALESCE(SUM(f.value), 0)       AS summed,
       COALESCE(MAX(f.stamp), 0)       AS newest,
       COALESCE(SUM(f.counted), 0)     AS counted
  FROM asset_locations l
  LEFT JOIN fold_input f ON f.asset_id = l.asset_id
 WHERE l.folder_id IS NOT NULL
 GROUP BY l.folder_id
"""

# Every folder there is, by where it sits. Folders that hold no files directly are in it too, which
# is the point: what a caller wants to say something ABOUT is often a folder whose files are all one
# level further down.
_EVERY_FOLDER = "SELECT id, root_id, rel_path FROM folders"


#: How many folders one statement is asked about. A bound list is bound one value at a time and
#: SQLite refuses past a ceiling, so a caller with thousands of folders is served by several
#: statements rather than by one that fails.
_CHUNK = 500


def _batched(ids: Sequence[str]) -> list[list[str]]:
    return [list(ids[start : start + _CHUNK]) for start in range(0, len(ids), _CHUNK)]


@dataclass(frozen=True, slots=True)
class FolderTotals:
    """What one folder's files add up to, for numbers the caller supplied per file."""

    #: The sum of the value each file carried.
    summed: int
    #: The largest stamp any of them carried.
    newest: int
    #: How many of the folder's files the caller marked as counting.
    counted: int


@dataclass(frozen=True, slots=True)
class FolderNode:
    """One folder that holds files, with the names above it already assembled."""

    id: str
    root_id: str
    rel_path: str
    name: str
    chain: tuple[str, ...]
    files: int
    newest: int


class TreeReads:
    """The library's folder shape, for a pass that runs for nobody."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def folders_with_files(self) -> list[FolderNode]:
        """Every folder holding files directly, each with the chain of names above it."""
        rows = await self._db.sweep_all(_FOLDER_TREE, what="folder tree")
        found = []
        for row in rows:
            chain = tuple(str(row["names"]).split(SEPARATOR))
            found.append(
                FolderNode(
                    id=str(row["id"]),
                    root_id=str(row["root_id"]),
                    rel_path=str(row["rel_path"]),
                    name=chain[-1],
                    chain=chain,
                    files=int(row["files"]),
                    newest=int(row["newest"]),
                )
            )
        return found

    async def assets_under(self, folder_id: str) -> list[str]:
        """Every file in a folder and everything below it."""
        rows = await self._db.fetch_all(_ASSETS_UNDER, {"folder_id": folder_id})
        return [str(row["id"]) for row in rows]

    async def assets_under_many(self, folder_ids: Sequence[str]) -> dict[str, list[str]]:
        """`assets_under` for each of these folders, every one of them a key."""
        wanted = list(dict.fromkeys(folder_ids))
        under: dict[str, list[str]] = {one: [] for one in wanted}
        for start in range(0, len(wanted), _FOLDERS_PER_ASK):
            sql, params = in_clause(_ASSETS_UNDER_MANY, wanted[start : start + _FOLDERS_PER_ASK])
            for row in await self._db.fetch_all(sql, params):
                under[str(row["folder"])].append(str(row["id"]))
        return under

    async def filenames_in(self, folder_id: str) -> list[str]:
        """What the files sitting directly in one folder are called."""
        rows = await self._db.fetch_all(_FILENAMES_IN, (folder_id,))
        return [str(row["name"]) for row in rows]

    async def filenames_by_folder(self, folder_ids: Sequence[str]) -> dict[str, list[str]]:
        """What the files are called in each of these folders, in one read per batch.

        For a pass that has a list of folders in front of it. Asking per folder is one round trip
        each, and a rebuild that touches every folder in a library then costs a query per folder,
        seconds of database time even on a small library, for an answer that is a single
        statement.

        Bounded to the folders the caller names rather than grouped over the whole table, which is
        the distinction that matters: a pass where three folders moved should read three folders'
        filenames, not two hundred thousand rows to answer about three.

        Batched because a bound list is bound one value at a time and SQLite has a ceiling on how
        many. A folder with no named files is simply absent, which is what a caller reading it with
        a default already expects.
        """
        found: dict[str, list[str]] = {}
        for batch in _batched(folder_ids):
            query, bound = in_clause(_FILENAMES_IN_MANY, batch)
            for row in await self._db.fetch_all(query, bound):
                found.setdefault(str(row["folder_id"]), []).append(str(row["name"]))
        return found

    async def files_in(self, folder_id: str) -> list[tuple[str, str]]:
        """The files sitting directly in one folder, as `(asset_id, filename)`.

        The pair rather than the name, for a caller that has to act on the file its reading of the
        name applies to: a folder where every filename carries a different person is answered one
        file at a time, and a list of names alone cannot say which.
        """
        rows = await self._db.fetch_all(_FILES_IN, (folder_id,))
        return [(str(row["id"]), str(row["name"])) for row in rows]

    async def files_in_on(self, connection: Connection, folder_id: str) -> list[tuple[str, str]]:
        """The same list, on a connection the caller already holds.

        A caller inside a write transaction cannot ask the database for a second connection: the
        write guard is not reentrant and the second request waits on the first for ever.
        """
        rows = await (await connection.execute(_FILES_IN, (folder_id,))).fetchall()
        return [(str(row["id"]), str(row["name"])) for row in rows]

    async def place_of(self, folder_id: str) -> tuple[str, str] | None:
        """Which library a folder is in and where, or None if it has gone."""
        row = await self._db.fetch_one(_FOLDER_PLACE, (folder_id,))
        return None if row is None else (str(row["root_id"]), str(row["rel_path"]))

    async def said_on(self, connection: Connection, folder_id: str) -> str | None:
        """A folder as a line says it (its path inside the library, or its name for the top one),
        or None if it has gone. On a connection the caller already holds, for the reason
        `files_in_on` gives: a record written inside a write names the folder it is about.
        """
        row = await (await connection.execute(_FOLDER_SAID, (folder_id,))).fetchone()
        return None if row is None else str(row["rel_path"]) or str(row["name"])

    async def folder_named(self, parent_id: str, name: str) -> tuple[str, str, str] | None:
        """The folder called `name` directly inside another, as `(id, root_id, rel_path)`, or
        None where there is none."""
        row = await self._db.fetch_one(_FOLDER_NAMED, (parent_id, name))
        if row is None:
            return None
        return str(row["id"]), str(row["root_id"]), str(row["rel_path"])

    async def folders_inside(self, root_id: str, rel_path: str) -> list[tuple[str, str]]:
        """Every folder below the one at `rel_path`, at any depth, as `(id, rel_path)`.

        Never the library's own folder: its path is empty, and every folder of the library is
        inside it, which a caller asks for with `folder_ids`.
        """
        if not rel_path:
            return []
        rows = await self._db.fetch_all(_FOLDERS_INSIDE, (root_id, f"{rel_path}/", f"{rel_path}0"))
        return [(str(row["id"]), str(row["rel_path"])) for row in rows]

    async def folder_ids(self) -> dict[tuple[str, str], str]:
        """Every folder keyed by where it sits, as `(root_id, rel_path) -> id`.

        The whole tree rather than the folders holding files, because a caller reading a chain of
        names ends up pointing at one of the folders ABOVE the files and needs its id.
        """
        rows = await self._db.sweep_all(_EVERY_FOLDER, what="every folder")
        return {(str(row["root_id"]), str(row["rel_path"])): str(row["id"]) for row in rows}

    async def totals_by_folder(
        self, values: Mapping[str, tuple[int, int]], counted: Collection[str]
    ) -> dict[str, FolderTotals]:
        """Per-file numbers, added up per folder, by the database.

        For the passes that have to say something per FOLDER about a fact stored per FILE, which
        is most of them. The caller supplies `values[asset] = (value, stamp)` and a set of files
        that `counted` should count; every folder holding files comes back with the sum of the
        values, the largest stamp, and how many of its files were in that set.

        **Not the whole file-to-folder map, with the arithmetic done in Python**: that is three
        reads of thousands of rows to produce a handful of numbers. The fold below costs less time
        and much less Python memory, and the difference widens with the
        library, because the map is the fastest-growing of the three and it never crosses into
        Python at all.

        **It is also the better boundary**, which is the part worth keeping.
        A feature holding the file-to-folder map of an entire library has more than it needs and
        more than it should be trusted with; what it needs is the total. The feature still never
        names a library table, and this module still never learns what the numbers mean: they are
        a value and a stamp, and whether they are faces or anything else is not asked.

        Nothing is written to the library file: the input lives in a temporary table belonging to
        the connection, which takes no lock against the writer and vanishes with it.
        """
        marked = set(counted)
        rows: list[tuple[str, int, int, int]] = [
            (asset_id, value, stamp, 1 if asset_id in marked else 0)
            for asset_id, (value, stamp) in values.items()
        ]
        rows.extend((asset_id, 0, 0, 1) for asset_id in marked if asset_id not in values)

        async with self._db.sweep("totals per folder") as connection:
            await connection.execute(_FOLD_INPUT)
            await connection.execute(_EMPTY_FOLD_INPUT)
            if rows:
                await connection.executemany(_ADD_TO_FOLD, rows)
            found = await connection.execute_fetchall(_FOLD_BY_FOLDER)

        return {
            str(row["folder_id"]): FolderTotals(
                summed=int(row["summed"]),
                newest=int(row["newest"]),
                counted=int(row["counted"]),
            )
            for row in found
        }
