# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a recap sends, and what it keeps: the recipe holds wholes, so it never leaves the server."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sift.kernel.wire import HistoryPiece, Wire
from sift.slices.insights.models import Calendar, Chart, Figure, NamedRow

CardKind = Literal[
    "headline",
    "top_person",
    "top_five",
    "top_site",
    "top_tag",
    "top_song",
    "theater",
    "when",
    "rated",
    "sift_did",
    "compared",
    "o",
    "closing",
    "top_file",
    "first_last",
    "new_favourite",
    "rediscovered",
    "session",
    "downloads",
    "theater_files",
    "alongside",
    "mosaic",
    "before_after",
    "race",
    "heatmap",
    "achievement",
]


class WallSlot(Wire):
    """Where one cell of a Theater wall sits: its top-left track and how many it covers, from 0."""

    row: int = Field(ge=0)
    col: int = Field(ge=0)
    row_span: int = Field(ge=1)
    col_span: int = Field(ge=1)


class Wall(Wire):
    """A Saved Layout's grid, for the card that draws the wall in miniature."""

    rows: int = Field(ge=1)
    cols: int = Field(ge=1)
    slots: list[WallSlot]


class RecapCard(Wire):
    """One card, as the reader may be shown it now."""

    id: str
    kind: CardKind
    statement: list[HistoryPiece]
    #: Empty on a card with no voice (an achievement), which leads with its statement.
    headline: list[HistoryPiece] = Field(default_factory=list)
    context: list[HistoryPiece] = Field(default_factory=list)
    figure: Figure | None = None
    cover: str | None = None
    rows: list[NamedRow] = Field(default_factory=list)
    chart: Chart | None = None
    calendar: Calendar | None = None
    figures: list[Figure] = Field(default_factory=list)
    #: Empty on a `hidden` card, which says nothing about what it would have named.
    hidden_things: list[str] = Field(default_factory=list)
    #: A locked tile, placeholder mode only; in Show nothing mode the card is absent.
    hidden: bool = False
    #: OKLCH degrees of the period's most-viewed file; None for a grey or unshown one.
    accent_hue: int | None = None
    wall: Wall | None = None


class RecapHead(Wire):
    """A recap as a list names it."""

    id: str
    #: 'day:YYYY-MM-DD' | 'week:2026-W39' | 'month:2026-09' | 'year:2026' | 'achievement:<name>'
    period: str
    title: str
    #: Weeks all titled "Your week" are told apart by this alone.
    span: str = ""
    made_at: int
    seen_at: int | None = None
    cards: int


class Recap(Wire):
    """One recap, opened."""

    id: str
    period: str
    title: str
    span: str
    heading: str
    made_at: int
    cards: list[RecapCard]
    first_day: str = ""
    #: Null in Show nothing mode above all: only placeholder mode says something is hidden.
    hidden_line: str | None = None


class RecapList(Wire):
    """Every recap of a period the reader has, newest first, and the one being announced."""

    recaps: list[RecapHead]
    announced: RecapHead | None = None


class NamedThing(Wire):
    """A thing a card names, as it was called when the card was built."""

    #: One of History's link kinds: person, site.
    kind: str
    id: str
    name: str


class Recipe(Wire):
    """What a card was built from: the figures are frozen, the words said again when drawn."""

    #: Whole figures; the hidden part is worked out when the recap is drawn.
    sources: dict[str, int] = Field(default_factory=dict)
    named: list[NamedThing] = Field(default_factory=list)
    compares: bool = False
    wall: Wall | None = None


class KeptCard(RecapCard):
    """A card as frozen in `recaps.body`. `hidden_things` here is every id the card names."""

    recipe: Recipe | None = None
    #: The file `accent_hue` was read from: a reader who may not be shown it is not given its hue.
    accent_of: str | None = None
