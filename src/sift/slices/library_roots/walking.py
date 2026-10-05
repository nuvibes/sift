# SPDX-License-Identifier: AGPL-3.0-or-later
"""Walking a library folder: what is on disk under it, and what a walk could not learn."""

from __future__ import annotations

import errno
import os
import stat as stat_module
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Protocol

from sift.kernel.archives import ARCHIVE_EXTENSIONS
from sift.kernel.content import (
    ContentStore,
    LibraryStore,
    Location,
)
from sift.kernel.ingress import (
    ALLOWED_EXTENSIONS,
    ALLOWED_MEDIA,
    Kind,
)
from sift.kernel.jobs import (
    JobFailedPermanently,
)
from sift.kernel.log import get_logger
from sift.kernel.paths import PathEscape, confine

log = get_logger(__name__)


#: What the walk stops to look at: media, and the archives media can be indexed out of, in one
#: `scandir`. A separate name from `ALLOWED_EXTENSIONS`: an archive is never itself an asset.
WORTH_OPENING = ALLOWED_EXTENSIONS | ARCHIVE_EXTENSIONS


#: What is indexed out of an archive: still pictures, built from the allowlist. No video: playing
#: one out of an archive means materialising the whole file, the second copy this design avoids.
_STILL_EXTENSIONS = frozenset(
    extension
    for media in ALLOWED_MEDIA
    if media.kind is not Kind.VIDEO
    for extension in media.extensions
)


class RootIsGone(Exception):
    """The root a scan was queued for has been removed. Not a failure worth retrying."""


class FolderIsGone(Exception):
    """The folder a scan was queued for is not there, or is not in this root."""


@dataclass(frozen=True, slots=True)
class Walked:
    """A file the walk found, and what it looked like when it was found.

    The size and the mtime come from the directory entry, in the same syscall that found the file:
    two answers taken a moment apart may be about different files."""

    rel_path: str
    path: Path
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class Walk:
    """What one pass over a directory tree found: the files, and the directories they sat in.

    `directories` (every one descended, the empty ones too, relative to where the walk started,
    not the start itself) is what lets an empty folder exist and a renamed one be recognised.
    `looked` tells a walk that was refused, or of an unplugged drive, from a walk of an empty
    library: anything that DELETES on the strength of not having seen something reads it."""

    files: tuple[Walked, ...]
    directories: tuple[str, ...]
    #: Each directory's own timestamp when the walk reached it (`"."` for the start), read BEFORE
    #: its listing; None where it would not answer. What the catch-up at start compares against.
    mtimes: dict[str, float | None] = field(default_factory=dict)
    #: The starting directory was really listed. False: nothing was learned.
    looked: bool = True
    #: Directories a listing named that would not list themselves: nothing under them is judged.
    unlisted: tuple[str, ...] = ()


def _worth_reading(entry: os.DirEntry[str], stack: list[Path]) -> os.stat_result | None:
    """A media file's stat, or None; a directory to descend goes on `stack`. Blocking.

    `follow_symlinks=False` keeps out a link to an ancestor (a loop) and a link out of the root. A
    JUNCTION (`mklink /J`, which needs no privilege on Windows) is neither to that test and is
    refused by `is_junction()`. Only a regular file is read: a FIFO blocks its reader for ever."""
    try:
        if entry.is_junction():
            return None
        if entry.is_dir(follow_symlinks=False):
            stack.append(Path(entry.path))
            return None
        if not entry.is_file(follow_symlinks=False):
            return None
        if Path(entry.name).suffix.lower() not in WORTH_OPENING:
            # A hint only, to avoid opening every document: the gate reads the bytes.
            return None
        return entry.stat(follow_symlinks=False)
    except OSError:
        # Gone between being listed and being asked about; the next pass finds it.
        return None


def walk_media(root: Path) -> Walk:
    """Every file under a root that might be media, and every directory they sat in. Blocking.

    `os.scandir` rather than `Path.rglob`: it carries the stat along with the name. What is taken
    from each entry is `_worth_reading`'s to say."""
    files: list[Walked] = []
    directories: list[str] = []
    mtimes: dict[str, float | None] = {}
    unlisted: list[str] = []
    looked = False
    stack = [root]
    while stack:
        directory = stack.pop()
        # BEFORE the listing: a change made in between leaves the older timestamp recorded, so the
        # folder is looked at again next time, the safe direction.
        try:
            seen_at: float | None = directory.stat().st_mtime
        except OSError:
            seen_at = None
        here = "." if directory == root else str(directory.relative_to(root).as_posix())
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            # A shut folder is not an empty one, nor a reason to stop: kept, and looked at again.
            log.warning("library.directory_unreadable", error=exc.strerror)
            if here != ".":
                directories.append(here)
                unlisted.append(here)
                mtimes[here] = None
            continue

        if here == ".":
            looked = True
        else:
            directories.append(here)
        mtimes[here] = seen_at

        for entry in entries:
            stat = _worth_reading(entry, stack)
            if stat is None:
                continue
            files.append(
                Walked(
                    rel_path=str(Path(entry.path).relative_to(root).as_posix()),
                    path=Path(entry.path),
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                )
            )

    return Walk(
        files=tuple(files),
        directories=tuple(directories),
        looked=looked,
        mtimes=mtimes,
        unlisted=tuple(unlisted),
    )


def look_at(root_abs: Path, rel_paths: Sequence[str]) -> Walk:
    """A walk of the NAMED files only, each confined before it is touched; anything gone or not a
    plain file is left out. `looked` is true: each path was looked at, so the sweep may conclude."""
    files: list[Walked] = []
    directories: set[str] = set()
    for rel_path in rel_paths:
        candidate = root_abs / rel_path
        try:
            path = confine(root_abs, candidate)
        except PathEscape:
            # Refused as the walk refuses its starting directory outside the root.
            log.warning("library.scan.named_path_outside_root")
            continue
        try:
            if path.is_junction() or not path.is_file():
                continue
            if path.suffix.lower() not in WORTH_OPENING:
                continue
            stat = path.stat()
        except OSError:
            # Gone since the notification, or unreadable: the sweep decides whether it is absent.
            continue
        files.append(
            Walked(rel_path=rel_path, path=path, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
        )
        parent = str(PurePosixPath(rel_path).parent)
        directories.add("." if parent == "." else parent)
    return Walk(files=tuple(files), directories=tuple(sorted(directories)), looked=True)


class FolderStoppedAnswering(JobFailedPermanently):
    """The folder being scanned stopped answering partway through: nothing in it was judged."""


class RootUnreachable(JobFailedPermanently):
    """The library folder did not answer, so the pass did nothing.

    Not `RootIsGone` (a row somebody removed): a drive not plugged in, a share that is off.
    Permanent for the job: the person plugs it in and scans again."""


def _root_answer(root_abs: Path) -> OSError | None:
    """Whether the root's own directory answers, as the error it gave if not. Blocking.

    One `stat`, before anything else; a root whose path has become a file is as unusable as one
    that is gone."""
    try:
        found = root_abs.stat()
    except OSError as error:
        return error
    if not stat_module.S_ISDIR(found.st_mode):
        return NotADirectoryError(errno.ENOTDIR, "the library path is not a folder")
    return None


def _quiet_from(base: Path, directory: Path) -> Path:
    """The highest folder from `directory` up to `base` that does not answer. Blocking."""
    while base in directory.parents and _root_answer(directory.parent) is not None:
        directory = directory.parent
    return directory


def _walk_confined(root_abs: Path, base: Path) -> Walk:
    """Walk `base` for media, but only once it is proved to sit under the root.

    `os.scandir` follows the *starting* directory, and a folder row can name a link or a junction
    that leads out of the root. A refusal comes back as a walk that SAW NOTHING (`looked` False)."""
    try:
        confine(root_abs, base)
    except PathEscape:
        log.warning("library.scan.folder_outside_root")
        return Walk(files=(), directories=(), looked=False)
    return walk_media(base)


class _Rows(Protocol):
    """The two stores a scan reads its rows through: a job's context, or a plan's."""

    @property
    def library(self) -> LibraryStore: ...

    @property
    def content(self) -> ContentStore: ...


def _renamed(rel_path: str, old: str, new: str) -> str:
    """This path with the folder `old` renamed to `new`, as `LibraryStore.move_folder` rewrites it."""
    if rel_path == old:
        return new
    if rel_path.startswith(old + "/"):
        return new + rel_path[len(old) :]
    return rel_path


@dataclass(frozen=True, slots=True)
class FolderMoves:
    """The folders a pass moves, as (from, to), in the order it moves them.

    A plan makes none of them, so its rows still sit at the old paths: a file is looked up where
    the rows record it now (`before`), and a row is judged at the path it would have (`of`)."""

    pairs: tuple[tuple[str, str], ...] = ()

    def before(self, rel_path: str) -> str:
        """Where the rows record, now, what will be at this path once the moves are made."""
        for was, now in reversed(self.pairs):
            rel_path = _renamed(rel_path, now, was)
        return rel_path

    def after(self, rel_path: str) -> str:
        """Where what the rows record at this path will be once the moves are made."""
        for was, now in self.pairs:
            rel_path = _renamed(rel_path, was, now)
        return rel_path

    def of(self, location: Location) -> Location:
        """A location as it will read once the moves are made; itself when nothing moves."""
        if not self.pairs:
            return location
        archive = location.archive_rel_path
        return replace(
            location,
            rel_path=self.after(location.rel_path),
            archive_rel_path=None if archive is None else self.after(archive),
        )


#: A pass whose folder rows have already moved, or that moves none.
NO_MOVES = FolderMoves()
