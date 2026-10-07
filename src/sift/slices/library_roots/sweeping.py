# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a walk records about the folders it saw, and the files it did not find."""

from __future__ import annotations

import asyncio
import os
import zipfile
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from sift.kernel.archives import is_archive
from sift.kernel.content import (
    ROOT_REL_PATH,
    FolderRow,
    Location,
    check_rel_path,
    subtree_prefix,
)
from sift.kernel.jobs import (
    JobContext,
)
from sift.kernel.log import get_logger
from sift.kernel.paths import PathEscape, confine, is_absence
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.walking import NO_MOVES, RootIsGone

if TYPE_CHECKING:
    from sift.slices.library_roots.jobs import FolderSettled
    from sift.slices.library_roots.walking import FolderMoves, Walk, _Rows

log = get_logger(__name__)


#: A file as both sides can describe it: its name, and its size where known. `None` for a picture
#: indexed out of an archive: the walk lists the archive, so the two agree on the name alone.
_Item = tuple[str, int | None]


def _parent_of(rel_path: str) -> str:
    """What holds this path, with the library's own folder spelled the way it is stored."""
    parent = str(PurePosixPath(rel_path).parent)
    return ROOT_REL_PATH if parent == "." else parent


def _anything_under(rel_path: str, paths: Iterable[str]) -> bool:
    """Whether any known path sits inside this one: such a folder is never matched as empty."""
    prefix = rel_path + "/"
    return any(one.startswith(prefix) for one in paths)


async def _recorded_in(
    context: _Rows, folder: FolderRow, refused: Mapping[str, set[_Item]]
) -> set[_Item]:
    """What Sift believes is directly inside one folder, in the shape a listing comes back in.

    A picture indexed out of an archive is reported as the ARCHIVE, once, with no size. A file the
    scan REFUSED is in it too, by the name and size the refusal recorded: Sift knows it is there,
    and left out the catch-up would name it for a scan again after every change to its folder.
    `refused` is `_refusals_by_folder` of the root's refusals, read once by the caller."""
    found: set[_Item] = set(refused.get(folder.rel_path, ()))
    for location in await context.library.locations_in_folder(folder.id):
        if location.inside_an_archive and location.archive_rel_path is not None:
            found.add((PurePosixPath(location.archive_rel_path).name, None))
        else:
            found.add((location.filename, location.size_bytes))
    return found


def _refusals_by_folder(refused: Mapping[str, tuple[int, int]]) -> dict[str, set[_Item]]:
    """A root's refusals, as `rejections_of_root` returns them, filed by the folder holding each."""
    filed: dict[str, set[_Item]] = {}
    for rel_path, (size, _mtime_ns) in refused.items():
        filed.setdefault(_parent_of(rel_path), set()).add((PurePosixPath(rel_path).name, size))
    return filed


async def _record_what_was_seen(
    context: JobContext, *, root_id: str, under: str, walk: Walk, unjudged: Iterable[str] = ()
) -> None:
    """Store each walked directory's timestamp against its folder row.

    Beside `_settle_folders`, not inside it: the same answer put to an unrelated use. A directory
    the walk could not stat records NULL, "cannot be ruled out": the next pass looks again."""
    for rel_dir, seen_at in walk.mtimes.items():
        # The walk reports directories relative to where it started; folder rows are relative to the
        # root. `.` is the directory the walk began in, which is `under` itself.
        here = under if rel_dir == "." else (subtree_prefix(under) + rel_dir)
        if (here + "/").startswith(tuple(unjudged)):
            seen_at = None
        folder = (
            await context.library.root_folder(root_id)
            if here == ROOT_REL_PATH
            else await context.library.folder_at(root_id, check_rel_path(here))
        )
        if folder is not None:
            await context.library.record_folder_mtime(folder.id, seen_at)


async def _settle_folders(
    context: JobContext,
    *,
    root_id: str,
    dirs: set[str],
    folder_settled: FolderSettled | None,
) -> None:
    """Tell whoever is listening about each folder this pass went through.

    One call per DIRECTORY. A listener that refuses must not take a scan down with it: the scan
    would be re-run, re-walking everything, for a grouping that is a convenience."""
    if folder_settled is None or not dirs:
        return
    # Said out loud: a pass that ran and declined is otherwise the same in a log as one that never
    # ran. `considered` rather than `folders`: a log key holding "folder", "path", "dir" or "home"
    # is redacted as naming somebody's filesystem.
    log.info("library.folders_settling", root_id=root_id, considered=len(dirs))
    for rel_dir in sorted(dirs):
        folder = (
            await context.library.root_folder(root_id)
            if rel_dir == "."
            else await context.library.folder_at(root_id, check_rel_path(rel_dir))
        )
        if folder is None:
            continue
        try:
            await folder_settled(folder.id, folder.name or PurePosixPath(rel_dir).name)
        except Exception:
            log.warning("library.folder_not_settled", root_id=root_id, folder_id=folder.id)


async def _member_still_there(context: _Rows, location: Location, root_abs: Path) -> bool:
    """Whether a picture is still inside the archive that holds it.

    Two questions: the archive can be deleted, and the picture can be taken out of an archive that
    is still there. Reading the index is the last few kilobytes, and only for locations the walk
    did NOT claim. Anything short of a definite no counts as still there: an archive that cannot be
    opened right now is a question about ACCESS, not absence."""
    archive = await context.content.container_path_of(location)

    def look() -> bool:
        try:
            with zipfile.ZipFile(archive) as opened:
                opened.getinfo(location.member_path or "")
        except FileNotFoundError as error:
            return not is_absence(error, under=root_abs)
        except KeyError:
            # The archive opened and does not hold it: a definite no about this picture only.
            return False
        except (OSError, zipfile.BadZipFile):
            return True
        return True

    return await asyncio.to_thread(look)


async def _folder_for(
    context: JobContext, root_id: str, rel_path: str, folders: dict[str, str] | None = None
) -> str:
    """The folder row a file lives in, and every folder above it, created if need be.

    `folders` remembers the answer per directory for the length of a pass."""
    parent = str(Path(rel_path).parent.as_posix())
    if folders is not None and parent in folders:
        return folders[parent]
    if parent == ".":
        top = await context.library.root_folder(root_id)
        if top is None:
            raise RootIsGone(f"library root {root_id} has no folder row")
        folder_id = top.id
    else:
        # A read first: the row usually exists, and an upsert of one is still a write.
        row = await context.library.folder_at(root_id, parent)
        if row is None:
            row = await context.library.upsert_folder(root_id, check_rel_path(parent))
        folder_id = row.id
    if folders is not None:
        folders[parent] = folder_id
    return folder_id


async def _sweep(
    context: JobContext,
    *,
    root_id: str,
    root_abs: Path,
    under: str,
    seen: set[str],
    only: list[str] | None = None,
    unjudged: Iterable[str] = (),
) -> None:
    """Mark what the walk did not find as missing. The asset survives.

    An asset whose every copy is missing comes back, with its tags, the moment its bytes turn up
    anywhere Sift can read. `_still_there` is the correctness guard: a file the walk did not mention
    is looked for, and only one really absent is marked. `under` is about cost, not safety: it
    filters the rows read to the folder scanned. Do not remove `_still_there` because the scope
    overlaps it. The rows are `_missing`'s answer, which a dry run counts; this marks them."""
    gone = 0
    async for location in _missing(
        context,
        root_id=root_id,
        root_abs=root_abs,
        under=under,
        seen=seen,
        only=only,
        unjudged=unjudged,
    ):
        # Counted by what the write says it did: two passes over one root may read the same rows,
        # and the count is of this pass's own marks.
        if await context.content.mark_missing(location.id):
            gone += 1

    if gone:
        log.info("library.scan_marked_missing", root_id=root_id, files=gone)


async def _missing(
    rows: _Rows,
    *,
    root_id: str,
    root_abs: Path,
    under: str,
    seen: set[str],
    only: list[str] | None = None,
    moves: FolderMoves = NO_MOVES,
    unjudged: Iterable[str] = (),
) -> AsyncIterator[Location]:
    """Every row a sweep would mark missing, a page of rows at a time. Writes nothing.

    A page at a time, never the whole library held at the same time. `moves` is for a plan: each row
    is judged at the path the moves would give it, which is where the walk saw its file."""
    # A pass of named files concludes nothing about a row it was not handed.
    examined = None if only is None else set(only)
    skipped = tuple(unjudged)
    # The iterator yields only rows the library believes present.
    async for location in rows.library.iter_locations_in_root(root_id, under=under):
        where = moves.of(location)
        if examined is not None and not _was_examined(where, examined):
            continue
        if where.rel_path in seen or where.rel_path.startswith(skipped):
            continue
        if await _still_there(rows, where, root_abs):
            # The walk did not see it, but it is there (a folder that became unreadable mid-pass):
            # marking it missing would take a file off the grid that sits on the disk.
            continue
        yield location


async def _forget_gone_refusals(
    service: LibraryService,
    *,
    root_id: str,
    root_abs: Path,
    under: str,
    refused: Mapping[str, tuple[int, int]],
    walked: set[str],
    only: list[str] | None,
    unjudged: Iterable[str] = (),
) -> None:
    """Forget the refusal of a file that is no longer there: the sweep's twin, for the files a scan
    said no to. A refusal nothing removed would be named for a scan again by the catch-up after
    every change to its folder. Only a definite "not there" forgets one, and a scan of named files
    concludes nothing about a path it was not handed."""
    examined = None if only is None else set(only)
    scope = subtree_prefix(under)
    # A picture removed from Sift is remembered INSIDE an archive, there while its archive is
    # (`remember_removed`); nothing under a folder that would not answer is judged.
    kept = tuple(path + "/" for path in walked if is_archive(Path(path))) + tuple(unjudged)
    unlisted = [
        rel_path
        for rel_path in refused
        if rel_path not in walked
        and not rel_path.startswith(kept)
        and (rel_path in examined if examined is not None else rel_path.startswith(scope))
    ]
    if not unlisted:
        return
    gone = await asyncio.to_thread(_definitely_gone, root_abs, unlisted)
    for rel_path in gone:
        await service.forget_rejection(root_id=root_id, rel_path=rel_path)
    if gone:
        log.info("library.refusals_forgotten", root_id=root_id, files=len(gone))


def _definitely_gone(root_abs: Path, rel_paths: Sequence[str]) -> list[str]:
    """Which of these paths are certainly not there. Blocking: one `stat` each, over what may be
    a share. Refused, unreachable or escaping the root all answer "cannot say", never "gone"."""
    gone: list[str] = []
    for rel_path in rel_paths:
        try:
            os.stat(confine(root_abs, root_abs / rel_path))
        except PathEscape:
            continue
        except OSError as error:
            if is_absence(error, under=root_abs):
                gone.append(rel_path)
    return gone


def _was_examined(location: Location, examined: set[str]) -> bool:
    """Whether this pass really looked at the file behind one row.

    The paths a pass is handed are FILES ON THE DISK: a picture out of an archive is recorded at
    `gallery.zip/inside.jpg` and what was named is `gallery.zip`. Looking at the archive IS looking
    at its pictures; `_member_still_there` then concludes about each one."""
    if location.rel_path in examined:
        return True
    return location.archive_rel_path is not None and location.archive_rel_path in examined


async def _still_there(context: _Rows, location: Location, root_abs: Path) -> bool:
    """Whether the file is really at this path. Anything short of "no" counts as yes.

    Not being allowed to look is not evidence of absence. **Never `Path.exists()`**: it answers
    False for anything it could not resolve, a file under a folder whose permissions changed
    included. `os.stat` tells "no such file" from an answer about ACCESS. A picture inside an
    archive is asked of the archive (`_member_still_there`): `path_of` would PRODUCE a cached copy
    of the member in order to look for it."""
    if location.inside_an_archive:
        return await _member_still_there(context, location, root_abs)
    path = await context.content.path_of(location)

    def look() -> bool:
        try:
            os.stat(path)
        except OSError as error:
            # Refused, or a share not answering, is not absent. On Windows a share that is off
            # raises the class a gone file does, so the error's code decides (`is_absence`).
            return not is_absence(error, under=root_abs)
        return True

    return await asyncio.to_thread(look)
