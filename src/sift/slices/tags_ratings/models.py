# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the tag and rating endpoints accept and send back."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from sift.kernel.content.user_state import MAX_RATING, MIN_RATING
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.text import clean_name
from sift.kernel.wire import Wire

#: Long enough for a sentence somebody meant, short enough that nothing else is a tag name.
MAX_TAG_NAME = 64

#: A tag has no colour: it is a word. A request still sending `color` is not refused (`Wire`
#: ignores undeclared fields), so an older screen keeps working.
#:
#: A description is a sentence or two of what a tag means; the alias bound keeps one request from
#: asking for unbounded writes in one transaction.
MAX_TAG_DESCRIPTION = 2000
MAX_TAG_ALIASES = 50

#: Assets one call may tag at once: a few hundred is an ordinary selection, and an unbounded list
#: would make one request hold the whole library.
MAX_BULK_ASSETS = 500

#: Tags one call may apply at once: the work is the product of the two lists under one write lock,
#: and the gesture this serves, dragging onto a tag, names one.
MAX_BULK_TAGS = 50


class CoverWrite(Wire):
    """The still a tag is drawn as, or None to go back to having none.

    Each slice declares its own copy rather than reaching into another slice's models: the copies
    are identical only as long as the features agree, which nobody can promise.
    """

    asset_id: str | None = None
    #: Which moment of a video, in milliseconds, or None for the file's own picture; written with
    #: the file in one statement so it cannot outlive it. No ceiling: the running time is unknown
    #: here, and a moment past the end gives the last frame, as a seek past the end does in ffmpeg.
    at_ms: int | None = Field(default=None, ge=0)
    #: The upload that is ALREADY the cover, named only to reframe it; any other upload is refused
    #: (`kernel/covers.py upload_kept_by_put`). Never beside `asset_id`.
    upload_id: str | None = None
    #: The window of the picture it is drawn as, or None for the whole of it. See
    #: `kernel/cover_frame.py CoverFrame` for the bounds, which are checked here, on the way in.
    frame: CoverFrame | None = None


class TagView(Wire):
    """A tag, and how many assets the person asking can see under it.

    The count is theirs, not the library's: two users asking get different numbers.
    """

    id: str
    name: str
    asset_count: int = 0
    #: Bytes of the files `asset_count` counts, for this viewer (the shut vault adds nothing). None
    #: where a reply does not say, which a screen keeps rather than reads as nothing.
    size_bytes: int | None = None
    #: This viewer's own O tally over the files with this tag they may see. Filled only on the tag's
    #: own page: a sum per card would cost sixty sums for a number no card draws.
    o_count: int = 0
    #: The still this tag is drawn as. Absent alike for no cover and a cover this viewer cannot see,
    #: since telling the two apart would say a hidden file exists.
    cover_asset_id: str | None = None
    #: An UPLOADED cover, never beside `cover_asset_id` (one statement writes both). It tells a
    #: screen there is a cover to draw at all, and is folded into the address so a replaced upload
    #: is fetched again.
    cover_upload_id: str | None = None
    #: Which moment of the file, for a chosen video frame; withheld with it. Folded into the
    #: cover's address so the browser keeps the picture (`kernel/covers.py names_its_cover`).
    cover_at_ms: int | None = None
    #: The window of the picture it is drawn as, or None for all of it; withheld with the file
    #: (`kernel/cover_frame.py CoverFrame`).
    cover_frame: CoverFrame | None = None
    #: The user's token for this row's pictures (`face_version` of the stamp). An uploaded cover is
    #: kept only under an address carrying it, since the stamp moves with what this user may see.
    art: str | None = None
    #: Whether anybody has been given this, or refused it: told only to an admin, who makes grants.
    #: A tag inherits from nothing, so the mark is always a decision on this row.
    shared: bool = False
    restricted: bool = False
    #: A locked tile on a wall: everything this viewer may see under it is in the shut vault with
    #: placeholders on, so the name comes back empty and the counts stay. Never on a read by id. The
    #: rule is `_LOCKED_TILE` in `kernel/access/repository/entities.py`.
    locked: bool = False
    #: What this tag means, its other names and its category, by field key: only on a one-tag
    #: route, since a wall draws none of it.
    record: dict[str, Any] | None = None
    #: In the vault and listed anyway (the vault is open), so a chip offers to take it back out.
    hidden: bool = False
    #: What the asking user thinks of it, theirs alone, as a file carries.
    favorite: bool = False
    rating: int | None = None
    #: Kept at the top of the wall by whoever is asking: where it sits, not an opinion.
    pinned: bool = False
    #: The card's counts beside the name, keyed by the tab each opens (`photo_sets`, `tags`,
    #: `sites`, `collections`, `people`), scoped as that tab's wall is. Wall listings only; an
    #: absent key is a cell the card does not draw.
    counts: dict[str, int] = Field(default_factory=dict)
    #: Whether this may never be sent outside the machine. See `PersonView.keep_local`.
    keep_local: bool = False
    #: Marked "Don't swap". See `PersonView.keep_from_swaps`.
    keep_from_swaps: bool = False
    #: The tag this one is filed under and its name, so a picker can draw a branch under its
    #: parent. Absent at the top of the tree, and for a parent this viewer has hidden.
    parent_id: str | None = None
    parent_name: str | None = None


class TagList(Wire):
    """One page of the Tags wall, and how many there are for whoever asked.

    The total comes from the statement the rows came from, so a pager cannot disagree with its page.
    """

    items: list[TagView]
    total: int
    limit: int
    offset: int


class VaultWrite(Wire):
    """Put a tag in the vault, or take it back out.

    Answered with no body: describing what was just concealed would contradict it.
    """

    vault: bool


class TagWrite(Wire):
    """Creating a tag, or renaming one. The same fields either way."""

    name: str = Field(min_length=1, max_length=MAX_TAG_NAME)
    #: The record half. Absent leaves a field alone, never clears it, so a rename form that knows
    #: nothing of the description cannot destroy it.
    description: str | None = Field(default=None, max_length=MAX_TAG_DESCRIPTION)
    category: str | None = Field(default=None, max_length=MAX_TAG_NAME)
    aliases: list[str] | None = Field(default=None, max_length=MAX_TAG_ALIASES)
    #: The tag this one is filed under, BY NAME, as the record's "Part of" is typed. Absent to leave
    #: it alone; empty to take the tag back to the top.
    parent: str | None = Field(default=None, max_length=MAX_TAG_NAME)

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str) -> str:
        """Trim the name before it is stored, and refuse one that could never be searched for.

        A trailing space is invisible, and "beach " would sit beside "beach" as a second tag; the
        rest is `clean_name`, the rule the search box reads with, so every tag can be found by name.
        """
        return clean_name(value, what="a tag's name")


class TagAssignment(Wire):
    """Which assets, and which tags, and whether they are going on or coming off.

    Both sides are lists, so one call covers one chip on one clip and a selection. `add=False`
    reverses it rather than being another endpoint, which would be another place to miss the
    permission check.
    """

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)
    tag_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_TAGS)
    add: bool = True


class RatingWrite(Wire):
    """Whole stars, or null to clear it.

    Zero is refused: a caller means "unrated" by it, and stored it would sort and filter as a real
    rating, so clearing says null.
    """

    rating: int | None = Field(default=None, ge=MIN_RATING, le=MAX_RATING)


#: The three things that can be done to an O counter, as words the route takes. An act, not a new
#: value: a value read and written back from two tabs loses a press; the arithmetic stays in SQL.
O_UP = "up"
O_DOWN = "down"
O_RESET = "reset"


class OCountWrite(Wire):
    """One press of the O mark, one press taken back, or the tally cleared.

    Per user, like the heart and the stars. A closed set of words, so a wrong one is a 422 from the
    model before this slice runs.
    """

    change: Literal["up", "down", "reset"]


class FavoriteWrite(Wire):
    """The heart. Independent of the rating: favouriting a three-star clip is not a contradiction,
    and clearing the heart leaves the stars alone."""

    favorite: bool


class FavoriteMany(Wire):
    """The heart, over a whole selection. One target state for all of them.

    Not a toggle each, which would leave a mixed selection more mixed: the caller decides the value
    once, as the pin and the stars do.
    """

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)
    favorite: bool


class RatingMany(Wire):
    """One rating across a selection, or null to clear it across all of them.

    The same bounds as the single write, zero refused for the same reason.
    """

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)
    rating: int | None = Field(default=None, ge=MIN_RATING, le=MAX_RATING)


class PinMany(Wire):
    """The pin, over a whole selection. One target state, for the reason `FavoriteMany` gives.

    Its own shape: `content.PinWrite` is the body of five entity routes, and a list there would
    reach all five.
    """

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)
    pinned: bool


class TagStateView(Wire):
    """One person's opinion of one TAG, handed back so an optimistic control can settle.

    A type of its own though the fields match the asset view: different questions, so a shared
    model would be a shared model to change.
    """

    favorite: bool = False
    rating: int | None = None
