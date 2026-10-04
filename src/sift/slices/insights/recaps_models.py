# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a recap sends, and what it keeps.

Two shapes for one card, on purpose. `RecapCard` is what crosses the wire: the words, the figure,
the picture, and which of the things it names are hidden for the reader NOW. `KeptCard` is what is
frozen in `recaps.body`: the same card as it was built over everything the User could see, plus the
RECIPE it was built from (the figures that went into it, whole, and the things it names), so the
reader can say it again for the vault state and the day it is opened in. The recipe never leaves
the server: it holds wholes, and a whole is exactly what a locked reader in Show nothing mode
must not be able to work out.

A card kept without a recipe (an achievement, a learning path goal reached) is drawn as it was
built; the only thing decided at draw time for it is whether a thing it names is hidden.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sift.kernel.wire import HistoryPiece, Wire
from sift.slices.insights.models import Chart, Figure, NamedRow

#: Every kind of card a recap holds. `achievement` is a learning path goal's; the rest are a
#: period's.
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
    "achievement",
]


class RecapCard(Wire):
    """One card, as the reader may be shown it now."""

    id: str
    kind: CardKind
    statement: list[HistoryPiece]
    figure: Figure | None = None
    cover: str | None = None
    #: The ranked rows of a card that names several things: the top five people.
    rows: list[NamedRow] = Field(default_factory=list)
    #: The favourite time's 24 hours, drawn as a ring.
    chart: Chart | None = None
    #: The ids on the card that are hidden for the reader NOW. Empty on a card whose `hidden` is
    #: true, which says nothing about what it would have named.
    hidden_things: list[str] = Field(default_factory=list)
    #: A locked tile: the card is there and what it says is not. Placeholder mode only; in Show
    #: nothing mode such a card is simply absent. Its statement, figure and cover are empty.
    hidden: bool = False


class RecapHead(Wire):
    """A recap as a list names it."""

    id: str
    #: 'week:2026-W39' | 'month:2026-09' | 'year:2026' | 'achievement:<name>'
    period: str
    #: "Your week", "Your September", "Your 2026".
    title: str
    #: The days it covers, in words: "September 21 to 27, 2026", "September 2026", "2026". A list
    #: of weeks all titled "Your week" is told apart by this and nothing else.
    span: str = ""
    made_at: int
    seen_at: int | None = None
    #: How many cards the reader will be shown, in their vault state now.
    cards: int


class Recap(Wire):
    """One recap, opened."""

    id: str
    period: str
    title: str
    span: str
    #: "September, in 8 cards".
    heading: str
    made_at: int
    cards: list[RecapCard]
    #: The period's first ISO day, so the recap can link to the same period on Insights. Empty for
    #: an achievement, which is not a period.
    first_day: str = ""
    #: Placeholder mode, locked, and something on these cards is hidden: "Some of September is
    #: hidden. Unlock to include it." Null in every other case, Show nothing mode above all.
    hidden_line: str | None = None


class RecapList(Wire):
    """Every recap of a period the reader has, newest first, and the one being announced."""

    recaps: list[RecapHead]
    #: The newest recap made in the last week that has been neither opened nor dismissed: the card
    #: at the top of Insights and the line on Browse's header. Null when there is none.
    announced: RecapHead | None = None


class NamedThing(Wire):
    """A thing a card names, as it was called when the card was built."""

    #: One of History's link kinds: person, site.
    kind: str
    id: str
    name: str


class Recipe(Wire):
    """What a card was built from, kept so it can be said again when it is drawn.

    A card is SAID when it is drawn, from this, for the reader's vault state and today's date: the
    figures are frozen and the words are not, so "last week" stays true a month later and a locked
    reader is told the figure over what is not hidden.
    """

    #: Every figure the card reads, WHOLE, by `metric|key` (the period before's with `prev:` before
    #: it). The hidden part is not kept: it is worked out when the recap is drawn.
    sources: dict[str, int] = Field(default_factory=dict)
    #: The things the card names.
    named: list[NamedThing] = Field(default_factory=list)
    #: Whether the period before was recorded whole and passed the floor, so the card may compare.
    compares: bool = False


class KeptCard(RecapCard):
    """A card as frozen in `recaps.body`. `hidden_things` here is every id the card names."""

    recipe: Recipe | None = None
