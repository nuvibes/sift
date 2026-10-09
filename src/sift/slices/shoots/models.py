# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the shoots routes hand back."""

from __future__ import annotations

from pydantic import Field, field_validator

from sift.kernel.text import clean_name
from sift.kernel.wire import HistoryLink, Wire


class ShootPictureView(Wire):
    """One picture of a proposed shoot, and whether it already carries the creator."""

    id: str
    #: Null until a thumbnail exists, which the client draws as a placeholder.
    art: str | None = None
    named: bool = True
    #: `image` or `gif`, so the viewer opened from a card knows which pictures play.
    media_type: str = "image"


class ShootView(Wire):
    """One proposed shoot: whose it is, what is in it, and what is still nameless."""

    id: str
    person_id: str
    name: str
    found_at: int
    pictures: int
    #: Composed once in `queue.asking` so the client keeps no second copy to drift.
    question: str = ""
    detail: str = ""
    #: The names in `question` drawn as links, as a history line's are; empty draws them plain.
    links: list[HistoryLink] = Field(default=[])
    items: list[ShootPictureView] = Field(default=[])
    unnamed: int = 0


class ShootList(Wire):
    shoots: list[ShootView] = Field(default=[])
    total: int = 0
    offset: int = 0
    #: Shown so a queue emptied by the switch is not read as nothing to do.
    auto_file: bool = False


class MakeWrite(Wire):
    """Creating the set; `name` absent takes the proposal's, the creator's."""

    name: str | None = None

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str | None) -> str | None:
        return None if value is None else clean_name(value, what="a Photo Set's name")


class MadeView(Wire):
    """What pressing Create Photo Set produced, and what the set is called."""

    photo_set_id: str
    pictures: int
    decision_id: str | None = None
    name: str = ""


class RefusedView(Wire):
    """How many pictures will not be offered as a shoot again."""

    refused: int = 0


class NamedView(Wire):
    """What pressing Name the rest put the creator on."""

    files: int = 0
    decision_id: str | None = None
