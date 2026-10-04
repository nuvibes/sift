# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the shoots routes hand back."""

from __future__ import annotations

from pydantic import Field, field_validator

from sift.kernel.text import clean_name
from sift.kernel.wire import HistoryLink, Wire


class ShootPictureView(Wire):
    """One picture of a proposed shoot, and whether it already carries the creator."""

    id: str
    #: The version of its thumbnail, so a client can address a picture that has been rebuilt. Null
    #: where the file has no thumbnail yet, which the client draws as a placeholder rather than as
    #: a broken picture.
    art: str | None = None
    named: bool = True
    #: What kind of file it is: `image` or `gif`, the two the pass groups. Sent so a card that
    #: opens the viewer on its pictures can say which of them RUN: a GIF plays on in the viewer and
    #: a still does not, and the viewer's own play-through is told which is which by whoever opens
    #: it (`openAsset`'s list). Read off the row the route already fetches for visibility.
    media_type: str = "image"


class ShootView(Wire):
    """One proposed shoot: whose it is, what is in it, and what is still nameless."""

    id: str
    person_id: str
    name: str
    found_at: int
    pictures: int
    #: The question this page's card asks about this shoot, and the line under it: "Do these 13
    #: photos of X belong together?" and "13 photos of X that are in no Photo Set". Sent rather
    #: than composed in the browser, from one place (`queue.asking`); a second copy of them in the
    #: client is a second copy to drift.
    question: str = ""
    detail: str = ""
    #: The things those two sentences name (the person the shoot is of) each with the run of
    #: characters it is said as, so the page draws that run IN THE QUESTION as a way to them (only
    #: the title line links; `detail` is drawn plain). The shape a history line's names travel
    #: in, and the client joins them to the words with the helper a history line uses. Empty draws
    #: the words plain, which is what a client that reads no links draws anyway.
    links: list[HistoryLink] = Field(default=[])
    items: list[ShootPictureView] = Field(default=[])
    #: How many of the pictures carry nobody at all. What "Name the rest" would be about, and zero
    #: on a shoot where there is nothing to offer.
    unnamed: int = 0


class ShootList(Wire):
    shoots: list[ShootView] = Field(default=[])
    total: int = 0
    offset: int = 0
    #: Whether shoots are made without anybody being asked. Drawn as a line on the page rather than
    #: left to be discovered: a queue that is empty because the switch is on reads exactly like a
    #: queue that is empty because there is nothing to do.
    auto_file: bool = False


class MakeWrite(Wire):
    """Making the set, and what to call it.

    `name` is optional and absent is the ordinary press: the set takes the name the proposal
    carries, which is the creator's. Given, it is the name somebody typed into "Create with a
    name...", tidied by the one rule every name in Sift goes through (`clean_name`: control
    characters dropped, a double quote refused out loud, blank refused). How LONG a Photo Set's
    name may be is the Photo Sets' own rule and is checked where it is handed in. See
    `ShootService.make`.
    """

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
    #: The name the set was made with: the proposal's, or the one typed in.
    name: str = ""


class RefusedView(Wire):
    """How many pictures will not be offered as a shoot again."""

    refused: int = 0


class NamedView(Wire):
    """What pressing Name the rest put the creator on."""

    files: int = 0
    decision_id: str | None = None
