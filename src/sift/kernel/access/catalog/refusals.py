# SPDX-License-Identifier: AGPL-3.0-or-later
"""Kept local and kept from swaps: the refusals a file, a person, a Site, a tag or a folder can
carry."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from sift.kernel.db import Connection, Database, PointRead, point_read

# What must never be sent outside this machine: catalog columns on catalog tables, kept here
# rather than in the stash-box slice because Sift's own passes read the refusal too.


#: Whether this thing is kept local, per kind: one point read per table, written out as `_MADE_BY`
#: is, keyed by the word the wire and the rows both use.
_KEPT_LOCAL: Mapping[str, PointRead] = {
    "asset": point_read("catalog.asset_kept_local", "SELECT keep_local FROM assets WHERE id = ?"),
    "person": point_read("catalog.person_kept_local", "SELECT keep_local FROM people WHERE id = ?"),
    "site": point_read("catalog.site_kept_local", "SELECT keep_local FROM sites WHERE id = ?"),
    "tag": point_read("catalog.tag_kept_local", "SELECT keep_local FROM tags WHERE id = ?"),
    "folder": point_read(
        "catalog.folder_kept_local", "SELECT keep_local FROM folders WHERE id = ?"
    ),
}

#: The kinds that carry "Don't enrich". A folder is never sent itself; its mark keeps the files
#: under it at home. A collection and a Photo Set are never sent.
KEPT_LOCAL_KINDS = tuple(_KEPT_LOCAL)

_SET_KEPT_LOCAL: Mapping[str, str] = {
    "asset": "UPDATE assets SET keep_local = ? WHERE id = ?",
    "person": "UPDATE people SET keep_local = ? WHERE id = ?",
    "site": "UPDATE sites SET keep_local = ? WHERE id = ?",
    "tag": "UPDATE tags SET keep_local = ? WHERE id = ?",
    "folder": "UPDATE folders SET keep_local = ? WHERE id = ?",
}


async def kept_local(database: Database, kind: str, local_id: str) -> bool:
    """Whether this thing must never be sent to a stash-box: the "enrich" purpose of `refused_here`.

    False for an unknown kind and for a missing row alike: neither is a refusal.
    """
    return await refused_here(database, "enrich", kind, local_id)


#: Whether a file is kept local by itself or by any person, tag or Site it is filed under, Sites
#: reaching up (`SITE_REACH`) so keeping a network local keeps its labels' files local, or by any
#: folder a copy of it sits in, at any depth (`folder_ancestry`). One point read over five arms, `LIMIT 1` since the answer is yes or no. A join rather than a stored column:
#: it is read once per outbound question, never once per row of a wall.
# The site-reach fragment is written out rather than joined in: the SQL rule refuses any joined
# statement. `test_kept_local_over_carries_the_site_reach_fragment` holds the copy to the original.
_KEPT_LOCAL_OVER = point_read(
    "catalog.kept_local_over",
    "SELECT 1 AS refused FROM assets WHERE id = ? AND keep_local = 1"
    " UNION ALL"
    " SELECT 1 FROM asset_people ap JOIN people p ON p.id = ap.person_id"
    " WHERE ap.asset_id = ? AND p.keep_local = 1"
    " UNION ALL"
    " SELECT 1 FROM asset_tags atg JOIN tags t ON t.id = atg.tag_id"
    " WHERE atg.asset_id = ? AND t.keep_local = 1"
    " UNION ALL"
    " SELECT 1 FROM asset_usernames aa JOIN usernames ac ON ac.id = aa.username_id"
    " JOIN ("
    "WITH RECURSIVE reach_up(site_id, ancestor_id) AS ("
    "SELECT id, id FROM sites"
    " UNION SELECT r.site_id, p.parent_id FROM reach_up r"
    " JOIN sites p ON p.id = r.ancestor_id WHERE p.parent_id IS NOT NULL)"
    " SELECT site_id, ancestor_id FROM reach_up"
    ") reach ON reach.site_id = ac.site_id"
    " JOIN sites pl ON pl.id = reach.ancestor_id"
    " WHERE aa.asset_id = ? AND pl.keep_local = 1"
    " UNION ALL"
    " SELECT 1 FROM asset_locations fl"
    " JOIN folder_ancestry fan ON fan.folder_id = fl.folder_id"
    " JOIN folders kf ON kf.id = fan.ancestor_id"
    " WHERE fl.asset_id = ? AND kf.keep_local = 1"
    " LIMIT 1",
)


async def kept_local_over(database: Database, asset_id: str) -> bool:
    """Whether this FILE is kept local, by itself or by anything it is filed under.

    The door asks this; a menu draws `kept_local`, the row's own switch.
    """
    if not asset_id:
        return False
    # Five bindings written out: the SQL rule reads a multiplied tuple as text being built.
    bound = (asset_id, asset_id, asset_id, asset_id, asset_id)
    return await database.fetch_one(_KEPT_LOCAL_OVER, bound) is not None


#: Which of a page of files are kept local, one statement for the page. The partial index
#: `ix_assets_kept_local` leaves nothing to walk where nothing is kept local.
_KEPT_LOCAL_AMONG = """
SELECT id FROM assets
 WHERE keep_local = 1 AND id IN (SELECT value FROM json_each(?))
"""


async def kept_local_among(database: Database, asset_ids: Sequence[str]) -> set[str]:
    """The ids among these that are kept local. Empty for an empty page."""
    if not asset_ids:
        return set()
    rows = await database.fetch_all(_KEPT_LOCAL_AMONG, (json.dumps(list(asset_ids)),))
    return {str(row["id"]) for row in rows}


async def set_kept_local_on(connection: Connection, kind: str, local_id: str, kept: bool) -> bool:
    """Keep this thing local, or let it be enriched again. False when there was no such row.

    It cannot recall what was already sent. On the caller's connection only, so the ledger's event
    is in the same transaction as the flag.
    """
    return await set_refused_on(connection, "enrich", kind, local_id, kept)


# --- THE REFUSAL, BY PURPOSE -----------------------------------------------------------------
#
# "Do not enrich" and "Do not swap" are one mechanism: a mark on a row, reaching every file filed
# under it, recorded in the ledger by its writer. The purposes nest one way: not enriching keeps a
# thing out of swaps too, and `refused_over` is the one place that decides it.

#: What a thing can be refused for. The words the routes and the ledger use.
Purpose = Literal["enrich", "swap"]
PURPOSES: tuple[Purpose, ...] = ("enrich", "swap")

#: WHETHER THIS THING IS MARKED "DO NOT SWAP" ON ITS OWN ROW, per kind. The `_KEPT_LOCAL` shape.
_KEPT_FROM_SWAPS: Mapping[str, PointRead] = {
    "asset": point_read(
        "catalog.asset_kept_from_swaps", "SELECT keep_from_swaps FROM assets WHERE id = ?"
    ),
    "person": point_read(
        "catalog.person_kept_from_swaps", "SELECT keep_from_swaps FROM people WHERE id = ?"
    ),
    "site": point_read(
        "catalog.site_kept_from_swaps", "SELECT keep_from_swaps FROM sites WHERE id = ?"
    ),
    "tag": point_read(
        "catalog.tag_kept_from_swaps", "SELECT keep_from_swaps FROM tags WHERE id = ?"
    ),
    "folder": point_read(
        "catalog.folder_kept_from_swaps", "SELECT keep_from_swaps FROM folders WHERE id = ?"
    ),
}

_SET_KEPT_FROM_SWAPS: Mapping[str, str] = {
    "asset": "UPDATE assets SET keep_from_swaps = ? WHERE id = ?",
    "person": "UPDATE people SET keep_from_swaps = ? WHERE id = ?",
    "site": "UPDATE sites SET keep_from_swaps = ? WHERE id = ?",
    "tag": "UPDATE tags SET keep_from_swaps = ? WHERE id = ?",
    "folder": "UPDATE folders SET keep_from_swaps = ? WHERE id = ?",
}

#: Each purpose's own-row read, the column it reads, and its writer.
_OWN: Mapping[Purpose, tuple[Mapping[str, PointRead], str, Mapping[str, str]]] = {
    "enrich": (_KEPT_LOCAL, "keep_local", _SET_KEPT_LOCAL),
    "swap": (_KEPT_FROM_SWAPS, "keep_from_swaps", _SET_KEPT_FROM_SWAPS),
}

#: WHETHER AN ENTITY IS KEPT OUT OF SWAPS: its own "Do not swap", or its own "Do not enrich". An
#: entity answers for itself alone, as `kept_local` explains: a person is not inside a Site.
_ENTITY_KEPT_FROM_SWAPS: Mapping[str, PointRead] = {
    "person": point_read(
        "catalog.person_refused_for_swaps",
        "SELECT 1 AS refused FROM people WHERE id = ? AND (keep_local = 1 OR keep_from_swaps = 1)",
    ),
    "site": point_read(
        "catalog.site_refused_for_swaps",
        "SELECT 1 AS refused FROM sites WHERE id = ? AND (keep_local = 1 OR keep_from_swaps = 1)",
    ),
    "tag": point_read(
        "catalog.tag_refused_for_swaps",
        "SELECT 1 AS refused FROM tags WHERE id = ? AND (keep_local = 1 OR keep_from_swaps = 1)",
    ),
}

#: WHETHER A FOLDER IS REFUSED BY THE WHOLE RULE, per purpose: its own mark or one on any folder
#: it is inside, since a folder's mark reaches every file at any depth under it. Its depth-0 row in
#: `folder_ancestry` is itself.
_FOLDER_REFUSED_OVER: Mapping[Purpose, PointRead] = {
    "enrich": point_read(
        "catalog.folder_kept_local_over",
        "SELECT 1 AS refused FROM folder_ancestry an JOIN folders f ON f.id = an.ancestor_id"
        " WHERE an.folder_id = ? AND f.keep_local = 1 LIMIT 1",
    ),
    "swap": point_read(
        "catalog.folder_refused_for_swaps",
        "SELECT 1 AS refused FROM folder_ancestry an JOIN folders f ON f.id = an.ancestor_id"
        " WHERE an.folder_id = ? AND (f.keep_local = 1 OR f.keep_from_swaps = 1) LIMIT 1",
    ),
}

#: WHETHER A FILE IS KEPT OUT OF SWAPS by itself or by anything it is filed under, under either
#: purpose. `_KEPT_LOCAL_OVER`'s shape and its site-reach fragment, with both columns asked on every
#: arm. `test_refused_over_carries_the_site_reach_fragment` holds the copy to the original.
_KEPT_FROM_SWAPS_OVER = point_read(
    "catalog.kept_from_swaps_over",
    "SELECT 1 AS refused FROM assets WHERE id = ? AND (keep_local = 1 OR keep_from_swaps = 1)"
    " UNION ALL"
    " SELECT 1 FROM asset_people ap JOIN people p ON p.id = ap.person_id"
    " WHERE ap.asset_id = ? AND (p.keep_local = 1 OR p.keep_from_swaps = 1)"
    " UNION ALL"
    " SELECT 1 FROM asset_tags atg JOIN tags t ON t.id = atg.tag_id"
    " WHERE atg.asset_id = ? AND (t.keep_local = 1 OR t.keep_from_swaps = 1)"
    " UNION ALL"
    " SELECT 1 FROM asset_usernames aa JOIN usernames ac ON ac.id = aa.username_id"
    " JOIN ("
    "WITH RECURSIVE reach_up(site_id, ancestor_id) AS ("
    "SELECT id, id FROM sites"
    " UNION SELECT r.site_id, p.parent_id FROM reach_up r"
    " JOIN sites p ON p.id = r.ancestor_id WHERE p.parent_id IS NOT NULL)"
    " SELECT site_id, ancestor_id FROM reach_up"
    ") reach ON reach.site_id = ac.site_id"
    " JOIN sites pl ON pl.id = reach.ancestor_id"
    " WHERE aa.asset_id = ? AND (pl.keep_local = 1 OR pl.keep_from_swaps = 1)"
    " UNION ALL"
    " SELECT 1 FROM asset_locations fl"
    " JOIN folder_ancestry fan ON fan.folder_id = fl.folder_id"
    " JOIN folders kf ON kf.id = fan.ancestor_id"
    " WHERE fl.asset_id = ? AND (kf.keep_local = 1 OR kf.keep_from_swaps = 1)"
    " LIMIT 1",
)


async def refused_here(database: Database, purpose: Purpose, kind: str, local_id: str) -> bool:
    """Whether this thing's OWN row carries the refusal for this purpose. What a switch draws.

    False for a kind this cannot describe and for a row that is not there, for the reason
    `kept_local` gives.
    """
    statements, column, _ = _OWN[purpose]
    statement = statements.get(kind)
    if statement is None or not local_id:
        return False
    row = await database.fetch_one(statement, (local_id,))
    return row is not None and bool(row[column])


async def refused_over(database: Database, purpose: Purpose, kind: str, local_id: str) -> bool:
    """Whether this thing is refused for this purpose by the WHOLE rule. What the door asks.

    A file answers for everything it is filed under as well as for itself, a folder for itself and
    every folder it is inside, any other entity for itself. For a swap, "Do not enrich" refuses as
    well (see the head of this section).
    """
    if not local_id:
        return False
    if kind == "folder":
        return await database.fetch_one(_FOLDER_REFUSED_OVER[purpose], (local_id,)) is not None
    if purpose == "enrich":
        if kind == "asset":
            return await kept_local_over(database, local_id)
        return await refused_here(database, "enrich", kind, local_id)
    if kind == "asset":
        bound = (local_id, local_id, local_id, local_id, local_id)
        return await database.fetch_one(_KEPT_FROM_SWAPS_OVER, bound) is not None
    statement = _ENTITY_KEPT_FROM_SWAPS.get(kind)
    if statement is None:
        return False
    return await database.fetch_one(statement, (local_id,)) is not None


async def set_refused_on(
    connection: Connection, purpose: Purpose, kind: str, local_id: str, refused: bool
) -> bool:
    """Mark this thing refused for this purpose, or take the mark off. False when there was no row.

    On the caller's connection, so the ledger's event is in the same transaction.
    """
    statement = _OWN[purpose][2].get(kind)
    if statement is None or not local_id:
        return False
    return bool((await connection.execute(statement, (1 if refused else 0, local_id))).rowcount)


#: Every file kept out of swaps by either mark, by itself or through what it is filed under: what a
#: guest leaves out of what it says it holds. Each arm starts from a partial index.
_FILES_KEPT_FROM_SWAPS = (
    "SELECT id FROM assets WHERE keep_local = 1"
    " UNION SELECT id FROM assets WHERE keep_from_swaps = 1"
    " UNION SELECT ap.asset_id FROM asset_people ap JOIN people p ON p.id = ap.person_id"
    " WHERE p.keep_local = 1 OR p.keep_from_swaps = 1"
    " UNION SELECT atg.asset_id FROM asset_tags atg JOIN tags t ON t.id = atg.tag_id"
    " WHERE t.keep_local = 1 OR t.keep_from_swaps = 1"
    " UNION SELECT aa.asset_id FROM asset_usernames aa JOIN usernames ac ON ac.id = aa.username_id"
    " JOIN ("
    "WITH RECURSIVE reach_up(site_id, ancestor_id) AS ("
    "SELECT id, id FROM sites"
    " UNION SELECT r.site_id, p.parent_id FROM reach_up r"
    " JOIN sites p ON p.id = r.ancestor_id WHERE p.parent_id IS NOT NULL)"
    " SELECT site_id, ancestor_id FROM reach_up"
    ") reach ON reach.site_id = ac.site_id"
    " JOIN sites pl ON pl.id = reach.ancestor_id"
    " WHERE pl.keep_local = 1 OR pl.keep_from_swaps = 1"
    " UNION SELECT fl.asset_id FROM folders kf"
    " CROSS JOIN folder_ancestry fan ON fan.ancestor_id = kf.id"
    " CROSS JOIN asset_locations fl ON fl.folder_id = fan.folder_id"
    " WHERE kf.keep_local = 1"
    " UNION SELECT fl.asset_id FROM folders kf"
    " CROSS JOIN folder_ancestry fan ON fan.ancestor_id = kf.id"
    " CROSS JOIN asset_locations fl ON fl.folder_id = fan.folder_id"
    " WHERE kf.keep_from_swaps = 1"
)


async def files_kept_from_swaps(database: Database) -> set[str]:
    """The ids of every file a swap must neither offer nor admit to holding. Empty on most libraries."""
    # A read over every file, so it goes down the sweep lane and never holds the ordinary pool.
    rows = await database.sweep_all(_FILES_KEPT_FROM_SWAPS, what="files kept from swaps")
    return {str(row["id"]) for row in rows}


#: Which of a page of files will not go in a swap: `_KEPT_FROM_SWAPS_OVER`'s rule over the page.
_REFUSED_FOR_SWAPS_AMONG = (
    "SELECT id FROM assets WHERE id IN (SELECT value FROM json_each(:ids))"
    " AND (keep_local = 1 OR keep_from_swaps = 1)"
    " UNION SELECT ap.asset_id FROM asset_people ap JOIN people p ON p.id = ap.person_id"
    " WHERE ap.asset_id IN (SELECT value FROM json_each(:ids))"
    " AND (p.keep_local = 1 OR p.keep_from_swaps = 1)"
    " UNION SELECT atg.asset_id FROM asset_tags atg JOIN tags t ON t.id = atg.tag_id"
    " WHERE atg.asset_id IN (SELECT value FROM json_each(:ids))"
    " AND (t.keep_local = 1 OR t.keep_from_swaps = 1)"
    " UNION SELECT aa.asset_id FROM asset_usernames aa JOIN usernames ac ON ac.id = aa.username_id"
    " JOIN ("
    "WITH RECURSIVE reach_up(site_id, ancestor_id) AS ("
    "SELECT id, id FROM sites"
    " UNION SELECT r.site_id, p.parent_id FROM reach_up r"
    " JOIN sites p ON p.id = r.ancestor_id WHERE p.parent_id IS NOT NULL)"
    " SELECT site_id, ancestor_id FROM reach_up"
    ") reach ON reach.site_id = ac.site_id"
    " JOIN sites pl ON pl.id = reach.ancestor_id"
    " WHERE aa.asset_id IN (SELECT value FROM json_each(:ids))"
    " AND (pl.keep_local = 1 OR pl.keep_from_swaps = 1)"
    " UNION SELECT fl.asset_id FROM asset_locations fl"
    " JOIN folder_ancestry fan ON fan.folder_id = fl.folder_id"
    " JOIN folders kf ON kf.id = fan.ancestor_id"
    " WHERE fl.asset_id IN (SELECT value FROM json_each(:ids))"
    " AND (kf.keep_local = 1 OR kf.keep_from_swaps = 1)"
)


async def refused_for_swaps_among(database: Database, asset_ids: Sequence[str]) -> set[str]:
    """The ids among these that no swap will send: what swap mode marks on a wall's tiles. Empty
    for an empty page."""
    if not asset_ids:
        return set()
    rows = await database.fetch_all(
        _REFUSED_FOR_SWAPS_AMONG, {"ids": json.dumps(list(dict.fromkeys(asset_ids)))}
    )
    return {str(row["id"]) for row in rows}


#: WHAT A FILE IS FILED UNDER THAT KEEPS IT OUT OF SWAPS: each person, tag and Site (a Site above
#: its own counts, through the reach) and each folder a copy sits in at any depth that carries
#: either mark, with which. What a press on a file
#: swap mode will not send says it by. The file's own marks are `refused_here`'s.
_REFUSERS_OF_FILE = point_read(
    "catalog.refusers_of_file",
    "SELECT 'person' AS kind, p.id AS id, p.keep_local AS keep_local FROM asset_people ap"
    " JOIN people p ON p.id = ap.person_id"
    " WHERE ap.asset_id = ? AND (p.keep_from_swaps = 1 OR p.keep_local = 1)"
    " UNION SELECT 'tag', t.id, t.keep_local FROM asset_tags atg JOIN tags t ON t.id = atg.tag_id"
    " WHERE atg.asset_id = ? AND (t.keep_local = 1 OR t.keep_from_swaps = 1)"
    " UNION SELECT 'site', pl.id, pl.keep_local FROM asset_usernames aa"
    " JOIN usernames ac ON ac.id = aa.username_id"
    " JOIN ("
    "WITH RECURSIVE reach_up(site_id, ancestor_id) AS ("
    "SELECT id, id FROM sites"
    " UNION SELECT r.site_id, p.parent_id FROM reach_up r"
    " JOIN sites p ON p.id = r.ancestor_id WHERE p.parent_id IS NOT NULL)"
    " SELECT site_id, ancestor_id FROM reach_up"
    ") reach ON reach.site_id = ac.site_id"
    " JOIN sites pl ON pl.id = reach.ancestor_id"
    " WHERE aa.asset_id = ? AND (pl.keep_local = 1 OR pl.keep_from_swaps = 1)"
    " UNION SELECT 'folder', kf.id, kf.keep_local FROM asset_locations fl"
    " JOIN folder_ancestry fan ON fan.folder_id = fl.folder_id"
    " JOIN folders kf ON kf.id = fan.ancestor_id"
    " WHERE fl.asset_id = ? AND (kf.keep_local = 1 OR kf.keep_from_swaps = 1)"
    " ORDER BY 1, 2",
)


@dataclass(frozen=True, slots=True)
class Refuser:
    """One thing a file is filed under that keeps it out of swaps, and by which mark."""

    #: `folder`, `person`, `site` or `tag`.
    kind: str
    id: str
    #: Kept local ("Don't enrich") when true; "Don't swap" when false.
    kept_local: bool


async def refusers_of_file(database: Database, asset_id: str) -> list[Refuser]:
    """Everything this file is filed under or sits in that keeps it out of swaps, by kind in
    alphabetical order (folders, people, Sites, tags), each in id order. Ids only: who may be told
    a name is the caller's question."""
    if not asset_id:
        return []
    # Four bindings written out: the SQL rule reads a multiplied tuple as text being built.
    rows = await database.fetch_all(_REFUSERS_OF_FILE, (asset_id, asset_id, asset_id, asset_id))
    return [
        Refuser(kind=str(row["kind"]), id=str(row["id"]), kept_local=bool(row["keep_local"]))
        for row in rows
    ]


#: THE FOLDERS THAT CARRY A MARK ON THEIR OWN ROW, with which: what a folder row wears. Each arm
#: starts from a partial index, so a library with no marked folder reads nothing.
_MARKED_FOLDERS = (
    "SELECT id, keep_local, keep_from_swaps FROM folders WHERE keep_local = ?"
    " UNION SELECT id, keep_local, keep_from_swaps FROM folders WHERE keep_from_swaps = ?"
)


async def marked_folders(database: Database) -> dict[str, tuple[bool, bool]]:
    """Each folder whose own row carries "Don't enrich" or "Don't swap", as (kept local, kept from
    swaps). Ids only, never names: which of them a viewer may see is the caller's read."""
    rows = await database.fetch_all(_MARKED_FOLDERS, (1, 1))
    return {str(row["id"]): (bool(row["keep_local"]), bool(row["keep_from_swaps"])) for row in rows}
