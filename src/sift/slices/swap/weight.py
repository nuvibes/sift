# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the picks would send before a swap starts, and what they leave out and why."""

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
from sift.slices.swap.manifest import Progress, grouping, manifest_of
from sift.slices.swap.models import Chosen, GroupKind, Learns, ManifestGroup, Offer

Mark = Literal["local", "swap"]

#: Every file either mark keeps out of swaps, the offer's two refusals turned round.
REFUSED = AnyOf((Where("enrichment", ("local",)), Where("kept_from_swaps")))

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
    """What the picks would offer, and what the marks leave out."""

    files: int = 0
    bytes: int = 0
    left_out: tuple[LeftOutBy, ...] = ()
    left_out_other: int = 0
    #: The manifest of what Start would offer, and what the other side would learn from it.
    groups: tuple[ManifestGroup, ...] = ()
    learns: Learns | None = None


async def _own_mark(database: Database, kind: str, local_id: str) -> Mark | None:
    """The mark on this thing's own row: Kept local first, since it keeps back more than a swap."""
    if await refused_here(database, "enrich", kind, local_id):
        return "local"
    if await refused_here(database, "swap", kind, local_id):
        return "swap"
    return None


async def _count(access: Repository, reader: Viewer, narrowed: AssetFilter) -> int:
    """How many files this filter reaches for this viewer: the wall's own total, one row read."""
    return (await access.visible_assets(reader, limit=1, asset_filter=narrowed)).total


async def _ids(access: Repository, reader: Viewer, narrowed: AssetFilter) -> set[str]:
    """Every file this filter reaches for this viewer, a page at a time, by seeking past the last."""
    found: set[str] = set()
    after: str | None = None
    while True:
        page = await access.visible_assets(
            reader, limit=MAX_PAGE_SIZE, asset_filter=narrowed, sort=offer.OFFER_ORDER, after=after
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
    """The picks that carry a mark with what each keeps back, and what other marks keep out."""
    reader = await offer.swap_reader(access, viewer)
    if reader is None:
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
        name = await refusal.name_for(access, reader, kind, one.id)
        if (
            name is None
        ):  # pragma: no cover (a marked pick the reader cannot open: gone since the screen)
            continue
        reached = AssetFilter(where=Where(offer.LEAF_OF_KIND[one.kind], (one.id,)))
        files = await _count(access, reader, reached)
        if files:
            named.append(LeftOutBy(kind=kind, id=one.id, name=name, mark=mark, files=files))

    # The rest: what a mark on something else keeps back, less what a named pick already explains.
    explained = tuple(Where(offer.LEAF_OF_KIND[one.kind], (one.id,)) for one in named)
    others: set[str] = set()
    for narrowing in await offer.chosen_filters(search, reader, chosen):
        refused = narrowing.also(REFUSED)
        if explained:
            refused = refused.also(Not(AnyOf(explained)))
        others |= await _ids(access, reader, refused)
    return tuple(named), len(others)


#: A left-out pick's row, by the kind of thing it is; a file or a folder is among the loose files.
_LEFT_OUT_KIND: dict[str, GroupKind] = {"person": "person", "site": "site"}

#: The picks drawn as a group of their own, since an offer does not carry them.
_PICKED_GROUPS: dict[str, GroupKind] = {"collection": "collection", "photo_set": "photo_set"}


def learns_of(offered: Offer) -> Learns:
    """What the other side learns from an offer, counted: the names it carries and the faces."""
    return Learns(
        people=len(offered.people),
        aliases=sum(len(one.aliases) for one in offered.people),
        sites=len(offered.sites),
        usernames=len({(one.site, one.username) for one in offered.files if one.username}),
        faces=sum(len(one.faces.faces) for one in offered.people if one.faces is not None),
    )


async def _picked(
    access: Repository, viewer: Viewer, chosen: Sequence[Chosen], offered: Offer
) -> list[ManifestGroup]:
    """A group per Collection and Photo Set picked: the offered files each one reaches."""
    reader = await offer.swap_reader(access, viewer)
    if reader is None:
        return []
    sizes = {one.key: one.size for one in offered.files}
    out: list[ManifestGroup] = []
    for one in dict.fromkeys(chosen):
        kind = _PICKED_GROUPS.get(one.kind)
        if kind is None:
            continue
        reached = AssetFilter(where=Where(offer.LEAF_OF_KIND[one.kind], (one.id,)))
        keys = [key for key in await _ids(access, reader, reached) if key in sizes]
        out.append(
            ManifestGroup(
                key=f"{one.kind}:{one.id}",
                kind=kind,
                name=await _name_of(access, reader, one),
                files=len(keys),
                bytes=sum(sizes[key] for key in keys),
            )
        )
    return out


async def _name_of(access: Repository, reader: Viewer, one: Chosen) -> str | None:
    if one.kind == "collection":
        collection = await access.visible_collection(reader, one.id)
        return None if collection is None else collection.name
    photo_set = await access.visible_photo_set(reader, one.id)
    return None if photo_set is None else photo_set.name


def _left_out_rows(named: Sequence[LeftOutBy]) -> list[ManifestGroup]:
    return [
        ManifestGroup(
            key=f"left_out:{one.kind}:{one.id}",
            kind=_LEFT_OUT_KIND.get(one.kind, "loose"),
            name=one.name,
            files=one.files,
            state="left_out",
            left_out=one.mark,
        )
        for one in named
    ]


async def weigh(
    access: Repository,
    database: Database,
    search: SavedFilterSeam,
    viewer: Viewer,
    chosen: Sequence[Chosen],
    *,
    faces: offer.FaceDescriptions | None = None,
) -> Weight:
    """What the picks would offer, read as the offer itself is (`offer.files_of`, `people_of`,
    `build`), its manifest and what the other side would learn; and what they leave out and why.
    `faces` counts the faces each person would carry; without it the count is none."""
    selection = await offer.files_of(access, database, search, viewer, chosen)
    people = await offer.people_of(database, selection, faces=faces)
    offered = offer.build(selection, people)
    named, other = await left_out(access, database, search, viewer, chosen)
    groups = [
        *manifest_of(offered, grouping(offered), Progress(), held=None),
        *await _picked(access, viewer, chosen, offered),
        *_left_out_rows(named),
    ]
    return Weight(
        files=len(offered.files),
        bytes=sum(one.size for one in offered.files),
        left_out=named,
        left_out_other=other,
        groups=tuple(groups),
        learns=learns_of(offered),
    )
