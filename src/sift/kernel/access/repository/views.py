# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes a scoped read hands back, and the conversions that build them from rows."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sift.kernel.access.viewer import ConcealerType, Effect, ObjectType, Role, Viewer
from sift.kernel.content import (
    Asset,
    DerivativeKind,
    asset_from_row,
)
from sift.kernel.cover_frame import CoverFrame, frame_of
from sift.kernel.db import Row
from sift.kernel.serving import art_carries, art_version


def _is_object_id(value: object) -> bool:
    """Whether a value can be bound as a single-object lookup: a NULL would match every row. Any
    non-empty string binds; the WHERE clause decides whether it exists."""
    return isinstance(value, str) and value != ""


class AccessError(ValueError):
    """A grant that could never apply to anything."""


@dataclass(frozen=True, slots=True)
class Grant:
    id: str
    object_type: ObjectType
    object_id: str | None
    subject_user_id: str
    effect: Effect
    created_at: int


@dataclass(frozen=True, slots=True)
class GrantMark:
    """Whether anything has been said about one object, without saying to whom. The `_here` pair
    says the decision was made on this object itself."""

    shared: bool
    restricted: bool
    shared_here: bool = False
    restricted_here: bool = False


@dataclass(frozen=True, slots=True)
class GrantSource:
    """One grant that reaches an object; `source_id` is None for the global grant."""

    subject_user_id: str
    username: str
    effect: Effect
    source_type: ObjectType
    source_id: str | None
    source_name: str | None


@dataclass(frozen=True, slots=True)
class Reaches:
    """One user, and whether they can see the thing asked about, read from the stored verdict."""

    user_id: str
    username: str
    role: Role
    disabled: bool
    sees: bool


@dataclass(frozen=True, slots=True)
class ReachReason:
    """One reason a page of files under an entity is reachable, and how many of the page's files it
    explains."""

    source_type: ObjectType
    source_id: str | None
    source_name: str | None
    files: int


@dataclass(frozen=True, slots=True)
class ReachThroughFiles:
    """The reasons, with how many files the page held and whether that was all of them."""

    reasons: tuple[ReachReason, ...]
    files: int
    complete: bool


@dataclass(frozen=True, slots=True)
class VaultSource:
    """One thing whose vault flag conceals the object asked about; `here` is the object itself."""

    source_type: ConcealerType
    source_id: str | None
    source_name: str | None
    here: bool


@dataclass(frozen=True, slots=True)
class Memberships:
    """How many of a set of files carry each thing: raw tallies, since only the caller knows the
    set's size."""

    people: Mapping[str, int]
    sites: Mapping[str, int]
    collections: Mapping[str, int]
    photo_sets: Mapping[str, int]
    tags: Mapping[str, int]
    songs: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class Actionable:
    """Which of a selection a viewer may write to. `refused` stays one pile, so not-yours and
    not-there cannot be told apart."""

    allowed: tuple[str, ...]
    concealed: tuple[str, ...]
    refused: tuple[str, ...]

    @property
    def skipped(self) -> int:
        """How many items the write must leave alone."""
        return len(self.concealed) + len(self.refused)


@dataclass(frozen=True, slots=True)
class PhotoSetView:
    """A photo set as one viewer may know it: its count and cover scoped to what they may see."""

    id: str
    name: str
    cover_asset_id: str | None
    cover_upload_id: str | None
    #: The moment of a video the cover is, withheld with the file.
    cover_at_ms: int | None
    vault: bool
    created_at: int
    item_count: int
    size_bytes: int = 0
    #: How the set came to exist: `manual`, `download` or `folder`.
    origin: str = "manual"
    origin_url: str | None = None
    folder_id: str | None = None
    notes: str | None = None
    favorite: bool = False
    rating: int | None = None
    pinned: bool = False
    #: The window of the cover drawn, withheld with the file (`cover_frame.frame_of`).
    cover_frame: CoverFrame | None = None
    #: A locked tile: everything this viewer may see under it is in the shut vault (`_LOCKED_TILE`
    #: in `repository/entities.py`).
    locked: bool = False


@dataclass(frozen=True, slots=True)
class SongView:
    """A song as one viewer may know it, scoped as `PhotoSetView` is."""

    id: str
    name: str
    cover_asset_id: str | None
    cover_upload_id: str | None
    cover_at_ms: int | None
    created_at: int
    item_count: int
    size_bytes: int = 0
    #: The AcoustID recording, or None where nothing said which. Withheld on a locked tile.
    recording_id: str | None = None
    notes: str | None = None
    favorite: bool = False
    rating: int | None = None
    pinned: bool = False
    cover_frame: CoverFrame | None = None
    locked: bool = False
    vault: bool = False
    #: The artists it credits, in order, as (id, name).
    artists: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class LoopTag:
    """One tag on one Loop. The same two fields a tag chip is drawn from anywhere else."""

    id: str
    name: str


#: How far apart two lengths may be and still be the same, in ms: the shortest Loop the service
#: accepts.
_SAME_LENGTH_MS = 250


@dataclass(frozen=True, slots=True)
class LoopView:
    """A Loop: a stretch of one video, as one viewer may know it. It reaches a viewer only through
    the visible set."""

    id: str
    asset_id: str
    name: str | None
    start_ms: int
    end_ms: int
    created_at: int
    #: None for a Loop whose user has gone.
    created_by: str | None
    media_type: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    #: The opinion of the file `asset_id` names: the Loop itself when it was cut by Save as Loop.
    favorite: bool = False
    rating: int | None = None
    pinned: bool = False
    views: int = 0
    o_count: int = 0
    #: Whether the file is in the vault; unlike `concealed`, still true with the vault open.
    hidden: bool = False
    #: Whether this user hid the file itself, which fills the eye.
    hidden_here: bool = False
    unreachable: bool = False
    #: The Loop's own tags, carried on the row so a tile draws without a request.
    own_tags: tuple[LoopTag, ...] = ()
    #: A frame of a concealed video is the video, so the vault takes the picture and leaves the row.
    concealed: bool = False
    has_thumb: bool = False
    #: Whether the video's hover clip is built: a Loop previews its file's clip.
    has_preview: bool = False
    #: Whether the Loop's own still is built; without it the video's picture is drawn.
    has_still: bool = False
    original_filename: str | None = None
    art_version: str | None = None

    @property
    def length_ms(self) -> int:
        """How long it runs, derived so it cannot disagree."""
        return self.end_ms - self.start_ms

    @property
    def whole(self) -> bool:
        """Whether the Loop covers the whole of its file, within `_SAME_LENGTH_MS`. A file with no
        duration reads as not whole."""
        if not self.duration_ms or self.duration_ms <= 0:
            return False
        return (
            self.start_ms <= _SAME_LENGTH_MS and self.end_ms >= self.duration_ms - _SAME_LENGTH_MS
        )


@dataclass(frozen=True, slots=True)
class PhotoSetPage:
    """One page of photo sets, and how many there are for whoever asked."""

    items: list[PhotoSetView]
    total: int


@dataclass(frozen=True, slots=True)
class SongPage:
    """One page of songs, and how many there are for whoever asked."""

    items: list[SongView]
    total: int


@dataclass(frozen=True, slots=True)
class LoopPage:
    """One page of loops, and how many there are for whoever asked."""

    items: list[LoopView]
    total: int


@dataclass(frozen=True, slots=True)
class FacetCount:
    """One value a dimension takes, how many rows on screen carry it, and the name where the value
    is an id."""

    value: str
    count: int
    label: str | None = None


@dataclass(frozen=True, slots=True)
class Enrichment:
    """One thing that wrote to a file without a person doing it: the `enriched:` filter's `via`, and
    for a stash-box its `name` and `box` slug, one per box."""

    via: str
    name: str | None = None
    box: str | None = None


@dataclass(frozen=True, slots=True)
class Folder:
    """A folder as somebody may see it; `folder_file_count` counts its files."""

    id: str
    root_id: str
    parent_id: str | None
    rel_path: str
    name: str
    vault: bool
    concealed: bool


@dataclass(frozen=True, slots=True)
class FolderContents:
    """What is under a folder, counted in one pass; bytes over copies."""

    files: int
    bytes: int
    folders: int
    #: When the latest file under it arrived, or None.
    newest_at: int | None


@dataclass(frozen=True, slots=True)
class AssetView:
    """An asset, and whether it is shown as a locked placeholder."""

    asset: Asset
    concealed: bool
    #: Whether the thumbnail is built, so the grid does not ask for a missing still.
    has_thumb: bool = False
    #: Whether the hover clip is built, for the same reason.
    has_preview: bool = False
    #: Why there is no still when the picture pass has given up, in its words.
    picture_verdict: str | None = None
    picture_verdict_code: str | None = None
    #: Concealed by a flag on this file rather than something above it: the mark is drawn solid.
    concealed_here: bool = False
    #: Whether no copy of this file is where Sift last saw it.
    unreachable: bool = False
    #: Pinned, read off the statement that orders by it.
    pinned: bool = False

    #: The token on every picture address for this asset, so a browser may keep them.
    art_version: str | None = None


@dataclass(frozen=True, slots=True)
class ServedDerivative:
    """A generated picture: the token its address carries, and whether it is in this user's
    vault."""

    path: Path
    version: str | None
    concealed: bool


@dataclass(frozen=True, slots=True)
class AssetPage:
    """A page, and the total out of the same statement."""

    items: list[AssetView]
    total: int
    #: The size of the files the total counts, for this viewer.
    total_bytes: int = 0


@dataclass(frozen=True, slots=True)
class PeoplePage:
    """One page of People and the scoped total."""

    items: list[PersonSuggestion]
    total: int


@dataclass(frozen=True, slots=True)
class TagPage:
    """One page of Tags and the scoped total."""

    items: list[TagSuggestion]
    total: int


@dataclass(frozen=True, slots=True)
class CollectionPage:
    """One page of Collections, and the scoped total."""

    items: list[CollectionView]
    total: int


@dataclass(frozen=True, slots=True)
class SitePage:
    """One page of Sites, and the scoped total."""

    items: list[SiteSuggestion]
    total: int


@dataclass(frozen=True, slots=True)
class PersonSuggestion:
    """A person, and how many files the viewer who asked can see of them."""

    id: str
    name: str
    vault: bool
    asset_count: int
    size_bytes: int = 0
    #: The still they are shown as, or None where none was chosen or it is concealed.
    cover_asset_id: str | None = None
    #: An uploaded cover, never set with `cover_asset_id`.
    cover_upload_id: str | None = None
    cover_at_ms: int | None = None
    cover_frame: CoverFrame | None = None
    #: A face out of that still, withheld with the file.
    cover_track_id: str | None = None
    favorite: bool = False
    rating: int | None = None
    pinned: bool = False
    #: Whether they make the edits, a property of the row.
    pmv_creator: bool = False
    #: Whether this row may never be sent outside the machine, carried so the card can draw a mark.
    keep_local: bool = False
    #: Marked "Don't swap", carried for the same reason.
    keep_from_swaps: bool = False
    #: The alias or username the search term matched, when it was not the name.
    matched_as: str | None = None
    #: A locked tile, as `PhotoSetView.locked`.
    locked: bool = False


@dataclass(frozen=True, slots=True)
class UsernameSuggestion:
    """One username on one site, and what the viewer can see under it. `person_id` is who it belongs
    to, None until somebody says."""

    id: str
    name: str
    asset_count: int
    size_bytes: int = 0
    display_name: str | None = None
    url: str | None = None
    site_id: str | None = None
    site_name: str | None = None
    person_id: str | None = None
    person_name: str | None = None
    #: How many people already answer to this spelling; zero unless the caller asked.
    name_candidates: int = 0


@dataclass(frozen=True, slots=True)
class UsernamePage:
    """One page of Usernames and the scoped total."""

    items: list[UsernameSuggestion]
    total: int


@dataclass(frozen=True, slots=True)
class SiteSuggestion:
    """One site, and what the viewer who asked can see from it."""

    id: str
    name: str
    asset_count: int
    size_bytes: int = 0
    #: The site's own address, the first of its links (`sites.SITE_ADDRESS`); the write path refuses
    #: `javascript:`.
    site_url: str | None = None
    notes: str | None = None
    cover_asset_id: str | None = None
    #: An uploaded cover, never set with `cover_asset_id`.
    cover_upload_id: str | None = None
    cover_at_ms: int | None = None
    cover_frame: CoverFrame | None = None
    favorite: bool = False
    rating: int | None = None
    pinned: bool = False
    vault: bool = False
    matched_as: str | None = None
    keep_local: bool = False
    keep_from_swaps: bool = False
    #: A locked tile, as `PhotoSetView.locked`.
    locked: bool = False


@dataclass(frozen=True, slots=True)
class AliasMatch:
    """Everyone a typed term names: two people can share a name."""

    term: str
    person_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TagSuggestion:
    """A tag, and how many files the viewer who asked can see under it."""

    id: str
    name: str
    asset_count: int
    size_bytes: int = 0
    #: None both for no cover and for a cover this viewer cannot see, so the two look alike.
    cover_asset_id: str | None = None
    #: An uploaded cover, never set with `cover_asset_id`.
    cover_upload_id: str | None = None
    cover_at_ms: int | None = None
    cover_frame: CoverFrame | None = None
    vault: bool = False
    matched_as: str | None = None
    favorite: bool = False
    rating: int | None = None
    pinned: bool = False
    keep_local: bool = False
    keep_from_swaps: bool = False
    #: A locked tile, as `PhotoSetView.locked`.
    locked: bool = False
    #: The tag this one is filed under, and its name, withheld for a hidden parent.
    parent_id: str | None = None
    parent_name: str | None = None


@dataclass(frozen=True, slots=True)
class CollectionView:
    """A collection as one viewer may know it: count and cover scoped, so neither leaks what is
    concealed."""

    id: str
    name: str
    cover_asset_id: str | None
    cover_upload_id: str | None
    cover_at_ms: int | None
    vault: bool
    owner_id: str | None
    created_at: int
    item_count: int
    size_bytes: int = 0
    favorite: bool = False
    rating: int | None = None
    pinned: bool = False
    cover_frame: CoverFrame | None = None
    #: A locked tile, as `PhotoSetView.locked`.
    locked: bool = False


def _frame(row: Row) -> CoverFrame | None:
    """The window of this row's cover, bound to the pointers as read (`cover_frame.frame_of`)."""
    return frame_of(
        row["cover_frame"],
        asset_id=row["cover_asset_id"],
        at_ms=row["cover_at_ms"],
        upload_id=row["cover_upload_id"],
    )


def _suggested(row: Row) -> PersonSuggestion:
    """One row of the people query as a person, for the listing and the lookup alike."""
    return PersonSuggestion(
        id=row["id"],
        name=row["name"],
        vault=bool(row["vault"]),
        asset_count=int(row["asset_count"]),
        size_bytes=int(row["size_bytes"]),
        cover_asset_id=row["cover_asset_id"],
        cover_upload_id=row["cover_upload_id"],
        cover_at_ms=row["cover_at_ms"],
        cover_frame=_frame(row),
        cover_track_id=row["cover_track_id"],
        favorite=bool(row["favorite"]),
        pinned=bool(row["pinned"]),
        pmv_creator=bool(row["pmv_creator"]),
        rating=None if row["rating"] is None else int(row["rating"]),
        # The alias first: it names the person, a username names their place on a site.
        keep_local=bool(row["keep_local"]),
        keep_from_swaps=bool(row["keep_from_swaps"]),
        matched_as=row["matched_alias"] or row["matched_username"] or None,
        locked=bool(row["locked"]),
    )


def _username_from_row(row: Row) -> UsernameSuggestion:
    """One row of the usernames query as a username, for the listing and the lookup alike."""
    return UsernameSuggestion(
        id=str(row["id"]),
        name=str(row["name"]),
        asset_count=int(row["asset_count"]),
        size_bytes=int(row["size_bytes"]),
        display_name=row["display_name"],
        url=row["url"],
        site_id=row["site_id"],
        site_name=row["site_name"],
        person_id=row["person_id"],
        person_name=row["person_name"],
        name_candidates=int(row["name_candidates"]),
    )


def _like_prefix(prefix: str) -> str:
    """A LIKE pattern matching this prefix literally."""
    escaped = _like_literal(prefix)
    return f"{escaped}%"


def _like_literal(term: str) -> str:
    """The wildcards taken out of a term, so LIKE reads it as the text somebody typed."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_anywhere(term: str) -> str:
    """A LIKE pattern matching this term anywhere: a wall filter, over a few hundred rows."""
    return f"%{_like_literal(term)}%"


def _optional_text(row: Row, name: str) -> str | None:
    """One nullable string off a row that may not carry the column at all."""
    try:
        value = row[name]
    except (IndexError, KeyError):
        return None
    return None if value is None else str(value)


def _asset_view(row: Row, viewer: Viewer) -> AssetView:
    """One row of the scoped read as an asset view, with its picture token folded once."""
    return AssetView(
        asset_from_row(row),
        bool(row["concealed"]),
        has_thumb=bool(row["has_thumb"]),
        has_preview=art_carries(row["art_marks"], DerivativeKind.PREVIEW.value),
        picture_verdict=_optional_text(row, "picture_verdict"),
        picture_verdict_code=_optional_text(row, "picture_verdict_code"),
        unreachable=bool(row["unreachable"]),
        art_version=art_version(row["art_marks"], viewer.cache_stamp),
        concealed_here=bool(row["concealed_here"]),
        pinned=bool(row["pinned"]),
    )


def _grant_from_row(row: Row) -> Grant:
    return Grant(
        id=row["id"],
        object_type=ObjectType(str(row["object_type"])),
        object_id=row["object_id"],
        subject_user_id=row["subject_user_id"],
        effect=Effect(row["effect"]),
        created_at=row["created_at"],
    )


def _photo_set_from_row(row: Row) -> PhotoSetView:
    """One row of the photo-set query as a set."""
    return PhotoSetView(
        id=str(row["id"]),
        name=str(row["name"]),
        cover_asset_id=row["cover_asset_id"],
        cover_upload_id=row["cover_upload_id"],
        cover_at_ms=row["cover_at_ms"],
        cover_frame=_frame(row),
        vault=bool(row["vault"]),
        created_at=int(row["created_at"]),
        item_count=int(row["item_count"]),
        size_bytes=int(row["size_bytes"]),
        origin=str(row["origin"]),
        origin_url=row["origin_url"],
        folder_id=row["folder_id"],
        notes=row["notes"],
        favorite=bool(row["favorite"]),
        pinned=bool(row["pinned"]),
        rating=None if row["rating"] is None else int(row["rating"]),
        locked=bool(row["locked"]),
    )


def _song_from_row(row: Row) -> SongView:
    """One row of the songs query as a song. The listing and the by-id lookup both use it."""
    return SongView(
        id=str(row["id"]),
        name=str(row["name"]),
        cover_asset_id=row["cover_asset_id"],
        cover_upload_id=row["cover_upload_id"],
        cover_at_ms=row["cover_at_ms"],
        cover_frame=_frame(row),
        created_at=int(row["created_at"]),
        item_count=int(row["item_count"]),
        size_bytes=int(row["size_bytes"]),
        recording_id=row["recording_id"],
        notes=row["notes"],
        favorite=bool(row["favorite"]),
        pinned=bool(row["pinned"]),
        rating=None if row["rating"] is None else int(row["rating"]),
        locked=bool(row["locked"]),
        vault=bool(row["vault"]),
        artists=_credits(row["artists"]),
    )


def _credits(packed: str | None) -> tuple[tuple[str, str], ...]:
    """A song's artists as the songs statement packs them (a JSON array of `[id, name]`)."""
    if not packed:
        return ()
    return tuple((str(one[0]), str(one[1])) for one in json.loads(packed))


#: Separators inside the packed tags column: the two characters `clean_stored_text` refuses to
#: store.
_BETWEEN_TAGS = "\x1f"
_BETWEEN_FIELDS = "\x1e"


def _loop_tags(packed: str | None) -> tuple[LoopTag, ...]:
    """The Loop's own tags, unpacked; NULL means none."""
    if not packed:
        return ()
    found = []
    for entry in packed.split(_BETWEEN_TAGS):
        tag_id, _, name = entry.partition(_BETWEEN_FIELDS)
        found.append(LoopTag(id=tag_id, name=name))
    return tuple(found)


def _loop_from_row(row: Row, viewer: Viewer) -> LoopView:
    """One row of the loop query as a loop; the viewer is for the picture token only."""
    return LoopView(
        id=str(row["id"]),
        asset_id=str(row["asset_id"]),
        name=row["name"],
        start_ms=int(row["start_ms"]),
        end_ms=int(row["end_ms"]),
        created_at=int(row["created_at"]),
        created_by=row["created_by"],
        media_type=str(row["media_type"]),
        width=row["width"],
        height=row["height"],
        duration_ms=row["duration_ms"],
        favorite=bool(row["favorite"]),
        rating=None if row["rating"] is None else int(row["rating"]),
        pinned=bool(row["pinned"]),
        views=int(row["view_count"]),
        o_count=int(row["o_count"]),
        hidden=bool(row["vault_hidden"]),
        hidden_here=bool(row["concealed_here"]),
        unreachable=bool(row["unreachable"]),
        concealed=not bool(row["showable"]),
        has_thumb=bool(row["has_thumb"]),
        has_preview=art_carries(row["art_marks"], DerivativeKind.PREVIEW.value),
        has_still=bool(row["has_still"]),
        original_filename=row["original_filename"],
        own_tags=_loop_tags(row["own_tags"]),
        art_version=art_version(row["art_marks"], viewer.cache_stamp),
    )


def _collection_from_row(row: Row) -> CollectionView:
    return CollectionView(
        id=row["id"],
        name=row["name"],
        cover_asset_id=row["cover_asset_id"],
        cover_upload_id=row["cover_upload_id"],
        cover_at_ms=row["cover_at_ms"],
        cover_frame=_frame(row),
        vault=bool(row["vault"]),
        owner_id=row["owner_id"],
        created_at=int(row["created_at"]),
        item_count=int(row["item_count"]),
        size_bytes=int(row["size_bytes"]),
        favorite=bool(row["favorite"]),
        pinned=bool(row["pinned"]),
        rating=None if row["rating"] is None else int(row["rating"]),
        locked=bool(row["locked"]),
    )


def _tag_from_row(row: Row) -> TagSuggestion:
    return TagSuggestion(
        id=row["id"],
        name=row["name"],
        vault=bool(row["vault"]),
        asset_count=int(row["asset_count"]),
        size_bytes=int(row["size_bytes"]),
        cover_asset_id=row["cover_asset_id"],
        cover_upload_id=row["cover_upload_id"],
        cover_at_ms=row["cover_at_ms"],
        cover_frame=_frame(row),
        favorite=bool(row["favorite"]),
        pinned=bool(row["pinned"]),
        rating=None if row["rating"] is None else int(row["rating"]),
        keep_local=bool(row["keep_local"]),
        keep_from_swaps=bool(row["keep_from_swaps"]),
        matched_as=row["matched_alias"] or None,
        locked=bool(row["locked"]),
        parent_id=row["parent_id"] if row["parent_name"] is not None else None,
        parent_name=row["parent_name"],
    )


def _site_from_row(row: Row) -> SiteSuggestion:
    return SiteSuggestion(
        id=row["id"],
        name=row["name"],
        asset_count=int(row["asset_count"]),
        size_bytes=int(row["size_bytes"]),
        site_url=row["site_url"],
        notes=row["notes"],
        cover_asset_id=row["cover_asset_id"],
        cover_upload_id=row["cover_upload_id"],
        cover_at_ms=row["cover_at_ms"],
        cover_frame=_frame(row),
        favorite=bool(row["favorite"]),
        pinned=bool(row["pinned"]),
        rating=None if row["rating"] is None else int(row["rating"]),
        vault=bool(row["vault"]),
        keep_local=bool(row["keep_local"]),
        keep_from_swaps=bool(row["keep_from_swaps"]),
        matched_as=row["matched_alias"] or None,
        locked=bool(row["locked"]),
    )


def _folder_from_row(row: Row) -> Folder:
    return Folder(
        id=row["id"],
        root_id=row["root_id"],
        parent_id=row["parent_id"],
        rel_path=row["rel_path"],
        name=row["name"],
        vault=bool(row["vault"]),
        concealed=bool(row["concealed"]),
    )
