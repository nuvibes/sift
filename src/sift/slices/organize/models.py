# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the organize endpoints take and send back: names and ids, never a server path."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

# The rule's own constant, so the bound cannot drift from it.
from sift.kernel.filenames import MAX_FILENAME_LENGTH
from sift.kernel.wire import Wire
from sift.slices.organize.batch import MOST_FILES


class RenameRequest(Wire):
    name: str = Field(min_length=1, max_length=MAX_FILENAME_LENGTH)
    location_id: str | None = None


class MoveRequest(Wire):
    """Where to put a file, by opaque folder id."""

    folder_id: str
    location_id: str | None = None


#: The same ceiling every bulk write declares.
MAX_BULK_ASSETS = 500


class MoveManyRequest(Wire):
    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)
    folder_id: str


#: Bounds what a request may make the server fill a thousand times.
MAX_TEMPLATE_LENGTH = 400


class RenameBatchRequest(Wire):
    """A template to name files by, in shown order (which `{n}` counts), and what a clash does."""

    asset_ids: list[str] = Field(min_length=1, max_length=MOST_FILES)
    template: str = Field(min_length=1, max_length=MAX_TEMPLATE_LENGTH)
    on_clash: Literal["number", "skip"] = "number"


class RenameRow(Wire):
    """One file of a batch rename and its planned `state`, with `reason` when refused."""

    asset_id: str
    before: str
    after: str
    state: Literal["renamed", "numbered", "same", "taken", "twice", "refused"]
    reason: str | None = None


class RenamePreview(Wire):
    """A batch rename planned in full and written nowhere; `rows` is only the start of it."""

    rows: list[RenameRow]
    total: int
    renaming: int
    numbered: int
    same: int
    clashes: int
    refused: int
    as_task: bool
    words: dict[str, str]


class RenameBatchDone(Wire):
    """What a batch rename did, or its `job_id` when it runs as a task."""

    renamed: int
    skipped: int
    receipt_id: str | None = None
    job_id: str | None = None
    reason: str | None = None


class OrganizeDone(Wire):
    """What the file is called now, and the `move_id` its undo names."""

    asset_id: str
    location_id: str
    filename: str
    folder_id: str | None = None
    move_id: str | None = None


class OrganizeOptions(Wire):
    """Whether rename and move belong on screen for this asset, with `reason` when not."""

    can_organize: bool
    reason: str | None = None
    undo_move_id: str | None = None
