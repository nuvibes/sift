# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the picks would send before a swap starts, and what they leave out and why.

## The figure is the offer's own read

`offer.weigh` counts the files the offer would hold: the same scoped read, as the sender with the
vault SHUT, so a file in Hidden adds nothing and the figure is what the offer will carry.

## What is left out, by name where one thing explains it

Two marks keep a file out of every swap: Kept local (`keep_local`, the "Don't enrich" switch, which
keeps everything about a thing on this device) and "Don't swap" (`keep_from_swaps`). Each sits on a
file, a person, a Site or a tag, and a file is under the mark of anything it is filed under. A pick
that wears one of them sends nothing of its own, and the sender is told so by its name: "Ava
Example is kept local: 1,200 files aren't offered."

So the answer names every PICK that carries a mark on its own row, with how many of its files the
mark keeps back, and then counts the rest: the files the picks reach that a mark ON SOMETHING ELSE
keeps out (a tag above them, a Site, the file's own switch). Both are read through the same filters
the offer reads (`offer.chosen_filters`) and the same scoped read, with the vault shut, so nothing in
Hidden is counted or named, and a pick this viewer could not open is never named.

A file whose format this device cannot strip (`transfer.NEVER_SENT_MIMES`, empty today) is neither
offered nor counted here: no mark explains it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast

from sift.kernel.access import AnyOf, AssetFilter, Not, Repository, Viewer, Where
from sift.kernel.access.catalog import refused_here
from sift.kernel.db import Database
from sift.kernel.paging import MAX_PAGE_SIZE
from sift.kernel.seams import SavedFilterSeam
from sift.slices.swap import offer, refusal
from sift.slices.swap.models import Chosen

#: Which mark keeps a thing back: Kept local, or "Don't swap".
Mark = Literal["local", "swap"]

#: Every file kept out of swaps by either mark, its own or anything it is filed under: the two
#: refusals the offer narrows by (`offer.NOT_KEPT_LOCAL`, `offer.NOT_KEPT_FROM_SWAPS`), turned round.
REFUSED = AnyOf((Where("enrichment", ("local",)), Where("kept_from_swaps")))

#: The kinds a pick can carry a mark on: the four the refusal covers.
_MARKABLE: frozenset[str] = frozenset(refusal.KINDS)


@dataclass(frozen=True, slots=True)
class LeftOutBy:
    """One pick that carries a mark on its own row, and how many of its files the mark keeps back."""

    kind: refusal.Kind
    id: str
    name: str
    mark: Mark
    files: int


@dataclass(frozen=True, slots=True)
class Weight:
    """What the picks would offer, and what they leave out: by the picks that carry a mark, and the
    rest of the files a mark on something else keeps back."""

    files: int = 0
    bytes: int = 0
    left_out: tuple[LeftOutBy, ...] = ()
    left_out_other: int = 0


async def _own_mark(database: Database, kind: str, local_id: str) -> Mark | None:
    """The mark on this thing's own row: Kept local first, since it keeps back more than a swap."""
    if await refused_here(database, "enrich", kind, local_id):
        return "local"
    if await refused_here(database, "swap", kind, local_id):
        return "swap"
    return None


async def _count(access: Repository, shut: Viewer, narrowed: AssetFilter) -> int:
    """How many files this filter reaches for this viewer: the wall's own total, one row read."""
    return (await access.visible_assets(shut, limit=1, asset_filter=narrowed)).total


async def _ids(access: Repository, shut: Viewer, narrowed: AssetFilter) -> set[str]:
    """Every file this filter reaches for this viewer, a page at a time, by seeking past the last."""
    found: set[str] = set()
    after: str | None = None
    while True:
        page = await access.visible_assets(
            shut, limit=MAX_PAGE_SIZE, asset_filter=narrowed, sort=offer.OFFER_ORDER, after=after
        )
        found.update(item.asset.id for item in page.items)
        if len(page.items) < MAX_PAGE_SIZE:
            return found
        after = page.items[-1].asset.id


async def left_out(
    access: Repository,
    database: Database,
    search: SavedFilterSeam,
    viewer: Viewer,
    chosen: Sequence[Chosen],
) -> tuple[tuple[LeftOutBy, ...], int]:
    """The picks that carry a mark, each with the files it keeps back, and how many more files the
    picks reach that a mark on something else keeps out. See the module docstring."""
    shut = await access.load_viewer(viewer.id)
    if shut is None:
        return (), 0
    named: list[LeftOutBy] = []
    seen: set[tuple[str, str]] = set()
    for one in chosen:
        if one.kind not in _MARKABLE or not one.id or (one.kind, one.id) in seen:
            continue
        seen.add((one.kind, one.id))
        mark = await _own_mark(database, one.kind, one.id)
        if mark is None:
            continue
        kind = cast(refusal.Kind, one.kind)
        name = await refusal.name_for(access, shut, kind, one.id)
        if name is None:
            # Not this viewer's to see with the vault shut: never named, and its files never counted.
            continue
        reached = AssetFilter(where=Where(offer.LEAF_OF_KIND[one.kind], (one.id,)))
        files = await _count(access, shut, reached)
        if files:
            named.append(LeftOutBy(kind=kind, id=one.id, name=name, mark=mark, files=files))

    # The rest: what a mark on something else keeps back, less what a named pick already explains.
    explained = tuple(Where(offer.LEAF_OF_KIND[one.kind], (one.id,)) for one in named)
    others: set[str] = set()
    for narrowing in await offer.chosen_filters(search, shut, chosen):
        refused = narrowing.also(REFUSED)
        if explained:
            refused = refused.also(Not(AnyOf(explained)))
        others |= await _ids(access, shut, refused)
    return tuple(named), len(others)


async def weigh(
    access: Repository,
    database: Database,
    search: SavedFilterSeam,
    viewer: Viewer,
    chosen: Sequence[Chosen],
) -> Weight:
    """What the picks would offer (`offer.weigh`), and what they leave out and why."""
    files, size = await offer.weigh(access, search, viewer, chosen)
    named, other = await left_out(access, database, search, viewer, chosen)
    return Weight(files=files, bytes=size, left_out=named, left_out_other=other)
