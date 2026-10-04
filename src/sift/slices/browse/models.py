# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the grid sends back.

A payload carries ids and facts about the media, never a path. Where a file sits on disk is the
one thing a client has no use for and an attacker has every use for: it names directory
structure, usernames, and mount points, none of which the browser needs to draw a tile.
"""

from __future__ import annotations

from pydantic import Field

from sift.kernel.access import AssetView, GrantMark
from sift.kernel.jobs.failure_words import why_left_out
from sift.kernel.wire import HistoryEvent, UndoPoint, Wire


class AssetSummary(Wire):
    """One tile.

    `width` and `height` lay out the justified grid and are null until the file is probed: a tile
    is shown from the moment the file is accepted, as a skeleton. A concealed asset arrives with
    `concealed` set and nothing else, the only shape a hidden file is ever described in.
    """

    id: str
    media_type: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    favorite: bool = False
    #: Pinned to the top of this wall by this user, drawn as a mark like the heart.
    pinned: bool = False
    rating: int | None = None
    #: How many times this user has opened it; zero is what the Unwatched filter narrows by.
    views: int = 0
    #: This user's O tally, riding on the read the wall already makes for the heart and stars,
    #: never a request per tile. Zero for a file with no row: a counter has no other "never".
    o_count: int = 0
    #: Where this user stopped in this video, in milliseconds (the tile has the length), drawn as a
    #: bar along its bottom; None for nowhere worth returning to; the kernel's `resume_point` decides.
    resume_ms: int | None = None
    concealed: bool = False
    #: Whether the still is built yet; the grid draws a shimmer until it is. False on a placeholder.
    thumb: bool = False
    #: Whether the hover clip is built yet, so a wall never asks for a clip and reads the 404 as the
    #: answer, which floods the server during an import. False on a placeholder.
    preview: bool = False
    #: Why there is no still, when a feature gave up on this file, said on hover in plain words
    #: chosen by the verdict's code (`failure_words.why_left_out`), never the decoder's text.
    verdict: str | None = None
    #: Why this file was left out, on a wall filtered to what a product gave up on
    #: (`left_out:thumbnails`), drawn under the tile. None on every other wall.
    left_out: str | None = None
    #: Whether NO copy of this file is where Sift last saw it (a drive unmounted, a folder moved):
    #: its thumbnail lives beside the library, so without this it looks present. False on a
    #: placeholder.
    unreachable: bool = False
    #: The suffix on this file's picture addresses naming what the pictures are and how often this
    #: user's visibility changed, so the browser keeps them a week. Never on a placeholder, where it
    #: would move with a concealed file's rebuilt thumbnail.
    art: str | None = None
    #: Whether this file is shared with or restricted from anybody, never with whom. Filled for an
    #: admin only: noise to a guest on their own shares, a leak on anything else.
    shared: bool = False
    restricted: bool = False
    #: Whether that decision was made on THIS file or comes from above it: something to undo here,
    #: or a consequence. Never on a placeholder.
    shared_here: bool = False
    restricted_here: bool = False
    #: In the vault and shown anyway (unlocked, or the Hidden screen). Never on a placeholder, whose
    #: whole point is withholding that.
    hidden: bool = False
    #: Concealed by a flag on THIS file rather than something it is in: drawn solid where the switch
    #: is here, hollow where it is above.
    hidden_here: bool = False
    #: The file's name (never a path), so a panel acting on a tile can say what it acts on.
    original_filename: str | None = None
    #: Whether no swap will send this file (Kept local or "Don't swap", here or above): swap mode's
    #: mark. Admin only, never on a placeholder.
    swap_refused: bool = False


class SpriteSheet(Wire):
    """How the scrub strip's frames are laid out on the one image that holds them.

    Every scrub frame is a tile on one image, and the layout cannot be read off the image (tile
    height follows the video's shape), so it is recorded when built and reported, never assumed.
    """

    # Read back off a stored row, so nothing guarantees them: nought columns is a division by zero
    # and a wrong row count draws the wrong frame. Refusing turns a nonsense layout into "no
    # layout", which the player handles (see the router).
    columns: int = Field(ge=1)
    rows: int = Field(ge=1)
    #: The width of one frame, in pixels. The height is whatever the video's shape made it.
    tile_width: int = Field(ge=1)
    #: How many cells hold a frame: the last row is usually short, and a scrubber picking an empty
    #: cell shows a blank square. Worked out from the file's length, since the layout is part of a
    #: sheet's address; bounded by the grid, so an older sheet is cut coarsely, never past its end.
    frames: int = Field(ge=1)


class RecordWrite(Wire):
    """The editable half of a file's record: its title, where it came from, and when it came out.

    Null and an empty string both mean "nothing here" (a cleared form sends one, an API client the
    other). **A field left out is left alone, not blanked:** the route asks which fields were sent,
    or a caller sending only a title would erase the address.
    """

    title: str | None = None
    download_url: str | None = None
    release_date: str | None = None
    details: str | None = None
    production_date: str | None = None
    site_code: str | None = None
    music: str | None = None
    #: Where the release can be found, as the whole list: the form saves the set in one press, and a
    #: partial write of a list could only grow it.
    links: list[str] | None = None


class EnrichedBy(Wire):
    """One thing that wrote to a file without a person doing it, said as who did it.

    `via` is the `enriched:` filter's own word (`stash`, `faces`, `folder`, `filename`,
    `watermark`), so the mark cannot say what the filter would not find. `name` is which stash-box,
    null where Sift did it or the box is unknown. One entry per box, so never key on `via` alone.
    `box` is the box's slug the mark is painted in; the name is what it says.
    """

    via: str
    name: str | None = None
    box: str | None = None


class MusicFrom(Wire):
    """The file a shared song's name came from: enough to name it and link it."""

    id: str
    name: str


class AssetDetail(AssetSummary):
    """The detail view. Everything the tile has, plus what a person might want to read.

    No stored MIME type: the screen shows the container and the player chooses by codecs, and it is
    ffprobe's guess rather than a fact.
    """

    #: A name for the file that is not its filename: the record field somebody types.
    title: str | None = None
    #: Where this file was fetched from, cleaned; blank when scanned. Shown to anybody who can see
    #: the file.
    download_url: str | None = None
    #: When the content was published, as an ISO date: not `added_at`, since an old clip can be
    #: fetched last night.
    release_date: str | None = None
    #: What the release is about, in the words of whoever put it out.
    details: str | None = None
    #: When it was filmed, often well before `release_date`, so neither stands in for the other.
    production_date: str | None = None
    #: The Site's own reference for it, what a stash-box is searched by when title and date are
    #: ambiguous.
    site_code: str | None = None
    #: The track it is set to, from the site it was downloaded from or typed by hand.
    music: str | None = None
    #: Where the track's name came from: `typed`, `site`, `acoustid` or `shared`; None with no
    #: track. Read off the ledger (`kernel.content.identity.music_provenance`).
    music_source: str | None = None
    #: The file a `shared` name came from, where this viewer may see it; None otherwise.
    music_from: MusicFrom | None = None
    #: What takes a `shared` name back, the receipt's door handed over; admin only, while the name
    #: is the shared one.
    music_undo: UndoPoint | None = None
    #: The song this file carries, whose page the Music field opens (`/songs/<id>`): whoever may see
    #: the file may open it.
    song_id: str | None = None
    #: Where the release can be found, as a stash-box says. Not `download_url`, which is where this
    #: copy came from.
    links: list[str] = Field(default=[])
    #: What wrote to this file, in the filter's own words through the same predicates the Browse
    #: column counts by, so the marks agree. Empty for a file nothing has touched.
    enriched_by: list[EnrichedBy] = Field(default=[])
    #: Lines on this file's History pane, so the tab wears its number as the dialog opens, like the
    #: other tabs. The kernel makes it the history route's own `total`, never a separate count (see
    #: `count_of_asset_history`); zero for a viewer who cannot read it.
    history_count: int = 0
    #: Fields a linked stash-box disagrees with, for the History tab's mark (the panel settling them
    #: is inside it). None where there is no question to ask (`DisagreementSeam`).
    disagreements: int | None = None
    #: The boxes those fields are about, by name, so the mark says which box it means.
    disagreement_boxes: list[str] = Field(default=[])
    #: Whether NO copy of this file is where Sift last saw it. See `AssetSummary`.
    unreachable: bool = False
    #: A picture only some browsers draw (a phone's HEIC): drawn from `/rendition` from the start.
    browser_may_not_draw: bool = False
    size_bytes: int | None = None
    container: str | None = None
    #: The picture and sound codecs and frame rate, read at import: what decides whether a browser
    #: plays it unaided, the question the stats panel answers.
    vcodec: str | None = None
    acodec: str | None = None
    fps: float | None = None
    #: Bits per colour sample (eight usually, ten for HDR). Zero records an unreadable file so the
    #: catch-up does not return for ever; it is shown as unknown.
    bit_depth: int | None = None
    original_filename: str | None = None
    #: The file's name on disk now, if Sift can see a copy; differs from `original_filename` after a
    #: rename. See the router.
    filename: str | None = None
    #: Where the file is, as this viewer may be told (`kernel.where`): the full path for an admin,
    #: otherwise the library folder's name and visible folders. None when no copy is readable.
    where: str | None = None
    added_at: int
    #: This user's O tally, as on the tile. Views are `views` above, named once.
    o_count: int = 0
    last_viewed_at: int | None = None
    #: The scrub strip's layout; absent means no strip to fetch, which the player reads as "no scrub
    #: preview".
    sprite: SpriteSheet | None = None
    #: Why this file has no fingerprints, the standing verdict's words, when the decoder refused its
    #: frames: the permanent answer, never the waiting one. Its own field because `verdict` is the
    #: PICTURE verdict.
    fingerprint_verdict: str | None = None
    #: What is done about a file storing its audio too far from its video to seek: `pending` (plays,
    #: seeks badly until the copy is built) or `repaired` (plays from the copy). Absent otherwise;
    #: explained, never warned about.
    playback_repair: str | None = None


class FilterProblem(Wire):
    """A filter on this page whose value nothing could act on, and why."""

    field: str
    value: str
    reason: str


class NarrowedToUsername(Wire):
    """The username a page was narrowed to (`?username=`), in the words its filter chip draws.

    A username has no page: it opens the Files wall narrowed to an opaque id, so the page answers
    with what the chip says rather than a by-id route existing only for that.
    """

    id: str
    username: str
    site: str | None = Field(default=None, description="The Site the username is on, or null.")
    person_id: str | None = Field(
        default=None, description="Who the username is joined to, or null where nobody is said."
    )


class AssetPageResponse(Wire):
    """A page, and how many rows there are in all.

    The total comes from the statement the rows did, so a pager cannot disagree with its page.
    """

    items: list[AssetSummary]
    total: int
    #: Bytes of the files `total` counts, for this viewer, off the same read. None where unsaid.
    total_bytes: int | None = None
    limit: int
    offset: int
    #: Whether this is the whole answer: False when asking by meaning stopped before filling the
    #: page, so a short page is said to be one rather than reading as an empty library.
    complete: bool = True
    #: Filters whose value could not be read; each matches nothing, so the caption can say why.
    problems: list[FilterProblem] = Field(default=[])
    #: The username this page was narrowed to, when this viewer may be shown it; null otherwise,
    #: agreeing with the page, which is empty for a hidden one.
    username: NarrowedToUsername | None = None


#: The most files one membership question may name: the browse slice's own promise for its route.
MAX_BULK_ASSETS = 500


class MembershipAsk(Wire):
    """Which files a picker is about to draw ticks for.

    A POST, though a read: five hundred ids in an address passes what proxies carry (a 414 at the
    edge). The body has the shape every bulk write uses, split the same way by the client.
    """

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)


class Membership(Wire):
    """Which of one kind of thing the files asked about are already on.

    `all` is a full tick, `some` a half one; anything in neither is on none of them.
    """

    #: Every one of the files carries this. Ticked.
    all: list[str] = Field(default=[])
    #: Some do and some do not. Half ticked.
    some: list[str] = Field(default=[])


class Memberships(Wire):
    """What a set of files is already on, per kind, as the pickers draw it.

    One answer for every kind plus the heart, so the pickers over one selection cannot disagree.
    The heart is a word: there is only one of it.
    """

    people: Membership = Field(default_factory=Membership)
    sites: Membership = Field(default_factory=Membership)
    collections: Membership = Field(default_factory=Membership)
    photo_sets: Membership = Field(default_factory=Membership)
    tags: Membership = Field(default_factory=Membership)
    #: The song the files carry. A file carries one, so `all` holds at most one song.
    songs: Membership = Field(default_factory=Membership)
    #: "all", "some" or "none": of the files asked about, how many this user has hearted.
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
    #: Every line the pane could draw, so the tab says the whole number and Show earlier says how
    #: many more there are, before anybody presses it.
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
    """A tile from what the access layer returned.

    `view.concealed` says the asset is in vaulted territory; `revealed` says whether THIS viewer may
    see it (a locked placeholder versus the Hidden screen). Withheld, it is described by its
    concealment and nothing else.
    """
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
