# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Insights page as it crosses the wire; every figure is worded on the server, never twice."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal
from urllib.parse import quote

from pydantic import Field, computed_field, model_validator

from sift.kernel.wire import HistoryPiece, Wire, pieces_of
from sift.slices.insights.definitions import definition
from sift.slices.insights.statements import figure_said

Unit = Literal["ms", "count", "bytes", "minute_of_day", "views", "times", "presses", "files"]


class Figure(Wire):
    """One number the page shows, with what it counts."""

    label: str
    value: int
    unit: Unit
    #: 0 whenever the vault is locked: a locked page says nothing about what it left out.
    hidden_part: int = 0
    caption: list[HistoryPiece] = Field(default_factory=list)
    trend: list[int] = Field(default_factory=list)
    #: By its label unless the caller names it, for a label two blocks use differently.
    defines: list[HistoryPiece] = Field(default_factory=list)

    @model_validator(mode="after")
    def _defined(self) -> Figure:
        if not self.defines:
            line = definition(self.label)
            self.defines = pieces_of(line) if line else []
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def said(self) -> str:
        """`value` in words; empty for a time of day and a size, worded on the reader's settings."""
        return figure_said(self.value, self.unit)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def hidden_said(self) -> str:
        """`hidden_part` in the same words."""
        return figure_said(self.hidden_part, self.unit)


class BarPart(Wire):
    """One kind's share of a bar, or `all` for a bar that is not split."""

    kind: str
    value: int
    #: Filled by the `Chart`, which knows the unit.
    said: str = ""


class Bar(Wire):
    """One bar of a chart: a day, a month, an hour or a weekday, as its axis names it."""

    label: str
    parts: list[BarPart]
    said: str = ""


class Chart(Wire):
    """A block's one chart: bars on a common baseline, or one `share` bar; never a pie."""

    kind: Literal["bars", "share"] = "bars"
    unit: Unit
    bars: list[Bar]
    #: The bar still counting, while the period holds it.
    today: int | None = None
    caption: list[HistoryPiece] = Field(default_factory=list)

    @model_validator(mode="after")
    def _words(self) -> Chart:
        """Every bar and part said in this chart's unit, so the screen words no figure itself."""
        for bar in self.bars:
            for part in bar.parts:
                part.said = figure_said(part.value, self.unit)
            bar.said = figure_said(sum(part.value for part in bar.parts), self.unit)
        return self


class DayValue(Wire):
    """One calendar day's figure, for a heat-map: `day` is an ISO day on this device's calendar."""

    day: str
    value: int
    said: str = ""


class Calendar(Wire):
    """Every day of a period so far with its figure, drawn as a heat-map a day to a cell."""

    unit: Unit
    days: list[DayValue]

    @model_validator(mode="after")
    def _words(self) -> Calendar:
        """Every day said in this calendar's unit, as a chart's bars are."""
        for day in self.days:
            day.said = figure_said(day.value, self.unit)
        return self


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
    """One row of a list; a 404 from `cover` is ordinary, every caller draws a letter."""

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
    """One block of the page; below its floor, one line and no figures, chart or lists."""

    id: str
    title: str
    floor_reached: bool
    statements: list[list[HistoryPiece]]
    figures: list[Figure] = Field(default_factory=list)
    chart: Chart | None = None
    lists: list[NamedList] = Field(default_factory=list)
    calendar: Calendar | None = None
    notes: list[list[HistoryPiece]] = Field(default_factory=list)


class InsightsPage(Wire):
    """The whole page for one period; `from` and `to` are inclusive ISO days."""

    period: Literal["day", "week", "month", "year", "all"]
    from_: str = Field(serialization_alias="from")
    to: str
    today_is_live: bool
    first_sentences: list[list[HistoryPiece]]
    blocks: list[InsightsBlock]
