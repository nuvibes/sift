# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every number on an entity page's tab strip: the scoped total of the listing each tab draws."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from sift.kernel import wiring
from sift.kernel.access import (
    CollectionView,
    EntityNarrowing,
    PersonSuggestion,
    PhotoSetView,
    Repository,
    SiteSuggestion,
    SongView,
    TagSuggestion,
    Viewer,
    related_filter,
)
from sift.kernel.seams import DisagreementSeam
from sift.slices.auth import current_viewer
from sift.slices.related.models import RelatedCounts

router = APIRouter(tags=["related"])

#: A product decision, so written out. A Photo Set has no Loops tab: stills carry none.
#: Must agree with `TABS_FOR` in the client's `related.svelte.ts`; a test compares the two.
TABS_FOR: dict[str, tuple[str, ...]] = {
    "person": ("files", "photo_sets", "loops", "tags", "sites", "collections", "songs", "people"),
    #: `tags_within`: the tags filed directly under this one (`tags.parent_id`).
    "tag": (
        "files",
        "photo_sets",
        "loops",
        "tags_within",
        "people",
        "sites",
        "collections",
        "songs",
    ),
    #: A Site can hold other Sites (`sites.parent_id`), so a network's page lists them.
    "site": (
        "files",
        "photo_sets",
        "loops",
        "sites_within",
        "people",
        "tags",
        "collections",
        "songs",
    ),
    "collection": ("files", "loops", "people", "tags", "sites", "songs"),
    "photo_set": ("files", "people", "tags", "sites"),
    # No Photo Sets: a Photo Set is stills, which carry no song.
    "song": ("files", "loops", "people", "tags", "sites", "collections"),
}

#: One wall's number, asked for only if this page has that wall.
Ask = Callable[[], Awaitable[int]]

#: A mapping so a kind added to `TABS_FOR` and missed here raises; None is hidden or absent.
_RESOLVE: dict[str, Callable[[Repository, Viewer, str], Awaitable[object | None]]] = {
    "person": lambda access, viewer, one: access.visible_person(viewer, one),
    "tag": lambda access, viewer, one: access.visible_tag(viewer, one),
    "site": lambda access, viewer, one: access.visible_site(viewer, one),
    "collection": lambda access, viewer, one: access.visible_collection(viewer, one),
    "photo_set": lambda access, viewer, one: access.visible_photo_set(viewer, one),
    "song": lambda access, viewer, one: access.visible_song(viewer, one),
}

#: The listings return the scoped total beside the page, so one row buys the number.
_ONE = 1


def _disagreement_seam(request: Request) -> DisagreementSeam:
    """The stash-box disagreements, through a seam since a slice may not import another."""
    return wiring.part_of(request, wiring.DISAGREEMENTS)


@router.get("/related/{kind}/{entity_id}")
async def related_counts(
    kind: str,
    entity_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    waiting: Annotated[DisagreementSeam, Depends(_disagreement_seam)],
) -> RelatedCounts:
    """Every number on one entity page's tab strip; an unknown kind is refused."""
    wanted = TABS_FOR.get(kind)
    if wanted is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown kind {kind!r}")

    subject = await _RESOLVE[kind](access, viewer, entity_id)
    counts = RelatedCounts()
    if subject is None:
        await _count_by_listing(counts, wanted, kind, entity_id, access, viewer)
    else:
        # The subject's row carries its files; one statement answers every tab its card carries.
        counts.files, counts.files_bytes = _files_of(subject)
        cells = (await access.card_counts(viewer, kind, [entity_id]))[entity_id]
        for wall in wanted:
            if wall in cells:
                setattr(counts, wall, cells[wall])
        rest = [wall for wall in wanted if wall != "files" and wall not in cells]
        await _count_by_listing(counts, rest, kind, entity_id, access, viewer, subject=subject)
    # Not a wall: where a stash-box disagrees with the RECORD, so not counted off a listing.
    counts.disagreements, counts.disagreement_boxes = await waiting.disagreement_mark(
        viewer, kind, entity_id
    )
    return counts


def _files_of(subject: object) -> tuple[int, int]:
    """The files a visible subject's row counts, and their size: its Files tab's two numbers."""
    match subject:
        case PersonSuggestion() | SiteSuggestion() | TagSuggestion():
            return subject.asset_count, subject.size_bytes
        case CollectionView() | PhotoSetView() | SongView():
            return subject.item_count, subject.size_bytes
    raise TypeError(f"no files count on {type(subject).__name__}")


async def _count_by_listing(
    counts: RelatedCounts,
    wanted: tuple[str, ...] | list[str],
    kind: str,
    entity_id: str,
    access: Repository,
    viewer: Viewer,
    *,
    subject: object | None = None,
) -> None:
    """Each wanted tab's number off the listing that tab draws, asked at one row."""
    narrowing = related_filter(**{kind: entity_id})
    # One read gives the files' total and size, so the two cannot describe different sets.

    async def files() -> int:
        page = await access.visible_assets(viewer, limit=_ONE, asset_filter=narrowing)
        counts.files_bytes = page.total_bytes
        return page.total

    walls: dict[str, Ask] = {
        "files": files,
        "photo_sets": lambda: _total(
            access.list_photo_sets(viewer, limit=_ONE, asset_filter=narrowing)
        ),
        # On a tag's page the tag goes as `tag` INSTEAD of the filter, or the marks tagged in their
        # own right would be lost. See `loops_query`.
        "loops": lambda: _total(
            access.list_loops(
                viewer,
                limit=_ONE,
                asset_filter=related_filter() if kind == "tag" else narrowing,
                tag=entity_id if kind == "tag" else None,
            )
        ),
        # A site's children are named by a column, so this filters by `parent`.
        "sites_within": lambda: _total(access.list_sites(viewer, "", limit=_ONE, parent=entity_id)),
        "tags_within": lambda: _total(
            access.list_tags(
                viewer,
                limit=_ONE,
                narrowing=EntityNarrowing.of(
                    "tag", {"parent": [entity_id]}, is_admin=viewer.is_admin
                ),
            )
        ),
        "tags": lambda: _total(access.list_tags(viewer, limit=_ONE, asset_filter=narrowing)),
        "sites": lambda: _total(access.list_sites(viewer, "", limit=_ONE, asset_filter=narrowing)),
        "collections": lambda: _total(
            access.list_collections(viewer, limit=_ONE, asset_filter=narrowing)
        ),
        "songs": lambda: _total(access.list_songs(viewer, limit=_ONE, asset_filter=narrowing)),
        "people": lambda: _people(access, viewer, kind, entity_id, narrowing, subject),
    }

    for wall in wanted:
        setattr(counts, wall, await walls[wall]())


async def _total(page: Coroutine[Any, Any, Any]) -> int:
    """The scoped total off any of the listings, which all carry one."""
    return int((await page).total)


async def _people(
    access: Repository,
    viewer: Viewer,
    kind: str,
    entity_id: str,
    narrowing: object,
    subject: object | None,
) -> int:
    """People this page's files reach; on a person's page, the others (the total less them)."""
    if kind != "person":
        page = await access.suggest_people(viewer, "", limit=_ONE, asset_filter=narrowing)  # type: ignore[arg-type]
        return page.total
    page = await access.suggest_people(
        viewer, "", limit=_ONE, asset_filter=related_filter(person=entity_id)
    )
    if page.total == 0:
        return 0
    return page.total - 1 if subject is not None else page.total
