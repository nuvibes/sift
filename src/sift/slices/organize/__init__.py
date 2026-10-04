# SPDX-License-Identifier: AGPL-3.0-or-later
"""Renaming and moving files, on a library folder that was handed over read-write.

Sift indexes files where they already are. Organizing them is the exception: an admin can rename a
file, move it to another folder, and move a whole folder with everything under it, wherever the
filesystem lets Sift write. A folder given to Sift read-only refuses the write itself, and the
actions are not offered there.

The index follows the file rather than being repaired by a later scan, and identity is the content
rather than the path, so a file that moves is the same file, and its tags, its rating and the
people on it come with it without being copied anywhere.

Every one of these operations is written down before it is done, so it can be taken back. Nothing
outside this package renames or moves anything in a library, and a rule in the build refuses any
code that tries.
"""

from __future__ import annotations

from sift.slices.organize import schema
from sift.slices.organize.batch import (
    RENAME_BATCH,
    BatchRenamer,
    BatchRenameReceipts,
    register_handlers,
)
from sift.slices.organize.router import router
from sift.slices.organize.service import (
    MAX_FILENAME_LENGTH,
    ORGANIZER,
    MoveKind,
    NotAllowed,
    NotFound,
    Organizability,
    Organized,
    Organizer,
    OrganizeRefused,
    check_filename,
)

# No preference to skip the move confirmation: a move is asked at the moment, every time, in the
# sheet that chooses where the files go, the same way a delete is. A question that can be turned off
# is not a protection.

__all__ = [
    "MAX_FILENAME_LENGTH",
    "ORGANIZER",
    "RENAME_BATCH",
    "BatchRenameReceipts",
    "BatchRenamer",
    "MoveKind",
    "NotAllowed",
    "NotFound",
    "Organizability",
    "OrganizeRefused",
    "Organized",
    "Organizer",
    "check_filename",
    "register_handlers",
    "router",
    "schema",
]
