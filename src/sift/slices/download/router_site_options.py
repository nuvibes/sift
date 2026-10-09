# SPDX-License-Identifier: AGPL-3.0-or-later
"""What each Site does differently: its naming rule, folder and downloader."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Annotated, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Request, status

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.content import LibraryStore
from sift.kernel.ledger import Actor
from sift.kernel.library_write import LibraryWriteRefused, check_folder_may_change
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.download import naming
from sift.slices.download.models import (
    DownloaderChoice,
    NamePreview,
    NamePreviewRequest,
    SetSiteOptionsRequest,
    SiteOptionItem,
    SiteOptionsResponse,
)
from sift.slices.download.router_parts import _service
from sift.slices.download.service import (
    DownloadService,
)
from sift.slices.download.site_options import DEFAULT_SCOPE as OPTIONS_DEFAULT
from sift.slices.download.site_options import SITE_OPTIONS, SiteOptionStore
from sift.slices.download.sources.registry import CHOOSABLE_DOWNLOADERS, is_a_downloader
from sift.slices.download.sources.sites import catalog
from sift.slices.download.sources.sites.catalog import SITES, SiteRecord, words_filled

router = APIRouter()


def _options(request: Request) -> SiteOptionStore:
    return part_of(request, SITE_OPTIONS)


@router.get("/site-options")
async def read_site_options(
    store: Annotated[SiteOptionStore, Depends(_options)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SiteOptionsResponse:
    """What everything follows, what each Site was given, and the tokens a template may use."""
    stored = await store.all()
    default = stored.get(OPTIONS_DEFAULT)
    return SiteOptionsResponse(
        default=SiteOptionItem(
            scope=OPTIONS_DEFAULT,
            naming=default.naming if default else naming.DEFAULT_TEMPLATE,
            dest_folder_id=default.dest_folder_id if default else None,
            downloader=default.downloader if default else None,
        ),
        sites=[
            SiteOptionItem(
                scope=scope,
                naming=one.naming,
                dest_folder_id=one.dest_folder_id,
                downloader=one.downloader,
            )
            for scope, one in sorted(stored.items())
            if scope != OPTIONS_DEFAULT
        ],
        tokens=dict(naming.TOKENS),
        downloaders=[
            DownloaderChoice(value=one.value, label=one.label, help=one.help)
            for one in CHOOSABLE_DOWNLOADERS
        ],
    )


@router.put(
    "/site-options/{scope}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def set_site_options(
    scope: str,
    body: SetSiteOptionsRequest,
    store: Annotated[SiteOptionStore, Depends(_options)],
    library: Annotated[LibraryStore, Depends(wiring.library)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Give a Site, or everything, its naming rule, destination and downloader."""
    if scope != OPTIONS_DEFAULT and not any(record.key == scope for record in SITES):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no site by that name.")
    _refuse_a_creator_nobody_fills(scope, body.naming)
    if body.dest_folder_id is not None:
        await _check_downloads_can_land_there(library, body.dest_folder_id)
    # A tool name nothing runs would be stored, shown back and ignored.
    if body.downloader is not None and not is_a_downloader(body.downloader):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Sift does not have a downloader by that name."
        )
    held = await store.held(scope)
    await store.set(
        scope,
        naming=body.naming,
        dest_folder_id=body.dest_folder_id,
        downloader=body.downloader,
        actor=Actor.user(viewer.id),
        folders=await _folder_words(library, (held.dest_folder_id, body.dest_folder_id)),
    )


async def _folder_words(library: LibraryStore, folder_ids: Iterable[str | None]) -> dict[str, str]:
    """Each named folder's path in the library, for the setting's History line."""
    words: dict[str, str] = {}
    for folder_id in {one for one in folder_ids if one}:
        folder = await library.get_folder(folder_id)
        if folder is not None:
            words[folder_id] = folder.rel_path or folder.name
    return words


def _refuse_a_creator_nobody_fills(scope: str, template: str | None) -> None:
    """Refuse `{creator}` in the rule of a Site that never says who posted."""
    record = catalog.by_key(scope)
    if record is None or "creator" in words_filled(record) or not template:
        return
    with_one = naming.fill(template, naming.Facts(site=record.site, username="x", original="n"))
    without = naming.fill(template, naming.Facts(site=record.site, username=None, original="n"))
    if with_one != without:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{record.site} doesn't say who posted a file, so {{creator}} would always be"
            " empty. Remove it from the name.",
        )


async def _check_downloads_can_land_there(library: LibraryStore, folder_id: str) -> None:
    """That the folder exists and its library was handed over read-write."""
    folder = await library.get_folder(folder_id)
    if folder is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That folder is not there any more.")
    root = await library.get_root(folder.root_id)
    if root is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That folder is not there any more.")
    try:
        await check_folder_may_change(Path(root.abs_path) / folder.rel_path)
    except LibraryWriteRefused as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from refusal


@router.delete(
    "/site-options/{scope}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def clear_site_options(
    scope: str,
    store: Annotated[SiteOptionStore, Depends(_options)],
    library: Annotated[LibraryStore, Depends(wiring.library)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Put a Site back to following the default; the default itself cannot be cleared."""
    if scope == OPTIONS_DEFAULT:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "The default cannot be cleared. Set it to something instead.",
        )
    held = await store.held(scope)
    await store.clear(
        scope,
        actor=Actor.user(viewer.id),
        folders=await _folder_words(library, (held.dest_folder_id,)),
    )


@router.post("/site-options/preview", dependencies=[Depends(csrf_protect)])
async def preview_name(
    body: NamePreviewRequest,
    viewer: Annotated[Viewer, Depends(require_admin)],
    service: Annotated[DownloadService, Depends(_service)],
) -> NamePreview:
    """What a template would name a download from the Site, refused as saving would refuse it."""
    _refuse_a_creator_nobody_fills(body.scope or OPTIONS_DEFAULT, body.naming)
    record = catalog.by_key(body.scope) if body.scope else None
    facts = _example_facts(record)
    # A real example from this library where there is one, under the Site's name now.
    if record is not None:
        facts = replace(facts, site=await service.site_name_now(record.key, record.site))
        named = await service.newest_named(record.key, record.site)
        if named is not None:
            return NamePreview(example=naming.fill(body.naming or "", named))
    if record is not None and facts.username is not None:
        real = await service.newest_creator(record.key, record.site)
        if real:
            facts = replace(facts, username=real)
    return NamePreview(example=naming.fill(body.naming or "", facts))


class _Example(NamedTuple):
    """One Site's made-up download, as far as its own shape goes."""

    name: str
    id: str | None = None
    title: str | None = None


#: An invented download per Site, in its own shape; a test holds every Site to an entry.
_EXAMPLES: dict[str, _Example] = {
    "tiktok": _Example("3f9a1c7e5b2d", "7401234567890123456", "A post caption"),
    "youtube": _Example("A_video_title", "aB3dE5fG7hJ", "A video title"),
    "instagram": _Example("AQOx7Hn2kLmPq9RtYv", "CxY7kLm2PqR"),
    "x": _Example("1801234567890123456_1", "1801234567890123456"),
    "redgifs": _Example("Outdoor_summer_clip", "gentlequietfox", "Outdoor summer clip"),
    "reddit": _Example("k3x9q2mzp7a1", "1f3xk9a", "A post title"),
    "goonbox": _Example("1536x2048_a1b2c3d4"),
    "pmvhaven": _Example("A_video_title", None, "A video title"),
    "fapello": _Example("1536x2048_a1b2c3d4"),
    "coomer": _Example("1536x2048_a1b2c3d4"),
    "kemono": _Example("1536x2048_a1b2c3d4"),
    "pornhub": _Example("A_video_title", "ph5f2a1b3c4d5e6", "A video title"),
    "hqporner": _Example("A_video_title", None, "A video title"),
    "redtube": _Example("A_video_title", "41234567", "A video title"),
    "xvideos": _Example("A_video_title", "81234567", "A video title"),
    "bunkr": _Example("holiday_clip_04"),
    "cyberdrop": _Example("holiday_clip_04"),
    "cyberfile": _Example("holiday_clip_04"),
    "gofile": _Example("holiday_clip_04"),
    "discord": _Example("image0"),
    "pixeldrain": _Example("holiday_clip_04", "aB3dE5fG"),
    "xbunkr": _Example("holiday_clip_04"),
    "jpg5": _Example("holiday_clip_04"),
    "turbovid": _Example("holiday_clip_04"),
    "saint": _Example("holiday_clip_04"),
    "imgur": _Example("k7Qp2Zx", "k7Qp2Zx", "A post title"),
}

#: An address Sift has no Site for, where every word can fill.
_ANY_ADDRESS = naming.Facts(
    site="Vimeo",
    username="someone",
    original="A_video_title",
    id="123456789",
    title="A video title",
    posted=date(2026, 8, 13),
)


def _example_facts(record: SiteRecord | None) -> naming.Facts:
    """The made-up download a preview fills, each word only where the Site can fill it."""
    if record is None:
        return _ANY_ADDRESS
    shape = _EXAMPLES.get(record.key, _Example(_ANY_ADDRESS.original))
    fills = set(words_filled(record))
    return naming.Facts(
        site=record.site,
        username="someone" if "creator" in fills else None,
        original=shape.name,
        id=shape.id if "id" in fills else None,
        title=shape.title if "title" in fills else None,
        posted=_ANY_ADDRESS.posted if "posted" in fills else None,
    )
