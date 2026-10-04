# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tags and their tree, the heart, and the star rating.

Organising happens logically. A clip dragged onto a tag chip is tagged and is not moved: the file
keeps its path and its bytes, and a folder full of everything can be as organised as a folder tree
without anything being rearranged on disk.

A tag may be filed under one other tag (its record's "Part of"), so the tags form a tree and never
a graph: `Sunset` under `Outdoors`. Filtering by a tag takes in everything filed under it, and a
tag's page lists the tags filed directly under it. Nothing is seeded: a fresh install has no tags
at all.

The heart and the stars are independent and per user. Favouriting a three-star clip is not a
contradiction, clearing one leaves the other alone, and two people rating the same file get their
own answers, which is the reason the state is a table rather than a column on the asset.

This slice owns no table. `tags` and `asset_tags` belong to the access layer, because a grant can
name a tag and the resolver has to join them; `asset_user_state` belongs to the content kernel,
because three features write it. What is here is the behaviour: the endpoints, the writes, and the
screens.
"""

from __future__ import annotations

from sift.kernel.settings_registry import ReadBy, register_setting
from sift.slices.tags_ratings.enrich import TagWriter
from sift.slices.tags_ratings.router import router
from sift.slices.tags_ratings.service import SERVICE, DuplicateTag, Tag, TagLoop, TagService

#: How many stars a rating is drawn as. The value is the number of stars, as text, because a
#: choice list is text everywhere else in the registry.
RATING_SCALE_KEY = "ratings.scale"

# How many stars to draw, and nothing else.
#
# What is STORED is always out of ten, whichever is chosen here, and that is the whole reason
# this can be switched freely. A scale that decided the stored number would have to rewrite
# every rating in the library on each change, and switching to five and back could not
# recover what an odd number meant, because there is no half star to put it back as.
#
# Drawn by the browser and acted on by the browser, like the appearance settings: the server
# keeps it so the choice follows the user to another device, and reads it never. Per user
# rather than install-wide, because a rating is already one person's opinion in their own row.
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
    # Not asked at first run: switching is free by design (see above, every rating is stored out
    # of ten either way), so nothing is saved by settling it early, and discovery is Appearance's
    # job, along with the settings search.
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
