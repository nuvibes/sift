# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the tag and rating endpoints accept and send back."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from sift.kernel.content.user_state import MAX_RATING, MIN_RATING
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.text import clean_name
from sift.kernel.wire import Wire

MAX_TAG_NAME = 64

MAX_TAG_DESCRIPTION = 2000
MAX_TAG_ALIASES = 50

#: Bounds on one bulk call: an unbounded list would make one request hold the whole library.
MAX_BULK_ASSETS = 500

MAX_BULK_TAGS = 50


class CoverWrite(Wire):
    """The still a tag is drawn as, or None to go back to having none."""

    asset_id: str | None = None
    at_ms: int | None = Field(default=None, ge=0)
    #: Only the upload that is already the cover, to reframe it; never beside `asset_id`.
    upload_id: str | None = None
    frame: CoverFrame | None = None


class TagView(Wire):
    """A tag, and how many assets the person asking can see under it."""

    id: str
    name: str
    asset_count: int = 0
    size_bytes: int | None = None
    o_count: int = 0
    #: Absent alike for no cover and an unseen one, so a hidden file is not given away.
    cover_asset_id: str | None = None
    cover_upload_id: str | None = None
    cover_at_ms: int | None = None
    cover_frame: CoverFrame | None = None
    art: str | None = None
    shared: bool = False
    restricted: bool = False
    locked: bool = False
    record: dict[str, Any] | None = None
    hidden: bool = False
    favorite: bool = False
    rating: int | None = None
    pinned: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    keep_local: bool = False
    keep_from_swaps: bool = False
    parent_id: str | None = None
    parent_name: str | None = None


class TagList(Wire):
    """One page of the Tags wall, and how many there are for whoever asked."""

    items: list[TagView]
    total: int
    limit: int
    offset: int


class VaultWrite(Wire):
    """Put a tag in the vault, or take it back out."""

    vault: bool


class TagWrite(Wire):
    """Creating a tag, or renaming one."""

    name: str = Field(min_length=1, max_length=MAX_TAG_NAME)
    #: Absent leaves a field alone, so a form that knows nothing of the description cannot destroy
    #: it.
    description: str | None = Field(default=None, max_length=MAX_TAG_DESCRIPTION)
    category: str | None = Field(default=None, max_length=MAX_TAG_NAME)
    aliases: list[str] | None = Field(default=None, max_length=MAX_TAG_ALIASES)
    parent: str | None = Field(default=None, max_length=MAX_TAG_NAME)

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str) -> str:
        """Trim the name and refuse one that could never be searched for."""
        return clean_name(value, what="a tag's name")


class TagAssignment(Wire):
    """Which assets, which tags, and whether they are going on or coming off."""

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)
    tag_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_TAGS)
    add: bool = True


class RatingWrite(Wire):
    """Whole stars, or null to clear; zero is refused so it never sorts as a real rating."""

    rating: int | None = Field(default=None, ge=MIN_RATING, le=MAX_RATING)


#: An act, not a new value: a value written back from two tabs would lose a press.
O_UP = "up"
O_DOWN = "down"
O_RESET = "reset"


class OCountWrite(Wire):
    """One press of the O mark, one press taken back, or the tally cleared."""

    change: Literal["up", "down", "reset"]


class FavoriteWrite(Wire):
    """The heart, independent of the rating."""

    favorite: bool


class FavoriteMany(Wire):
    """The heart over a whole selection, one target state for all of them."""

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)
    favorite: bool


class RatingMany(Wire):
    """One rating across a selection, or null to clear it across all of them."""

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)
    rating: int | None = Field(default=None, ge=MIN_RATING, le=MAX_RATING)


class PinMany(Wire):
    """The pin over a whole selection, one target state for all of them."""

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)
    pinned: bool


class TagStateView(Wire):
    """One person's opinion of one tag, handed back so an optimistic control can settle."""

    favorite: bool = False
    rating: int | None = None
