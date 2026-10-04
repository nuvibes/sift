# SPDX-License-Identifier: AGPL-3.0-or-later
"""The face endpoints: who is in a file, where somebody turns up, and the piles awaiting a name.

Three rules hold for every route, here because a route is the control and a screen a courtesy.
**A crop is a fragment of its file**, served by the resolver's answer for the file, never a second
copy of the rule. **A name is a second question**: the file is resolved, then each name separately,
since hiding somebody must not be undone by a caption. **Curation is admin-only, and so is looking
at it**: a control somebody cannot see but can call is not access control. Who is in a file one may
already see stays open, as tags do.
"""

from __future__ import annotations

from fastapi import APIRouter

from sift.slices.faces import (
    router_answers,
    router_faces,
    router_folder,
    router_lists,
    router_people,
    router_setup,
)
from sift.slices.faces.folder_import import MAX_FOLDER_BYTES, MAX_FOLDER_FILES
from sift.slices.faces.router_folder import _drop_the_chosen_folder, _safe_relative
from sift.slices.faces.router_setup import _pack_filename

#: The names callers import from here, wherever among the routes they are defined.
__all__ = [
    "MAX_FOLDER_BYTES",
    "MAX_FOLDER_FILES",
    "_drop_the_chosen_folder",
    "_pack_filename",
    "_safe_relative",
    "router",
]

#: Every faces route, in the order they are matched.
router = APIRouter()
for part in (
    router_lists,
    router_people,
    router_answers,
    router_faces,
    router_setup,
    router_folder,
):
    router.include_router(part.router)
