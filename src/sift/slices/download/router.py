# SPDX-License-Identifier: AGPL-3.0-or-later
"""The download endpoints. Admin-only, all of them, enforced here on the server.

This whole slice is admin-only and permanently so. Downloading reaches out from the server to the
internet, holds the sites' saved cookies, and shows a queue that is a picture of what is being
fetched: none of it is a guest's, and there is no setting that opens it to one. Every route below
depends on `require_admin`, which refuses a guest called directly with no interface in the way. The
guest-facing ability to keep a copy of a file is a different feature and lives elsewhere.

Cookies are accepted and never returned. `POST /site-connections` takes a jar and seals it; nothing
here has a response field it could come back through, and every answer about one is derived facts:
how many, which sites, when they run out, when they were last used.

The word is cookies, never a login. Sift holds no account name and no password for any site: an
export from a browser is the whole of what it is given. The route names keep the word
`site-connections`, which names the row rather than what is in it and is what every client asks
for; renaming an address would need a redirect of its own.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Annotated, NamedTuple
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
from sift.kernel.ledger import Actor
from sift.kernel.library_write import LibraryWriteRefused, check_folder_may_change
from sift.kernel.log import get_logger
from sift.kernel.tunnels import (
    DEFAULT_SCOPE,
    DIRECT,
    DIRECT_LABEL,
    EGRESS,
    TUNNELS,
    EgressRouter,
    TunnelError,
    TunnelHosting,
    TunnelStore,
)
from sift.kernel.wire import FacetCounts, FacetValue
from sift.kernel.wiring import SETTINGS_HUB, part_of, part_or_none
from sift.slices.auth import csrf_protect, master_key, require_admin
from sift.slices.download import bulk, naming, tools
from sift.slices.download.art import SITE_ART, ArtStore, creator_scope_for
from sift.slices.download.models import (
    MAX_PASTED_LINKS,
    BulkPreview,
    BulkQueued,
    BulkRequest,
    ConnectionCheck,
    ConnectionItem,
    CookiePreview,
    CreatorsWithArt,
    DownloaderChoice,
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
    NamePreview,
    NamePreviewRequest,
    PastedLinks,
    PasteLinksRequest,
    PreviewConnectionRequest,
    QueueSummary,
    RefusedLink,
    RenameTunnelRequest,
    ReplaceTunnelConfigRequest,
    RouteRequest,
    RoutesResponse,
    SaveConnectionRequest,
    SavedConnection,
    SetSiteOptionsRequest,
    SiteChoice,
    SiteOptionItem,
    SiteOptionsResponse,
    StopTunnelRequest,
    SubmitDownloadRequest,
    SubmittedDownload,
    SupportedSite,
    TunnelItem,
)
from sift.slices.download.service import (
    PAUSED_KEY,
    SERVICE,
    DownloadNarrowing,
    DownloadService,
    DownloadShow,
    DownloadSort,
    DownloadView,
    FileFacts,
    PasteChoices,
    site_name_of,
)
from sift.slices.download.site_options import DEFAULT_SCOPE as OPTIONS_DEFAULT
from sift.slices.download.site_options import SITE_OPTIONS, SiteOptionStore
from sift.slices.download.sources import cookie_health, failures, progress
from sift.slices.download.sources.cookies import CookieInvalid, header_for, understand
from sift.slices.download.sources.net import guarded_session
from sift.slices.download.sources.normalize import url_hash
from sift.slices.download.sources.policy import read_policy
from sift.slices.download.sources.progress import PROGRESS
from sift.slices.download.sources.registry import CHOOSABLE_DOWNLOADERS, is_a_downloader
from sift.slices.download.sources.sites import catalog
from sift.slices.download.sources.sites.catalog import SITES, SiteRecord, words_filled
from sift.slices.download.sources.tuning import RunPolicy
from sift.slices.download.url_guard import SAFE_SCHEMES

router = APIRouter(tags=["download"])

# Which tools the downloader runs and what version each is: a module of its own, because it asks
# the tools and a release feed rather than the queue; included so the slice mounts one router.
router.include_router(tools.router)

log = get_logger(__name__)


def _service(request: Request) -> DownloadService:
    return part_of(request, SERVICE)


def _egress(request: Request) -> EgressRouter:
    """The same router the download job uses, so a listing request goes out the way that site's
    downloads do rather than through a second wiring that could differ."""
    return part_of(request, EGRESS)


async def _policy(request: Request) -> RunPolicy:
    """What the tools are told, read live from the preferences.

    The listing request below is a request to the same site that is about to receive a great many
    more, so it is paced by the numbers a download from that site would be paced by.
    """
    return await read_policy(part_of(request, SETTINGS_HUB).get_app)


def _bulk_site(url: str) -> SiteRecord:
    try:
        return bulk.bulk_site(url)
    except bulk.BulkRefused as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


def _watching(request: Request) -> progress.Registry:
    """What is in flight right now. Held on the application, never written down. See the module
    that owns it for why a running transfer is the one thing not worth a row in a table."""
    return part_of(request, PROGRESS)


def _workspaces(request: Request) -> Workspaces | None:
    """Where each job keeps what it has half-written: the pool's own, so a paused row is measured
    in the directory its job wrote into. None where no pool runs, and a paused row then says what it
    said before (no figure) rather than a route failing for want of one."""
    pool = part_or_none(request, wiring.POOL)
    return pool.workspaces if pool is not None else None


def _options_or_none(request: Request) -> SiteOptionStore | None:
    """What each site does differently, or None where none is wired: a row then names only a
    folder somebody chose for it."""
    return part_or_none(request, SITE_OPTIONS)


def _named(
    asset_id: str | None, file: AssetView | None, names: dict[str, str] | None
) -> str | None:
    """What a download's file is called: the name on disk now, and the name it arrived under only
    while no copy is anywhere Sift can currently see. None once the file is gone from the library.

    The distinction is the access layer's (`names_on_disk`): the imported name is written once and
    never again, so after a rename (a person's, or the pass that takes the download tool's id
    off) it is a name that exists nowhere, and a row labelled with it reads as Sift having lost
    the file. One rule for the list and for a row's own file list, so the two never disagree.
    """
    if file is None:
        return None
    return (names or {}).get(asset_id or "") or file.asset.original_filename


def _failure_said(view: DownloadView) -> str | None:
    """What a failed row SAYS, worded when it is shown from the code it recorded and its site.

    A sentence composed when a failure happened and stored would keep its words for good, however
    stale. So saved text is rendered when shown: wherever a code was recorded the row says what
    this build knows about that code on that site today, in three steps, the most specific first:

    - **The site's own reading of the code**, from its record now (`failures.reading_now` at tier
      3): "Pornhub answered 410 Gone: ..." where 410 means something else on most servers.
    - **The table of codes this application words itself** (`sentences.download_failed`): a status
      said with what it means, cookies rather than a login.
    - **The failure reader's own sentence for the code**: a status's phrase, a wall of that kind on
      that site, a condition.

    A tunnel is named where the reading says one gets past the refusal and this download went out
    DIRECTLY, which the row recorded (`via`), so it is a fact about that download rather than
    about today's setting. Only for a status: a wall's own sentence already says it.

    None (the stored sentence, drawn by the client) only where no code was recorded, or where
    the code alone cannot say it (a code this build has never heard of). A codeless sentence that
    was retired since it was stored is said as the same failure is stored today
    (`sentences.failure_today`).
    """
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
        # A paused row after a restart: the registry's figure went with the process and the bytes
        # did not. What is on disk is the same fact the figure reported, read at its source; the
        # total and the rate belong to a fetch that is not running, so neither is claimed.
        reading = progress.Progress(done_bytes=(kept or {})[view.id])
    file = (files or {}).get(view.asset_id or "")
    return DownloadItem(
        id=view.id,
        status=view.status,
        dest_folder_id=view.dest_folder_id,
        site=view.site,
        username=view.username,
        asset_id=view.asset_id,
        # Already plain-language and redacted by the time it was written to the ledger.
        error=view.error,
        created_at=view.created_at,
        error_code=view.error_code,
        error_tier=view.error_tier,
        # Worded when shown, from the recorded code and the site. See `_failure_said`.
        sentence=_failure_said(view),
        via=view.via,
        via_address=view.via_address,
        site_key=view.site_key,
        site_name=view.site_name,
        creator_scope=view.creator_scope,
        url=view.url,
        shown_url=view.shown_url,
        # The name on disk now; the imported name only while no copy is anywhere Sift can see.
        filename=_named(view.asset_id, file, names),
        size_bytes=file.asset.size_bytes if file is not None else None,
        remembered_filename=view.remembered_filename,
        finished_at=view.finished_at,
        site_id=view.site_id,
        person_id=view.person_id,
        # Only while it is running or held. A settled row carries its status, and a bar left under a
        # finished download would be a stale number that reads as authoritative; a paused row's
        # figure is what is on disk, which is the one thing it can truthfully say ("8.1 of 226 MB
        # kept"), and the registry keeps it for exactly that.
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
        # Only ever a number for a row that is waiting: the ledger's read leaves it null for
        # every other row, so nothing here has to decide what a position means for a download that
        # has already started or already finished.
        position=view.position,
        folder=(folders or {}).get(view.id),
    )


#: The states in which a row's job has already resolved the folder it writes into, so the row names
#: the one the job recorded rather than the one the rule gives now. `running` as well as the landed
#: two: the job writes the record before it fetches, so a setting changed mid-download does not
#: move the folder the row says it is saving to, and does not move the file either.
_FOLDER_RESOLVED = frozenset({"running", "done", "duplicate"})


async def _folders_of(
    views: Sequence[DownloadView],
    options: SiteOptionStore | None,
    library: LibraryStore,
) -> dict[str, DownloadFolder]:
    """The folder each download goes into, by row id: named, and where it is on disk.

    A row whose job has resolved its folder (running, or landed) names THAT folder, the one the
    job recorded (`DownloadService.record_folder`). The setting that chose it can have changed
    since, and a landed row is a record of where the file went, not of where the setting points.

    Every other row (waiting, paused, failed, cancelled) names the folder it WILL use, by the
    same rule the download follows, asked of the same place: the folder chosen for this one, else
    what `SiteOptionStore.resolve` answers for its Site: the Site's own folder, else the one for
    everything. `resolve` is the one author of that fallback; the job calls it with the key
    `match_site` gives the address, and `site_key` on a row is that same key. A paused or failed row
    is on the rule and not on its record because running it again resolves the folder again: the
    record says where the LAST attempt was going, and the next one goes where the rule says.

    A landed row older than the recorded column falls back to the rule too. That is the one place
    the answer can be wrong (a setting changed since it landed), and it is kept because the
    alternative is a finished row naming no folder at all; it ends as those rows age out.

    Asked once per Site and once per folder rather than once per row: a page of twenty downloads
    from two Sites into one folder is two reads of the settings and one of the folder.
    """
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
    """One folder's name and its directory, or None for a folder or a library that has gone.

    The directory is joined the way the import joins it (`capture.pipeline`: the library's path,
    then the folder's path inside it) and never resolved against the disk: this labels a row.
    """
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


# --- Downloads --------------------------------------------------------------------------------


#: What stands in the id column for a drop on Favorites, which is a PLACE and not a row.
#:
#: A real value rather than NULL, because the ledger's read treats a missing third of the aim as
#: "not aimed anywhere", which is the reading that cannot go wrong for two columns of a pair and
#: would silently discard every drop on the heart.
_THE_HEART = "favorite"


async def _resolve_aim(
    access: Repository, viewer: Viewer, kind: str, target_id: str | None
) -> None:
    """Refuse a drop on something this user may not be shown, or on nothing at all.

    404 for a target that is not there or not theirs, which is the answer every other route in Sift
    gives for both, so aiming at ids teaches nothing about what the library holds.

    Favorites is the one kind with no row behind it: it is a place, the heart is this user's own,
    and there is nothing to resolve. An id sent with it is refused rather than ignored, because a
    caller that sent one meant something this cannot do.
    """
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
    """Queue a download. The guards (the address check, the skip-a-re-drop check) run in the job
    that follows, so a paste, a drop, and a re-run all go through the same checks in the same order.

    A link dropped ON something also says what it was dropped on. That is resolved HERE, while there
    is a request and a viewer to resolve it against: the job that files it runs minutes later with
    neither, so a check made there would either be no check at all or a second permission model.

    **The shape is checked here first, by the same rule and in the same words as the several-links
    route** (`_not_an_address`), so a line that cannot be a download never gets a row. Only
    the shape: whether the host is private or reachable is still the job's question, for both
    routes, so a paste of one and a paste of many differ in nothing but how many they answer for.
    """
    # Stripped once and that is what is stored, as the several-links route stores each line: a
    # stray space around a pasted link is not part of the address.
    url = body.url.strip()
    problem = _not_an_address(url)
    if problem is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem)
    if body.aimed_kind is not None:
        await _resolve_aim(access, viewer, body.aimed_kind, body.aimed_id)
    elif body.aimed_id is not None:
        # An id with no kind names a row without saying what kind of row it is. Refused rather than
        # ignored: silently dropping half of what somebody sent is how a drop comes to do nothing.
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
    """Queue several pasted addresses, one download each.

    **Not the same thing as the bulk routes below.** Those take ONE address and ask the site what is
    behind it; this one is handed the addresses already and asks nothing. Somebody who has copied a
    list out of a page or a notes file has done the enumerating themselves.

    **One download per link, exactly as a single paste makes.** Every guard, the ledger check, the
    per-site naming and folder, the routing: all of it applies per link through the same call the
    single route uses, so there is no second path to keep in step with the first.

    **One bad line never loses the rest.** A pasted list has a stray word in it or a line that is
    not an address, and refusing the whole paste over one of them is what makes somebody go back to
    pasting them one at a time. Each line that cannot be an address is named and skipped.

    Only the SHAPE is checked here. Whether a host resolves, whether it is reachable, whether it is
    a private address: all of that is the job's business, exactly as it is for a single paste, and
    doing it here would mean a paste of five hundred links making five hundred DNS lookups before
    anything was queued.
    """
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
        # Written down as it was pasted, compared as it will be fetched: two lines differing only
        # in a tracking parameter are one download, and queueing both would fetch the same post
        # twice before the ledger ever saw either of them.
        key = url_hash(url)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        # Counted after the shape check and the duplicate check, so a paste of six hundred lines
        # where a hundred repeat is not reported as being over a limit it never reached.
        if len(taking) >= MAX_PASTED_LINKS:
            left_over.append(url)
            continue
        taking.append(url)

    queued = 0
    # One answer for the whole paste: the two choices were made once, above the box, for all of it.
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
            # The shape check above catches a line that is not an address; anything else that goes
            # wrong while a link is being written down is caught here too, or it would answer 500
            # and say nothing about the links already in ("one bad line lost the paste" by a
            # different door).
            log.warning("downloads.paste_refused", error=str(refusal))
            refused.append(RefusedLink(url=url[:200], reason="That link could not be queued."))
            continue
        queued += 1
    return PastedLinks(queued=queued, refused=refused, duplicates=duplicates, left_over=left_over)


def _not_an_address(url: str) -> str | None:
    """Why this line cannot be a download, or None if it looks like one.

    Cheap and local on purpose. See the route. It answers the question a person can act on
    ("that line is not a link"), and leaves every question about the far end to the job.
    """
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
    """Ask what is behind a playlist or channel address, without fetching any of it.

    Nothing is queued here. Taking everything a creator has posted is a decision, and it is made by
    somebody who has been told the number first, which costs one listing request and no media.

    Goes out the same way a download from that site would: a site routed through a tunnel is asked
    through it, and refuses by name if it is not up.
    """
    site = _bulk_site(body.url)
    try:
        async with router_.route_for(body.url) as proxy:
            found = await bulk.find_items(body.url, proxy=proxy, policy=policy)
    except bulk.BulkRefused as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except TunnelError as exc:
        raise _refused(exc) from exc
    # The count is what will actually be queued, and `truncated` says whether the site held more
    # than that. Reporting the whole number and queueing fewer would be the one thing this step
    # exists to prevent: a decision made against a figure that is not what happens.
    return BulkPreview(
        site=site.site,
        count=min(len(found), bulk.MAX_ITEMS),
        truncated=len(found) > bulk.MAX_ITEMS,
        # Sent rather than left for the screen to know: the sentence about what one go takes is
        # about this build's limit, and a client holding its own copy of the number is a second
        # place it has to be changed.
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
    """Queue everything behind a playlist or channel address, one download per item.

    Listed again rather than trusting a count sent back from a screen: what is queued has to be what
    the site says is there now, and a list that arrived from a client is a list a client chose.

    One download each, never one job for the lot. A failure is then one video rather than all of
    them, each has its own progress and its own retry, and every other part of the slice (the
    ledger, the pacing, the routing) works exactly as it does for a pasted link.
    """
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
    """Every site Sift has a record for, and what it can do with each.

    Reference material rather than a setting: it answers "what happens when I paste a link from
    here", which is a question asked before anything is configured.
    """
    return [
        SupportedSite(
            key=record.key,
            name=record.site,
            hosts=list(record.hosts),
            media=list(record.media),
            bulk=record.bulk,
            supported=record.supported,
            tested=record.tested,
            # The same fact `classify` reads for attribution, so the naming builder and the filing
            # cannot disagree about whether this Site has a creator to name.
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


#: One Site's name in the address, as `sites` lists it. Bounded, like every other word a route reads.
SiteName = Annotated[str, StringConstraints(min_length=1, max_length=200)]

#: How many Sites one read may be narrowed to. Far past the number any queue has held: a ceiling
#: so an address cannot hand the statement an unbounded list, not a limit anybody meets.
MAX_SITES_NARROWED = 50

#: The dimensions the Downloads screen's filter panel has. The state is the tab strip, not a column:
#: two controls for one narrowing would be two answers to "which state is showing".
DOWNLOAD_FACETS = ("site",)


@router.get("/downloads/glance")
async def downloads_at_a_glance(
    request: Request,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> DownloadsAtAGlance:
    """What the Downloads row on the rail says: fetching, waiting for cookies, ended unseen.

    From the download rows, one read, and NOT from the work queue's page, which is the newest fifty
    jobs of every kind: a download that scrolled off it before it ended would never light the dot,
    and one waiting behind a paused queue would turn the glyph as if it were fetching. Re-read when the
    connection says the downloads, the queue or a setting moved: waiting for cookies is the
    job's state, and the pause is a setting.

    Declared before any `/downloads/{download_id}` route, for the reason `facets` gives.
    """
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
    """Somebody has looked at how the downloads ended: the dot goes out, in every window.

    What opening the Downloads screen does, and what "Mark downloads as seen" on the rail row does.
    Nothing is removed: every row stays where it is on the screen. On the rows rather than in one
    window's memory, so a dot put out in one window is out in the others and does not come back on
    a reload.
    """
    await service.mark_seen()


@router.get("/downloads/facets")
async def download_facets(
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    facet: Annotated[str, Query()],
    show: Annotated[DownloadShow, Query()] = "all",
) -> FacetCounts:
    """What the queue is made of along one dimension, with counts: the filter panel's column.

    Declared before any `/downloads/{download_id}` route: routes match in declaration order, and the
    other way round this address would be read as a download called "facets".

    The Site column counts inside the lit tab and NOT inside the Site already chosen, for the reason
    every wall's column does: each value is what ticking it would add. Admin-only with the rest of
    the slice.
    """
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
    """A page of the download queue, narrowed and ordered, each row shown with its live status.

    `show` is the state tab (`needs` is blocked or failed: the rows waiting on a person), `site`
    a Site's name as `sites` lists it (repeated, either of them, which is how the filter panel's
    Site column writes two ticks; a leading minus leaves that Site out), `q` the search box and `sort` the order. Every one of them
    narrows the SERVER's list, so the pager's `matched` and the rows agree however deep the page.

    The summary is worked out from everything in flight rather than from this page, because it
    answers a question about the queue and not about what happens to be on screen: a five hundred
    item paste shows twenty rows, and "how fast is this going" is about all five hundred.

    The bounds are declared rather than checked in the body: same enforcement, and a schema that
    says so, as the jobs listing's are.
    """

    async def files_of(asset_ids: Sequence[str]) -> dict[str, FileFacts]:
        # The one read that decides who may see which file. A file it withholds is absent, and the
        # order then treats that row as having no file, the same thing the row itself shows.
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
    # What each row's file is called and how big it is, asked of the access layer rather than joined
    # in the ledger's own query: one read for the whole page, scoped by the same rule every screen
    # uses. A row whose file has been deleted is simply absent from the answer.
    files = await access.assets_of(
        viewer, [view.asset_id for view in page.downloads if view.asset_id]
    )
    # What each file is called NOW. A file is renamed after it lands (by a person, or by Sift's
    # own pass that takes the download tool's id off the name), and the imported name the asset
    # row keeps is then a name that exists nowhere. The same question the file page asks.
    names = await access.names_on_disk(viewer, list(files))
    # Where each row goes, named. Read per request for the reason the job reads it per download: a
    # folder chosen while the queue is draining is where the NEXT one lands, and a row says so.
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
    """The one line above the list: how many, how fast, and how long is left.

    The time left is the honest one only when every running download knows its own total. One that
    does not would otherwise be treated as instant, and the whole queue would report an ending it
    cannot reach, worse than saying nothing, because a number invites planning around it.
    """
    rate = sum(one.bytes_per_second or 0.0 for one in live.values())
    remaining = [one.seconds_left for one in live.values()]
    left = max((one for one in remaining if one is not None), default=None)
    if any(one is None for one in remaining):
        left = None
    return QueueSummary(
        running=running, queued=queued, bytes_per_second=rate, seconds_left=left, by_state=by_state
    )


@router.post(
    "/downloads/{download_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def cancel_download(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Cancel a download that is queued or running: it stops fetching and leaves the active queue.

    Idempotent: cancelling one that has already finished, or is already cancelled, is a success (204),
    not an error: a repeated click, or a click that races the download finishing, is harmless.
    """
    await service.cancel(download_id, by=viewer.id)


@router.post(
    "/downloads/{download_id}/anyway",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def download_anyway(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Fetch a link that was skipped because it had been fetched before.

    The record of what has already been downloaded is a convenience, not a rule: a file removed from
    the library, or replaced at the other end, is a reason to want it again. Only that one check is
    passed. The address is still checked, and a file the library already holds byte for byte is
    still recognised as a duplicate when it arrives.

    Idempotent, exactly as cancelling is: an id naming no row is a success and does nothing, and a
    link that was never skipped in the first place simply runs. Answering differently for a row that
    is not there would make this route a way of asking which download ids exist.
    """
    await service.fetch_anyway(download_id)


@router.post(
    "/downloads/{download_id}/retry",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def retry_download(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Put a failed, cancelled or quarantined download back in the queue, as itself.

    Idempotent, exactly as cancelling is: one that is running, finished or simply not there is a
    success that does nothing. Answering differently would make this a way of asking which download
    ids exist and what state each is in.
    """
    await service.retry(download_id)


@router.post(
    "/downloads/{download_id}/remove",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def remove_download(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Take a settled download out of the list. The row itself stays.

    **Nothing is deleted and nothing about the file is touched.** The ledger row is what recognises
    a re-pasted link, and the file it produced reads its own history from it; somebody tidying a
    list has asked for neither of those to be undone. So this is a date on the row, and every later
    read of the queue skips it.

    Refused for anything still going. A queued, running or waiting download made to disappear is
    something fetching with nowhere left to watch it or stop it, and the answer says the one thing
    that gets there: cancel it, which settles it, and then it can be put away.

    Idempotent otherwise. Removing a row twice, or removing one a second window has already removed,
    is a success: the same reasoning the delete beside it gives for answering 204 to a row that
    was never there.
    """
    still_going = await service.hide(download_id)
    if still_going is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cancel it first")


@router.post(
    "/downloads/{download_id}/pause",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def pause_download(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Stop a download that is running or waiting, keeping what has already arrived.

    What it is NOT is a cancel. The bytes stay where they are, the row holds its place, and Resume
    picks the fetch up from where it stopped, which is why this refuses rather than shrugging:
    every other verb on this row is idempotent because doing it twice is harmless, and telling
    somebody a download is paused when it has in fact finished is a screen that is simply wrong.

    Refused for everything else with the one sentence that is true of all of them. A download that
    is done, failed, cancelled or waiting for cookies is already stopped, by itself or by something
    that has to be fixed rather than resumed.
    """
    if not await service.pause(download_id, by=viewer.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "This download is not running")


@router.post(
    "/downloads/{download_id}/resume",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def resume_download(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Start a paused download again, from what it had already fetched.

    It goes back into the line with the priority and the attempts it had, so a download paused
    halfway is not sent to the back for having been paused. Refused for anything that is not
    paused, for the reason the pause beside it is: the answer has to be true.
    """
    if not await service.resume(download_id, by=viewer.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "This download is not paused")


@router.post(
    "/downloads/{download_id}/restore",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def restore_download(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Put a removed download back in the list. The undo of Remove from the list.

    Answered 404 for a row that was not removed, which is the one place in this slice where a verb
    is not idempotent about a row it cannot find, and deliberately. There is no screen listing
    what has been put away, so the only way here is the message that says one just was: an answer
    of "fine" to a press that restored nothing would leave somebody looking at a list for a row
    that is not coming back.
    """
    if not await service.restore(download_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This download is not one that was removed")


@router.post(
    "/downloads/{download_id}/first",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def promote_download(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Move a waiting download to the front of the queue.

    Only one that is still waiting. A download already running cannot be made to have started
    earlier, and this quietly does nothing rather than appearing to reorder something that is not in
    the queue at all.
    """
    await service.promote(download_id)


# --- Site connections -------------------------------------------------------------------------


@router.get("/site-connections")
async def list_connections(
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> list[ConnectionItem]:
    """Every site with saved cookies. Says that they exist, never what they are.

    `state` is decided by the service and sent down already made up, so one rule about dates and
    health lives in one place. A client handed the dates instead would be a second copy of it, going
    wrong quietly, on the screen somebody is reading.
    """
    connections = await service.list_connections()
    return [
        ConnectionItem(
            id=connection.id,
            site=connection.site,
            status=connection.status,
            updated_at=connection.updated_at,
            # The service says this as a plain word and the wire narrows it to the four. The
            # closed set belongs beside the other wire shapes, and `cookie_health` (which is
            # where the rule lives) is a level below the models and cannot import them.
            state=connection.state,  # type: ignore[arg-type]
            expires_at=connection.expires_at,
            expires_last=connection.expires_last,
            last_used_at=connection.last_used_at,
        )
        for connection in connections
    ]


@router.post("/site-connections/preview", dependencies=[Depends(csrf_protect)])
async def preview_connection(
    body: PreviewConnectionRequest,
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> CookiePreview:
    """Read pasted cookies back to whoever pasted them. Nothing is saved.

    The read-back before Save: how many cookies, which sites they are for, and when they run out.
    It exists because the two downloader tools are no help at all when the file is wrong (one
    refuses it, the other carries on signed out), and both then report the failure days later as a
    sign-in problem, which sends somebody to export cookies that were never the fault.

    **It writes nothing, and it needs no master key.** There is nothing to seal, so this works
    before anybody has unlocked anything, which matters, because pasting the wrong file and
    unlocking are two separate problems and meeting both together is how a form becomes a wall.

    The same reading `POST /site-connections` does, from the same function, so the numbers somebody
    approves are the numbers that get stored rather than a second parse that could differ.
    """
    record = catalog.by_site(body.site)
    try:
        _, summary = await understand(
            body.cookie, domain=record.hosts[0] if record is not None else None
        )
    except CookieInvalid as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return CookiePreview(
        cookies=summary.count,
        domains=list(summary.domains),
        expires_at=summary.expires_at,
        expires_last=summary.expires_last,
        expired=summary.expired,
    )


@router.post(
    "/site-connections",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(csrf_protect)],
)
async def save_connection(
    body: SaveConnectionRequest,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    key: Annotated[bytes | None, Depends(master_key)],
) -> SavedConnection:
    """Save a site's cookies, sealed under the master key.

    Sealing needs the key, which exists only while an admin is signed in with their password. A
    session resumed from a browser cookie after a restart has no key yet, so this asks for a
    password first rather than storing something it cannot protect.

    The jar is read before it is stored, and this is the only moment worth doing it: the downloader
    tools disagree with each other about a malformed one and both end up reporting it days later as
    a sign-in problem, which sends somebody to replace cookies that were never the fault. The short
    form a browser's own tools hand out is converted rather than refused, because it is what most
    people will try first.

    What was read is kept beside the row (how many, and the two expiry dates), so the cookies
    screen can say when they run out without ever unsealing them again.
    """
    if key is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your saved cookies and tunnels are locked. Enter your password in the box at the top "
            "of this page to unlock them, then save the cookies again.",
        )
    record = catalog.by_site(body.site)
    try:
        readable, summary = await understand(
            body.cookie, domain=record.hosts[0] if record is not None else None
        )
    except CookieInvalid as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    connection_id = await service.save_connection(
        site=body.site,
        cookie=readable,
        master_key=key,
        by=viewer.id,
        expires_at=summary.expires_at,
        expires_last=summary.expires_last,
    )
    return SavedConnection(
        id=connection_id,
        cookies=summary.count,
        domains=list(summary.domains),
        expires_at=summary.expires_at,
        expires_last=summary.expires_last,
        expired=summary.expired,
    )


#: When each site was last asked whether its cookies still work, by site key.
#:
#: Process memory, deliberately, exactly as the cookie killswitch beside it is. What a restart does
#: is allow one more check, which costs one request to one site, and the alternative is a column
#: that has to be migrated, read and written for a limit whose whole purpose is to stop somebody
#: leaning on a button.
_CHECKED_AT: dict[str, float] = {}

#: How long one site is left alone between checks. A minute, because the answer cannot change faster
#: than that: what it reads is whether a jar of cookies is still accepted, and a jar does not recover
#: on its own in under a minute. Anything shorter is a button somebody presses twice.
_CHECK_EVERY_SECONDS = 60.0


def forget_checks() -> None:
    """Forget every check's timing: the state a fresh process starts in. For tests, and named so
    that is obvious: nothing on a running system wants this."""
    _CHECKED_AT.clear()


@router.post("/site-connections/{connection_id}/check", dependencies=[Depends(csrf_protect)])
async def check_connection(
    connection_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    router_: Annotated[EgressRouter, Depends(_egress)],
    key: Annotated[bytes | None, Depends(master_key)],
) -> ConnectionCheck:
    """Ask one site, now, whether it still accepts the cookies saved for it.

    **What it proves, and what it cannot.** Sift sends the saved cookies to the site's own home
    address and reads what comes back. A page means the site took them; a refusal means it did not,
    which is the thing worth knowing and the thing nothing else can tell you until the next download
    has already failed. It does NOT prove that any particular post will be served: a post can be
    private, deleted or age-gated with the cookies working perfectly, and this asks the site about
    the cookies rather than about a post. The cheapest request that would prove more is a request
    for something specific, and there is nothing specific to ask for at the moment somebody presses
    a button on a settings screen.

    It goes out the way a download from that site goes out: through the same router, so a site
    routed through a tunnel is asked through it, and refuses by name if the tunnel is not up. Asking
    a site directly that somebody deliberately routes away from the machine's own address would be
    the exact leak the routing exists to prevent.

    One check a minute per site, because the answer cannot change faster than that and a button is
    easy to lean on.

    A site Sift could not reach is not a verdict and is not returned as one: it answers 502 with a
    sentence, and the health written down is left exactly as it was. Condemning a jar of cookies for
    a network that was down for a second is the failure this whole screen exists to avoid.
    """
    if key is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your saved cookies and tunnels are locked. Enter your password in the box at the top "
            "of this page to unlock them, then check again.",
        )
    saved = await service.connection_by_id(connection_id)
    if saved is None or saved.secret_id is None or saved.site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There are no saved cookies to check.")

    record = catalog.by_site(saved.site)
    if record is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Sift has no record of {saved.site}, so it has no address to ask.",
        )
    site_key = record.key
    now = time.monotonic()
    last = _CHECKED_AT.get(site_key)
    if last is not None and now - last < _CHECK_EVERY_SECONDS:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Checked a moment ago. Try again in a minute.",
        )

    jar = await service.open_cookie(saved.secret_id, key)
    if jar is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Sift could not read the cookies saved for {saved.site}. Add them again.",
        )
    host = record.hosts[0]
    try:
        header = header_for(jar, host)
    except CookieInvalid as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if not header:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"None of the saved cookies are for {saved.site}. Export them again from that site.",
        )

    _CHECKED_AT[site_key] = now
    # The Site's NAME in the sentence, never its key: "coomer still accepts" is how the key would
    # read, and the key is Sift's word for the row, not anybody's for the site.
    called = site_name_of(f"https://{host}/") or saved.site
    accepted = await _still_accepted(router_, f"https://{host}/", header)
    if accepted is None:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Sift could not reach {saved.site} just now. Try the check again in a little while.",
        )
    if accepted:
        # The same two writes a working download makes: the row says the cookies are fine, and the
        # in-memory switch that had stopped sending them is released. Without the second, a site
        # whose switch tripped this run would go on being fetched without its cookies however many
        # times somebody proved they work.
        await service.record_login_health(saved.site, cookie_health.SAVED)
        cookie_health.clear_for_site(saved.site)
        return ConnectionCheck(accepted=True, said=f"{called} still accepts these cookies")
    await service.record_login_health(saved.site, cookie_health.NEEDS_COOKIES)
    return ConnectionCheck(
        accepted=False,
        said=(
            f"{called} turned these cookies away. Sign in again in your browser and export a "
            "fresh file."
        ),
    )


async def _still_accepted(router_: EgressRouter, url: str, header: str) -> bool | None:
    """Whether the site served a page to these cookies. None when it could not be reached at all.

    Three answers and not two, because "the site said no" and "nothing answered" are different
    facts with different consequences: the first is worth writing down about the cookies and the
    second is worth writing down about nothing.

    401 and 403 are the no. Everything else that answered at all is a yes, including a redirect,
    which is followed here rather than read as a refusal: sites redirect a perfectly good request
    from the bare host to a canonical one constantly, and treating that as a rejection would
    condemn working cookies on a large share of the internet. Each hop is vetted by the guarded
    session, which is what makes following them safe.
    """
    try:
        async with (
            router_.route_for(url) as proxy,
            guarded_session(proxy=proxy) as session,
            session.get(url, headers={"Cookie": header}) as response,
        ):
            return response.status not in (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            )
    except TunnelError as exc:
        raise _refused(exc) from exc
    except Exception as exc:
        # Everything a network can do, which is a great deal, and none of it says anything about
        # the cookies. Logged once and reported as "could not reach" rather than as a verdict.
        log.info("download.cookie_check_unreachable", detail=str(exc))
        return None


@router.delete(
    "/site-connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def delete_connection(
    connection_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Forget a site's saved cookies and the sealed jar behind them.

    Idempotent: forgetting cookies that are already gone is a success, not a 404. There is no reason
    to distinguish the two for an admin, and answering 204 either way keeps a removed row from
    lingering just because a first request was retried.
    """
    await service.delete_connection(connection_id, by=viewer.id)


# --- Tunnels and routing ------------------------------------------------------------------------


def _tunnels(request: Request) -> TunnelStore:
    return part_of(request, TUNNELS)


def _needs_a_key(key: bytes | None) -> bytes:
    """A tunnel's configuration is sealed under the master key, which exists only while somebody is
    signed in with their password. A session resumed from a browser cookie after a restart has
    none yet."""
    if key is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your saved cookies and tunnels are locked. Enter your password in the box at the top "
            "of this page to unlock them, then import the tunnel again.",
        )
    return key


def _refused(exc: TunnelError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


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
    """Import a provider's configuration as a named tunnel. It is not started by importing it.

    The configuration is checked with the tunnel client's own parser before anything is stored, so
    a file that was never going to work is refused here rather than at the next download from a
    site routed through it.
    """
    try:
        tunnel_id = await store.add(
            name=body.name, config=body.config, master_key=_needs_a_key(key)
        )
    except TunnelError as exc:
        raise _refused(exc) from exc
    # The tunnels moved: every open screen that offers them (the swap's chooser above all) re-reads.
    # Told immediately: the store's own writes are done, and a route holds no transaction of its own.
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
    """Swap in a reissued configuration. A running tunnel restarts on it: the process it is running
    now is still holding the old one."""
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
    """Rename a tunnel. The name is what a site's route is chosen by on a screen, and nothing else
    depends on it: the routes point at the id."""
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
    """Turn a tunnel on, and wait for the far end to answer before saying it is on.

    Returning before the handshake would put a green control over a tunnel carrying nothing, which
    is the one thing worse than an obviously broken one.
    """
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
    """Turn a tunnel off. By default the downloads already on it finish first and no new one may
    take it; `now` stops it immediately, which fails those transfers on purpose. Refused while
    it hosts a swap, with the words to show: the swap is ended first."""
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
    """Remove a tunnel and forget its configuration.

    Sites routed through it keep pointing at it, and their downloads then refuse and say it is
    gone. Idempotent: one that is already removed is a success. Refused while it hosts a swap.
    """
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
    """Point a site (or everything, under the reserved scope) at a way out.

    Both halves are checked. A scope naming no site would store a route nothing ever reads, and a
    route naming no tunnel would refuse every download from that site with nothing on the screen
    explaining why.
    """
    if scope != DEFAULT_SCOPE and not any(record.key == scope for record in SITES):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no site by that name.")
    if body.route != DIRECT and not any(view.id == body.route for view in await store.list()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no tunnel by that name.")
    await store.set_route(scope, body.route)
    # Every screen drawing where a Site's downloads go (`Settings > Tunnels`, a download's own
    # choices) follows the settings bell, as it does for a tunnel added or renamed above.
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
    """Put a site back to following the default. Idempotent, and the default itself cannot be
    cleared: everything has to follow something."""
    if scope == DEFAULT_SCOPE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "The default has to be one thing or the other \u2014 set it to Direct instead.",
        )
    await store.clear_route(scope)
    announce_now(EVERY_ADMIN, About.SETTINGS)


# --- What each site does differently ------------------------------------------------------------


def _options(request: Request) -> SiteOptionStore:
    return part_of(request, SITE_OPTIONS)


@router.get("/site-options")
async def read_site_options(
    store: Annotated[SiteOptionStore, Depends(_options)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SiteOptionsResponse:
    """What everything follows, what each site was given, and the tokens a template may use."""
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
    """Give a site (or everything, under the reserved scope) its naming rule and destination.

    The scope is checked for the same reason a route's is: a scope naming no site would store a
    rule nothing ever reads, which looks exactly like a rule that is being ignored.
    """
    if scope != OPTIONS_DEFAULT and not any(record.key == scope for record in SITES):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no site by that name.")
    _refuse_a_creator_nobody_fills(scope, body.naming)
    if body.dest_folder_id is not None:
        await _check_downloads_can_land_there(library, body.dest_folder_id)
    # Checked here for the same reason the scope above is: a tool name nothing runs would be stored,
    # shown back on the screen as the chosen answer, and ignored by every download, which is
    # indistinguishable from the setting not working.
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
    """What each folder a Site's setting names is called, for its History line: its path in the
    library, or the library folder's own name at the top of it."""
    words: dict[str, str] = {}
    for folder_id in {one for one in folder_ids if one}:
        folder = await library.get_folder(folder_id)
        if folder is not None:
            words[folder_id] = folder.rel_path or folder.name
    return words


def _refuse_a_creator_nobody_fills(scope: str, template: str | None) -> None:
    """Refuse `{creator}` in the naming rule of a Site that never says who posted a file.

    On Discord, a file host or a board the word fills EMPTY on every download, so a rule that
    leads with it names every file the same as a rule without it, and one built only from it and
    the Site's name names them all `Discord.mp4`, `Discord-1.mp4`. Refused where it is saved rather
    than accepted and silently empty a hundred files later.

    One answer, the catalog's `words_filled`: the words a download from the Site can fill, which
    the screen offers and each shipped name is held to. `creator` is in it where the Site's
    uploader is a person (`username_is_a_person`, sent to the screen as `names_creators`), so the
    server refuses exactly what the naming field marks as always empty.

    Whether the rule USES the word is asked of the naming module, which is the one place that
    knows what a token is (its case, its braces): filled with a creator and without one, a rule
    that does not use the word names the file the same both ways. The rule for everything
    (`*default*`) is never refused: it applies to Sites that do name creators too.
    """
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
    """That the folder exists, and that Sift was given permission to write in the library it is in.

    In the server rather than in the screen. The chooser only offers folders inside a library that
    was handed over read-write, and that is a courtesy to whoever is looking at it. This is what
    makes it true of a request that never went near one. Without it the setting stores a folder
    every download into it will be refused by, and the refusal arrives later, somewhere else, on a
    screen that has nothing to do with the choice.
    """
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
    """Put a site back to following the default. The default itself cannot be cleared: everything
    has to follow something."""
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
    """What a template would produce, checked at the setting rather than a hundred files later.

    Against the newest download from the Site, as it was named (`newest_named`), so the example is
    a real one from this library. A Site nothing has been downloaded from yet still needs one, and
    gets a download made up in the Site's own shape (`_EXAMPLES`): a TikTok file arrives as a
    twelve-character code and a file host's as whatever the uploader called it, so one example for
    every Site would
    teach a name no real download from it gets. Each word is filled only where the Site can fill it
    (`words_filled`, the catalog's one answer, which the screen also offers the words from): a
    template using `{posted}` on Instagram previews without a date because Instagram never says
    when something was posted, which is exactly what the files will show.

    A template the Site's settings would refuse is refused here in the same words
    (`_refuse_a_creator_nobody_fills`), so the box says so while it is being typed in rather than
    only when it is saved. An unknown scope falls back to the example for an address Sift has no
    Site for rather than refusing: this is a preview, and the worst an unrecognised name should do
    is show a less specific one.
    """
    _refuse_a_creator_nobody_fills(body.scope or OPTIONS_DEFAULT, body.naming)
    record = catalog.by_key(body.scope) if body.scope else None
    facts = _example_facts(record)
    # The newest finished download from this Site, as it was named: a wholly real example, so the
    # preview reads like this library's files and not like anybody's. A Site whose rows predate
    # the naming facts lends only its creator, and the rest stays invented in the Site's shape.
    # Either way the Site is named as it is named here now, which is what a download from it files
    # under, never the catalog's word for a Site somebody renamed.
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

    #: The name the file arrives with, before any rule: what `{name}` fills with.
    name: str
    #: The post's own ID in the Site's own shape, or None where the Site has none.
    id: str | None = None
    #: The post's title or caption, or None where the Site has none.
    title: str | None = None


#: What a download from each Site looks like when it is named, keyed by catalog key. Invented, and
#: in each Site's own shape: TikTok's 19-digit post number and twelve-character arrival code,
#: Instagram's eleven-character post code, a file host's uploader file name. Every catalog record
#: has an entry: a test holds the two lists together, so a Site added without one fails there
#: rather than previewing as something it is not.
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

#: An address Sift has no Site for, which is what the rule for all Sites governs now that every
#: Site has a name of its own: the site is the address's own label, as the catch-all files it
#: (`registry._site_from_host`), and every word can fill, because yt-dlp's reader for the address
#: may know an ID, a title and a date, and the page read may name a creator.
_ANY_ADDRESS = naming.Facts(
    site="Vimeo",
    username="someone",
    original="A_video_title",
    id="123456789",
    title="A video title",
    posted=date(2026, 8, 13),
)


def _example_facts(record: SiteRecord | None) -> naming.Facts:
    """The made-up download a preview fills a template from, for one Site or for any address.

    A word the Site cannot fill is left empty rather than invented, and the posting date is plainly
    not today's, so `{posted}` cannot pass for `{date}`. One file, so `{n}` is empty here exactly as
    it is on every post of one file.
    """
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


@router.get("/downloads/{download_id}/files")
async def files_of_download(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    access: Annotated[Repository, Depends(wiring.access)],
) -> DownloadFiles:
    """Every file one paste produced, named. Asked for when a row is opened rather than with the
    list, because most rows produced one file and the list is read once a second.

    The ids come from the ledger and the names from the access layer, which is the same division as
    everywhere else here: this slice knows what it fetched, and the library decides who may see it.
    """
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


# --- The picture a creator is shown with -----------------------------------------------------------


def _art(request: Request) -> ArtStore:
    return part_of(request, SITE_ART)


@router.get("/creator-art")
async def creators_with_a_picture(
    store: Annotated[ArtStore, Depends(_art)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> CreatorsWithArt:
    """Every name Sift has a creator picture for, in one answer.

    So a screen full of People can ask once instead of once per card. Without it, a library with two
    hundred People makes two hundred requests on every visit to that screen and nearly all of them
    are answered "no picture", which is a slow screen built out of correct answers.

    Names only: this says which names have a picture, never where any of them came from.
    """
    return CreatorsWithArt(usernames=await store.creators_with_art())


@router.get("/creator-art/{username}")
async def creator_art(
    username: str,
    store: Annotated[ArtStore, Depends(_art)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    site: Annotated[str | None, Query(max_length=200)] = None,
    address: Annotated[str | None, Query(max_length=2000)] = None,
) -> FileResponse:
    """The picture Sift keeps for one creator, found by their name rather than by site.

    For the screens that show People, which know a name and nothing about where a file came from. A
    download files what it fetched under a person named by the username, so the two are the same
    string, and a person with no picture is answered 404 and keeps their monogram.

    With `site` (the Site's name) and, where known, `address` (the username's page), it is the
    picture kept for that username ON that Site and no other: a screen drawing a username beside
    its Site knows both, and the same name on another Site may be somebody else.
    """
    if site is not None:
        scope = creator_scope_for(site=site, username=username, address=address)
        known = await store.known(scope) if scope is not None else None
    else:
        known = await store.for_creator(username)
    if known is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no picture for that name.")
    # Said rather than guessed from the name: what the store hands back is always the cover door's
    # own JPEG, never a file a site sent, so there is one type this can be.
    return FileResponse(known.path, media_type="image/jpeg")
