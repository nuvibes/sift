# SPDX-License-Identifier: AGPL-3.0-or-later
"""Saved walls: reading them back, writing them down, and refusing the ones that do not add up.

The whole slice is this file and the two tables under it. Everything a wall actually DOES (what
each cell plays, what it sounds like, when it moves on) happens in the browser against routes that
already existed, because a cell is a filter run through the search engine and a video played through
the player's own decision route. Nothing here streams, decides or resolves anything.

Two rules run through it. **Every statement is scoped to the asking user in its own WHERE
clause**, so there is no path that reads somebody else's row and then declines to return it. And
**a wall that does not add up is refused rather than stored**: a layout has a fixed number of cells,
and an arrangement carrying a different number is a wall that cannot be drawn.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from sift.kernel.access import Viewer
from sift.kernel.access.repository import SORT_KEYS
from sift.kernel.audience import Audience
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database
from sift.kernel.ids import new_id
from sift.kernel.seams import FilterEngine
from sift.kernel.wiring import Part


class NameTaken(Exception):
    """This user already has a wall under that name.

    Its own exception rather than a bare integrity error, because it is an answer somebody can act
    on (pick another name) and a route that let the database error through would report a server
    fault for an ordinary collision.
    """


class Unusable(ValueError):
    """The arrangement cannot be drawn as described. The message is meant to be read."""


class TooMany(Exception):
    """This user is holding as many walls as they may.

    Its own exception for the same reason `NameTaken` is one: it is something somebody can act on
    (delete one) rather than a server fault. Changing a wall they already have is never refused by
    it, because that replaces a row instead of adding one.
    """


#: The layouts, and how many cells each one holds.
#:
#: A wall is BUILT now (one feed to begin with, and each feed able to put another beside, above or
#: below it), so the layouts are a starting point rather than the whole vocabulary. They are still
#: here because a wall saved before that is stored under one of these names and has no shape of its
#: own, and because "two side by side" should stay one press.
#:
#: `custom` is what a built wall is stored under. It has no cell count: the shape says how many
#: there are, which is the whole point of storing one.
LAYOUT_CELLS: dict[str, int] = {
    "single": 1,
    "side_by_side": 2,
    "stacked": 2,
    "side_by_side_by_side": 3,
    "grid": 4,
    # The focus half is ONE feed and the strip holds five, so a Center Stage wall is six cells.
    # The name is what the wall opens as; growing the focus half to four is a shape change like any
    # other, and a wall in a shape of its own is stored under `custom` with its shape beside it.
    "center_stage": 6,
    # The same strip over each of the three walls: one, two, three or four in focus and five
    # waiting underneath. Four up is nine, which is the ceiling.
    "center_stage_two": 7,
    "center_stage_three": 8,
    "center_stage_grid": 9,
    # Retired from the picker and still ACCEPTED here, which is the whole distinction this
    # dictionary draws. These are names already written into people's saved walls; refusing them
    # would turn shortening a menu into losing somebody's data, and a wall kept under one of them
    # would stop opening and could never be saved again. What is OFFERED is the browser's layout
    # list and the choices on `theater.layout`; what is ACCEPTED is this.
    "stacked_three": 3,
    "one_above_two": 3,
    "two_above_one": 3,
}

#: What a wall that is not one of the layouts is stored under.
CUSTOM = "custom"

#: The most cells a wall may hold. The client has the same number and a gate checks they agree.
MOST_CELLS = 9

#: How many previews a Center stage layout OPENS with. The client has the same number.
#:
#: Not a ceiling: the strip may hold whatever the focus half is not using, up to the wall's own
#: `MOST_CELLS`. Capping it at five as well would stop a wall of three in focus at eight cells
#: while a wall of four reached nine: the same nine, and one arrangement able to use them all.
MOST_PREVIEWS = 5


@dataclass(frozen=True, slots=True)
class Slot:
    """Where one cell sits: its top-left corner, and how many tracks it covers. All from zero."""

    row: int
    col: int
    row_span: int
    col_span: int

    def covers(self, row: int, col: int) -> bool:
        return (
            self.row <= row < self.row + self.row_span
            and self.col <= col < self.col + self.col_span
        )


@dataclass(frozen=True, slots=True)
class Shape:
    """A wall: a grid that size, holding those cells in that order."""

    rows: int
    cols: int
    slots: tuple[Slot, ...]


def _read_shape(stored: str | None) -> Shape | None:
    """A stored shape, or None where there is none or it cannot be read.

    None rather than a raise. A row that will not parse is a wall somebody saved and can still open:
    it falls back to the layout it was also stored under, which is what every wall saved before
    shapes existed does anyway.
    """
    if not stored:
        return None
    try:
        held = json.loads(stored)
    except ValueError:
        return None
    if not isinstance(held, dict):
        return None
    try:
        slots = tuple(
            Slot(
                row=int(one["row"]),
                col=int(one["col"]),
                row_span=int(one["row_span"]),
                col_span=int(one["col_span"]),
            )
            for one in held["slots"]
        )
        return Shape(rows=int(held["rows"]), cols=int(held["cols"]), slots=slots)
    except (KeyError, TypeError, ValueError):
        return None


def _write_shape(shape: Shape | None) -> str | None:
    if shape is None:
        return None
    return json.dumps(
        {
            "rows": shape.rows,
            "cols": shape.cols,
            "slots": [
                {
                    "row": slot.row,
                    "col": slot.col,
                    "row_span": slot.row_span,
                    "col_span": slot.col_span,
                }
                for slot in shape.slots
            ],
        },
        separators=(",", ":"),
    )


#: Whether a cell walks its source or picks from it at random.
ORDERINGS: tuple[str, ...] = ("in_order", "shuffle")

#: What a cell does when a file ends.
#:
#: The player's own three names, deliberately, rather than a second vocabulary for the same three
#: behaviours. A slice never imports another slice, so they are written out again here, and a gate
#: reads the registered playback setting and refuses this tuple if the two ever drift apart.
END_BEHAVIOURS: tuple[str, ...] = ("loop_one", "loop_all", "once")

#: What a cell will draw. Either things that move, or those and photographs as well.
MEDIA_KINDS: tuple[str, ...] = ("video_gif", "all")

#: Every search mode a cell may be saved with, taken from the one place that decides them rather
#: than written out again here. A cell's source can be a SMART search (ranked by meaning instead
#: of matched by word), and that is a different set of files, not a different order over the same
#: ones, so it has to survive being saved.
SORTS: frozenset[str] = SORT_KEYS

#: The longest timer a cell will accept, in seconds. An hour, which is longer than any wall anybody
#: is watching needs and short enough that a mistyped number is caught rather than stored.
MAX_TIMER_SECONDS = 3600

#: How long a source query may be, matching the cap the search route puts on the same text.
MAX_SOURCE = 1000

#: How long a wall's name may be.
MAX_NAME = 80

#: The most walls one user may keep.
#:
#: A wall is several cells' worth of deliberate setting up, so nobody arranging these by hand will
#: meet this, and a user whose credentials have been taken cannot use the route to grow the
#: database without bound. Refused at the cap rather than trimmed to it: these are somebody's own
#: arrangements, and quietly dropping the oldest to make room would lose one they meant to keep.
MAX_ARRANGEMENTS = 100


@dataclass(frozen=True, slots=True)
class Cell:
    """One cell of a saved wall. Its source and how it behaves, never what it was playing."""

    source: str
    media_kind: str
    ordering: str
    end_behaviour: str
    timer_seconds: int | None
    volume: int
    #: How the source is searched, or None for the ordinary answer. See the schema's column.
    sort: str | None = None
    #: What shape the cell is held to, or `dynamic` for the shape of whatever it is playing.
    #:
    #: Stored and not judged, which is the difference between this and the four values above it.
    #: Those decide what a cell DOES (which files it draws, in what order, what happens at the end)
    #: and a word the service does not understand would make a cell behave in a way nothing here
    #: intended. A shape decides how the browser DRAWS one, the server never draws anything, and the
    #: client already falls back to Dynamic for a name it cannot draw. Judging it here would be a
    #: second copy of a list that lives in the client, kept in step for no benefit, and it would make
    #: adding a seventh shape a release of both halves.
    aspect: str = "dynamic"


@dataclass(frozen=True, slots=True)
class Arrangement:
    """One saved wall: a layout, and a cell for each place in it, in order."""

    id: str
    name: str
    layout: str
    #: The grid and where each cell sits in it, exactly as the client sent it. None on a wall saved
    #: before walls could be built: those are drawn from the name in `layout`.
    shape: Shape | None
    #: How many of `cells` are PREVIEWS in the strip under the wall rather than feeds in it. The
    #: shape's places come first and the strip is the rest, which is the order the client draws them
    #: in. Zero on every wall saved before Center stage.
    strip: int
    cells: tuple[Cell, ...]


_INSERT_ARRANGEMENT = """
INSERT INTO theater_arrangements
  (id, user_id, name, layout, shape, strip, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_CELL = """
INSERT INTO theater_cells
  (arrangement_id, position, source, media_kind, ordering, end_behaviour, timer_seconds, volume,
   sort, aspect)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_LIST_ARRANGEMENTS = """
SELECT id, name, layout, shape, strip FROM theater_arrangements
 WHERE user_id = ?
 ORDER BY id DESC
"""

# Every cell of every wall this user has, in one read rather than one read per wall. The join is
# what scopes it: a cell is reachable only through an arrangement, and the arrangement is filtered
# by the asking user here.
_LIST_CELLS = """
SELECT c.arrangement_id, c.source, c.media_kind, c.ordering, c.end_behaviour,
       c.timer_seconds, c.volume, c.sort, c.aspect
  FROM theater_cells c
  JOIN theater_arrangements a ON a.id = c.arrangement_id
 WHERE a.user_id = ?
 ORDER BY c.arrangement_id, c.position
"""

# Scoped in the statement, exactly as the saved-search writes are: an id belonging to another
# user names no row here, so there is no path that reads somebody else's row and then declines to
# change it. `RETURNING id` tells "not yours, or not there" from "changed" without a second
# statement that could disagree with this one about which rows exist.
_UPDATE_ARRANGEMENT = """
UPDATE theater_arrangements SET name = ?, layout = ?, shape = ?, strip = ?, updated_at = ?
 WHERE user_id = ? AND id = ?
RETURNING id
"""

_DELETE_ARRANGEMENT = "DELETE FROM theater_arrangements WHERE user_id = ? AND id = ?"

_DELETE_CELLS = "DELETE FROM theater_cells WHERE arrangement_id = ?"

# Whether this user already keeps a DIFFERENT wall under the wanted name.
#
# Asked rather than inferred from a failed write, because a slice does not import the database
# driver and so cannot tell a unique-index violation from any other error, and because the two
# outcomes need different answers: a name already used is a conflict somebody can act on, and an id
# that is not theirs is a plain not-found. Read inside the same write transaction as the write
# below, and there is one writer at a time, so nothing can slip between the two.
_NAME_IS_TAKEN = """
SELECT 1 FROM theater_arrangements
 WHERE user_id = ? AND name = ? AND id <> ?
 LIMIT 1
"""

#: How many walls this user already keeps. Read inside the same transaction as the insert, so two
#: saves arriving together cannot both find room and both take it.
_ARRANGEMENT_COUNT = "SELECT COUNT(*) AS held FROM theater_arrangements WHERE user_id = ?"


def _clean_name(name: str) -> str:
    """The name with its spacing tidied, or a refusal. Length is capped at the edge, not here."""
    cleaned = " ".join(name.split())
    if not cleaned:
        raise Unusable("a wall needs a name")
    return cleaned


def _checked_shape(shape: Shape, strip: int, cells: tuple[Cell, ...]) -> None:
    """A built wall that can actually be drawn, or a refusal saying what is wrong with it.

    Every one of these is a wall that would arrive on somebody's screen wrong rather than absent:
    a cell reaching past the edge of the grid draws over its neighbour, two cells on one square draw
    one on top of the other, and a shape with a different number of places than cells leaves a
    picture with no settings behind it or settings with no picture.
    """
    if shape.rows < 1 or shape.cols < 1:
        raise Unusable("a wall needs at least one row and one column")
    # The shape's places, and then the strip. A wall in Center stage sends more cells than the grid
    # has room for on purpose: the extra ones are the previews under it.
    if len(shape.slots) + strip != len(cells):
        raise Unusable(
            f"the shape has {len(shape.slots)} places and a strip of {strip}, "
            f"and there are {len(cells)} cells"
        )
    if not 1 <= len(shape.slots) <= MOST_CELLS:
        raise Unusable(f"a wall holds 1 to {MOST_CELLS} cells, not {len(shape.slots)}")
    # AFTER the count above, not before it: a shape with more places than the wall holds leaves
    # NEGATIVE room, and answering "room for -1 previews" describes the consequence of the fault
    # rather than the fault. The narrower message first.
    room = MOST_CELLS - len(shape.slots)
    if not 0 <= strip <= room:
        raise Unusable(
            f"this shape leaves room for {room} previews, not {strip}: a wall holds {MOST_CELLS} "
            "cells in all, however they are divided between the places and the strip"
        )
    for index, slot in enumerate(shape.slots):
        where = f"cell {index + 1}"
        if slot.row_span < 1 or slot.col_span < 1:
            raise Unusable(f"{where}: covers no squares")
        if slot.row < 0 or slot.col < 0:
            raise Unusable(f"{where}: sits outside the wall")
        if slot.row + slot.row_span > shape.rows or slot.col + slot.col_span > shape.cols:
            raise Unusable(f"{where}: reaches past the edge of the wall")
    for row in range(shape.rows):
        for col in range(shape.cols):
            if sum(1 for slot in shape.slots if slot.covers(row, col)) > 1:
                raise Unusable(f"two cells share the square at row {row + 1}, column {col + 1}")


def _checked(
    layout: str, shape: Shape | None, strip: int, cells: tuple[Cell, ...]
) -> tuple[str, Shape | None, int, tuple[Cell, ...]]:
    """The layout, its shape and its cells, or a refusal saying which part does not add up.

    A wall is refused rather than stored, because every one of these is a wall that cannot be drawn:
    a layout nothing knows how to lay out, the wrong number of cells for the one named, a shape that
    overlaps itself, or a cell carrying a behaviour with no meaning. Storing it would move the
    failure to whoever loads it.

    A wall with a SHAPE is judged by the shape, and `layout` is then only the name it is filed
    under. A wall without one is a layout and is judged by its cell count, which is what every wall
    saved before walls could be built is.
    """
    if shape is not None:
        _checked_shape(shape, strip, cells)
        if layout != CUSTOM and layout not in LAYOUT_CELLS:
            raise Unusable(f"unknown layout {layout!r}")
    else:
        # A wall with no shape is a layout, and a layout's cell count includes whatever strip it
        # opens with. See `LAYOUT_CELLS`, where Center stage is six.
        wanted = LAYOUT_CELLS.get(layout)
        if wanted is None:
            raise Unusable(f"unknown layout {layout!r}")
        if len(cells) != wanted:
            raise Unusable(f"{layout} holds {wanted} cells, not {len(cells)}")
        if not 0 <= strip <= MOST_CELLS:
            raise Unusable(f"a wall holds {MOST_CELLS} cells, so a strip cannot hold {strip}")
    for index, cell in enumerate(cells):
        where = f"cell {index + 1}"
        if cell.media_kind not in MEDIA_KINDS:
            raise Unusable(f"{where}: unknown media kind {cell.media_kind!r}")
        if cell.ordering not in ORDERINGS:
            raise Unusable(f"{where}: unknown order {cell.ordering!r}")
        if cell.end_behaviour not in END_BEHAVIOURS:
            raise Unusable(f"{where}: unknown end behavior {cell.end_behaviour!r}")
        if cell.timer_seconds is not None and not 1 <= cell.timer_seconds <= MAX_TIMER_SECONDS:
            raise Unusable(f"{where}: a timer runs from 1 to {MAX_TIMER_SECONDS} seconds")
        if not 0 <= cell.volume <= 100:
            raise Unusable(f"{where}: volume runs from 0 to 100")
        if cell.sort is not None and cell.sort not in SORTS:
            raise Unusable(f"{where}: unknown sort {cell.sort!r}")
    return layout, shape, strip, cells


class TheaterService:
    """The saved walls of whoever is asking, and nobody else's.

    **A cell's source is kept by id and read back under today's names.** A source is typed filter
    text (`tags:harbour people:"Wren Halloway"`), and kept as typed it names things by NAME: rename
    the tag and the wall goes on asking for `harbour`, which nothing is called any more, so the
    cell draws nothing and its filter still reads the old name. So the text is handed to the one
    filter engine on the way in (`kept`, every thing it names as its id) and on the way out
    (`shown`, every id as what its thing is called now). The engine is the only thing that knows
    which words in a filter are names; this slice never splits the text itself.
    """

    def __init__(
        self,
        database: Database,
        filters: FilterEngine,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = database
        self._filters = filters
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock())

    async def arrangements(self, viewer: Viewer) -> list[Arrangement]:
        """This user's saved walls, newest first.

        Filtered by `user_id` in both statements, so there is no path here that reads another
        user's row and then declines to return it.
        """
        rows = await self._db.fetch_all(_LIST_ARRANGEMENTS, (viewer.id,))
        held = await self._db.fetch_all(_LIST_CELLS, (viewer.id,))
        # Every cell's source in one reading, so a user with a hundred walls is one lookup per kind
        # of thing named rather than one per cell.
        sources = await self._filters.shown(viewer, [str(row["source"]) for row in held])
        cells: dict[str, list[Cell]] = {}
        for row, source in zip(held, sources, strict=True):
            cells.setdefault(row["arrangement_id"], []).append(
                Cell(
                    source=source,
                    media_kind=row["media_kind"],
                    ordering=row["ordering"],
                    end_behaviour=row["end_behaviour"],
                    timer_seconds=row["timer_seconds"],
                    volume=row["volume"],
                    sort=row["sort"],
                    # Null on every cell saved before shapes existed, which is exactly what they
                    # were drawn as. The column's default says the same thing; both are here
                    # because a migrated row carries the null and a fresh one carries the word.
                    aspect=row["aspect"] or "dynamic",
                )
            )
        return [
            Arrangement(
                id=row["id"],
                name=row["name"],
                layout=row["layout"],
                shape=_read_shape(row["shape"]),
                strip=int(row["strip"] or 0),
                cells=tuple(cells.get(row["id"], ())),
            )
            for row in rows
        ]

    async def save(
        self,
        viewer: Viewer,
        name: str,
        layout: str,
        shape: Shape | None,
        strip: int,
        cells: tuple[Cell, ...],
    ) -> Arrangement:
        """Keep a wall under a name, for this user.

        A name already used is refused rather than replaced. A wall is several cells' worth of
        setting up, and quietly overwriting one because the name matched is a loss somebody only
        notices later, so saving over a wall is done deliberately, by updating the one they meant.
        """
        cleaned = _clean_name(name)
        layout, shape, strip, cells = _checked(layout, shape, strip, cells)
        kept = await self._kept(viewer, cells)
        identifier = new_id()
        now = self._now()
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            taken = await connection.execute_fetchall(
                _NAME_IS_TAKEN, (viewer.id, cleaned, identifier)
            )
            if taken:
                raise NameTaken(cleaned)
            # Only this path adds a row. Updating a wall replaces one and is deliberately not
            # capped: somebody at the limit must still be able to edit what they already have.
            held = list(await connection.execute_fetchall(_ARRANGEMENT_COUNT, (viewer.id,)))
            if int(held[0]["held"]) >= MAX_ARRANGEMENTS:
                raise TooMany(
                    # Read on screen as it is, in the panel's word.
                    f"You already have {MAX_ARRANGEMENTS} Saved Layouts. "
                    "Delete one to save another."
                )
            await connection.execute(
                _INSERT_ARRANGEMENT,
                (identifier, viewer.id, cleaned, layout, _write_shape(shape), strip, now, now),
            )
            await self._write_cells(connection, identifier, kept)
        return Arrangement(
            id=identifier, name=cleaned, layout=layout, shape=shape, strip=strip, cells=cells
        )

    async def update(
        self,
        viewer: Viewer,
        arrangement_id: str,
        name: str,
        layout: str,
        shape: Shape | None,
        strip: int,
        cells: tuple[Cell, ...],
    ) -> Arrangement | None:
        """Change a saved wall (its name, its layout, its cells), or None if it is not theirs.

        One route rather than a rename and a separate re-save, because a wall is edited by being
        used: somebody loads one, moves a cell to a different source, and expects to keep the
        result. The cells are replaced wholesale rather than reconciled position by position, since
        a layout change moves how many there are and a partial update would leave a wall carrying
        cells from the shape it used to be.
        """
        cleaned = _clean_name(name)
        layout, shape, strip, cells = _checked(layout, shape, strip, cells)
        kept = await self._kept(viewer, cells)
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            taken = await connection.execute_fetchall(
                _NAME_IS_TAKEN, (viewer.id, cleaned, arrangement_id)
            )
            if taken:
                raise NameTaken(cleaned)
            changed = await connection.execute_fetchall(
                _UPDATE_ARRANGEMENT,
                (
                    cleaned,
                    layout,
                    _write_shape(shape),
                    strip,
                    self._now(),
                    viewer.id,
                    arrangement_id,
                ),
            )
            if not changed:
                return None
            await connection.execute(_DELETE_CELLS, (arrangement_id,))
            await self._write_cells(connection, arrangement_id, kept)
        return Arrangement(
            id=arrangement_id, name=cleaned, layout=layout, shape=shape, strip=strip, cells=cells
        )

    async def delete(self, viewer: Viewer, arrangement_id: str) -> None:
        """Drop one saved wall.

        Scoped in the statement: an id belonging to somebody else names no row here, so a guessed id
        deletes nothing and is answered the same way a real one is. The cells go with it: foreign
        keys are on for every connection, and the reference cascades.
        """
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            await connection.execute(_DELETE_ARRANGEMENT, (viewer.id, arrangement_id))

    async def _kept(self, viewer: Viewer, cells: tuple[Cell, ...]) -> tuple[Cell, ...]:
        """The cells as they are stored: each source by id. Asked before the write transaction
        opens, because resolving a name is a read and the one writer is not held for it."""
        sources = await self._filters.kept(viewer, [cell.source for cell in cells])
        return tuple(
            replace(cell, source=source) for cell, source in zip(cells, sources, strict=True)
        )

    @staticmethod
    async def _write_cells(
        connection: Connection, arrangement_id: str, cells: tuple[Cell, ...]
    ) -> None:
        for position, cell in enumerate(cells):
            await connection.execute(
                _INSERT_CELL,
                (
                    arrangement_id,
                    position,
                    cell.source,
                    cell.media_kind,
                    cell.ordering,
                    cell.end_behaviour,
                    cell.timer_seconds,
                    cell.volume,
                    cell.sort,
                    cell.aspect,
                ),
            )


#: Saved walls, and nothing else.
SERVICE: Part[TheaterService] = Part("theater")
