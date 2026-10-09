# SPDX-License-Identifier: AGPL-3.0-or-later
"""Renaming and moving files on a read-write library folder, each recorded first so it can be
undone."""

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

# No preference skips the move confirmation: a question that can be turned off protects nothing.

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
