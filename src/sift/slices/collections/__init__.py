# SPDX-License-Identifier: AGPL-3.0-or-later
"""Manual collections: the mixtape, the shortlist, the best-of.

A collection is a set somebody put together by hand, in an order they chose. That is what makes it
different from the other two ways of grouping media: a tag describes what something is, a folder is
where the file physically sits, and a collection is neither: it is a decision.

Order is part of the data. A collection is a sequence, not a bag, so `position` is written on every
add and rewritten on every rearrange, and it is what a collection's contents come back in.

Membership is manual, and only manual. Nothing here derives what is in a collection from a saved
search: every row is there because a person put it there.

This slice owns no table. `collections` and `collection_items` live in the kernel because the
permission resolver joins them: a grant can name a collection, and the assets it reaches are
reached through the membership rows.
"""

from __future__ import annotations

from sift.slices.collections.router import router
from sift.slices.collections.service import (
    SERVICE,
    Collection,
    CollectionService,
    UnknownItem,
)

__all__ = [
    "SERVICE",
    "Collection",
    "CollectionService",
    "UnknownItem",
    "router",
]
