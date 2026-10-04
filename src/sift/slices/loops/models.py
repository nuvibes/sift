# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Loops screens send and receive."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import Wire

MAX_LOOP_NAME = 120


class TagOnLoop(Wire):
    id: str
    name: str


class LoopSummary(Wire):
    """One loop, as the person asking may know it.

    It carries the source file's shape because a loop is drawn as a still from that file and the
    wall lays out before the picture arrives. `duration_ms` is the loop's own length, sent so no
    reader computes it a second way.
    """

    id: str
    asset_id: str
    name: str | None = None
    start_ms: int
    end_ms: int
    created_at: int = 0
    media_type: str = "video"
    width: int | None = None
    height: int | None = None
    #: How long the LOOP runs, not the video, under the name every tile draws its badge from: one
    #: quantity, one name, so a loop's badge never shows the video's running time.
    duration_ms: int | None = None
    #: The opinion of the FILE `asset_id` names: a loop saved by Save as Loop IS its file (`whole`),
    #: so these are the clip's own; a loop that is a stretch of a longer video shares the source
    #: video's heart, stars and pin with every loop cut from it.
    favorite: bool = False
    rating: int | None = None
    #: That file's pin, floated above every sort by `_VISIBLE_LOOPS`, so the wall needs no
    #: `pinned_first` parameter.
    pinned: bool = False
    #: How many times this user opened the file, named as `AssetSummary` names it because the shared
    #: tile reads one set of names.
    views: int = 0
    #: That file's O count, likewise; `AssetGrid.contract.test.ts` holds both row shapes to the
    #: tile's list.
    o_count: int = 0
    #: Always None, sent rather than omitted because the shared tile reads one set of names: a
    #: position in the source video would be measured against a different timeline from the loop's
    #: own length and draw a wrong bar.
    resume_ms: int | None = None
    #: Whether the loop may be drawn as a picture at all, or only as a locked placeholder.
    concealed: bool = False
    #: The rest of what the shared tile (`Tile.svelte`) reads, under `AssetSummary`'s names: the
    #: vault eye, whether hidden HERE, the torn page for a file gone, and the sharing glyph
    #: `markFor` builds from the four below. A missing field draws NO mark, silently.
    hidden: bool = False
    hidden_here: bool = False
    unreachable: bool = False
    #: Whether anything was said about who may reach the file, and whether HERE: an admin's
    #: question, answered nothing for a guest by `visible_marks`.
    shared: bool = False
    restricted: bool = False
    shared_here: bool = False
    restricted_here: bool = False
    #: Whether the video's still has been built yet. False means "on its way", which is a shimmer.
    thumb: bool = True
    #: Whether the VIDEO's hover clip is built: a loop previews its file, having no clip of its own.
    preview: bool = False
    #: Whether the loop's own still (a frame at its start) exists. False draws the video's picture,
    #: so a library mid-sweep looks normal. See the loops router for why the moment is never a
    #: caller's to name.
    still: bool = False
    #: The token every picture address for the video carries, so a browser may keep them for a week.
    art: str | None = None
    #: The name the video was imported under, which is what a saved copy is named after.
    original_filename: str | None = None
    #: Whether this row IS a clip rather than a stretch of something longer: only a stretch can be
    #: cut into a file of its own, and the screen lacks the file's duration to tell.
    whole: bool = False
    #: The loop's own tags, not the video's, on the summary so a wall is not a request per tile.
    tags: list[TagOnLoop] = Field(default=[])


class LoopList(Wire):
    """One page of marks, in the shape every wall of tiles reads.

    The asset listing's own shape, since the wall of loops IS the media grid at this address.
    `complete` is always true (nothing here is ranked by meaning), sent so the reader need not know.
    """

    items: list[LoopSummary]
    total: int
    limit: int
    offset: int
    complete: bool = True


class LoopWrite(Wire):
    """Marking a stretch of a file. The two ends in milliseconds, and an optional name.

    Whether the end sits inside the file needs its duration, so that check lives in the service.
    """

    asset_id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    name: str | None = Field(default=None, max_length=MAX_LOOP_NAME)


#: The most loops one forget may carry: every bulk write's five hundred, repeated since a slice may
#: not import another.
MAX_BULK_LOOPS = 500


class ForgetLoops(Wire):
    """Which marks to forget, as one request.

    A selection, so a wall of loops can forget many at once: the shared Delete verb is hidden there
    on purpose (it removes the FILE), and `DELETE /loops/{id}` is per row.
    """

    loop_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_LOOPS)


class LoopRename(Wire):
    name: str | None = Field(default=None, max_length=MAX_LOOP_NAME)


class LoopTagWrite(Wire):
    tag_id: str
    add: bool = True
