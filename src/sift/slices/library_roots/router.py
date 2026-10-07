# SPDX-License-Identifier: AGPL-3.0-or-later
"""The library's endpoints: the roots and the folder tree.

Two audiences, and the split is deliberate. Everything about a *root* is admin-only: a root is a
directory on the server's disk, adding one is telling Sift where somebody's life is kept, and the
list of them describes the machine. Everything about a *folder* is scoped to whoever is asking,
through the access repository, because the folder tree is what a guest browses.

Nothing here writes into a library. Reading the tree and moving it are different operations with
different consequences, and the second one lives with everything else that changes a file somebody
else put there.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path, PurePath
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status

from sift.kernel import wiring
from sift.kernel.access import (
    Folder,
    FolderContents,
    GrantMark,
    ObjectType,
    Repository,
    Viewer,
)
from sift.kernel.access.catalog import marked_folders
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce
from sift.kernel.config import Settings
from sift.kernel.content import FolderRow, LibraryError, LibraryStore, Root, RootKind
from sift.kernel.db import Database
from sift.kernel.hardware import machine_name
from sift.kernel.jobs import WAITED_ON_PRIORITY, JobQueue
from sift.kernel.ledger import Actor
from sift.kernel.library_write import LibraryWriteRefused
from sift.kernel.log import get_logger
from sift.kernel.paths import PathEscape, Presence, confine, is_writable, presence
from sift.kernel.seams import SettingsSeam
from sift.kernel.where import blurred, profile_to_blur
from sift.kernel.wiring import part_of, part_or_none
from sift.kernel.workbench import Recorder
from sift.slices.auth import csrf_protect, current_viewer, require_admin, require_vault_pin
from sift.slices.library_roots import quarantine
from sift.slices.library_roots import queue as queues
from sift.slices.library_roots.browse import BrowseRefused, look, machine_roots
from sift.slices.library_roots.jobs import queue_scan, scan_shape
from sift.slices.library_roots.models import (
    BrowseEntry,
    BrowseView,
    FolderChange,
    FolderDetail,
    FolderFacts,
    FolderProperties,
    FoldersFacts,
    FoldersView,
    FolderView,
    GrantsView,
    GrantView,
    NewFolder,
    NewGrant,
    NewRoot,
    QuarantinedView,
    QuarantineView,
    RejectionView,
    RootChange,
    RootMoved,
    RootRejectionsView,
    RootsView,
    RootView,
)
from sift.slices.library_roots.service import SERVICE, LibraryService
from sift.slices.library_roots.watcher import WATCHER

log = get_logger(__name__)

router = APIRouter(prefix="/library", tags=["library"])


def _service(request: Request) -> LibraryService:
    return part_of(request, SERVICE)


async def _rewatch(request: Request) -> None:
    """Tell the watcher, if one runs, that the roots changed, rather than wait for a restart."""
    watcher = part_or_none(request, WATCHER)
    if watcher is not None:
        await watcher.refresh()


#: How long a folder on another machine gets to say it is still there: enough for a busy share.
_REACHABLE_TIMEOUT = 1.5


async def _reachable(path: Path) -> bool:
    """Whether a folder on another machine answers, without a dead mount holding the request."""
    return await _presence_elsewhere(path) == "here"


async def _presence_elsewhere(path: Path) -> Presence:
    """`presence` for a folder on another machine; one that did not answer in time is silent."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(presence, path), _REACHABLE_TIMEOUT)
    except (TimeoutError, OSError):
        return "silent"


def _root_view(
    root: Root,
    *,
    profile: PurePath | None = None,
    hidden: bool = False,
    mark: GrantMark | None = None,
    reachable: bool | None = None,
    presence: Presence | None = None,
    machine: str | None = None,
) -> RootView:
    """A root as a screen sees it. `hidden` is the asking user's own; `mark` is read off the root's
    top folder, so it matches the tree; `profile` is kept out of the path (`kernel.where.blurred`).
    """
    return RootView(
        id=root.id,
        # Read off the stored path, not the name column, so the two cannot disagree.
        name=Path(root.abs_path).name or str(root.abs_path),
        path=blurred(str(root.abs_path), profile),
        kind=root.kind,
        vault=hidden,
        created_at=root.created_at,
        shared=mark.shared if mark else False,
        restricted=mark.restricted if mark else False,
        shared_here=mark.shared_here if mark else False,
        restricted_here=mark.restricted_here if mark else False,
        reachable=reachable,
        presence=presence,
        machine=machine,
    )


def _folder_view(
    folder: Folder,
    mark: GrantMark | None = None,
    refused: tuple[bool, bool] = (False, False),
    writable: bool | None = None,
) -> FolderView:
    return FolderView(
        id=folder.id,
        root_id=folder.root_id,
        parent_id=folder.parent_id,
        name=folder.name,
        rel_path=folder.rel_path,
        shared=mark.shared if mark else False,
        restricted=mark.restricted if mark else False,
        shared_here=mark.shared_here if mark else False,
        restricted_here=mark.restricted_here if mark else False,
        hidden=folder.vault,
        keep_local=refused[0],
        keep_from_swaps=refused[1],
        writable=writable,
    )


async def _writable_roots(library: LibraryStore, root_ids: set[str]) -> dict[str, bool]:
    """Whether Sift may write in each of these libraries, asked of the disk now and never stored.

    A share that does not answer within the reachability wait reads as not writable.
    """
    roots = [root for root in await library.roots() if root.id in root_ids]

    async def asked(root: Root) -> bool:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(is_writable, Path(root.abs_path)), _REACHABLE_TIMEOUT
            )
        except (TimeoutError, OSError):
            return False

    answers = await asyncio.gather(*(asked(root) for root in roots))
    return {root.id: answer for root, answer in zip(roots, answers, strict=True)}


# --- choosing a folder -----------------------------------------------------------------------


@router.get("/grants")
async def list_grants(
    library: Annotated[LibraryStore, Depends(wiring.library)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> GrantsView:
    """The folders Sift has been given, which is the set the picker is allowed to look inside."""
    return GrantsView(
        grants=[
            GrantView(id=grant.id, path=grant.abs_path, granted_at=grant.granted_at)
            for grant in await library.grants()
        ]
    )


@router.post("/grants", status_code=status.HTTP_201_CREATED, dependencies=[Depends(csrf_protect)])
async def add_grant(
    body: NewGrant,
    library: Annotated[LibraryStore, Depends(wiring.library)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> GrantView:
    """Record a folder chosen in the operating system's folder dialog.

    Every refusal (the folder is not there, it is a file, Sift cannot read it, it is one of Sift's
    own directories, it overlaps a folder already granted) comes back as a 400 carrying the
    sentence the kernel wrote for it. Those are written for the person looking at the screen, so
    they are handed over unchanged.
    """
    try:
        grant = await library.grant(Path(body.path))
    except LibraryError as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from refusal
    return GrantView(id=grant.id, path=grant.abs_path, granted_at=grant.granted_at)


@router.get("/browse")
async def browse(
    library: Annotated[LibraryStore, Depends(wiring.library)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    path: str | None = None,
    scope: Literal["granted", "machine"] = "granted",
) -> BrowseView:
    """The folders inside one of the places that were handed to Sift, so one can be picked.

    Admin-only, read-only, and confined to the folders somebody granted. With no `path`,
    the granted folders themselves. A `path` inside none of them is refused in the same sentence
    whether it got there by `..`, by naming somewhere else outright, or through a symlink: the
    reason is not told apart, because the three are the same request and telling them apart would
    describe the machine to whoever was trying them.

    The set it is rooted at comes from the DATABASE, not from configuration. The confinement is the
    grant list, and a folder gets into that list by somebody choosing it in Windows' own dialog,
    which no page can open, drive or read.

    An install with no grants yet (a fresh one, before its first folder) lists nothing, and the
    screen says how to hand a folder over.
    """
    if scope == "machine":
        # THE WHOLE COMPUTER, on purpose, and it is what makes a Sift on another machine
        # administrable at all: without it a folder could only be added by walking to the keyboard
        # and opening the operating system's own dialog. It grants nothing (see machine_roots),
        # and an administrator's session could already name any path to `POST /library/grants`.
        roots = await machine_roots()
    else:
        roots = [Path(grant.abs_path) for grant in await library.grants()]
    try:
        listing = await look(roots, Path(path) if path else None)
    except BrowseRefused as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from refusal

    return BrowseView(
        path=listing.path,
        entries=[BrowseEntry(name=entry.name, path=entry.path) for entry in listing.entries],
        breadcrumb=[BrowseEntry(name=crumb.name, path=crumb.path) for crumb in listing.breadcrumb],
        file_count=listing.file_count,
        nothing_granted=listing.nothing_granted,
        writable=listing.writable,
        read_only_mount=listing.read_only_mount,
    )


# --- roots -----------------------------------------------------------------------------------


@router.get("/roots")
async def list_roots(
    library: Annotated[LibraryStore, Depends(wiring.library)],
    access: Annotated[Repository, Depends(wiring.access)],
    preferences: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> RootsView:
    """Every library root. Admin-only: the list of them is a description of the server's disk.

    Each row carries the sharing mark of its own top folder. Asked through the folder rather than
    invented here, so a root and the folder directly under it in the tree cannot draw two different
    answers to the same question.
    """
    hidden = await library.hidden_roots(viewer.id)
    roots = await library.roots()
    # The top folder of each root, which is the object the mark is really about. A root with no
    # folder row yet (added but not yet scanned) simply has no mark, which is the truth.
    tops = {
        folder.root_id: folder
        for folder in await access.visible_folders(viewer)
        if folder.parent_id is None
    }
    marks = await access.visible_marks(
        viewer, ObjectType.FOLDER, [folder.id for folder in tops.values()]
    )
    # Every root is asked, because a folder that is not there is worth saying so about wherever it
    # lives: a local disk unplugged, a container started without the mount that carries it, a share
    # whose server has gone. Only the shares need protecting from their own timeout, and those are
    # asked all at once: one after another, a list holding several dead shares would wait the
    # timeout multiplied by however many there are. A local folder is a stat, and is asked here.
    elsewhere = [root for root in roots if root.kind == RootKind.NAS]
    answered = await asyncio.gather(
        *(_presence_elsewhere(Path(root.abs_path)) for root in elsewhere)
    )
    where = dict(zip((root.id for root in elsewhere), answered, strict=True))
    for root in roots:
        if root.kind != RootKind.NAS:
            where[root.id] = presence(Path(root.abs_path))
    # `reachable` is the older one-word reading of the same answer, kept because every reader of
    # the row asks it; `presence` is what it is short for.
    reachable = {root_id: found == "here" for root_id, found in where.items()}
    # Which computer the local folders are on: one answer for all of them, because there is only one
    # machine running this server. Asked once outside the loop rather than per root: it is the same
    # question every time, and a per-root call would read as though it could differ. A share is not
    # given it at all: it is already named by its own address, which is on the row.
    here = machine_name()
    profile = await profile_to_blur(viewer, preferences)
    return RootsView(
        roots=[
            _root_view(
                root,
                profile=profile,
                hidden=root.id in hidden,
                mark=marks.get(tops[root.id].id) if root.id in tops else None,
                reachable=reachable.get(root.id),
                presence=where.get(root.id),
                machine=None if root.kind == RootKind.NAS else here,
            )
            for root in roots
        ]
    )


@router.post("/roots", status_code=status.HTTP_201_CREATED, dependencies=[Depends(csrf_protect)])
async def add_root(
    body: NewRoot,
    request: Request,
    service: Annotated[LibraryService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> RootView:
    """Point Sift at a folder.

    Every way this can be refused (the folder is not there, it is a file, Sift cannot read it, it
    overlaps a library that is already here, it is one of Sift's own directories) comes back as a
    400 carrying the sentence the kernel wrote for it. Those messages are written for the person
    who typed the path, so they are handed over unchanged rather than being replaced with something
    about a constraint.

    A library can be added straight out of sight, which conceals every file beneath it, on the
    screens of the user who added it, and nobody else's. That needs a PIN to already exist:
    without one there would be nothing to open it with again, and the whole library would be added
    invisible.
    """
    if body.vault:
        await require_vault_pin(request, viewer)
    try:
        root = await service.add_root(abs_path=Path(body.abs_path))
    except LibraryError as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from refusal
    await _rewatch(request)
    # Read it now, rather than only from here on.
    # Watching reports what changes AFTER it starts, so a folder of existing media (which is what
    # somebody adding a folder almost always has) would stay invisible until they found the rescan
    # button and pressed it. Adding a folder means "read this", so this is what was meant.
    # Unless the caller says otherwise, which only the first-run flow does. See `scan` on NewRoot:
    # a scan starting underneath somebody who is still answering setup questions competes with the
    # rest of that flow for the same machine, and they have not been told it is about to happen.
    # The reaction to a folder added goes first, and may take the scan over: the first folder on a
    # device never measured queues the benchmark, and the scan waits behind it so it reads under
    # the settings the benchmark chose (`wiring.ON_FOLDER_ADDED`).
    react = part_or_none(request, wiring.ON_FOLDER_ADDED)
    taken_over = await react(root.id, viewer.id, body.scan) if react is not None else False
    if body.scan and not taken_over:
        # Somebody's press, so the pass it starts names them. See `jobs.requested_by`.
        await queue_scan(queue, {"root_id": root.id}, requested_by=viewer.id)
    if body.vault:
        await service.set_root_hidden(viewer, root.id, hidden=True)
    return _root_view(root, hidden=body.vault)


@router.patch("/roots/{root_id}", dependencies=[Depends(csrf_protect)])
async def change_root(
    root_id: str,
    body: RootChange,
    request: Request,
    library: Annotated[LibraryStore, Depends(wiring.library)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> RootView:
    """Change how a root behaves, which is whether this user keeps it in the vault. Neither its
    name nor its path is changed here.

    Hiding a library needs a PIN, the same as hiding anything else. Bringing it back needs Hidden
    actually open, also the same as everything else, and that is the more important half. This
    screen lists a library whether or not the user has hidden it, so without the check the whole
    of Hidden could be undone from a signed-in browser with no PIN at all, which is precisely the
    person it exists to stop. Nothing is stranded by it: a library can only have been hidden by
    somebody who had a PIN, so the way back is the one they already hold.
    """
    if body.vault:
        await require_vault_pin(request, viewer)
    if body.vault is False and not viewer.show_hidden:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "unlock Hidden before bringing a library back",
        )
    root = await library.get_root(root_id)
    if root is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "there's no library folder with that id")
    if body.vault is not None:
        await library.set_root_hidden(viewer.id, root_id, hidden=body.vault)
    return _root_view(root, hidden=root_id in await library.hidden_roots(viewer.id))


@router.delete(
    "/roots/{root_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def remove_root(
    root_id: str,
    request: Request,
    service: Annotated[LibraryService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Forget a root and everything indexed under it. Deletes no file.

    The confirmation in front of this says so in as many words, because it is the one destructive-
    sounding thing here that is not destructive at all: the rows go, the files stay exactly where
    they are, and pointing Sift back at the folder rebuilds the library from the digests with
    everything anybody recorded about those files still attached.
    """
    if not await service.remove_root(root_id, actor=Actor.user(viewer.id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "there's no library folder with that id")
    await _rewatch(request)


@router.post(
    "/roots/{root_id}/rescan",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(csrf_protect)],
)
async def rescan_root(
    root_id: str,
    library: Annotated[LibraryStore, Depends(wiring.library)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    folder_id: Annotated[str | None, Query()] = None,
    scan_only: Annotated[bool, Query()] = False,
) -> dict[str, str]:
    """Walk a root again now, or just one folder of it, rather than waiting for the watcher.

    The root is looked up first so that a made-up id is a 404 rather than a job that starts, reads
    nothing, and fails somewhere nobody is looking.

    `folder_id` narrows the walk AND the sweep to that folder's subtree: what the watcher asks
    for when a single file lands, and what a person asks for to pick up one folder that changed
    without a whole-root rescan, which on a large library is minutes of work.

    The folder is resolved here as well as in the job, so asking about one that is not there, or
    one belonging to another library, is a 404 in front of somebody rather than a job that starts
    and fails where only a log would say so. The job checks again regardless, because between this
    line and the walk a folder can be removed.
    """
    if await library.get_root(root_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "there's no library folder with that id")
    folder = None
    if folder_id is not None:
        folder = await library.get_folder(folder_id)
        if folder is None or folder.root_id != root_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "there's no such folder in that root")
    # ONE SPELLING FOR ONE WALK. `scan_shape` drops a folder id that names the root's own top
    # folder, so a press and the watcher ask the same question in the same words and the dedupe
    # below can see that they did. See `scan_shape` for what the two spellings cost.
    payload: dict[str, object] = scan_shape(root_id, folder)
    # Only when asked for, and never as a default. A scan is what a watched folder runs by itself
    # whenever something lands in it, and that one has to hand out the rest of the pipeline or a
    # file dropped into a folder would never get a picture. `scan_only` is one button's request for
    # one stage; see `_probe_payload`. Left out of the payload entirely when false, so an ordinary
    # rescan queues the same job it always did: the queue matches a payload exactly to decide
    # whether work is already coming, and a field that is always present would change that identity
    # for every scan in the library.
    if scan_only:
        payload["scan_only"] = True
    # DEDUPED, because this is a button and buttons get pressed twice: undeduped, a double press
    # walks the same folder twice at once, minutes each time on a large root, for one answer.
    # `dedupe` and not `is_live`: with no parent, the row it collapses onto IS the scan asked for.
    # It deliberately does not stand down for a RUNNING scan. That one built its file list before
    # this request existed, so a file that arrived since would wait for a walk nobody is going to
    # ask for again, the same reasoning written on `enqueue_when_settled`.
    # AND AT THE WAITED-ON PRIORITY, because a press is waited on: at the same priority as
    # everything else the order is arrival alone, and a press can wait minutes behind a thousand
    # file reads. `WAITED_ON_PRIORITY` records why the per-type caps cannot fix this: a cap only
    # lets a FREE worker skip an over-quota type, and while a long pass is allowed every worker
    # there is, no worker is ever free for the cap to have anything to say to. Priority is read at
    # the moment of claiming, so the next slot to open takes this.
    # It does not preempt or make the scan faster: it bounds the wait at its worst to one job rather
    # than the whole queue. A scan the MACHINE started (the watcher, the catch-up at boot) stays at
    # the ordinary priority, which is the whole point of naming this one.
    job_id = await queue_scan(queue, payload, priority=WAITED_ON_PRIORITY, requested_by=viewer.id)
    return {"job_id": job_id}


# --- what Sift would not take ------------------------------------------------------------------


#: How many of a root's refusals one answer carries. The count beside them says how many there
#: are; a root refusing a hundred thousand files would otherwise be a hundred thousand rows in one
#: response.
REJECTIONS_PAGE = 500


@router.get("/quarantine", response_model=QuarantineView)
async def quarantined(
    library: Annotated[LibraryStore, Depends(wiring.library)],
    database: Annotated[Database, Depends(wiring.database)],
    settings: Annotated[Settings, Depends(wiring.settings)],
    preferences: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> QuarantineView:
    """Everything Sift refused, in the two piles it actually falls into.

    A file can be quarantined with nowhere else in the application to see that it was. The reason
    for a moved file is in the note beside it, not only a line in the security log, so the listing
    can say *why* as well as what and when and how big, which is the one thing somebody opening
    this screen wants.

    Both piles at once, deliberately. They have opposite answers to "where is my file": one is in a
    folder of Sift's own and one is untouched in the reader's library, and a screen showing either
    alone would leave somebody looking in the wrong place.

    Admin-only, like everything else here: it names files on the server's disk.
    """
    roots = await library.roots()
    refused, counts = await quarantine.refused_in_roots(
        database, [root.id for root in roots], limit=REJECTIONS_PAGE
    )
    left_alone = [
        RootRejectionsView(
            root_id=root.id,
            root_name=root.name,
            rejections=[
                RejectionView(
                    rel_path=str(row["rel_path"]),
                    reason=str(row["reason"]),
                    detected=row["detected"],
                    size_bytes=int(row["size_bytes"]),
                    first_seen_at=int(row["first_seen_at"]),
                    last_seen_at=int(row["last_seen_at"]),
                )
                for row in refused.get(root.id, [])
            ],
            rejections_total=counts.get(root.id, 0),
        )
        for root in roots
    ]
    # Reading the directory is a handful of stats, and it is on a filesystem that may be a network
    # mount: off the loop, like every other directory walk in Sift.
    moved = await asyncio.to_thread(quarantine.listing, settings)
    return QuarantineView(
        moved=[
            QuarantinedView(
                id=one.id,
                original_name=one.original_name,
                reason=one.reason,
                detected=one.detected,
                origin=one.origin,
                size_bytes=one.size_bytes,
                quarantined_at=one.quarantined_at,
                explained=one.explained,
            )
            for one in moved
        ],
        left_alone=[one for one in left_alone if one.rejections],
        keep_days=quarantine.keep_days_from(await preferences.get_app(quarantine.KEEP_DAYS_KEY)),
    )


@router.delete(
    "/quarantine/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def delete_quarantined(
    name: str,
    settings: Annotated[Settings, Depends(wiring.settings)],
    database: Annotated[Database, Depends(wiring.database)],
    recorder: Annotated[Recorder, Depends(wiring.recorder)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Remove one quarantined file for good, and the note beside it.

    The name is a bare filename and the store refuses anything else (see `quarantine.resolve`).
    This is the one route in Sift whose whole job is deleting the file it is handed, so a name
    carrying a separator would be a way to reach anything the process can write to.

    The receipt is written after the file is gone and on a connection of its own, which is the only
    order available: what happens here is an unlink, and no database transaction has ever been able
    to contain one. Writing it first would record a deletion that may then fail.

    NO SUBJECTS, and that is a fact about quarantine rather than an omission. A quarantined file was
    refused on the way in, so it was never imported: there is no asset, no folder row and nothing
    else in the library this decision could name. The only thing it has is a filename, and a
    filename is not a subject anything could later look a history up by.
    """
    if not await asyncio.to_thread(quarantine.remove, settings, name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "there's no quarantined file of that name")
    async with database.write() as connection:
        await recorder.record_on(
            connection,
            queue=queues.QUARANTINE_QUEUE,
            user_id=viewer.id,
            title="Deleted a file Sift had quarantined",
            detail=(
                f"{name} was removed from quarantine and from your disk. It was never imported, "
                "so nothing else was recorded about it. This cannot be undone."
            ),
            payload=json.dumps({"name": name}),
        )
        # Organize > Quarantine and the decisions list on every admin's other tabs.
        announce(EVERY_ADMIN, About.LIBRARY)


@router.post(
    "/roots/{root_id}/rejections/allow",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def allow_rejection(
    root_id: str,
    rel_path: Annotated[str, Body(embed=True)],
    library: Annotated[LibraryStore, Depends(wiring.library)],
    service: Annotated[LibraryService, Depends(_service)],
    database: Annotated[Database, Depends(wiring.database)],
    recorder: Annotated[Recorder, Depends(wiring.recorder)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Forget that this file was refused, so the next scan looks at it again.

    Not "import it now", and the difference matters. The refusal is a *memory* (it is what stops
    the scanner re-reading the same unreadable file on every pass), so removing the memory puts the
    file back in front of the gate rather than past it. A file that is genuinely a disguised
    executable is refused again, and says so again, which is the right outcome for somebody who
    pressed this on a hunch.

    NO SUBJECTS, for the same reason the quarantine route above has none: a refused file was never
    imported, so there is no asset and no folder row to name. The library FOLDER it sits under is
    not one either: `root_id` names a library root, which is a different table from `folders` and
    a different id space, and writing it as a folder subject would file this decision against a
    folder somebody else's history would then read.
    """
    root = await library.get_root(root_id)
    if root is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "there's no library folder with that id")
    await service.forget_rejection(root_id=root_id, rel_path=rel_path)
    async with database.write() as connection:
        await recorder.record_on(
            connection,
            queue=queues.SKIPPED_QUEUE,
            user_id=viewer.id,
            title="Asked Sift to look at a skipped file again",
            detail=(
                f"{rel_path} in {root.name} was refused by the check on the way in, and that "
                "refusal has been forgotten. The next scan reads the file again \u2014 if it really is "
                "what Sift thought, it is refused again. Nothing was moved or deleted."
            ),
            payload=json.dumps({"root_id": root_id, "rel_path": rel_path}),
        )
        # The receipt lands after the forgetting announced, so the decisions list is told again.
        announce(EVERY_ADMIN, About.LIBRARY)


# --- the folder tree -------------------------------------------------------------------------


@router.get("/folders")
async def list_folders(
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    library: Annotated[LibraryStore, Depends(wiring.library)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    root: str | None = None,
    parent: str | None = None,
    writable: bool = False,
) -> FoldersView:
    """The folder tree, flat, and only what this viewer may see.

    With no parameters this is every folder in every library, which is what the browser opens on
    and builds its tree from. Flat rather than nested, and all at once rather than a level at a
    time: folders are cheap where files are not, and the alternative is a request per row or a
    second statement answering "has children the viewer may see", which is the scoping rule
    written twice, and two copies of that rule can disagree.

    Roots themselves are never listed here. A root is a path on somebody's disk; the folder row
    standing for it is the same place with none of that attached, so a guest shown one folder of
    one library learns nothing about where any of it lives.

    `parent` narrows to one folder's direct children, `root` to one library. A folder somebody may
    not see is absent, not a 403, which would confirm that it exists.

    Both are handed down together rather than one being chosen between. They are narrowings of one
    read, and passing them as such means a request naming a folder in a library it is not in gets
    the honest empty answer instead of having half of what it asked silently dropped.

    `writable` asks the disk which folders Sift may write in, for a chooser of where files land;
    the tree does not ask, so a share that has gone silent never slows it.
    """
    folders = await access.visible_folders(viewer, root_id=root, parent_id=parent)
    marks = await access.visible_marks(viewer, ObjectType.FOLDER, [folder.id for folder in folders])
    # The two refusals are an admin's to read and to change, as the sharing marks are.
    refused = await marked_folders(database) if viewer.is_admin else {}
    may_write = (
        await _writable_roots(library, {folder.root_id for folder in folders}) if writable else {}
    )
    return FoldersView(
        folders=[
            _folder_view(
                folder,
                marks.get(folder.id),
                refused.get(folder.id, (False, False)),
                may_write.get(folder.root_id, False) if writable else None,
            )
            for folder in folders
        ]
    )


# BEFORE `/folders/{folder_id}`, and that is not a preference.
# FastAPI matches routes in the order they are declared, so a literal segment written after a
# parameterised one at the same depth is unreachable: `/folders/facts` would reach `get_folder` as
# a folder that is not there, a 404 rather than a route not mounted, which nobody thinks to check.
@router.get("/folders/facts")
async def folder_facts(
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    parent: Annotated[str | None, Query()] = None,
) -> FoldersFacts:
    """What the folders directly inside one hold, so a screen can put them in an order.

    ONE request for a screenful rather than one per row, which is the rule the folder tree already
    follows, and it is asked for only when somebody chooses an order that needs it. Alphabetical
    is what the view opens in and it needs none of this, so the ordinary case pays nothing.

    Scoped to the DIRECT children of `parent`, or to the library folders when it is absent, because
    that is what a screen draws. The numbers themselves are over each folder's whole subtree: a
    folder is as big as everything under it, which is what somebody comparing two of them means.

    Admin-only, like `folder_properties`, and for the same reason: these count what a folder
    physically holds rather than what the caller may open.
    """
    folders = await access.visible_folders(viewer, parent_id=parent, top_level=parent is None)
    facts = []
    for folder in folders:
        under = await access.folder_contents(viewer, folder)
        if under is None:
            continue
        facts.append(
            FolderFacts(
                id=folder.id,
                file_count=under.files,
                size_bytes=under.bytes,
                newest_at=under.newest_at,
            )
        )
    return FoldersFacts(folders=facts)


@router.get("/folders/{folder_id}")
async def get_folder(
    folder_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> FolderDetail:
    """One folder, and (for an admin) how many files a move would carry.

    This also serves the deep link: `/library/{folder_ulid}` is a real, refreshable URL, and the id
    in it is opaque on purpose. A slug would put somebody's folder names in their browser history
    and in every screenshot of the address bar, which is part of what the vault is concealing.
    """
    folder = await access.get_folder(viewer, folder_id)
    if folder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return FolderDetail(
        id=folder.id,
        root_id=folder.root_id,
        parent_id=folder.parent_id,
        name=folder.name,
        file_count=await access.folder_file_count(viewer, folder),
    )


@router.get("/folders/{folder_id}/properties")
async def folder_properties(
    folder_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    library: Annotated[LibraryStore, Depends(wiring.library)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FolderProperties:
    """What a folder is: where it sits, how big it is, what is in it, when it was made.

    Admin-only, whole, and the reason is the one `FolderDetail.file_count` already gives. Every field
    is either a fact about the server's disk or a count of what a folder PHYSICALLY holds, not of
    what the person asking may open, which is a different and smaller number. Answering a guest with
    the physical figures would tell them how much is in a folder they can see two files of.

    The path is resolved through `_inside`, which is the same confinement every write to a library
    goes through: a folder row can name an in-library link leading out of the root, and a properties
    panel is no reason to be the one place that reads through one.

    Three separate failures are all a 404, deliberately: no such folder, one this viewer may not see,
    and one whose root has gone. Telling them apart would answer "does this id exist" for somebody
    who may not see it.
    """
    folder = await access.get_folder(viewer, folder_id)
    if folder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")

    root = await library.get_root(folder.root_id)
    if root is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")

    try:
        directory = await asyncio.to_thread(_folder_directory, Path(root.abs_path), folder.rel_path)
    except (LibraryError, OSError, ValueError, PathEscape):
        # A folder that will not resolve inside its root cannot be described; `PathEscape` is not a
        # `ValueError`, and a 500 would confirm the id belongs to something.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found") from None

    contents = await access.folder_contents(viewer, folder)
    # Only a guest gets None, and `require_admin` keeps them out: zeroes rather than an exception.
    counted = contents or FolderContents(files=0, bytes=0, folders=0, newest_at=None)

    return FolderProperties(
        location=str(directory),
        size_bytes=counted.bytes,
        file_count=counted.files,
        folder_count=counted.folders,
        created_at=await asyncio.to_thread(_created_at, directory),
    )


def _folder_directory(base: Path, rel_path: str) -> Path:
    """Where a folder really is, confined to its library as a write is. Blocking: use a thread."""
    return confine(base, base / rel_path) if rel_path else confine(base, base)


def _created_at(directory: Path) -> float | None:
    """When the directory was made, or None: `st_birthtime`, else `st_ctime` (Windows' creation)."""
    try:
        stat = directory.stat()
    except OSError:
        return None
    born = getattr(stat, "st_birthtime", None)
    return float(born) if born is not None else float(stat.st_ctime)


# --- arranging the folders in a library ----------------------------------------------------------
# Making, renaming and moving a folder all write into a library, so all three go through the one
# door that decides whether Sift may change files there.


def _written(folder: FolderRow) -> FolderView:
    """A folder as a write into its library hands it back."""
    return FolderView(
        id=folder.id,
        root_id=folder.root_id,
        parent_id=folder.parent_id,
        name=folder.name,
        rel_path=folder.rel_path,
    )


@router.post("/folders", status_code=status.HTTP_201_CREATED, dependencies=[Depends(csrf_protect)])
async def make_folder(
    body: NewFolder,
    service: Annotated[LibraryService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FolderView:
    """Make a folder inside a library, so nothing has to be arranged anywhere but here.

    Refused where the library was not handed over read-write, and refused for a name that is not a
    name. Both come back as a 400 carrying the sentence written for the person who typed it.
    """
    try:
        folder = await service.create_folder(parent_id=body.parent_id, name=body.name)
    except LibraryWriteRefused as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from refusal
    return _written(folder)


@router.post("/folders/placed", dependencies=[Depends(csrf_protect)])
async def place_folder(
    body: NewFolder,
    service: Annotated[LibraryService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FolderView:
    """The folder with this name inside another, recorded if it is on the disk and made if not.

    For choosing where downloads go: the person points at a place, and whether a scan has recorded
    it yet is not theirs to know. `POST /folders` still refuses a name already taken, which is
    right for "make a new folder". Refused where the library was not handed over read-write, and
    for a name that is not a name or that a file already holds, each as a 400 with its sentence.
    """
    try:
        folder = await service.place_folder(parent_id=body.parent_id, name=body.name)
    except LibraryWriteRefused as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from refusal
    return _written(folder)


@router.patch("/folders/{folder_id}", dependencies=[Depends(csrf_protect)])
async def change_folder(
    folder_id: str,
    body: FolderChange,
    service: Annotated[LibraryService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FolderView:
    """Rename a folder or move it, on the disk and in the tree together.

    **The folder keeps its id**, so a share, a restriction, a concealment and the rule saying whose
    files land in it all survive being rearranged, which is the whole reason this exists rather
    than leaving people to do it in a file manager and have Sift work out what happened afterwards.
    """
    try:
        folder = await service.move_folder(
            folder_id=folder_id,
            parent_id=body.parent_id,
            name=body.name,
            actor=Actor.user(viewer.id),
        )
    except LibraryWriteRefused as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from refusal
    return _written(folder)


@router.post("/roots/{root_id}/moved", dependencies=[Depends(csrf_protect)])
async def root_moved(
    root_id: str,
    body: RootMoved,
    request: Request,
    library: Annotated[LibraryStore, Depends(wiring.library)],
    service: Annotated[LibraryService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> RootView:
    """Tell Sift where a library folder is now, after it was moved or renamed outside Sift.

    A folder inside a library is recognised on the next walk, from what is inside it. The library
    folder itself cannot be: Sift is pointed at it by its path, so when that path stops existing
    Sift is not looking anywhere near the new one and there is nothing to recognise it by.

    **Nothing under it is re-read.** Every file is recorded relative to its library folder, so one
    stored path changes and the whole library is correct again, with every share, concealment and
    attribution still attached to the folders that carry them. Removing the library and adding it
    back recovers the files by their digests and loses all of that, and reads every byte to do it.
    """
    try:
        # Through the service, which tells every admin's library list; the store alone would move
        # the row and leave every other screen showing the old place.
        root = await service.repoint_root(root_id, Path(body.abs_path))
    except LibraryError as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from refusal
    if root is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "there's no library folder with that id")
    await _rewatch(request)
    # Read it where it now is. A library that has been away has almost certainly changed while it
    # was, and the alternative is somebody hunting for the rescan button after every move.
    await queue_scan(queue, {"root_id": root.id}, requested_by=viewer.id)
    return _root_view(root, hidden=root_id in await library.hidden_roots(viewer.id))
