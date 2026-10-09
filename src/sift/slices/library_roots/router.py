# SPDX-License-Identifier: AGPL-3.0-or-later
"""The library's endpoints: the roots, admin-only, and the folder tree, scoped to whoever asks."""

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


#: Enough for a busy share.
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
    """A root as a screen sees it: `hidden` is the asker's own, `mark` comes from its top folder."""
    return RootView(
        id=root.id,
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
    """Whether Sift may write in each of these libraries, asked of the disk now and never stored."""
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
    """Record a folder chosen in the operating system's folder dialog; refusals are 400s with their
    sentence."""
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
    """The folders inside a granted place, so one can be picked. Admin-only, read-only, confined to
    grants."""
    if scope == "machine":
        # The whole computer, so a remote Sift can be administered; it grants nothing.
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


@router.get("/roots")
async def list_roots(
    library: Annotated[LibraryStore, Depends(wiring.library)],
    access: Annotated[Repository, Depends(wiring.access)],
    preferences: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> RootsView:
    """Every library root. Admin-only: the list describes the server's disk."""
    hidden = await library.hidden_roots(viewer.id)
    roots = await library.roots()
    # A root not yet scanned has no top folder, so no mark.
    tops = {
        folder.root_id: folder
        for folder in await access.visible_folders(viewer)
        if folder.parent_id is None
    }
    marks = await access.visible_marks(
        viewer, ObjectType.FOLDER, [folder.id for folder in tops.values()]
    )
    # Shares are asked together, so several dead ones cost one timeout; local roots are a stat.
    elsewhere = [root for root in roots if root.kind == RootKind.NAS]
    answered = await asyncio.gather(
        *(_presence_elsewhere(Path(root.abs_path)) for root in elsewhere)
    )
    where = dict(zip((root.id for root in elsewhere), answered, strict=True))
    for root in roots:
        if root.kind != RootKind.NAS:
            where[root.id] = presence(Path(root.abs_path))
    # `reachable` is the older one-word reading of `presence`.
    reachable = {root_id: found == "here" for root_id, found in where.items()}
    # One machine runs this server, so one answer for every local root.
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
    """Point Sift at a folder; refusals are 400s with their sentence. Adding into the vault needs a
    PIN."""
    if body.vault:
        await require_vault_pin(request, viewer)
    try:
        root = await service.add_root(abs_path=Path(body.abs_path))
    except LibraryError as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from refusal
    await _rewatch(request)
    # Read it now, since watching only reports later changes; the first-run flow says not to.
    # The folder-added reaction goes first and may hold the scan behind a benchmark.
    react = part_or_none(request, wiring.ON_FOLDER_ADDED)
    taken_over = await react(root.id, viewer.id, body.scan) if react is not None else False
    if body.scan and not taken_over:
        # See `jobs.requested_by`.
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
    """Change whether this user keeps a root in the vault: hiding needs a PIN, showing needs Hidden
    open."""
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
    """Forget a root and everything indexed under it. Deletes no file."""
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
    """Walk a root again now, or one folder of it; an unknown root or folder is a 404, not a failed
    job."""
    if await library.get_root(root_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "there's no library folder with that id")
    folder = None
    if folder_id is not None:
        folder = await library.get_folder(folder_id)
        if folder is None or folder.root_id != root_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "there's no such folder in that root")
    # `scan_shape` gives a press and the watcher the same spelling, so the dedupe sees them.
    payload: dict[str, object] = scan_shape(root_id, folder)
    # Only when asked; left out when false so an ordinary rescan keeps its dedupe identity.
    if scan_only:
        payload["scan_only"] = True
    # Deduped against a double press, and at the waited-on priority so a press waits for one job at
    # most.
    # A scan the machine started stays at the ordinary priority.
    job_id = await queue_scan(queue, payload, priority=WAITED_ON_PRIORITY, requested_by=viewer.id)
    return {"job_id": job_id}


#: The count beside them says how many there are.
REJECTIONS_PAGE = 500


@router.get("/quarantine", response_model=QuarantineView)
async def quarantined(
    library: Annotated[LibraryStore, Depends(wiring.library)],
    database: Annotated[Database, Depends(wiring.database)],
    settings: Annotated[Settings, Depends(wiring.settings)],
    preferences: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> QuarantineView:
    """Everything Sift refused, in its two piles together: moved, and left where it was. Admin-only."""
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
    # It may be a network mount.
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
    """Remove one quarantined file for good, and its note; the receipt follows the unlink and has no
    subjects."""
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
    """Forget that this file was refused, so the next scan puts it back in front of the gate."""
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
        announce(EVERY_ADMIN, About.LIBRARY)


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
    """The folder tree, flat, and only what this viewer may see; a hidden folder is absent, not a
    403.
    `parent` and `root` narrow one read together. `writable` asks the disk, for a chooser of
    where files land."""
    folders = await access.visible_folders(viewer, root_id=root, parent_id=parent)
    marks = await access.visible_marks(viewer, ObjectType.FOLDER, [folder.id for folder in folders])
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


# Before `/folders/{folder_id}`: FastAPI matches in declaration order.
@router.get("/folders/facts")
async def folder_facts(
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    parent: Annotated[str | None, Query()] = None,
) -> FoldersFacts:
    """What the folders directly inside one hold, so a screen can order them. Admin-only."""
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
    """One folder, and (for an admin) how many files a move would carry. Also serves the deep link."""
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
    """What a folder is: where it sits, how big it is, what is in it, when it was made. Admin-only."""
    folder = await access.get_folder(viewer, folder_id)
    if folder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")

    root = await library.get_root(folder.root_id)
    if root is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")

    try:
        directory = await asyncio.to_thread(_folder_directory, Path(root.abs_path), folder.rel_path)
    except (LibraryError, OSError, ValueError, PathEscape):
        # A 500 would confirm the id belongs to something.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found") from None

    contents = await access.folder_contents(viewer, folder)
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


# Creating, renaming and moving a folder all write into a library through the one door.


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
    """Create a folder inside a library; refusals are 400s with their sentence."""
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
    """The folder with this name inside another, recorded if on the disk and created if not."""
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
    """Rename a folder or move it, on the disk and in the tree together; it keeps its id."""
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
    """Tell Sift where a library folder is now, after it was moved or renamed outside Sift."""
    try:
        # Through the service, which tells every admin's library list.
        root = await service.repoint_root(root_id, Path(body.abs_path))
    except LibraryError as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from refusal
    if root is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "there's no library folder with that id")
    await _rewatch(request)
    # A library that has been away has almost certainly changed.
    await queue_scan(queue, {"root_id": root.id}, requested_by=viewer.id)
    return _root_view(root, hidden=root_id in await library.hidden_roots(viewer.id))
