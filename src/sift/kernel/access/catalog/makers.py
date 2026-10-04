# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who made a row, written and read back: the pass, the person, or the stash-box behind it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sift.kernel.access.history import Actor, maker_of
from sift.kernel.access.viewer import Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database, PointRead, point_read
from sift.kernel.vocabulary import MADE_VIAS

#: Mark somebody as a PMV creator. ONE DIRECTION ONLY (see `mark_pmv_creator`).
_MARK_PMV_CREATOR = "UPDATE people SET pmv_creator = 1 WHERE id = ? AND pmv_creator = 0"

#: Which box made a row, written once (`IS NULL`): a second box describing it later must not take
#: the credit. The kind and the pass (`stash`) are set in the same statement, because a row naming a
#: box while saying 'sift' would be a disagreement, and the pass outlives a forgotten box.
_MARK_CREATED_BY_BOX: dict[str, str] = {
    "person": (
        "UPDATE people SET created_by_box_id = ?, created_by_kind = 'box',"
        " created_by_via = 'stash', created_by_user_id = NULL"
        " WHERE id = ? AND created_by_box_id IS NULL"
    ),
    "site": (
        "UPDATE sites SET created_by_box_id = ?, created_by_kind = 'box',"
        " created_by_via = 'stash', created_by_user_id = NULL"
        " WHERE id = ? AND created_by_box_id IS NULL"
    ),
    "tag": (
        "UPDATE tags SET created_by_box_id = ?, created_by_kind = 'box',"
        " created_by_via = 'stash', created_by_user_id = NULL"
        " WHERE id = ? AND created_by_box_id IS NULL"
    ),
}

#: Which pass made a row the shared lookups made under the stash-box word, for a caller that is not
#: a stash-box. Only a row still exactly as the lookups wrote it moves.
_MARK_MADE_VIA: dict[str, str] = {
    "person": (
        "UPDATE people SET created_by_via = ? WHERE id = ? AND created_by_kind = 'sift'"
        " AND created_by_via = 'stash' AND created_by_box_id IS NULL"
    ),
    "site": (
        "UPDATE sites SET created_by_via = ? WHERE id = ? AND created_by_kind = 'sift'"
        " AND created_by_via = 'stash' AND created_by_box_id IS NULL"
    ),
    "tag": (
        "UPDATE tags SET created_by_via = ? WHERE id = ? AND created_by_kind = 'sift'"
        " AND created_by_via = 'stash' AND created_by_box_id IS NULL"
    ),
}

#: A removed box's marks, cleared. The column carries no foreign key (`schema.py` says why), and a
#: stale id would come back to life if a box were later added at the same address.
_CLEAR_CREATED_BY_BOX: tuple[str, ...] = (
    "UPDATE people SET created_by_box_id = NULL WHERE created_by_box_id = ?",
    "UPDATE sites SET created_by_box_id = NULL WHERE created_by_box_id = ?",
    "UPDATE tags SET created_by_box_id = NULL WHERE created_by_box_id = ?",
)


# The `_on` forms take a connection the caller already holds: the write guard is not reentrant,
# and a caller inside `db.write()` that opened a second write would deadlock.


async def mark_pmv_creator_on(connection: Connection, person_id: str) -> bool:
    """Say this person makes the edits, on a connection somebody else opened (a schema step)."""
    return bool((await connection.execute(_MARK_PMV_CREATOR, (person_id,))).rowcount)


async def mark_pmv_creator(db: Database, person_id: str) -> bool:
    """Say that this person makes the edits. True when the mark was not already there.

    Here because `people` is the kernel's table and the two deciders (a stash-box link and the
    People screen) are slices that may not import each other. It only ever sets: taking the mark off
    is a person's decision, saved with the whole record.
    """
    async with telling(db, EVERY_ADMIN, About.LIBRARY) as connection:
        return await mark_pmv_creator_on(connection, person_id)


async def mark_created_by_box(db: Database, kind: str, local_id: str, box_id: str) -> bool:
    """Say that this row was invented from a stash-box's answer. True when the fact landed.

    A link says a box knows a row; this says a box made it, which no later link changes. False
    (already named, no such row, or an unknown `kind`) is not a failure and is not reported.
    """
    statement = _MARK_CREATED_BY_BOX.get(kind)
    if statement is None or not local_id or not box_id:
        return False
    async with telling(db, EVERY_ADMIN, About.LIBRARY) as connection:
        return bool((await connection.execute(statement, (box_id, local_id))).rowcount)


async def mark_made_via(db: Database, kind: str, local_id: str, via: str) -> bool:
    """Say which pass of Sift's made a row the shared lookups just made. True when it moved.

    See `_MARK_MADE_VIA`. `via` must be one of `MADE_VIAS`; an unknown `kind` moves nothing.
    """
    statement = _MARK_MADE_VIA.get(kind)
    if statement is None or not local_id or via not in MADE_VIAS:
        return False
    async with telling(db, EVERY_ADMIN, About.LIBRARY) as connection:
        return bool((await connection.execute(statement, (via, local_id))).rowcount)


async def clear_created_by_box(db: Database, box_id: str) -> int:
    """Forget that a removed box made anything, in place of a foreign key. How many rows moved."""
    moved = 0
    async with telling(db, EVERY_ADMIN, About.LIBRARY) as connection:
        for statement in _CLEAR_CREATED_BY_BOX:
            moved += (await connection.execute(statement, (box_id,))).rowcount
    return moved


# One read over every table that carries the maker columns, so "who made this" has one answer.


#: One point read per table, written out because a table name cannot be bound. A collection, a
#: Photo Set and a song have no box column (no box answer invents one), so it is supplied as NULL
#: and every row has one shape for `maker_of`; only a tag carries the act that made it.
_MADE_BY: Mapping[str, PointRead] = {
    "person": point_read(
        "catalog.person_made_by",
        "SELECT created_by_kind, created_by_via, created_by_user_id, created_by_box_id,"
        " NULL AS created_by_act FROM people WHERE id = ?",
    ),
    "site": point_read(
        "catalog.site_made_by",
        "SELECT created_by_kind, created_by_via, created_by_user_id, created_by_box_id,"
        " NULL AS created_by_act FROM sites WHERE id = ?",
    ),
    "tag": point_read(
        "catalog.tag_made_by",
        "SELECT created_by_kind, created_by_via, created_by_user_id, created_by_box_id,"
        " created_by_act FROM tags WHERE id = ?",
    ),
    "collection": point_read(
        "catalog.collection_made_by",
        "SELECT created_by_kind, created_by_via, created_by_user_id,"
        " NULL AS created_by_box_id, NULL AS created_by_act FROM collections WHERE id = ?",
    ),
    "photo_set": point_read(
        "catalog.photo_set_made_by",
        "SELECT created_by_kind, created_by_via, created_by_user_id,"
        " NULL AS created_by_box_id, NULL AS created_by_act FROM photo_sets WHERE id = ?",
    ),
    "song": point_read(
        "catalog.song_made_by",
        "SELECT created_by_kind, created_by_via, created_by_user_id,"
        " NULL AS created_by_box_id, NULL AS created_by_act FROM songs WHERE id = ?",
    ),
}

#: Every kind this read answers for, in the server's own word for each.
MADE_BY_KINDS = tuple(_MADE_BY)

#: The box that made a row, by its name and the slug its mark takes its colour from.
_BOX_NAMED = point_read(
    "catalog.made_by_box_named", "SELECT name, slug FROM stash_boxes WHERE id = ?"
)

#: `stash_boxes` belongs to a feature, so a process that never imported that slice lacks it.
_TABLES = "SELECT name FROM sqlite_master WHERE type = 'table'"


@dataclass(frozen=True, slots=True)
class EntityMaker:
    """Who made one row: the actor's word, the pass, and the box where a box made it.

    `actor` is decided by `maker_of`, in the history's closed vocabulary. A box since forgotten
    reads as `somebody`.
    """

    actor: Actor
    via: str | None = None
    act: str | None = None
    box_id: str | None = None
    box_name: str | None = None
    box_slug: str | None = None


async def made_by(
    database: Database, viewer: Viewer, kind: str, entity_id: str
) -> EntityMaker | None:
    """Who invented one person, site, tag, collection, Photo Set or song. None where it is unsaid.

    Unscoped: the caller resolved the subject against the viewer already, and the viewer only turns
    a user's making into "you" or "another user". An unknown kind is None too, not a failure.
    """
    statement = _MADE_BY.get(kind)
    if statement is None or not entity_id:
        return None
    row = await database.fetch_one(statement, (entity_id,))
    if row is None or row["created_by_kind"] is None:
        return None
    box_id = row["created_by_box_id"]
    name: str | None = None
    slug: str | None = None
    if box_id is not None:
        here = {str(one["name"]) for one in await database.fetch_all(_TABLES)}
        if "stash_boxes" in here:
            found = await database.fetch_one(_BOX_NAMED, (str(box_id),))
            if found is not None:
                name = str(found["name"])
                slug = None if found["slug"] is None else str(found["slug"])
    actor, _named = maker_of(row, viewer, name)
    return EntityMaker(
        actor=actor,
        via=None if row["created_by_via"] is None else str(row["created_by_via"]),
        act=None if row["created_by_act"] is None else str(row["created_by_act"]),
        box_id=None if name is None else str(box_id),
        box_name=name,
        box_slug=slug,
    )
