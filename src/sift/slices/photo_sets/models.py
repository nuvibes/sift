# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the photo-set screens send and receive.

Ids and names, never a path. `item_count` is what the asking viewer may see, not the rows in the
set, which is why these views are built from the access layer's answer.
"""

from __future__ import annotations

from pydantic import Field, field_validator

from sift.kernel.content.user_state import MAX_RATING, MIN_RATING
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.text import clean_name
from sift.kernel.wire import Wire

#: Long enough for a real name and short enough that the wall stays a wall of cards.
MAX_PHOTO_SET_NAME = 120

#: The most pictures one call may add, remove or rearrange: a dragged selection is a handful, and
#: the write loop holds the one write lock while it runs.
MAX_BULK_ITEMS = 500


class PhotoSetSummary(Wire):
    """One photo set, as the person asking may know it.

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
    #: This viewer's own O tally over the files here they may see. Filled only on the set's own
    #: page: a sum per card would cost sixty sums for a number no card draws.
    o_count: int = 0
    #: How this set came to exist: `manual`, `download` or `folder`. Shown on the card, because
    #: "these forty pictures arrived together from one page" is most of what a set means.
    origin: str = "manual"
    origin_url: str | None = None
    notes: str | None = None
    created_at: int = 0
    #: What the asking user thinks of it, theirs alone, as a file, person and collection carry.
    favorite: bool = False
    rating: int | None = None
    #: Kept at the top of the wall by whoever is asking: where it sits, not an opinion.
    pinned: bool = False
    #: The card's counts beside the name, keyed by the tab each opens (`photo_sets`, `tags`,
    #: `sites`, `collections`, `people`), scoped as that tab's wall is. Wall listings only; an
    #: absent key is a cell the card does not draw.
    counts: dict[str, int] = Field(default_factory=dict)
    #: Whether anybody has been given this set, or refused it. Filled for an admin and left false for
    #: everybody else: a grant is a decision about a user, and only whoever makes them is told.
    shared: bool = False
    restricted: bool = False
    #: A locked tile on a wall: everything this viewer may see under it is in the shut vault with
    #: placeholders on, so the name comes back empty and the counts stay. Never on a read by id. The
    #: rule is `_LOCKED_TILE` in `kernel/access/repository/entities.py`.
    locked: bool = False


class PhotoSetList(Wire):
    """One page of the Photo Sets wall, and how many there are for whoever asked."""

    items: list[PhotoSetSummary]
    total: int
    limit: int
    offset: int


class PhotoSetWrite(Wire):
    """Making or renaming a set.

    No vault flag, as for a collection: a create that concealed would have to describe something
    the caller may no longer be shown.
    """

    name: str = Field(min_length=1, max_length=MAX_PHOTO_SET_NAME)

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str) -> str:
        # `what` names the field in the refusal; `clean_name` refuses a blank itself.
        return clean_name(value, what="a photo set's name")


class ItemsWrite(Wire):
    """Pictures to put in or take out, by id."""

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ITEMS)


class CoverWrite(Wire):
    """The picture the set is drawn as. Null takes the cover off."""

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


class NotesWrite(Wire):
    """Free text about the set. Null clears it."""

    notes: str | None = Field(default=None, max_length=4000)


class FavoriteWrite(Wire):
    favorite: bool


class RatingWrite(Wire):
    """Stars, or null to clear them. Zero is not a rating: clearing is null, so a query for
    "rated at all" is a null check rather than a magic number somebody has to remember."""

    rating: int | None = Field(default=None, ge=MIN_RATING, le=MAX_RATING)


class VaultWrite(Wire):
    vault: bool


class PhotoSetStateView(Wire):
    """What this user thinks of a set, after a write to it."""

    favorite: bool
    rating: int | None = None


class TagOnPhotoSet(Wire):
    id: str
    name: str


class PhotoSetTagWrite(Wire):
    tag_id: str
    add: bool = True
