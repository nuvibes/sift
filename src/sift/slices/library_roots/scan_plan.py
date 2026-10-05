# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a walk of a library folder would do, worked out and written nowhere: a dry run of Scan."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from sift.kernel import lanes
from sift.kernel.archives import ArchiveRefused
from sift.kernel.archives import inspect as inspect_archive
from sift.kernel.content import (
    ROOT_REL_PATH,
    Root,
    subtree_prefix,
)
from sift.kernel.ingress import ALLOWED_MEDIA, Kind
from sift.kernel.log import get_logger
from sift.slices.library_roots.moved_folders import _plan_folders
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.sweeping import _missing, _parent_of
from sift.slices.library_roots.taking_in import Verdict, _decide, _member_rel_path
from sift.slices.library_roots.walking import (
    _STILL_EXTENSIONS,
    NO_MOVES,
    _root_answer,
    _walk_confined,
)

if TYPE_CHECKING:
    from sift.slices.library_roots.moved_folders import FolderPlan
    from sift.slices.library_roots.walking import Walked, _Rows

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PlannedRead:
    """A file a walk would read or look inside, as a plan names it."""

    #: Where the file is on the disk, relative to the library folder.
    rel_path: str
    #: Where the rows record it now, which differs from `rel_path` under a renamed folder.
    recorded_at: str
    #: The nearest recorded folder at or above it, which decides who may see it.
    folder: str


@dataclass(frozen=True, slots=True)
class ScanPlan:
    """What one walk of a library would do, worked out and written nowhere. See `plan_scan`."""

    #: False when the library folder did not answer, so nothing was planned.
    answered: bool = True
    #: What happens to the folder rows; None when the walk could not list the folder.
    folders: FolderPlan | None = None
    #: Files it would read that no row records.
    new: int = 0
    #: Files it would read again because their size or age changed.
    changed: int = 0
    #: Files marked missing whose bytes are back as they were: marked present, not read.
    returned: int = 0
    #: Archives it would look inside, which it does on every pass.
    archives: int = 0
    #: Files it would mark missing.
    missing: int = 0
    #: The first files it would read or look inside.
    reading: tuple[PlannedRead, ...] = ()
    #: The first files it would mark missing, by asset.
    going: tuple[str, ...] = ()


async def plan_scan(
    rows: _Rows, service: LibraryService, root: Root, *, first: int = 50
) -> ScanPlan:
    """What a walk of this whole library would do, through the functions the walk itself runs, so
    the two cannot disagree about one disk. Writes nothing."""
    root_abs = Path(root.abs_path)
    if await asyncio.to_thread(_root_answer, root_abs) is not None:
        return ScanPlan(answered=False)
    async with lanes.reading(root_abs / "walk"):
        walk = await asyncio.to_thread(_walk_confined, root_abs, root_abs)
    folders = await _plan_folders(
        rows, root_id=root.id, under=ROOT_REL_PATH, walk=walk, prefix="", service=service
    )
    moves = NO_MOVES if folders is None else folders.moves
    recorded = {one.rel_path for one in await rows.library.folders_in_root(root.id)}
    refusals = await service.rejections_of_root(root.id)

    new = changed = returned = archives = 0
    reading: list[PlannedRead] = []
    # What the take-ins would claim, which is what the sweep is judged against: each file its own
    # path, and an archive the pictures its index lists, unless it was refused before.
    seen: set[str] = set()
    for item in walk.files:
        verdict = await _decide(
            item,
            rel_path=item.rel_path,
            root_id=root.id,
            service=service,
            context=rows,
            refused=refusals,
            moves=moves,
        )
        recorded_at = moves.before(item.rel_path)
        if verdict is Verdict.ARCHIVE:
            archives += 1
            if refusals.get(recorded_at) != (item.size, item.mtime_ns):
                seen |= await asyncio.to_thread(_claimed_inside, item)
        else:
            seen.add(item.rel_path)
        if verdict is Verdict.RETURNED:
            returned += 1
        elif verdict is Verdict.READ:
            if await rows.content.location_at(root.id, recorded_at) is None:
                new += 1
            else:
                changed += 1
        if verdict.reads and len(reading) < first:
            folder = _nearest(_parent_of(recorded_at), recorded)
            reading.append(PlannedRead(item.rel_path, recorded_at, folder))

    missing = 0
    going: list[str] = []
    if walk.looked:
        async for location in _missing(
            rows, root_id=root.id, root_abs=root_abs, under=ROOT_REL_PATH, seen=seen, moves=moves
        ):
            missing += 1
            if len(going) < first:
                going.append(location.asset_id)
    return ScanPlan(
        folders=folders,
        new=new,
        changed=changed,
        returned=returned,
        archives=archives,
        missing=missing,
        reading=tuple(reading),
        going=tuple(going),
    )


@dataclass(frozen=True, slots=True)
class ToRead:
    """What a scan of one folder will read, counted ahead of it. See `count_to_read`."""

    #: Files and archives the scan will open; its units on the queue.
    files: int
    #: The same files by media kind, as their names say (`kind_by_name`).
    kinds: dict[str, int] = field(default_factory=dict)


#: The kind a name says, the first listed winning: a `.webp` is a still until it is read.
_KIND_OF_EXTENSION = {
    extension: media.kind.value
    for media in reversed(ALLOWED_MEDIA)
    for extension in media.extensions
}


def kind_by_name(item: Walked, verdict: Verdict) -> str:
    """The media kind a file to read is, by its name; an archive's members are stills."""
    if verdict is Verdict.ARCHIVE:
        return Kind.IMAGE.value
    return _KIND_OF_EXTENSION.get(item.path.suffix.lower(), Kind.IMAGE.value)


async def count_to_read(
    rows: _Rows, service: LibraryService, root: Root, *, under: str = ROOT_REL_PATH
) -> ToRead | None:
    """How many files a scan of this folder would read, decided as the scan decides; None when the
    library folder did not answer. Writes nothing."""
    root_abs = Path(root.abs_path)
    if await asyncio.to_thread(_root_answer, root_abs) is not None:
        return None
    prefix = subtree_prefix(under)
    async with lanes.reading(root_abs / "walk"):
        walk = await asyncio.to_thread(_walk_confined, root_abs, root_abs / under)
    folders = await _plan_folders(
        rows, root_id=root.id, under=under, walk=walk, prefix=prefix, service=service
    )
    moves = NO_MOVES if folders is None else folders.moves
    refusals = await service.rejections_of_root(root.id)
    kinds: dict[str, int] = {}
    for item in walk.files:
        verdict = await _decide(
            item,
            rel_path=prefix + item.rel_path,
            root_id=root.id,
            service=service,
            context=rows,
            refused=refusals,
            moves=moves,
        )
        if verdict.reads:
            kind = kind_by_name(item, verdict)
            kinds[kind] = kinds.get(kind, 0) + 1
    return ToRead(files=sum(kinds.values()), kinds=kinds)


def _claimed_inside(item: Walked) -> set[str]:
    """The pictures a take-in of this archive would claim. Blocking: it reads the archive's index.

    Nothing when the archive is refused, as the take-in claims nothing then either."""
    try:
        members = inspect_archive(item.path, wanted=_STILL_EXTENSIONS)
    except ArchiveRefused:
        return set()
    return {_member_rel_path(item.rel_path, member.path) for member in members}


def _nearest(rel_path: str, recorded: set[str]) -> str:
    """This folder's path, or the nearest folder above it that has a row."""
    while rel_path != ROOT_REL_PATH and rel_path not in recorded:
        rel_path = _parent_of(rel_path)
    return rel_path
