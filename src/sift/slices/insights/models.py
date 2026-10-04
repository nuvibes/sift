# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Insights page as it crosses the wire: a period, its first sentences, and its blocks.

Every sentence is PIECES (`HistoryPiece`), built on the server by `statements.py` and drawn by the
client's `HistorySentence`, which builds nothing. A figure is a number and its unit, never a
formatted string: the client lays figures out (a tile, a bar), and every figure that is SAID is
said by a statement, so no number is worded twice.

`hidden_part` is the part of a figure that came from hidden things. The page is drawn for the
reader's vault state, so `value` is already what the reader may see; while the vault is locked
`hidden_part` is always 0 (a locked page says nothing about what it left out: in the default mode
not even that it left something out), and while it is unlocked it is how much of `value` the vault
holds. A frozen recap card carries the two so its figure can be worked out for the reader's state
when it is opened (`recaps.py`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal
from urllib.parse import quote

from pydantic import Field, computed_field, model_validator

from sift.kernel.wire import HistoryPiece, Wire
from sift.slices.insights.statements import figure_said

#: What a figure's number counts. `count` is a count of the thing the label names; the plain
#: nouns after it are counts that say what they count ("13 views"), for a list of things whose
#: figure would otherwise be a bare number beside a name.
Unit = Literal["ms", "count", "bytes", "minute_of_day", "views", "times", "presses", "files"]


class Figure(Wire):
    """One number the page shows, with what it counts."""

    label: str
    value: int
    unit: Unit
    #: The part of `value` that came from hidden things. 0 whenever the vault is locked.
    hidden_part: int = 0
    #: The one sentence a card says under the figure (a comparison, a busiest day), or empty.
    caption: list[HistoryPiece] = Field(default_factory=list)
    #: The figure over the period, bar by bar of the Overview's chart (a week's days, a month's, a
    #: year's months, a day's hours), for the line of little bars a tile draws under its number;
    #: empty where the period has no such run of it (a day has no hours of sessions).
    trend: list[int] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def said(self) -> str:
        """`value` in words, by the statements' own rule (`statements.figure_said`), or empty for
        a time of day and a size, which the screen words on the reader's own settings."""
        return figure_said(self.value, self.unit)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def hidden_said(self) -> str:
        """`hidden_part` in the same words."""
        return figure_said(self.hidden_part, self.unit)


class BarPart(Wire):
    """One kind's share of a bar: videos, pictures, GIFs or Theater, or `all`, for a bar that is
    not split (the hours of the day)."""

    kind: str
    value: int
    #: `value` in words, by `statements.figure_said` in the chart's unit: filled by the `Chart`
    #: it stands in, which is what knows the unit. The screen draws these and words no bar itself.
    said: str = ""


class Bar(Wire):
    """One bar of a chart: a day, a month, an hour or a weekday, as its axis names it."""

    label: str
    parts: list[BarPart]
    #: The whole bar (its parts added up) in words, filled by its `Chart` like each part's.
    said: str = ""


class Chart(Wire):
    """A block's one chart. Bars on a common baseline, the one encoding people read accurately,
    or `share`: one bar whose parts are the shares of a whole. Never a pie, never a gauge."""

    kind: Literal["bars", "share"] = "bars"
    unit: Unit
    bars: list[Bar]
    #: Which bar is today (or this month), while the period holds it: still counting.
    today: int | None = None
    #: The one sentence under the chart (its biggest bar, said), or empty.
    caption: list[HistoryPiece] = Field(default_factory=list)

    @model_validator(mode="after")
    def _words(self) -> Chart:
        """Every bar and every part of one said in this chart's unit (`figure_said`), so no
        figure on the screen is worded there: a bar's readout, its row in the table under the
        chart and the top of its scale read the words the tiles and statements are said by."""
        for bar in self.bars:
            for part in bar.parts:
                part.said = figure_said(part.value, self.unit)
            bar.said = figure_said(sum(part.value for part in bar.parts), self.unit)
        return self


class DayValue(Wire):
    """One calendar day's figure, for a heat-map: `day` is an ISO day on this device's calendar."""

    day: str
    value: int
    #: `value` in words, filled by its `Calendar` in the calendar's unit, as a bar's is.
    said: str = ""


class Calendar(Wire):
    """Every day of a period so far with its figure, drawn as a heat-map a day to a cell."""

    unit: Unit
    days: list[DayValue]

    @model_validator(mode="after")
    def _words(self) -> Calendar:
        """Every day said in this calendar's unit (`figure_said`), as a chart's bars are."""
        for day in self.days:
            day.said = figure_said(day.value, self.unit)
        return self


#: Where each kind of thing's picture is served: the address its own wall draws it from.
COVERS: Mapping[str, str] = {
    "person": "/api/people/{}/cover",
    "site": "/api/sites/{}/cover",
    "tag": "/api/tags/{}/cover",
    "collection": "/api/collections/{}/cover",
    "photo_set": "/api/photo-sets/{}/cover",
    "song": "/api/songs/{}/cover",
    "asset": "/api/assets/{}/thumb",
}


def cover_of(kind: str, key: str) -> str | None:
    """The address a thing's picture is served at, or None for a kind with no picture."""
    template = COVERS.get(kind)
    return None if template is None else template.format(quote(key, safe=""))


class NamedRow(Wire):
    """One row of a list: the thing, as a piece that links to it, its figure, and its picture.

    `cover` is the address the thing's own picture is served at, the same one its wall and its
    page draw (`/api/people/<id>/cover`, `/api/assets/<id>/thumb`), or null for a thing with no
    picture (a saved wall, a task family). A 404 from it is ordinary: every caller draws a letter.
    """

    piece: HistoryPiece
    value: int
    unit: Unit
    cover: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def said(self) -> str:
        """`value` in words, as `Figure.said` says it."""
        return figure_said(self.value, self.unit)


class NamedList(Wire):
    """A short list of named things under a heading: the top people, Sites, tags, files."""

    title: str
    rows: list[NamedRow]


class InsightsBlock(Wire):
    """One block of the page. Below its floor it carries `floor_reached: false` and the one line
    "Not enough yet to say." as its only statement, and no figures, chart or lists."""

    id: str
    title: str
    floor_reached: bool
    statements: list[list[HistoryPiece]]
    figures: list[Figure] = Field(default_factory=list)
    chart: Chart | None = None
    lists: list[NamedList] = Field(default_factory=list)
    calendar: Calendar | None = None
    #: Lines that stand under the block on their own: that Sift is still counting, that some of
    #: the period is hidden. The figures carry everything else the statements say.
    notes: list[list[HistoryPiece]] = Field(default_factory=list)


class InsightsPage(Wire):
    """The whole page for one period.

    `from` and `to` are inclusive ISO days on this device's calendar. `today_is_live` says today's
    figures were counted just now from the raw tables rather than read from the added-up days.
    """

    period: Literal["day", "week", "month", "year", "all"]
    #: `from` is a Python keyword, so the field is spelled with a trailing underscore here and
    #: crosses the wire as `from`.
    from_: str = Field(serialization_alias="from")
    to: str
    today_is_live: bool
    first_sentences: list[list[HistoryPiece]]
    blocks: list[InsightsBlock]
