# SPDX-License-Identifier: AGPL-3.0-or-later
"""The grid: what a person sees when they open Sift, and how they get a copy of it.

Reads only. Nothing here generates a thumbnail or a preview: those are built when a file is
imported, so that scrolling costs nothing but reading. If the grid is triggering work, something
upstream of it is wrong.

The one thing it owns is the record of what has been saved onto somebody's own machine.
"""

from __future__ import annotations

from sift.kernel.settings_registry import ReadBy, register_setting
from sift.slices.browse import schema
from sift.slices.browse.router import (
    SAVE_TO_DEVICE_KEY,
    router,
)
from sift.slices.browse.service import SERVICE, BrowseService
from sift.slices.browse.tile_marks import (
    ANSWER_LABELS,
    ANSWERS,
    TILE_MARK_KEYS,
    TILE_MARKS,
)

# "Show recently viewed" is RETIRED: the strip it governed was drawn on no screen anybody could
# open, so the switch changed nothing. Recently viewed is a screen of its own on the rail. The
# stored answers are forgotten by the settings component's v8 step.

# What a tile draws in its corners, one answer per mark.
#
# A SINGLE SWITCH, "Mark shared files", is retired into these seven: it was the sharing one. Its
# stored value is carried across by the settings component's v3 step: somebody who had turned the
# badge off gets `never` for that mark and the tile they already had. See `tile_marks` for why the
# warning about a missing file is not among them.
#
# Sharing is admin-only in effect: the server fills those marks for an admin and for nobody else,
# so a guest choosing "always" sees nothing appear. It is offered to everybody rather than hidden
# from guests because hiding it would be a second rule to keep in step with the first, and the
# choice is about clutter rather than about permission.
for _mark in TILE_MARKS:
    register_setting(
        key=_mark.key,
        # Drawn by the browser and acted on by the browser. The server keeps it so the
        # preference follows the user to another device, and reads it never.
        read_by=ReadBy.CLIENT,
        scope="user",
        default=_mark.default,
        choices=ANSWERS,
        choice_labels=ANSWER_LABELS,
        section="Appearance",
        label=_mark.label,
        help=_mark.help,
    )

__all__ = [
    "SAVE_TO_DEVICE_KEY",
    "SERVICE",
    "TILE_MARKS",
    "TILE_MARK_KEYS",
    "BrowseService",
    "router",
    "schema",
]
