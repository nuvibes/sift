# SPDX-License-Identifier: AGPL-3.0-or-later
"""Photo sets: the pictures that arrived together and belong together.

One folder or archive of stills somebody pointed Sift at, or a shoot somebody agreed to. It is the
still-image counterpart of a video and it is the unit a person actually thinks in: nobody
remembers the 43rd file of a shoot, they remember the shoot.

## Why this is not the collections slice with a flag on it

A collection is a DECISION: "membership is manual, and only manual. Nothing here derives what is in
a collection from a saved search: every row is there because a person put it there." A photo set is
DERIVED (from a folder, from an archive) and is a fact about where the files came from. Folding
one into the other would have broken the invariant the collections slice is built on, and it would
have put an owner on a thing nobody owns and left a set's origin nowhere to live.

They read the same way on screen and are different underneath, which is the right way round.

## What this slice owns and does not

It owns the write path and the endpoints. `photo_sets`, `photo_set_items`, `photo_set_tags` and
`photo_set_user_state` live in the kernel for the reason `collections` does: the permission resolver
joins them, so they have to exist where the resolver can see them.

A photo set is a grant object: a grant can name one, `acl_grants` accepts the type, and the
resolver carries the arm, so sharing a set reaches what is inside it, and keeps reaching what
arrives in it later. Hiding one conceals its pictures as well as its row, because a set taken off
somebody's screen whose pictures stayed on it is not hidden.
"""

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

#: Whether a folder holding pictures and nothing else becomes a photo set on its own.
#:
#: On out of the box. A folder of pictures already IS a shoot as far as whoever made it is
#: concerned, and Sift knowing that costs one row: nothing is copied, nothing is moved, and
#: deleting the set leaves every picture exactly where it is.
#:
#: Instance-wide and admin-only: it changes what the library collects rather than one person's view
#: of it. Read at the end of each folder's scan, so turning it off stops the next scan rather than
#: the next restart.
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

#: Whether a ZIP of pictures becomes a photo set on its own.
#:
#: On out of the box, which is the right default: a gallery arrives as a
#: `.zip` more often than as a folder, and somebody who made an archive of exactly these pictures
#: has already said they belong together more plainly than a folder ever does.
#:
#: What it governs is only the GROUPING. The pictures inside an archive are indexed either way:
#: they are what makes the archive anything other than an unopenable tile, so turning this off
#: means the pictures are in the library and there is no set over them.
#:
#: Instance-wide and admin-only, like the folder switch it sits beside, and read at the end of each
#: archive rather than at boot: turning it off stops the next scan, not the next restart.
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
