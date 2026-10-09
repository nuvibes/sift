# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shape of the library, unscoped, for passes that run for nobody; none of it reaches a screen.

The chain of folder names is built by one recursive query rather than a walk per folder."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from sift.kernel.db import Connection, Database, in_clause

#: A newline, the least likely character in a folder name; one containing it reads one extra part.
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

# Everything under one folder at any depth, by ancestry, as a path prefix test uses no index.
_ASSETS_UNDER = """
SELECT DISTINCT l.asset_id AS id
  FROM folder_ancestry an
  JOIN asset_locations l ON l.folder_id = an.folder_id
 WHERE an.ancestor_id = :folder_id
"""

# Only files whose present copy sits under the folder.
_ASSETS_PRESENT_UNDER = """
SELECT DISTINCT l.asset_id AS id
  FROM folder_ancestry an
  JOIN asset_locations l ON l.folder_id = an.folder_id
 WHERE an.ancestor_id = :folder_id AND l.status = 'present'
"""

# The same for several folders, each file named with the folder it was asked under.
_ASSETS_UNDER_MANY = """
SELECT DISTINCT an.ancestor_id AS folder, l.asset_id AS id
  FROM folder_ancestry an
  JOIN asset_locations l ON l.folder_id = an.folder_id
 WHERE an.ancestor_id IN (?*)
"""

_FOLDERS_PER_ASK = 500

# Who each file's folders were answered as, up the ancestry, present copies only.
_PEOPLE_ANSWERED_FOR = """
SELECT DISTINCT l.asset_id AS asset_id, fp.person_id AS person_id
  FROM asset_locations AS l
  JOIN folder_ancestry AS an ON an.folder_id = l.folder_id
  JOIN folder_people AS fp ON fp.folder_id = an.ancestor_id
 WHERE l.asset_id IN (?*) AND l.status = 'present'
"""

_FILES_PER_ASK = 500


async def people_answered_for(db: Database, asset_ids: Iterable[str]) -> dict[str, frozenset[str]]:
    """The people each file's folders were answered as, by file; a file under none is absent."""
    wanted = list(dict.fromkeys(asset_ids))
    found: dict[str, set[str]] = {}
    for start in range(0, len(wanted), _FILES_PER_ASK):
        sql, params = in_clause(_PEOPLE_ANSWERED_FOR, wanted[start : start + _FILES_PER_ASK])
        for row in await db.fetch_all(sql, tuple(params)):
            found.setdefault(str(row["asset_id"]), set()).add(str(row["person_id"]))
    return {asset_id: frozenset(people) for asset_id, people in found.items()}


# Names of the files directly in one folder, apart from those below it.
_FILENAMES_IN = """
SELECT a.original_filename AS name
  FROM asset_locations l
  JOIN assets a ON a.id = l.asset_id
 WHERE l.folder_id = ?
   AND a.original_filename IS NOT NULL
"""

# The same for several folders together, grouped by folder, instead of a trip per folder.
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

#: A folder as a line says it: its path, or its name for the top one.
_FOLDER_SAID = "SELECT rel_path, name FROM folders WHERE id = ?"

#: The folder of this name directly inside another.
_FOLDER_NAMED = "SELECT id, root_id, rel_path FROM folders WHERE parent_id = ? AND name = ?"

#: Every folder below one, as a range of paths; `0` is the character after `/`.
_FOLDERS_INSIDE = (
    "SELECT id, rel_path FROM folders WHERE root_id = ? AND rel_path > ? AND rel_path < ?"
)

# Per-file numbers to fold, in a connection's temporary table: no write to the library, and
# the sweep lane admits one fold at a time.
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

# The fold: a LEFT JOIN, so a folder none of whose files were given still answers with zeroes.
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

# Every folder, including those holding files only further down.
_EVERY_FOLDER = "SELECT id, root_id, rel_path FROM folders"


#: Folders per statement, under SQLite's ceiling on bound values.
_CHUNK = 500


def _batched(ids: Sequence[str]) -> list[list[str]]:
    return [list(ids[start : start + _CHUNK]) for start in range(0, len(ids), _CHUNK)]


@dataclass(frozen=True, slots=True)
class FolderTotals:
    """What one folder's files add up to, for numbers the caller supplied per file."""

    summed: int
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

    async def assets_present_under(self, folder_id: str) -> list[str]:
        """`assets_under`, counting only the files whose present copy is there."""
        rows = await self._db.fetch_all(_ASSETS_PRESENT_UNDER, {"folder_id": folder_id})
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
        """What the files are called in each named folder, one read per batch; empty ones absent."""
        found: dict[str, list[str]] = {}
        for batch in _batched(folder_ids):
            query, bound = in_clause(_FILENAMES_IN_MANY, batch)
            for row in await self._db.fetch_all(query, bound):
                found.setdefault(str(row["folder_id"]), []).append(str(row["name"]))
        return found

    async def files_in(self, folder_id: str) -> list[tuple[str, str]]:
        """The files directly in one folder as `(asset_id, filename)`, to act on each."""
        rows = await self._db.fetch_all(_FILES_IN, (folder_id,))
        return [(str(row["id"]), str(row["name"])) for row in rows]

    async def files_in_on(self, connection: Connection, folder_id: str) -> list[tuple[str, str]]:
        """The same, on a held connection: the write guard is not reentrant."""
        rows = await (await connection.execute(_FILES_IN, (folder_id,))).fetchall()
        return [(str(row["id"]), str(row["name"])) for row in rows]

    async def place_of(self, folder_id: str) -> tuple[str, str] | None:
        """Which library a folder is in and where, or None if it has gone."""
        row = await self._db.fetch_one(_FOLDER_PLACE, (folder_id,))
        return None if row is None else (str(row["root_id"]), str(row["rel_path"]))

    async def said_on(self, connection: Connection, folder_id: str) -> str | None:
        """A folder as a line says it, or None if gone, on a held connection inside a write."""
        row = await (await connection.execute(_FOLDER_SAID, (folder_id,))).fetchone()
        return None if row is None else str(row["rel_path"]) or str(row["name"])

    async def folder_named(self, parent_id: str, name: str) -> tuple[str, str, str] | None:
        """The folder called `name` directly in another, as `(id, root_id, rel_path)`, or None."""
        row = await self._db.fetch_one(_FOLDER_NAMED, (parent_id, name))
        if row is None:
            return None
        return str(row["id"]), str(row["root_id"]), str(row["rel_path"])

    async def folders_inside(self, root_id: str, rel_path: str) -> list[tuple[str, str]]:
        """Every folder below `rel_path` at any depth, as `(id, rel_path)`; never the root's."""
        if not rel_path:
            return []
        rows = await self._db.fetch_all(_FOLDERS_INSIDE, (root_id, f"{rel_path}/", f"{rel_path}0"))
        return [(str(row["id"]), str(row["rel_path"])) for row in rows]

    async def folder_ids(self) -> dict[tuple[str, str], str]:
        """Every folder keyed by `(root_id, rel_path)`, holding files or not."""
        rows = await self._db.sweep_all(_EVERY_FOLDER, what="every folder")
        return {(str(row["root_id"]), str(row["rel_path"])): str(row["id"]) for row in rows}

    async def totals_by_folder(
        self, values: Mapping[str, tuple[int, int]], counted: Collection[str]
    ) -> dict[str, FolderTotals]:
        """Per-file `(value, stamp)` summed per folder by the database, with how many count.

        The feature never holds the file-to-folder map, and the input lives in a temporary table."""
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
