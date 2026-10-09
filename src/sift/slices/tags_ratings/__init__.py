# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tags and their tree, the heart, and the star rating."""

from __future__ import annotations

from sift.kernel.settings_registry import ReadBy, register_setting
from sift.slices.tags_ratings.enrich import TagWriter
from sift.slices.tags_ratings.router import router
from sift.slices.tags_ratings.service import SERVICE, DuplicateTag, Tag, TagLoop, TagService

RATING_SCALE_KEY = "ratings.scale"

# Only how many stars to draw: a rating is always stored out of ten, so switching is free.
register_setting(
    key=RATING_SCALE_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default="5",
    choices=("5", "10"),
    choice_labels=("Five stars", "Ten stars"),
    section="Appearance",
    label="Rating scale",
    disclosure="There are no half stars, and ratings you already gave keep their meaning.",
    help="How many stars a rating shows.",
    # Not asked at first run: switching is free, so nothing is saved by settling it early.
)

__all__ = [
    "RATING_SCALE_KEY",
    "SERVICE",
    "DuplicateTag",
    "Tag",
    "TagLoop",
    "TagService",
    "TagWriter",
    "router",
]
