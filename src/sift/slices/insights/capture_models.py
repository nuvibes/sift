# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the client reports about the pages it showed, as it crosses the wire. See `capture`."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sift.kernel.wire import Wire

#: The kinds of page a visit can be to: one thing (its id in `ref`), or one place (its name).
VisitPlace = Literal[
    "person",
    "tag",
    "site",
    "collection",
    "photo_set",
    "song",
    "folder",
    "wall",
    "settings",
    "organize",
    "insights",
]


class VisitReport(Wire):
    """One page opened, as the client kept it. Every time is how long AGO, in milliseconds."""

    #: The client's own id for this visit, the same every time it is reported.
    id: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    place: VisitPlace
    #: Which one: the thing's id, or the wall's, the Settings section's or the Organize queue's
    #: name. Empty where the page is the only one of its kind.
    ref: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_.-]*$")
    opened_ago_ms: int = Field(ge=0)
    #: How long ago it was last in front: 0 for a page in front now.
    last_ago_ms: int = Field(ge=0)
    #: How long it has been in front, the time a hidden tab spent hidden left out.
    front_ms: int = Field(ge=0)


class VisitsReport(Wire):
    """A batch of visits."""

    visits: list[VisitReport] = Field(max_length=240)


class VisitsKept(Wire):
    """How many of a batch were written: fewer where a page was not the person's to be shown, and
    none while their history is paused."""

    kept: int
