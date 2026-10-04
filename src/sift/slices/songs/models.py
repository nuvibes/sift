# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Music page and a song's page send and receive.

The same shapes the Photo Sets screens use, for the same reasons (see `slices/photo_sets/models.py`):
ids and names and never a path, and `item_count` as the number of files carrying the song that the
viewer who asked may see, which is not the number of rows behind it.
"""

from __future__ import annotations

from pydantic import Field, field_validator

from sift.kernel.content.user_state import MAX_RATING, MIN_RATING
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.text import clean_name
from sift.kernel.wire import Wire

#: Long enough for an artist and a title, the shape most song names take, and short enough that
#: the wall stays a wall of cards.
MAX_SONG_NAME = 200

#: The most files one call may put on a song or take off it. See `photo_sets.models.MAX_BULK_ITEMS`.
MAX_BULK_ITEMS = 500

#: The most songs one merge may fold together. A selection somebody made, never the library.
MAX_MERGED = 100

#: The most artists one song may credit. A credit list, never a roster.
MAX_ARTISTS = 20


class ArtistCredit(Wire):
    """One artist a song credits: the artist's id, which a Music wall narrows by, and its name."""

    id: str
    name: str


class SongSummary(Wire):
    """One song, as the person asking may know it.

    The covers are scoped as a Photo Set's are (`PhotoSetSummary`): a cover the viewer may not open
    comes back empty, the same answer a song with no cover gets, and a song with no cover is drawn
    as the music glyph.
    """

    id: str
    name: str
    cover_asset_id: str | None = None
    #: An UPLOADED cover. See `PhotoSetSummary.cover_upload_id`.
    cover_upload_id: str | None = None
    #: Which moment of the cover's file, withheld with it. See `PhotoSetSummary.cover_at_ms`.
    cover_at_ms: int | None = None
    cover_frame: CoverFrame | None = None
    #: The user's own token for this row's pictures. See `PhotoSetSummary.art`.
    art: str | None = None
    #: How many files carrying this song the viewer may see.
    item_count: int = 0
    #: How big those same files are, in bytes. None where an answer does not say (a write's reply).
    size_bytes: int | None = None
    #: This viewer's own O tally over the files carrying it. Filled only on the song's own page.
    o_count: int = 0
    #: The AcoustID recording this song is, where AcoustID said which; None for a name read off a
    #: Site's page or typed. An identity, never edited, and the address of the recording on
    #: AcoustID is built from it by the screen that links there.
    recording_id: str | None = None
    notes: str | None = None
    created_at: int = 0
    favorite: bool = False
    rating: int | None = None
    pinned: bool = False
    #: What the card draws beside the name: how many people, tags and Sites the files carrying it
    #: reach, keyed by the tab of its page each number opens. See `PhotoSetSummary.counts`.
    counts: dict[str, int] = Field(default_factory=dict)
    #: A LOCKED TILE on a wall. See `PhotoSetSummary.locked`.
    locked: bool = False
    #: Whether THIS viewer hid it. See `PhotoSetSummary.vault`.
    vault: bool = False
    #: The artists it credits, in order. Empty on a locked tile and on a song crediting nobody.
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
    """The picture the song is drawn as. Null takes the cover off, and the music glyph is drawn.
    The same fields as `PhotoSets.CoverWrite`, for the same reasons."""

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
    """The artists a song credits, in order, by name: the whole list, so adding, removing,
    reordering and correcting one are all this one write. A name finds the artist of that name
    (case aside) or makes one; an empty list credits nobody."""

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
    """Several songs that are one piece of music, folded into one in a single act.

    `into` survives and `songs` go. The survivor may be named among them without it being an error:
    somebody selects four cards and then says which of the four to keep.
    """

    into: str = Field(min_length=1, max_length=100)
    songs: list[str] = Field(min_length=1, max_length=MAX_MERGED)


class SongsMerged(Wire):
    """What a merge of songs moved, or would move: the files that change songs, by the songs
    going and the one kept, counted before anything moves (`/songs/weigh-merge`) and after."""

    into_name: str
    from_names: list[str]
    files: int = 0
