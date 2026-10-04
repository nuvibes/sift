# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Theater endpoints send and accept.

The same shape in both directions, deliberately: a wall read back is a wall that can be written
again without translation, so loading one, changing a cell and saving it is the client sending back
what it was given. The validation that matters (a layout holding the right number of cells, a
behaviour that means something) is the service's, because it is the same check whichever route
arrives at it.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from sift.kernel.wire import Wire
from sift.slices.theater.service import MAX_NAME, MAX_SOURCE, MOST_CELLS


class CellBody(Wire):
    """One cell of a wall: what it draws from, and how it behaves. Never what it was playing.

    Only the two lengths are capped here, and only because there is no reason to carry a megabyte of
    text into the service to find out it is too long. What a value MEANS (a behaviour that exists,
    a timer in range) is the service's to decide, so that saving a wall and loading one are judged
    by the same code rather than by a model on one path and a check on the other.
    """

    #: The query this cell draws from. Empty means the whole library, which is what a cell added
    #: before anybody has chosen a source shows.
    source: str = Field(default="", max_length=MAX_SOURCE)
    media_kind: str
    ordering: str
    end_behaviour: str
    #: Null means the cell moves on when the clip ends and not before.
    timer_seconds: int | None = None
    volume: int
    #: How the source is searched. Null is the ordinary answer and is what nearly every cell holds.
    sort: str | None = Field(default=None, max_length=32)
    #: What shape the cell is held to. `dynamic` is the shape of whatever it is playing, which is
    #: what every wall was before shapes existed. Capped and not checked against a list: see the
    #: service's own field, which says why the server has no opinion about this one.
    aspect: str = Field(default="dynamic", max_length=32)


class SlotBody(Wire):
    """Where one cell sits in the grid: its top-left corner, and how many tracks it covers."""

    row: int = Field(ge=0, le=MOST_CELLS)
    col: int = Field(ge=0, le=MOST_CELLS)
    row_span: int = Field(ge=1, le=MOST_CELLS)
    col_span: int = Field(ge=1, le=MOST_CELLS)


class ShapeBody(Wire):
    """A wall's grid, and where each cell sits in it.

    Only the sizes are bounded here, and only so that a nonsense number never reaches the service.
    Whether the slots actually FIT (inside the grid, one to a square, as many as there are cells)
    is the service's, so that saving a wall and updating one are judged by the same code.
    """

    rows: int = Field(ge=1, le=MOST_CELLS)
    cols: int = Field(ge=1, le=MOST_CELLS)
    slots: list[SlotBody] = Field(max_length=MOST_CELLS)


class ArrangementBody(Wire):
    """A wall as it is saved: a shape, a name, and a cell for each place in it."""

    name: str = Field(max_length=MAX_NAME)
    #: The layout it is in, or `custom`. What a wall saved before walls could be built has instead
    #: of a shape, and what one falls back to when a stored shape cannot be drawn.
    layout: str
    shape: ShapeBody | None = None
    #: How many of the cells below are PREVIEWS in the strip under the wall, rather than feeds in
    #: it. The shape's places come first and the strip is the rest, which is the order they are
    #: drawn in. Zero is what every wall saved before Center stage had, and what a client that does
    #: not know about strips still sends.
    strip: int = Field(default=0, ge=0, le=MOST_CELLS)
    cells: list[CellBody]


class ArrangementOut(Wire):
    """A saved wall, read back."""

    id: str
    name: str
    layout: str
    shape: ShapeBody | None = None
    #: How many of the cells below are PREVIEWS in the strip under the wall, rather than feeds in
    #: it. The shape's places come first and the strip is the rest, which is the order they are
    #: drawn in. Zero is what every wall saved before Center stage had, and what a client that does
    #: not know about strips still sends.
    strip: int = Field(default=0, ge=0, le=MOST_CELLS)
    cells: list[CellBody]


class Arrangements(Wire):
    """This user's saved walls, newest first. There is no route to anybody else's."""

    items: list[ArrangementOut]


#: The longest one Theater session may claim to have been open. A week: a wall left running across a
#: long weekend is a real thing somebody can do, and anything past a week is a counter that ran away
#: rather than an evening. Bounded because it arrives from a browser.
MAX_SESSION_MS = 7 * 24 * 60 * 60 * 1000


class SessionReport(Wire):
    """One report of a Theater session: the wall opening, or the wall closing.

    Facts only, the way a sitting's report is. The wall sends one when it opens, with nothing on it
    but the time, and one when it closes, with what it was: the layout, how many cells, what each
    was drawing from, the saved wall it came from, and how many different files it showed. Every
    field is optional so that the opening report is the same shape as the closing one, and so a
    route answering a keepalive from a closing page never refuses the whole report over one field.
    """

    #: How long the wall has been open, as the browser measured it. Zero on the opening report.
    elapsed_ms: int = Field(default=0, ge=0, le=MAX_SESSION_MS)
    #: This report is the wall closing.
    ended: bool = False
    #: The layout the wall is in, or `custom` for one no layout answers to.
    layout: str | None = Field(default=None, max_length=32)
    #: How many cells were drawn, previews included.
    cells: int | None = Field(default=None, ge=0, le=MOST_CELLS)
    #: The saved wall it was loaded from, if it was.
    arrangement: str | None = Field(default=None, max_length=64)
    #: Each drawn cell's source, in the order the wall draws them.
    sources: list[Annotated[str, Field(max_length=MAX_SOURCE)]] | None = Field(
        default=None, max_length=MOST_CELLS
    )
    #: How many different files the wall showed.
    files: int = Field(default=0, ge=0, le=1_000_000)
