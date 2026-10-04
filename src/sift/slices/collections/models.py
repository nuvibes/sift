# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the collections screens send and receive.

Ids and names, never a path. `item_count` is what the asking viewer may see, not the rows in the
collection, which is why these views are built from the access layer's answer.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from sift.kernel.cover_frame import CoverFrame
from sift.kernel.text import clean_name
from sift.kernel.wire import Wire

#: Long enough for a real name, short enough that a grid of names stays a list.
MAX_COLLECTION_NAME = 120

#: The most items one call may add, remove or reorder: a dragged selection is a handful, and the
#: write loop holds the one write lock while it runs.
MAX_BULK_ITEMS = 500


class CollectionSummary(Wire):
    """One collection, as the person asking may know it.

    `cover_asset_id` is empty both when there is no cover and when this viewer may not see it,
    indistinguishably: naming a picture they cannot open would publish it.
    """

    id: str
    name: str
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
    vault: bool = False
    item_count: int = 0
    #: Bytes of the files `item_count` counts, for this viewer (the shut vault adds nothing). None
    #: where a reply does not say, which a screen keeps rather than reads as nothing.
    size_bytes: int | None = None
    #: This viewer's own O tally over the files they may see here. Filled only on the collection's
    #: own page: a sum per card would cost sixty sums for a number no card draws.
    o_count: int = 0
    #: What the asking user thinks of it, theirs alone, as a file and a person carry.
    favorite: bool = False
    rating: int | None = None
    #: Kept at the top of the wall by whoever is asking: where it sits, not an opinion.
    pinned: bool = False
    #: The card's counts beside the name, keyed by the tab each opens (`photo_sets`, `tags`,
    #: `sites`, `collections`, `people`), scoped as that tab's wall is. Wall listings only; an
    #: absent key is a cell the card does not draw.
    counts: dict[str, int] = Field(default_factory=dict)
    #: Whether anybody has been given this, or refused it: told only to an admin, who makes grants.
    #: A collection inherits from nothing, so the mark is always a decision on this row.
    shared: bool = False
    restricted: bool = False
    #: A locked tile on a wall: everything this viewer may see under it is in the shut vault with
    #: placeholders on, so the name comes back empty and the counts stay. Never on a read by id. The
    #: rule is `_LOCKED_TILE` in `kernel/access/repository/entities.py`.
    locked: bool = False


class CollectionList(Wire):
    """One page of the Collections wall, and how many there are for whoever asked.

    The total comes from the statement the rows came from, so a pager cannot disagree with its page.
    """

    items: list[CollectionSummary]
    total: int
    limit: int
    offset: int


class CollectionItem(Wire):
    """One item inside a collection, in the arranged order.

    A concealed item is described by its concealment and nothing else, as a concealed grid tile
    is. Whether it appears at all is the viewer's placeholder choice; either way the count agrees
    with the list, because both come from one read.
    """

    id: str
    media_type: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    concealed: bool = False
    #: Whether the still is built yet; the grid draws a shimmer until it is. False on a placeholder.
    thumb: bool = False
    #: The suffix that lets the browser keep this item's pictures, as on the grid's row; never on a
    #: placeholder.
    art: str | None = None
    #: The name the file was imported under, so a drag from a remote library can say what it is
    #: fetching. Never on a placeholder.
    original_filename: str | None = None
    #: Pinned by this user: this wall is read `pinned_first`, deliberately above the arranged order
    #: (a pin under it would stop working when the order changed). A move is computed from stored
    #: positions, never the drawn order, so the arrangement survives. Never on a placeholder.
    pinned: bool = False
    #: This user's heart and stars, so this wall hands its selection to the shared file verbs like
    #: every wall. Read for the page in one statement, never per row. Never on a placeholder.
    favorite: bool = False
    #: Out of five, or absent where this user has not said. Same read as `favorite`.
    rating: int | None = None
    #: Where this item sits in the arranged sequence, from zero, so Move earlier and Move later
    #: rearrange the real sequence rather than saving the pinned-first order the wall draws.
    position: int | None = None


class CollectionContents(Wire):
    """A page of one collection's items, and how many of them this viewer may see.

    `total` comes from the statement that produced the rows, so the count and the contents agree.
    """

    items: list[CollectionItem]
    total: int
    limit: int
    offset: int


class CollectionWrite(Wire):
    """Making or renaming a collection.

    No vault flag: concealing is its own request, or a create that concealed would have to answer
    with something the caller may no longer be shown.
    """

    name: str = Field(min_length=1, max_length=MAX_COLLECTION_NAME)

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str) -> str:
        """Trim the name, and refuse one that could never be typed as a filter token.

        A collection is named in a query as `collections:"summer 2024"`, so the kernel's shared rule
        applies: no double quote, and no control character that would make it unfindable.
        """
        return clean_name(value, what="a collection's name")


class FavoriteWrite(Wire):
    """The heart. Independent of the stars: favouriting a three-star collection is not a
    contradiction, and clearing the heart leaves the rating alone."""

    favorite: bool


class RatingWrite(Wire):
    """Whole stars, or null to clear them.

    Zero is refused: a caller means "unrated" by it, and stored it would sort as a real rating.
    """

    rating: int | None = Field(default=None, ge=1, le=5)


class CollectionStateView(Wire):
    """One person's opinion of one collection, handed back so an optimistic control can settle."""

    favorite: bool = False
    rating: int | None = None


class VaultWrite(Wire):
    """Put a collection in the vault, or take it back out.

    Answered with no body: describing what was just concealed would contradict it.
    """

    vault: bool


class CoverWrite(Wire):
    """Which item to wear as the cover, or null to go back to none.

    The asset must be in the collection, or a cover would be a second, weaker kind of membership.
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


class ItemsWrite(Wire):
    """Adding to, removing from, or rearranging a collection.

    One body with a named action, so add, remove and reorder cannot drift in what they accept. For
    `reorder`, `asset_ids` is the new order.
    """

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ITEMS)
    action: Literal["add", "remove", "reorder"] = "add"


class TagOnCollection(Wire):
    """One tag, as a collection carries it.

    This slice's own model rather than another's import: two fields are not worth coupling features.
    """

    id: str
    name: str


class CollectionTagWrite(Wire):
    tag_id: str
    add: bool = True
