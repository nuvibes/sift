# SPDX-License-Identifier: AGPL-3.0-or-later
"""The grid: what a person sees when they open Sift, read-only, and the record of saved copies."""

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

# Retired: the strip it governed was on no screen; its stored answers go in the v8 step.

# What a tile draws in its corners, one answer per mark; sharing marks reach admins only.
for _mark in TILE_MARKS:
    register_setting(
        key=_mark.key,
        # Kept by the server so it follows the user; read only by the browser.
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
