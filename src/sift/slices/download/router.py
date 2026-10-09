# SPDX-License-Identifier: AGPL-3.0-or-later
"""The download endpoints: admin-only, every one of them, enforced here on the server.

Cookies are accepted and never returned: an answer about them is only derived facts.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import StringConstraints

from sift.kernel import wiring
from sift.kernel.access import AssetView, Repository, Viewer, sentences
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now
from sift.kernel.content import LibraryStore, Root
from sift.kernel.jobs import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Workspaces
from sift.kernel.log import get_logger
from sift.kernel.tunnels import (
    DEFAULT_SCOPE,
    DIRECT,
    DIRECT_LABEL,
    TUNNELS,
    EgressRouter,
    TunnelError,
    TunnelHosting,
    TunnelStore,
)
from sift.kernel.wire import FacetCounts, FacetValue
from sift.kernel.wiring import SETTINGS_HUB, part_of, part_or_none
from sift.slices.auth import csrf_protect, master_key, require_admin
from sift.slices.download import (
    bulk,
    router_actions,
    router_connections,
    router_site_options,
    tools,
)
from sift.slices.download.art import SITE_ART, ArtStore, creator_scope_for
from sift.slices.download.models import (
    MAX_PASTED_LINKS,
    BulkPreview,
    BulkQueued,
    BulkRequest,
    CreatorsWithArt,
    DownloadFile,
    DownloadFiles,
    DownloadFolder,
    DownloadItem,
    DownloadProgress,
    DownloadsAtAGlance,
    DownloadSiteCount,
    DownloadsPage,
    ImportedTunnel,
    ImportTunnelRequest,
    PastedLinks,
    PasteLinksRequest,
    QueueSummary,
    RefusedLink,
    RenameTunnelRequest,
    ReplaceTunnelConfigRequest,
    RouteRequest,
    RoutesResponse,
    SiteChoice,
    StopTunnelRequest,
    SubmitDownloadRequest,
    SubmittedDownload,
    SupportedSite,
    TunnelItem,
)
from sift.slices.download.router_parts import _egress, _policy, _refused, _service
from sift.slices.download.service import (
    PAUSED_KEY,
    DownloadNarrowing,
    DownloadService,
    DownloadShow,
    DownloadSort,
    DownloadView,
    FileFacts,
    PasteChoices,
)
from sift.slices.download.site_options import SITE_OPTIONS, SiteOptionStore
from sift.slices.download.sources import failures, progress
from sift.slices.download.sources.normalize import url_hash
from sift.slices.download.sources.progress import PROGRESS
from sift.slices.download.sources.sites.catalog import SITES, SiteRecord, words_filled
from sift.slices.download.sources.tuning import RunPolicy
from sift.slices.download.url_guard import SAFE_SCHEMES

router = APIRouter(tags=["download"])

# Which tools the downloader runs and their versions; included so the slice mounts one router.
router.include_router(tools.router)

log = get_logger(__name__)


def _bulk_site(url: str) -> SiteRecord:
    try:
        return bulk.bulk_site(url)
    except bulk.BulkRefused as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


def _watching(request: Request) -> progress.Registry:
    """What is in flight right now, held on the application and never written down."""
    return part_of(request, PROGRESS)


def _workspaces(request: Request) -> Workspaces | None:
    """The pool's workspaces, so a paused row is measured where its job wrote; None with no pool."""
    pool = part_or_none(request, wiring.POOL)
    return pool.workspaces if pool is not None else None


def _options_or_none(request: Request) -> SiteOptionStore | None:
    """What each Site does differently, or None where none is wired."""
    return part_or_none(request, SITE_OPTIONS)


def _named(
    asset_id: str | None, file: AssetView | None, names: dict[str, str] | None
) -> str | None:
    """A file's name on disk now, or the name it arrived under while no copy can be seen."""
    if file is None:
        return None
    return (names or {}).get(asset_id or "") or file.asset.original_filename


def _failure_said(view: DownloadView) -> str | None:
    """What a failed row says, worded when shown from its recorded code and Site."""
    code = view.error_code
    if not code:
        return sentences.failure_today(view.error)
    site = view.site_name or view.site
    now = failures.reading_now(view.url or "", code)
    if now is not None and now.tier == 3:
        said: str | None = now.sentence
    else:
        said = sentences.download_failed(code, site) or (now.sentence if now else None)
    if (
        said is not None
        and now is not None
        and now.a_tunnel_would_help
        and code.startswith("http-")
        and view.via == DIRECT_LABEL
    ):
        said = f"{said.rstrip('.')}. Routing {site or 'this site'} through a tunnel gets past it."
    return said


def _item(
    view: DownloadView,
    live: dict[str, progress.Progress] | None = None,
    files: dict[str, AssetView] | None = None,
    kept: dict[str, int] | None = None,
    folders: dict[str, DownloadFolder] | None = None,
    names: dict[str, str] | None = None,
) -> DownloadItem:
    reading = (live or {}).get(view.id)
    if reading is None and view.id in (kept or {}):
        # A paused row after a restart: what is on disk is the figure; no total or rate is claimed.
        reading = progress.Progress(done_bytes=(kept or {})[view.id])
    file = (files or {}).get(view.asset_id or "")
    return DownloadItem(
        id=view.id,
        status=view.status,
        dest_folder_id=view.dest_folder_id,
        site=view.site,
        username=view.username,
        asset_id=view.asset_id,
        error=view.error,
        created_at=view.created_at,
        error_code=view.error_code,
        error_tier=view.error_tier,
        sentence=_failure_said(view),
        via=view.via,
        via_address=view.via_address,
        site_key=view.site_key,
        site_name=view.site_name,
        creator_scope=view.creator_scope,
        url=view.url,
        shown_url=view.shown_url,
        filename=_named(view.asset_id, file, names),
        size_bytes=file.asset.size_bytes if file is not None else None,
        remembered_filename=view.remembered_filename,
        finished_at=view.finished_at,
        site_id=view.site_id,
        person_id=view.person_id,
        # Only while running or held: a bar under a settled row would be a stale number.
        progress=(
            DownloadProgress(
                done_bytes=reading.done_bytes,
                total_bytes=reading.total_bytes,
                total_is_estimated=reading.total_is_estimated,
                done_files=reading.done_files,
                total_files=reading.total_files,
                bytes_per_second=reading.bytes_per_second,
                seconds_left=reading.seconds_left,
            )
            if reading is not None and view.status in ("running", "paused")
            else None
        ),
        job_id=view.job_id,
        files_offered=view.files_offered,
        files_left_out=view.files_left_out,
        reads_refused=view.reads_refused,
        position=view.position,
        folder=(folders or {}).get(view.id),
    )


#: The states in which the job has recorded the folder it writes into.
_FOLDER_RESOLVED = frozenset({"running", "done", "duplicate"})


async def _folders_of(
    views: Sequence[DownloadView],
    options: SiteOptionStore | None,
    library: LibraryStore,
) -> dict[str, DownloadFolder]:
    """The folder each download goes into, by row id: the recorded one, else the rule's."""
    per_site: dict[str | None, str | None] = {}
    going_to: dict[str, str] = {}
    for view in views:
        recorded = view.folder_id if view.status in _FOLDER_RESOLVED else None
        folder_id = recorded or view.dest_folder_id
        if not folder_id and options is not None:
            if view.site_key not in per_site:
                per_site[view.site_key] = (await options.resolve(view.site_key)).dest_folder_id
            folder_id = per_site[view.site_key]
        if folder_id:
            going_to[view.id] = folder_id
    roots: dict[str, Root | None] = {}
    named: dict[str, DownloadFolder | None] = {}
    for folder_id in set(going_to.values()):
        named[folder_id] = await _named_folder(library, folder_id, roots)
    return {row_id: found for row_id, folder_id in going_to.items() if (found := named[folder_id])}


async def _named_folder(
    library: LibraryStore, folder_id: str, roots: dict[str, Root | None]
) -> DownloadFolder | None:
    """One folder's name and directory, joined as the import joins it; None once it is gone."""
    folder = await library.get_folder(folder_id)
    if folder is None:
        return None
    if folder.root_id not in roots:
        roots[folder.root_id] = await library.get_root(folder.root_id)
    root = roots[folder.root_id]
    if root is None:
        return None
    directory = Path(root.abs_path) / folder.rel_path if folder.rel_path else Path(root.abs_path)
    return DownloadFolder(id=folder.id, name=folder.name, path=str(directory))


#: The aim's id for a drop on Favorites, a place; NULL would read as not aimed at all.
_THE_HEART = "favorite"


async def _resolve_aim(
    access: Repository, viewer: Viewer, kind: str, target_id: str | None
) -> None:
    """Refuse a drop on nothing, or on something this user may not be shown (404, as everywhere)."""
    if kind == "favorite":
        if target_id is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "favorites is a place, not a thing to name"
            )
        return
    if not target_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "that kind of drop names what it landed on"
        )
    found: object | None
    if kind == "person":
        found = await access.visible_person(viewer, target_id)
    elif kind == "site":
        found = await access.visible_site(viewer, target_id)
    elif kind == "collection":
        found = await access.visible_collection(viewer, target_id)
    elif kind == "photo_set":
        found = await access.visible_photo_set(viewer, target_id)
    elif kind == "song":
        found = await access.visible_song(viewer, target_id)
    else:
        found = await access.visible_tag(viewer, target_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such thing to drop this on")


@router.post(
    "/downloads", status_code=status.HTTP_201_CREATED, dependencies=[Depends(csrf_protect)]
)
async def submit_download(
    body: SubmitDownloadRequest,
    service: Annotated[DownloadService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SubmittedDownload:
    """Queue a download; what it was dropped on is resolved here, while there is a viewer."""
    url = body.url.strip()
    problem = _not_an_address(url)
    if problem is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem)
    if body.aimed_kind is not None:
        await _resolve_aim(access, viewer, body.aimed_kind, body.aimed_id)
    elif body.aimed_id is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "an aimed id needs the kind of thing it names"
        )
    download_id = await service.submit_url(
        url=url,
        dest_folder_id=body.dest_folder_id,
        aimed_kind=body.aimed_kind,
        aimed_id=body.aimed_id or _THE_HEART,
        aimed_by=viewer.id if body.aimed_kind else None,
        choices=PasteChoices(remember=body.remember),
        requested_by=viewer.id,
    )
    return SubmittedDownload(id=download_id)


@router.post(
    "/downloads/links", status_code=status.HTTP_201_CREATED, dependencies=[Depends(csrf_protect)]
)
async def submit_links(
    body: PasteLinksRequest,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> PastedLinks:
    """Queue several pasted links, one download each; a line that is not a link is skipped."""
    seen: set[str] = set()
    refused: list[RefusedLink] = []
    duplicates = 0
    taking: list[str] = []
    left_over: list[str] = []
    for line in body.urls:
        url = line.strip()
        if not url:
            continue
        problem = _not_an_address(url)
        if problem is not None:
            refused.append(RefusedLink(url=url[:200], reason=problem))
            continue
        # Compared as it will be fetched: two links differing in a tracking parameter are one.
        key = url_hash(url)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        if len(taking) >= MAX_PASTED_LINKS:
            left_over.append(url)
            continue
        taking.append(url)

    queued = 0
    choices = PasteChoices(remember=body.remember)
    for url in taking:
        try:
            await service.submit_url(
                url=url,
                dest_folder_id=body.dest_folder_id,
                choices=choices,
                requested_by=viewer.id,
            )
        except Exception as refusal:  # one link's failure is one link's failure
            # Anything else would answer 500 and say nothing about the links already queued.
            log.warning("downloads.paste_refused", error=str(refusal))
            refused.append(RefusedLink(url=url[:200], reason="That link could not be queued."))
            continue
        queued += 1
    return PastedLinks(queued=queued, refused=refused, duplicates=duplicates, left_over=left_over)


def _not_an_address(url: str) -> str | None:
    """Why this line cannot be a download, or None; the far end is the job's question."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in SAFE_SCHEMES:
        return "Only http and https links can be downloaded."
    if not parts.netloc:
        return "That line has no website in it."
    return None


@router.post("/downloads/bulk-preview", dependencies=[Depends(csrf_protect)])
async def preview_bulk(
    body: BulkRequest,
    viewer: Annotated[Viewer, Depends(require_admin)],
    router_: Annotated[EgressRouter, Depends(_egress)],
    policy: Annotated[RunPolicy, Depends(_policy)],
) -> BulkPreview:
    """Count what is behind a playlist or channel address without fetching or queueing any of it."""
    site = _bulk_site(body.url)
    try:
        async with router_.route_for(body.url) as proxy:
            found = await bulk.find_items(body.url, proxy=proxy, policy=policy)
    except bulk.BulkRefused as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except TunnelError as exc:
        raise _refused(exc) from exc
    # The count is what will be queued; `truncated` says the Site held more.
    return BulkPreview(
        site=site.site,
        count=min(len(found), bulk.MAX_ITEMS),
        truncated=len(found) > bulk.MAX_ITEMS,
        limit=bulk.MAX_ITEMS,
    )


@router.post(
    "/downloads/bulk", status_code=status.HTTP_201_CREATED, dependencies=[Depends(csrf_protect)]
)
async def queue_bulk(
    body: BulkRequest,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    router_: Annotated[EgressRouter, Depends(_egress)],
    policy: Annotated[RunPolicy, Depends(_policy)],
) -> BulkQueued:
    """Queue everything behind a playlist or channel address, listed again, one download each."""
    _bulk_site(body.url)
    try:
        async with router_.route_for(body.url) as proxy:
            found = await bulk.find_items(body.url, proxy=proxy, policy=policy)
    except bulk.BulkRefused as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except TunnelError as exc:
        raise _refused(exc) from exc
    taking = found[: bulk.MAX_ITEMS]
    choices = PasteChoices(remember=body.remember)
    for item in taking:
        await service.submit_url(
            url=item, dest_folder_id=body.dest_folder_id, choices=choices, requested_by=viewer.id
        )
    return BulkQueued(queued=len(taking))


@router.get("/supported-sites")
async def supported_sites(
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> list[SupportedSite]:
    """Every Site that Sift has a record for, and what it can do with each."""
    return [
        SupportedSite(
            key=record.key,
            name=record.site,
            hosts=list(record.hosts),
            media=list(record.media),
            bulk=record.bulk,
            supported=record.supported,
            tested=record.tested,
            names_creators=record.username_is_a_person,
            default_naming=record.default_naming,
            name_words=list(words_filled(record)),
            walls=[wall.sentence for wall in record.walls],
            cookies=record.cookies.value,
            cookies_why=record.cookies_why,
            cookies_with_a_tool=(
                record.cookies_with_a_tool.value if record.cookies_with_a_tool is not None else None
            ),
        )
        for record in sorted(SITES, key=lambda one: one.site.lower())
    ]


SiteName = Annotated[str, StringConstraints(min_length=1, max_length=200)]

#: A ceiling on one read's narrowing, far past any real queue.
MAX_SITES_NARROWED = 50

#: The filter panel's dimensions; the state is the tab strip, not a column.
DOWNLOAD_FACETS = ("site",)


@router.get("/downloads/glance")
async def downloads_at_a_glance(
    request: Request,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> DownloadsAtAGlance:
    """What the Downloads row on the rail says, read from the download rows."""
    paused = await part_of(request, SETTINGS_HUB).get_app(PAUSED_KEY) is True
    facts = await service.rail(paused=paused)
    return DownloadsAtAGlance(
        downloading=facts.downloading,
        waiting_for_cookies=facts.waiting_for_cookies,
        landed_unseen=facts.landed_unseen,
        failed_unseen=facts.failed_unseen,
    )


@router.post(
    "/downloads/seen", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(csrf_protect)]
)
async def mark_downloads_seen(
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Put out the dot on how downloads ended, in every window; nothing is removed."""
    await service.mark_seen()


@router.get("/downloads/facets")
async def download_facets(
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    facet: Annotated[str, Query()],
    show: Annotated[DownloadShow, Query()] = "all",
) -> FacetCounts:
    """The queue's counts along one dimension; declared before `/downloads/{download_id}`."""
    if facet not in DOWNLOAD_FACETS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown facet {facet!r}")
    counted = await service.site_counts(show)
    return FacetCounts(
        facet=facet,
        values=[FacetValue(value=one.name, count=one.count, label=None) for one in counted],
    )


@router.get("/downloads")
async def list_downloads(
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    access: Annotated[Repository, Depends(wiring.access)],
    watching: Annotated[progress.Registry, Depends(_watching)],
    workspaces: Annotated[Workspaces | None, Depends(_workspaces)],
    library: Annotated[LibraryStore, Depends(wiring.library)],
    options: Annotated[SiteOptionStore | None, Depends(_options_or_none)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    show: Annotated[DownloadShow, Query()] = "all",
    site: Annotated[list[SiteName] | None, Query(max_length=MAX_SITES_NARROWED)] = None,
    q: Annotated[str, Query(max_length=200)] = "",
    sort: Annotated[DownloadSort, Query()] = "newest",
) -> DownloadsPage:
    """A page of the download queue, narrowed and ordered on the server, with its summary."""

    async def files_of(asset_ids: Sequence[str]) -> dict[str, FileFacts]:
        seen = await access.assets_of(viewer, asset_ids)
        return {
            key: FileFacts(name=one.asset.original_filename, size=one.asset.size_bytes)
            for key, one in seen.items()
        }

    page = await service.list_downloads(
        limit=limit,
        offset=offset,
        narrowing=DownloadNarrowing(show=show, sites=tuple(site or ()), search=q, sort=sort),
        files_of=files_of,
    )
    live = watching.all()
    kept = await service.kept_on_disk(page.downloads, workspaces, measured=live.keys())
    files = await access.assets_of(
        viewer, [view.asset_id for view in page.downloads if view.asset_id]
    )
    # Named as on disk now: a file renamed after it lands no longer has its imported name.
    names = await access.names_on_disk(viewer, list(files))
    folders = await _folders_of(page.downloads, options, library)
    return DownloadsPage(
        downloads=[_item(view, live, files, kept, folders, names) for view in page.downloads],
        total=page.total,
        summary=_summary(page.running, page.queued, live, page.by_state),
        matched=page.matched,
        counts=page.counts,
        sites=[DownloadSiteCount(name=one.name, count=one.count) for one in page.sites],
    )


def _summary(
    running: int, queued: int, live: dict[str, progress.Progress], by_state: dict[str, int]
) -> QueueSummary:
    """The line above the list; time left only when every running download knows its total."""
    rate = sum(one.bytes_per_second or 0.0 for one in live.values())
    remaining = [one.seconds_left for one in live.values()]
    left = max((one for one in remaining if one is not None), default=None)
    if any(one is None for one in remaining):
        left = None
    return QueueSummary(
        running=running, queued=queued, bytes_per_second=rate, seconds_left=left, by_state=by_state
    )


router.include_router(router_actions.router)
router.include_router(router_connections.router)


def _tunnels(request: Request) -> TunnelStore:
    return part_of(request, TUNNELS)


def _needs_a_key(key: bytes | None) -> bytes:
    """The master key a tunnel's configuration is sealed under, or a 409 asking for it."""
    if key is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your saved cookies and tunnels are locked. Enter your password in the box at the top "
            "of this page to unlock them, then import the tunnel again.",
        )
    return key


@router.get("/tunnels")
async def list_tunnels(
    store: Annotated[TunnelStore, Depends(_tunnels)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> list[TunnelItem]:
    """Every tunnel that has been set up, with what it is actually doing folded in."""
    return [
        TunnelItem(
            id=view.id,
            name=view.name,
            enabled=view.enabled,
            running=view.running,
            up=view.up,
            draining=view.draining,
            last_handshake_at=view.last_handshake_at,
            endpoint=view.endpoint,
            problem=view.problem,
            can_host=view.can_host,
        )
        for view in await store.list()
    ]


@router.post("/tunnels", status_code=status.HTTP_201_CREATED, dependencies=[Depends(csrf_protect)])
async def import_tunnel(
    body: ImportTunnelRequest,
    store: Annotated[TunnelStore, Depends(_tunnels)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    key: Annotated[bytes | None, Depends(master_key)],
) -> ImportedTunnel:
    """Import a provider's configuration as a named tunnel, checked first; not started."""
    try:
        tunnel_id = await store.add(
            name=body.name, config=body.config, master_key=_needs_a_key(key)
        )
    except TunnelError as exc:
        raise _refused(exc) from exc
    announce_now(EVERY_ADMIN, About.SETTINGS)
    return ImportedTunnel(id=tunnel_id)


@router.post(
    "/tunnels/{tunnel_id}/config",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def replace_tunnel_config(
    tunnel_id: str,
    body: ReplaceTunnelConfigRequest,
    store: Annotated[TunnelStore, Depends(_tunnels)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    key: Annotated[bytes | None, Depends(master_key)],
) -> None:
    """Swap in a reissued configuration; a running tunnel restarts on it."""
    try:
        await store.replace_config(tunnel_id, config=body.config, master_key=_needs_a_key(key))
    except TunnelError as exc:
        raise _refused(exc) from exc
    announce_now(EVERY_ADMIN, About.SETTINGS)


@router.patch(
    "/tunnels/{tunnel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def rename_tunnel(
    tunnel_id: str,
    body: RenameTunnelRequest,
    store: Annotated[TunnelStore, Depends(_tunnels)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Rename a tunnel; routes point at its id, so nothing else changes."""
    await store.rename(tunnel_id, body.name)
    announce_now(EVERY_ADMIN, About.SETTINGS)


@router.post(
    "/tunnels/{tunnel_id}/start",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def start_tunnel(
    tunnel_id: str,
    store: Annotated[TunnelStore, Depends(_tunnels)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    key: Annotated[bytes | None, Depends(master_key)],
) -> None:
    """Turn a tunnel on, answering only once the far end has answered."""
    try:
        await store.start(tunnel_id, _needs_a_key(key))
    except TunnelError as exc:
        raise _refused(exc) from exc
    announce_now(EVERY_ADMIN, About.SETTINGS)


@router.post(
    "/tunnels/{tunnel_id}/stop",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def stop_tunnel(
    tunnel_id: str,
    body: StopTunnelRequest,
    store: Annotated[TunnelStore, Depends(_tunnels)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Turn a tunnel off, letting its downloads finish unless `now`; refused while hosting."""
    try:
        await store.stop(tunnel_id, drain=not body.now)
    except TunnelHosting as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    announce_now(EVERY_ADMIN, About.SETTINGS)


@router.delete(
    "/tunnels/{tunnel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def delete_tunnel(
    tunnel_id: str,
    store: Annotated[TunnelStore, Depends(_tunnels)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Remove a tunnel; Sites routed through it then refuse. Refused while it hosts a swap."""
    try:
        await store.remove(tunnel_id)
    except TunnelHosting as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    announce_now(EVERY_ADMIN, About.SETTINGS)


@router.get("/download-routes")
async def read_routes(
    store: Annotated[TunnelStore, Depends(_tunnels)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> RoutesResponse:
    """The default route and every site that has one of its own."""
    routes = await store.routes()
    return RoutesResponse(
        default=routes.pop(DEFAULT_SCOPE, DIRECT),
        sites=routes,
        available=[SiteChoice(key=record.key, name=record.site) for record in SITES],
    )


@router.put(
    "/download-routes/{scope}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def set_route(
    scope: str,
    body: RouteRequest,
    store: Annotated[TunnelStore, Depends(_tunnels)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Point a Site, or everything, at a way out; both the scope and the tunnel must exist."""
    if scope != DEFAULT_SCOPE and not any(record.key == scope for record in SITES):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no site by that name.")
    if body.route != DIRECT and not any(view.id == body.route for view in await store.list()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no tunnel by that name.")
    await store.set_route(scope, body.route)
    announce_now(EVERY_ADMIN, About.SETTINGS)


@router.delete(
    "/download-routes/{scope}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def clear_route(
    scope: str,
    store: Annotated[TunnelStore, Depends(_tunnels)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Put a Site back to following the default; the default itself cannot be cleared."""
    if scope == DEFAULT_SCOPE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "The default has to be one thing or the other \u2014 set it to Direct instead.",
        )
    await store.clear_route(scope)
    announce_now(EVERY_ADMIN, About.SETTINGS)


router.include_router(router_site_options.router)


@router.get("/downloads/{download_id}/files")
async def files_of_download(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    access: Annotated[Repository, Depends(wiring.access)],
) -> DownloadFiles:
    """Every file one paste produced, named, asked for when a row is opened."""
    ids = await service.files_of(download_id)
    found = await access.assets_of(viewer, ids)
    names = await access.names_on_disk(viewer, list(found))
    return DownloadFiles(
        files=[
            DownloadFile(
                asset_id=asset_id,
                filename=_named(asset_id, found[asset_id], names),
                size_bytes=found[asset_id].asset.size_bytes,
            )
            for asset_id in ids
            if asset_id in found
        ]
    )


def _art(request: Request) -> ArtStore:
    return part_of(request, SITE_ART)


@router.get("/creator-art")
async def creators_with_a_picture(
    store: Annotated[ArtStore, Depends(_art)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> CreatorsWithArt:
    """Every name Sift has a creator picture for, so a screen of People asks once."""
    return CreatorsWithArt(usernames=await store.creators_with_art())


@router.get("/creator-art/{username}")
async def creator_art(
    username: str,
    store: Annotated[ArtStore, Depends(_art)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    site: Annotated[str | None, Query(max_length=200)] = None,
    address: Annotated[str | None, Query(max_length=2000)] = None,
) -> FileResponse:
    """The picture kept for one creator by name, or for that username on `site` alone."""
    if site is not None:
        scope = creator_scope_for(site=site, username=username, address=address)
        known = await store.known(scope) if scope is not None else None
    else:
        known = await store.for_creator(username)
    if known is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no picture for that name.")
    # Always the cover door's own JPEG, never a file a Site sent.
    return FileResponse(known.path, media_type="image/jpeg")
