# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Theater endpoints send and accept: one shape both ways, judged by the service."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from sift.kernel.wire import Wire
from sift.slices.theater.service import MAX_NAME, MAX_SOURCE, MOST_CELLS


class CellBody(Wire):
    """One cell of a wall: what it draws from and how it behaves; only lengths are capped here."""

    #: The query this cell draws from; empty means the whole library.
    source: str = Field(default="", max_length=MAX_SOURCE)
    media_kind: str
    ordering: str
    end_behaviour: str
    #: Null means the cell moves on when the clip ends and not before.
    timer_seconds: int | None = None
    volume: int
    sort: str | None = Field(default=None, max_length=32)
    #: The shape the cell is held to; `dynamic` follows what it plays. Capped, never listed.
    aspect: str = Field(default="dynamic", max_length=32)


class SlotBody(Wire):
    """Where one cell sits in the grid: its top-left corner, and how many tracks it covers."""

    row: int = Field(ge=0, le=MOST_CELLS)
    col: int = Field(ge=0, le=MOST_CELLS)
    row_span: int = Field(ge=1, le=MOST_CELLS)
    col_span: int = Field(ge=1, le=MOST_CELLS)


class ShapeBody(Wire):
    """A wall's grid, and where each cell sits in it; whether they fit is the service's."""

    rows: int = Field(ge=1, le=MOST_CELLS)
    cols: int = Field(ge=1, le=MOST_CELLS)
    slots: list[SlotBody] = Field(max_length=MOST_CELLS)


class ArrangementBody(Wire):
    """A wall as it is saved: a shape, a name, and a cell for each place in it."""

    name: str = Field(max_length=MAX_NAME)
    #: The layout, or `custom`: what a wall without a usable shape is drawn as.
    layout: str
    shape: ShapeBody | None = None
    #: How many of the cells are previews in the strip, after the shape's places.
    strip: int = Field(default=0, ge=0, le=MOST_CELLS)
    cells: list[CellBody]


class ArrangementOut(Wire):
    """A saved wall, read back."""

    id: str
    name: str
    layout: str
    shape: ShapeBody | None = None
    strip: int = Field(default=0, ge=0, le=MOST_CELLS)
    cells: list[CellBody]


class Arrangements(Wire):
    """This user's saved walls, newest first. There is no route to anybody else's."""

    items: list[ArrangementOut]


#: The longest one Theater session may claim to have been open: a week.
MAX_SESSION_MS = 7 * 24 * 60 * 60 * 1000


class SessionReport(Wire):
    """One report of a Theater session opening or closing; every field is optional."""

    #: How long the wall has been open, as the browser measured it. Zero on the opening report.
    elapsed_ms: int = Field(default=0, ge=0, le=MAX_SESSION_MS)
    ended: bool = False
    layout: str | None = Field(default=None, max_length=32)
    cells: int | None = Field(default=None, ge=0, le=MOST_CELLS)
    arrangement: str | None = Field(default=None, max_length=64)
    sources: list[Annotated[str, Field(max_length=MAX_SOURCE)]] | None = Field(
        default=None, max_length=MOST_CELLS
    )
    files: int = Field(default=0, ge=0, le=1_000_000)
