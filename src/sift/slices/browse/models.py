# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the grid sends back: ids and facts about the media, never a path."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.access import AssetView, GrantMark
from sift.kernel.jobs.failure_words import why_left_out
from sift.kernel.wire import HistoryEvent, UndoPoint, Wire


class AssetSummary(Wire):
    """One tile; a concealed asset arrives with `concealed` set and nothing else."""

    id: str
    media_type: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    favorite: bool = False
    pinned: bool = False
    rating: int | None = None
    #: Zero is what the Unwatched filter narrows by.
    views: int = 0
    #: Zero for a file with no row: a counter has no other "never".
    o_count: int = 0
    #: Where this user stopped in this video, in milliseconds; None for nowhere worth returning to.
    resume_ms: int | None = None
    concealed: bool = False
    thumb: bool = False
    #: So a wall never asks for a clip and reads the 404 as the answer, flooding an import.
    preview: bool = False
    #: Plain words chosen by the verdict's code, never the decoder's text.
    verdict: str | None = None
    left_out: str | None = None
    #: Its thumbnail lives beside the library, so without this a missing file looks present.
    unreachable: bool = False
    #: Never on a placeholder, where it would move with a concealed file's rebuilt thumbnail.
    art: str | None = None
    #: Admin only: noise to a guest on their own shares, a leak on anything else.
    shared: bool = False
    restricted: bool = False
    shared_here: bool = False
    restricted_here: bool = False
    #: Never on a placeholder, whose whole point is withholding that.
    hidden: bool = False
    hidden_here: bool = False
    original_filename: str | None = None
    swap_refused: bool = False


class SpriteSheet(Wire):
    """How the scrub strip's frames are laid out on the one image that holds them."""

    # Read off a stored row, so checked: a nonsense layout becomes "no layout", which the player
    # handles.
    columns: int = Field(ge=1)
    rows: int = Field(ge=1)
    tile_width: int = Field(ge=1)
    #: How many cells hold a frame: the last row is usually short.
    frames: int = Field(ge=1)


class RecordWrite(Wire):
    """The editable half of a file's record; a field left out is left alone, not blanked."""

    title: str | None = None
    download_url: str | None = None
    release_date: str | None = None
    details: str | None = None
    production_date: str | None = None
    site_code: str | None = None
    music: str | None = None
    #: The whole list: a partial write of a list could only grow it.
    links: list[str] | None = None


class EnrichedBy(Wire):
    """One thing that wrote to a file without a person; one entry per box, so never key on `via`."""

    via: str
    name: str | None = None
    box: str | None = None


class MusicFrom(Wire):
    """The file a shared song's name came from: enough to name it and link it."""

    id: str
    name: str


class AssetDetail(AssetSummary):
    """The detail view: everything the tile has, plus what a person might want to read."""

    title: str | None = None
    download_url: str | None = None
    #: Not `added_at`, since an old clip can be fetched last night.
    release_date: str | None = None
    details: str | None = None
    production_date: str | None = None
    site_code: str | None = None
    music: str | None = None
    music_source: str | None = None
    music_from: MusicFrom | None = None
    music_undo: UndoPoint | None = None
    song_id: str | None = None
    #: Not `download_url`, which is where this copy came from.
    links: list[str] = Field(default=[])
    #: Through the same predicates the Browse column counts by, so the marks agree.
    enriched_by: list[EnrichedBy] = Field(default=[])
    disagreements: int | None = None
    disagreement_boxes: list[str] = Field(default=[])
    unreachable: bool = False
    browser_may_not_draw: bool = False
    size_bytes: int | None = None
    container: str | None = None
    vcodec: str | None = None
    acodec: str | None = None
    fps: float | None = None
    #: Zero records an unreadable file so the catch-up does not return for ever.
    bit_depth: int | None = None
    original_filename: str | None = None
    filename: str | None = None
    where: str | None = None
    added_at: int
    o_count: int = 0
    last_viewed_at: int | None = None
    sprite: SpriteSheet | None = None
    fingerprint_verdict: str | None = None
    playback_repair: str | None = None


class FilterProblem(Wire):
    """A filter on this page whose value nothing could act on, and why."""

    field: str
    value: str
    reason: str


class NarrowedToUsername(Wire):
    """The username a page was narrowed to, in the words its filter chip draws."""

    id: str
    username: str
    site: str | None = Field(default=None, description="The Site the username is on, or null.")
    person_id: str | None = Field(
        default=None, description="Who the username is joined to, or null where nobody is said."
    )


class AssetPageResponse(Wire):
    """A page, and how many rows there are in all, from the statement the rows came from."""

    items: list[AssetSummary]
    total: int
    total_bytes: int | None = None
    limit: int
    offset: int
    #: False when meaning stopped before filling the page, so a short page is not read as empty.
    complete: bool = True
    problems: list[FilterProblem] = Field(default=[])
    username: NarrowedToUsername | None = None


MAX_BULK_ASSETS = 500


class MembershipAsk(Wire):
    """Which files a picker is about to draw ticks for; a POST, as the ids overflow an address."""

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)


class Membership(Wire):
    """Which of one kind of thing the files asked about are already on."""

    all: list[str] = Field(default=[])
    some: list[str] = Field(default=[])


class Memberships(Wire):
    """What a set of files is already on, per kind, as the pickers draw it."""

    people: Membership = Field(default_factory=Membership)
    sites: Membership = Field(default_factory=Membership)
    collections: Membership = Field(default_factory=Membership)
    photo_sets: Membership = Field(default_factory=Membership)
    tags: Membership = Field(default_factory=Membership)
    songs: Membership = Field(default_factory=Membership)
    favorite: str = "none"


class SaveRecord(Wire):
    """One line of the save log. Admin-only, and ids rather than names."""

    id: str
    user_id: str | None
    asset_id: str | None
    saved_at: int


class FileHistoryPage(Wire):
    """The newest lines of a file's History, oldest first, and how many lines it has in all."""

    items: list[HistoryEvent] = Field(default=[])
    total: int = 0


class SaveLogResponse(Wire):
    items: list[SaveRecord]
    total: int


def summary(
    view: AssetView,
    *,
    revealed: bool,
    favorite: bool = False,
    rating: int | None = None,
    views: int = 0,
    o_count: int = 0,
    resume_ms: int | None = None,
    mark: GrantMark | None = None,
    left_out: str | None = None,
    swap_refused: bool = False,
) -> AssetSummary:
    """A tile from what the access layer returned; a withheld one carries its concealment alone."""
    if view.concealed and not revealed:
        return AssetSummary(id=view.asset.id, media_type="", concealed=True)
    return AssetSummary(
        id=view.asset.id,
        media_type=view.asset.media_type,
        width=view.asset.width,
        height=view.asset.height,
        duration_ms=view.asset.duration_ms,
        favorite=favorite,
        rating=rating,
        views=views,
        o_count=o_count,
        resume_ms=resume_ms,
        concealed=False,
        thumb=view.has_thumb,
        preview=view.has_preview,
        verdict=(
            None
            if view.picture_verdict is None
            else why_left_out(view.picture_verdict_code or "", view.picture_verdict)
        ),
        unreachable=view.unreachable,
        art=view.art_version,
        hidden=view.concealed,
        hidden_here=view.concealed_here,
        pinned=view.pinned,
        left_out=left_out,
        original_filename=view.asset.original_filename,
        shared=mark.shared if mark else False,
        restricted=mark.restricted if mark else False,
        shared_here=mark.shared_here if mark else False,
        restricted_here=mark.restricted_here if mark else False,
        swap_refused=swap_refused,
    )
