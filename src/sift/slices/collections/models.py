# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the collections screens send and receive: ids and names, never a path."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from sift.kernel.cover_frame import CoverFrame
from sift.kernel.text import clean_name
from sift.kernel.wire import Wire

MAX_COLLECTION_NAME = 120

#: Bounded: the write loop holds the one write lock while it runs.
MAX_BULK_ITEMS = 500


class CollectionSummary(Wire):
    """One collection; an unseen cover is reported as none, so it is never published."""

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
    favorite: bool = False
    rating: int | None = None
    pinned: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    shared: bool = False
    restricted: bool = False
    locked: bool = False


class CollectionList(Wire):
    """One page of the Collections wall, and how many there are for whoever asked."""

    items: list[CollectionSummary]
    total: int
    limit: int
    offset: int


class CollectionItem(Wire):
    """One item inside a collection; a concealed one is described by its concealment alone."""

    id: str
    media_type: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    concealed: bool = False
    thumb: bool = False
    art: str | None = None
    original_filename: str | None = None
    pinned: bool = False
    favorite: bool = False
    rating: int | None = None


class CollectionContents(Wire):
    """A page of one collection's items, and how many of them this viewer may see."""

    items: list[CollectionItem]
    total: int
    limit: int
    offset: int


class CollectionWrite(Wire):
    """Creating or renaming a collection; concealing is its own request."""

    name: str = Field(min_length=1, max_length=MAX_COLLECTION_NAME)

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str) -> str:
        """Trim the name and refuse one that could never be typed as a filter token."""
        return clean_name(value, what="a collection's name")


class FavoriteWrite(Wire):
    """The heart, independent of the stars."""

    favorite: bool


class RatingWrite(Wire):
    """Whole stars, or null to clear; zero is refused so it never sorts as a real rating."""

    rating: int | None = Field(default=None, ge=1, le=5)


class CollectionStateView(Wire):
    """One person's opinion of one collection, handed back so an optimistic control can settle."""

    favorite: bool = False
    rating: int | None = None


class VaultWrite(Wire):
    """Put a collection in the vault, or take it back out."""

    vault: bool


class CoverWrite(Wire):
    """Which item to wear as the cover (it must be in the collection), or null for none."""

    asset_id: str | None = None
    at_ms: int | None = Field(default=None, ge=0)
    #: Only the upload that is already the cover, to reframe it; never beside `asset_id`.
    upload_id: str | None = None
    frame: CoverFrame | None = None


class ItemsWrite(Wire):
    """Adding to or removing from a collection: one body, so the two cannot drift."""

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ITEMS)
    action: Literal["add", "remove"] = "add"


class TagOnCollection(Wire):
    """One tag, as a collection carries it."""

    id: str
    name: str


class CollectionTagWrite(Wire):
    tag_id: str
    add: bool = True
