# SPDX-License-Identifier: AGPL-3.0-or-later
"""Saved walls: reading them back, writing them down, and refusing the ones that do not add up.

Every statement is scoped to the asking user in its own WHERE clause.
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
    """This user already has a wall under that name."""


class Unusable(ValueError):
    """The arrangement cannot be drawn as described. The message is meant to be read."""


class TooMany(Exception):
    """This user is holding as many walls as they may; editing one they have is never refused."""


#: The layouts and how many cells each holds; a built wall is stored under `custom`.
LAYOUT_CELLS: dict[str, int] = {
    "single": 1,
    "side_by_side": 2,
    "stacked": 2,
    "side_by_side_by_side": 3,
    "grid": 4,
    # One feed in focus and a strip of five.
    "center_stage": 6,
    "center_stage_two": 7,
    "center_stage_three": 8,
    "center_stage_grid": 9,
    # Retired from the picker but still accepted: saved walls carry these names.
    "stacked_three": 3,
    "one_above_two": 3,
    "two_above_one": 3,
}

#: What a wall that is not one of the layouts is stored under.
CUSTOM = "custom"

#: The most cells a wall may hold. The client has the same number and a gate checks they agree.
MOST_CELLS = 9

#: How many previews a Center stage layout opens with; the strip may hold more, up to `MOST_CELLS`.
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
    """A stored shape, or None where there is none or it cannot be read (the layout then stands)."""
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

#: What a cell does when a file ends: the player's own three names, held equal by a gate.
END_BEHAVIOURS: tuple[str, ...] = ("loop_one", "loop_all", "once")

#: What a cell will draw. Either things that move, or those and photographs as well.
MEDIA_KINDS: tuple[str, ...] = ("video_gif", "all")

#: Every search mode a cell may be saved with: a smart search is a different set of files.
SORTS: frozenset[str] = SORT_KEYS

#: The longest timer a cell will accept, in seconds.
MAX_TIMER_SECONDS = 3600

#: How long a source query may be, matching the cap the search route puts on the same text.
MAX_SOURCE = 1000

#: How long a wall's name may be.
MAX_NAME = 80

#: The most walls one user may keep; refused at the cap rather than dropping the oldest.
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
    #: How the source is searched, or None for the ordinary answer.
    sort: str | None = None
    #: The cell's shape, or `dynamic`; stored and not judged, since only the client draws it.
    aspect: str = "dynamic"


@dataclass(frozen=True, slots=True)
class Arrangement:
    """One saved wall: a layout, and a cell for each place in it, in order."""

    id: str
    name: str
    layout: str
    #: The grid and where each cell sits; None on a wall saved before walls could be built.
    shape: Shape | None
    #: How many of `cells` are previews in the strip, after the shape's places.
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

# The join scopes it: a cell is reachable only through an arrangement of the asking user.
_LIST_CELLS = """
SELECT c.arrangement_id, c.source, c.media_kind, c.ordering, c.end_behaviour,
       c.timer_seconds, c.volume, c.sort, c.aspect
  FROM theater_cells c
  JOIN theater_arrangements a ON a.id = c.arrangement_id
 WHERE a.user_id = ?
 ORDER BY c.arrangement_id, c.position
"""

# Scoped in the statement; `RETURNING id` tells "not yours, or not there" from "changed".
_UPDATE_ARRANGEMENT = """
UPDATE theater_arrangements SET name = ?, layout = ?, shape = ?, strip = ?, updated_at = ?
 WHERE user_id = ? AND id = ?
RETURNING id
"""

_DELETE_ARRANGEMENT = "DELETE FROM theater_arrangements WHERE user_id = ? AND id = ?"

_DELETE_CELLS = "DELETE FROM theater_cells WHERE arrangement_id = ?"

# Asked rather than inferred from a failed write, since a slice cannot see the driver's errors.
_NAME_IS_TAKEN = """
SELECT 1 FROM theater_arrangements
 WHERE user_id = ? AND name = ? AND id <> ?
 LIMIT 1
"""

#: Read inside the insert's transaction, so two saves cannot both take the last room.
_ARRANGEMENT_COUNT = "SELECT COUNT(*) AS held FROM theater_arrangements WHERE user_id = ?"


def _clean_name(name: str) -> str:
    """The name with its spacing tidied, or a refusal. Length is capped at the edge, not here."""
    cleaned = " ".join(name.split())
    if not cleaned:
        raise Unusable("a wall needs a name")
    return cleaned


def _checked_shape(shape: Shape, strip: int, cells: tuple[Cell, ...]) -> None:
    """A built wall that can be drawn, or a refusal saying what is wrong with it."""
    if shape.rows < 1 or shape.cols < 1:
        raise Unusable("a wall needs at least one row and one column")
    # Center stage sends more cells than the grid has room for: the extras are the strip.
    if len(shape.slots) + strip != len(cells):
        raise Unusable(
            f"the shape has {len(shape.slots)} places and a strip of {strip}, "
            f"and there are {len(cells)} cells"
        )
    if not 1 <= len(shape.slots) <= MOST_CELLS:
        raise Unusable(f"a wall holds 1 to {MOST_CELLS} cells, not {len(shape.slots)}")
    # After the count above, so too many places is not reported as negative room.
    room = MOST_CELLS - len(shape.slots)
    if not 0 <= strip <= room:
        raise Unusable(
            f"this shape leaves room for {room} previews, not {strip}: a wall holds {MOST_CELLS} "
            "cells in all, however they are divided between the places and the strip"
        )
    _checked_slots(shape)


def _checked_slots(shape: Shape) -> None:
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
    """The wall as given, or a refusal naming the part that cannot be drawn."""
    if shape is not None:
        _checked_shape(shape, strip, cells)
        if layout != CUSTOM and layout not in LAYOUT_CELLS:
            raise Unusable(f"unknown layout {layout!r}")
    else:
        # A layout's cell count includes the strip it opens with (`LAYOUT_CELLS`).
        wanted = LAYOUT_CELLS.get(layout)
        if wanted is None:
            raise Unusable(f"unknown layout {layout!r}")
        if len(cells) != wanted:
            raise Unusable(f"{layout} holds {wanted} cells, not {len(cells)}")
        if not 0 <= strip <= MOST_CELLS:
            raise Unusable(f"a wall holds {MOST_CELLS} cells, so a strip cannot hold {strip}")
    _checked_cells(cells)
    return layout, shape, strip, cells


def _checked_cells(cells: tuple[Cell, ...]) -> None:
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


class TheaterService:
    """The saved walls of whoever is asking; a cell's source is kept by id, shown by name."""

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
        """This user's saved walls, newest first."""
        rows = await self._db.fetch_all(_LIST_ARRANGEMENTS, (viewer.id,))
        held = await self._db.fetch_all(_LIST_CELLS, (viewer.id,))
        # Every source in one reading: one lookup per kind of thing named, not per cell.
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
                    # Null on cells saved before shapes existed.
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
        """Keep a wall under a name, for this user; a name already used is refused, not replaced."""
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
            # Only adding is capped: somebody at the limit can still edit what they have.
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
        """Change a saved wall, cells replaced wholesale, or None if it is not theirs."""
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
        """Drop one saved wall; its cells go with it by the cascade."""
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            await connection.execute(_DELETE_ARRANGEMENT, (viewer.id, arrangement_id))

    async def _kept(self, viewer: Viewer, cells: tuple[Cell, ...]) -> tuple[Cell, ...]:
        """The cells as stored, each source by id; resolved before the write opens."""
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
