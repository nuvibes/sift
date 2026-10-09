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
    """One loop, as the person asking may know it."""

    id: str
    asset_id: str
    name: str | None = None
    start_ms: int
    end_ms: int
    created_at: int = 0
    media_type: str = "video"
    width: int | None = None
    height: int | None = None
    #: The loop's length, not the video's, under the name every tile draws its badge from.
    duration_ms: int | None = None
    #: The opinion of the file `asset_id` names, shared with every loop cut from the same video.
    favorite: bool = False
    rating: int | None = None
    pinned: bool = False
    views: int = 0
    o_count: int = 0
    #: Always None: a position in the source video would draw a wrong bar on the loop.
    resume_ms: int | None = None
    concealed: bool = False
    #: What the shared tile reads, under `AssetSummary`'s names: a missing field draws no mark.
    hidden: bool = False
    hidden_here: bool = False
    unreachable: bool = False
    shared: bool = False
    restricted: bool = False
    shared_here: bool = False
    restricted_here: bool = False
    thumb: bool = True
    preview: bool = False
    still: bool = False
    art: str | None = None
    original_filename: str | None = None
    whole: bool = False
    tags: list[TagOnLoop] = Field(default=[])


class LoopList(Wire):
    """One page of marks, in the shape every wall of tiles reads."""

    items: list[LoopSummary]
    total: int
    limit: int
    offset: int
    complete: bool = True


class LoopWrite(Wire):
    """Marking a stretch of a file: the two ends in milliseconds, and an optional name."""

    asset_id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    name: str | None = Field(default=None, max_length=MAX_LOOP_NAME)


MAX_BULK_LOOPS = 500


class ForgetLoops(Wire):
    """Which marks to forget, as one request."""

    loop_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_LOOPS)


class LoopRename(Wire):
    name: str | None = Field(default=None, max_length=MAX_LOOP_NAME)


class LoopTagWrite(Wire):
    tag_id: str
    add: bool = True
