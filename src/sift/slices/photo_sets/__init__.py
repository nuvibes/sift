# SPDX-License-Identifier: AGPL-3.0-or-later
"""Photo sets: the pictures that arrived together, derived rather than chosen as a collection is."""

from __future__ import annotations

from sift.kernel.photo_sets import MIN_PICTURES
from sift.kernel.settings_registry import register_setting
from sift.slices.photo_sets.derive import (
    set_from_archive,
    set_from_folder,
    set_from_post,
    set_from_shoot,
    set_from_stash_library,
)
from sift.slices.photo_sets.jobs import DISSOLVE_UNDER_FLOOR, register_handlers
from sift.slices.photo_sets.models import MAX_PHOTO_SET_NAME
from sift.slices.photo_sets.router import router
from sift.slices.photo_sets.service import (
    SERVICE,
    PhotoSetService,
    UnknownItem,
)

#: Whether a folder of pictures alone becomes a photo set; read at each scan, not at boot.
FOLDER_SETS_KEY = "photo_sets.from_folders"

register_setting(
    key=FOLDER_SETS_KEY,
    scope="app",
    default=True,
    section="Importing",
    label="Create Photo Sets from folders",  # nosemgrep: sift-no-asset-sql-outside-kernel
    # Built from the floor itself, so the sentence cannot drift from the rule.
    help=(
        f"A folder with {MIN_PICTURES} or more photos and no videos becomes a Photo Set named "
        "after the folder."
    ),
)

#: Whether a ZIP of pictures becomes a photo set; its pictures are indexed either way.
ARCHIVE_SETS_KEY = "photo_sets.from_archives"

register_setting(
    key=ARCHIVE_SETS_KEY,
    scope="app",
    default=True,
    section="Importing",
    label="Create Photo Sets from ZIP files",
    help=(
        f"A ZIP file with {MIN_PICTURES} or more photos inside becomes a Photo Set named after the "
        "file."
    ),
)

__all__ = [
    "ARCHIVE_SETS_KEY",
    "DISSOLVE_UNDER_FLOOR",
    "FOLDER_SETS_KEY",
    "MAX_PHOTO_SET_NAME",
    "MIN_PICTURES",
    "SERVICE",
    "PhotoSetService",
    "UnknownItem",
    "register_handlers",
    "router",
    "set_from_archive",
    "set_from_folder",
    "set_from_post",
    "set_from_shoot",
    "set_from_stash_library",
]
