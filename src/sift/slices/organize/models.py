# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the organize endpoints take and send back.

A payload carries names and ids, never a path. The name is the one part of a file's address the
person actually chose and is looking at on screen; the rest of it describes the server's disk, which
the browser has no use for and an attacker has every use for.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

# The bound the shape is validated against is the same constant the rule itself uses, taken from
# where the rule lives. Restated as a number here it would be a second answer to the same question,
# and the two would be one edit apart from disagreeing.
from sift.kernel.filenames import MAX_FILENAME_LENGTH
from sift.kernel.wire import Wire
from sift.slices.organize.batch import MOST_FILES


class RenameRequest(Wire):
    """The new name for a file. A name, never a path: the service refuses anything else."""

    name: str = Field(min_length=1, max_length=MAX_FILENAME_LENGTH)
    location_id: str | None = None


class MoveRequest(Wire):
    """Where to put a file. The folder is named by its id, which is opaque."""

    folder_id: str
    location_id: str | None = None


#: The most files one request may move. The same ceiling every bulk write in Sift declares.
MAX_BULK_ASSETS = 500


class MoveManyRequest(Wire):
    """Where to put a selection. One folder for the whole of it."""

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)
    folder_id: str


#: The longest template a batch rename takes. Far past any real one; a bound on what a request
#: may make the server fill a thousand times.
MAX_TEMPLATE_LENGTH = 400


class RenameBatchRequest(Wire):
    """A template to name a set of files by, and what a name that clashes does.

    The files are named by id, in the order they are shown, which is the order `{n}` counts in:
    a selection on a wall, or every file a folder shows.
    """

    asset_ids: list[str] = Field(min_length=1, max_length=MOST_FILES)
    template: str = Field(min_length=1, max_length=MAX_TEMPLATE_LENGTH)
    on_clash: Literal["number", "skip"] = "number"


class RenameRow(Wire):
    """One file of a batch rename: its name now, the name it would get, and what the plan says.

    `state` is one of `renamed`, `numbered` (the name clashed, so it takes the next number),
    `same` (it keeps its name), `taken` (the name is another file's in that folder, and clashes are
    being left alone), `twice` (an earlier file of this batch takes that name) or `refused`, with
    `reason` saying why.
    """

    asset_id: str
    before: str
    after: str
    state: Literal["renamed", "numbered", "same", "taken", "twice", "refused"]
    reason: str | None = None


class RenamePreview(Wire):
    """A batch rename planned in full and written nowhere.

    `rows` is the start of the batch, in order; the counts are the whole of it. `as_task` says
    carrying it out runs as a task with progress rather than while the screen waits. `words` is
    what each naming word means for a file in the library, for the chips that put one in.
    """

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
    """What a batch rename did, or the task carrying it out.

    `receipt_id` is the one record the whole batch is undone by. `job_id` is set instead when the
    batch runs as a task, and the counts are then zero until it has run.
    """

    renamed: int
    skipped: int
    receipt_id: str | None = None
    job_id: str | None = None
    reason: str | None = None


class OrganizeDone(Wire):
    """What the file is called now, and what would take it back.

    `move_id` is what the undo affordance holds on to. It is returned with the operation rather
    than looked up afterwards, so the screen offering "undo" is naming the exact move it just made
    and not whatever the most recent one happens to be by the time somebody clicks.
    """

    asset_id: str
    location_id: str
    filename: str
    folder_id: str | None = None
    move_id: str | None = None


class OrganizeOptions(Wire):
    """Whether the rename and move actions belong on screen for this asset at all.

    The interface hides them rather than greying them out, so this is asked before the menu is
    drawn. `reason` is filled in only when the answer is no, and it is a sentence, because the one
    place it is worth showing is where somebody has asked why the folder cannot be changed.
    """

    can_organize: bool
    reason: str | None = None
    undo_move_id: str | None = None
