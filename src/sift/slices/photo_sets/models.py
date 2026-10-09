# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the photo-set screens send and receive: ids and names, never a path."""

from __future__ import annotations

from pydantic import Field, field_validator

from sift.kernel.content.user_state import MAX_RATING, MIN_RATING
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.text import clean_name
from sift.kernel.wire import Wire

MAX_PHOTO_SET_NAME = 120

#: Bounded: the write loop holds the one write lock while it runs.
MAX_BULK_ITEMS = 500


class PhotoSetSummary(Wire):
    """One photo set; an unseen cover is reported as none, so it is never published."""

    id: str
    name: str
    cover_asset_id: str | None = None
    cover_upload_id: str | None = None
    cover_at_ms: int | None = None
    cover_frame: CoverFrame | None = None
    art: str | None = None
    vault: bool = False
    item_count: int = 0
    size_bytes: int | None = None
    o_count: int = 0
    #: How this set came to exist: `manual`, `download` or `folder`, shown on the card.
    origin: str = "manual"
    origin_url: str | None = None
    notes: str | None = None
    created_at: int = 0
    favorite: bool = False
    rating: int | None = None
    pinned: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    #: Admin only: a grant is a decision about a user, told only to whoever makes them.
    shared: bool = False
    restricted: bool = False
    locked: bool = False


class PhotoSetList(Wire):
    """One page of the Photo Sets wall, and how many there are for whoever asked."""

    items: list[PhotoSetSummary]
    total: int
    limit: int
    offset: int


class PhotoSetWrite(Wire):
    """Creating or renaming a set; concealing is its own request."""

    name: str = Field(min_length=1, max_length=MAX_PHOTO_SET_NAME)

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str) -> str:
        return clean_name(value, what="a photo set's name")


class ItemsWrite(Wire):
    """Pictures to put in or take out, by id."""

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ITEMS)


class CoverWrite(Wire):
    """The picture the set is drawn as. Null takes the cover off."""

    asset_id: str | None = None
    at_ms: int | None = Field(default=None, ge=0)
    #: Only the upload that is already the cover, to reframe it; never beside `asset_id`.
    upload_id: str | None = None
    frame: CoverFrame | None = None


class NotesWrite(Wire):
    """Free text about the set. Null clears it."""

    notes: str | None = Field(default=None, max_length=4000)


class FavoriteWrite(Wire):
    favorite: bool


class RatingWrite(Wire):
    """Stars, or null to clear them; zero is not a rating."""

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
