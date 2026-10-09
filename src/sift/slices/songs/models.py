# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Music page and a song's page send and receive; shaped like the Photo Sets models."""

from __future__ import annotations

from pydantic import Field, field_validator

from sift.kernel.content.user_state import MAX_RATING, MIN_RATING
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.text import clean_name
from sift.kernel.wire import Wire

MAX_SONG_NAME = 200

MAX_BULK_ITEMS = 500

MAX_MERGED = 100

MAX_ARTISTS = 20


class ArtistCredit(Wire):
    """One artist a song credits: the artist's id, which a Music wall narrows by, and its name."""

    id: str
    name: str


class SongSummary(Wire):
    """One song as the person asking may know it; a cover they may not open comes back empty."""

    id: str
    name: str
    cover_asset_id: str | None = None
    cover_upload_id: str | None = None
    cover_at_ms: int | None = None
    cover_frame: CoverFrame | None = None
    art: str | None = None
    item_count: int = 0
    #: None where an answer does not say.
    size_bytes: int | None = None
    #: Filled only on the song's own page.
    o_count: int = 0
    #: AcoustID's recording id, or None for a name from a Site's page or typed.
    recording_id: str | None = None
    notes: str | None = None
    created_at: int = 0
    favorite: bool = False
    rating: int | None = None
    pinned: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    locked: bool = False
    #: Whether this viewer hid it.
    vault: bool = False
    artists: list[ArtistCredit] = Field(default_factory=list)


class SongList(Wire):
    """One page of the Music wall, and how many there are for whoever asked."""

    items: list[SongSummary]
    total: int
    limit: int
    offset: int


class SongWrite(Wire):
    """Making or renaming a song."""

    name: str = Field(min_length=1, max_length=MAX_SONG_NAME)

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str) -> str:
        return clean_name(value, what="a song's name")


class NotesWrite(Wire):
    """Free text about the song. Null clears it."""

    notes: str | None = Field(default=None, max_length=4000)


class FilesWrite(Wire):
    """Files to put on the song or take off it, by id."""

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ITEMS)


class CoverWrite(Wire):
    """The picture the song is drawn as, or null for none, as `PhotoSets.CoverWrite`."""

    asset_id: str | None = None
    at_ms: int | None = Field(default=None, ge=0)
    upload_id: str | None = None
    frame: CoverFrame | None = None


class FavoriteWrite(Wire):
    favorite: bool


class VaultWrite(Wire):
    """Hide the song from this user, or stop hiding it."""

    vault: bool


class ArtistsWrite(Wire):
    """The whole ordered artist list by name; a name finds its artist or creates one."""

    names: list[str] = Field(default_factory=list, max_length=MAX_ARTISTS)

    @field_validator("names")
    @classmethod
    def _tidy(cls, value: list[str]) -> list[str]:
        cleaned = [clean_name(one, what="an artist's name") for one in value]
        if any(len(one) > MAX_SONG_NAME for one in cleaned):
            raise ValueError(f"an artist's name is at most {MAX_SONG_NAME} characters")
        return cleaned


class ArtistWrite(Wire):
    """An artist renamed, on every song that credits it."""

    name: str = Field(min_length=1, max_length=MAX_SONG_NAME)

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str) -> str:
        return clean_name(value, what="an artist's name")


class RatingWrite(Wire):
    """Stars, or null to clear them. See `PhotoSets.RatingWrite`."""

    rating: int | None = Field(default=None, ge=MIN_RATING, le=MAX_RATING)


class SongStateView(Wire):
    """What this user thinks of a song, after a write to it."""

    favorite: bool
    rating: int | None = None


class MergeSongs(Wire):
    """Several songs folded into one; `into` may be among `songs`."""

    into: str = Field(min_length=1, max_length=100)
    songs: list[str] = Field(min_length=1, max_length=MAX_MERGED)


class SongsMerged(Wire):
    """What merging songs moves, counted before and after."""

    into_name: str
    from_names: list[str]
    files: int = 0
