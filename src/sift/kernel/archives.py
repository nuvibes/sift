# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a ZIP of pictures in place, refusing an unsafe archive from its index alone, and the
local copies a share's bytes are read into once.

Nothing is decompressed until the central directory passes every check; videos are skipped. A file
written into the cache is written under a name of its own (`PART_SUFFIX`) and renamed into place, so
a reader never opens half of one and two writers of the same picture never meet."""

from __future__ import annotations

import contextlib
import os
import stat
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from sift.kernel.paths import O_NONBLOCK, PathEscape, confine

__all__ = [
    "ARCHIVE_EXTENSIONS",
    "ArchiveRefused",
    "CopyChanged",
    "Member",
    "OpenArchive",
    "copy_settled",
    "extract_member",
    "inspect",
    "is_archive",
]

#: One family: every format added is another parser reading a stranger's bytes.
ARCHIVE_EXTENSIONS = frozenset({".zip"})

MAX_MEMBERS = 10_000

MAX_TOTAL_BYTES = 32 * 1024**3

MAX_MEMBER_BYTES = 1024**3

#: Over the whole archive, as one tiny text file legitimately compresses very well.
MAX_RATIO = 200

#: Small enough that a member is never held in memory whole, big enough not to syscall per kilobyte.
_CHUNK = 1 << 20

#: A share's own read size: plain 4 MB reads gave 24.5 to 26.4 MB/s where 128 KB samples gave 11.5
#: to 14.9 on the same share.
COPY_CHUNK = 4 * 1024 * 1024

#: The end of a file still being written into the cache; whatever keeps the cache passes it over.
PART_SUFFIX = ".part"

#: How many times a cache write tries again when its folder went in the moment it was made.
_PLACE_TRIES = 3


class ArchiveRefused(Exception):
    """This archive will not be indexed, and the sentence says why."""


@dataclass(frozen=True, slots=True)
class Member:
    """One file inside an archive; `size_bytes` is the index's claim, checked on extraction."""

    path: str
    size_bytes: int

    @property
    def name(self) -> str:
        """Just the filename, which is what a picture is called once it is an asset."""
        return self.path.rsplit("/", 1)[-1]


def is_archive(path: Path) -> bool:
    """Whether this is something to open rather than to index as a file."""
    return path.suffix.lower() in ARCHIVE_EXTENSIONS


def inspect(archive: Path, *, wanted: frozenset[str]) -> list[Member]:
    """What is worth indexing in an archive, refusing the whole archive from its index alone."""
    try:
        with zipfile.ZipFile(archive) as opened:
            entries = opened.infolist()
    except (zipfile.BadZipFile, OSError) as broken:
        raise ArchiveRefused("this file is not a readable archive") from broken

    if len(entries) > MAX_MEMBERS:
        raise ArchiveRefused(
            f"this archive says it holds {len(entries)} files, which is more than Sift will index"
        )

    packed = 0
    unpacked = 0
    found: list[Member] = []
    for entry in entries:
        if entry.flag_bits & 0x1:
            raise ArchiveRefused("this archive is password-protected, so Sift cannot read it")
        if entry.file_size > MAX_MEMBER_BYTES:
            raise ArchiveRefused(
                f"something inside this archive is too big to index: {entry.filename}"
            )
        packed += entry.compress_size
        unpacked += entry.file_size
        if unpacked > MAX_TOTAL_BYTES:
            raise ArchiveRefused("this archive says it unpacks to more than Sift will index")
        if entry.is_dir():
            continue
        name = _safe_name(entry.filename, archive)
        if _extension_of(name) in wanted:
            found.append(Member(path=name, size_bytes=entry.file_size))

    # Whole-archive, so a readme does not refuse it; `packed` is nought for empty files.
    if packed > 0 and unpacked / packed > MAX_RATIO:
        raise ArchiveRefused(
            "this archive unpacks to far more than its size, so Sift will not open it"
        )

    return found


def _cache_place(destination: Path, cache_dir: Path) -> Path:
    """`destination` proved inside the cache, its folder made. Asked again when the folder goes in
    the moment it is made: the cache's keeper removes a picture's folder with its last picture."""
    for attempt in range(1, _PLACE_TRIES + 1):
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            return confine(cache_dir, destination)
        except (PathEscape, OSError) as failed:
            going = not isinstance(failed, PathEscape) or isinstance(failed.__cause__, OSError)
            if not going or attempt == _PLACE_TRIES:
                raise ArchiveRefused("that is not a place inside the cache") from failed
    raise AssertionError("unreachable")  # pragma: no cover


def _part_of(target: Path) -> Path:
    """A name of this write's own beside `target`, renamed onto it once whole."""
    return target.with_name(f"{target.name}.{uuid.uuid4().hex}{PART_SUFFIX}")


class OpenArchive:
    """An archive opened at its first member and kept open for the rest, so a share is not asked
    for its index once per picture. Used from one thread at a time."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._zip: zipfile.ZipFile | None = None

    def zip(self) -> zipfile.ZipFile:
        """The archive, opened on first asking. Blocking."""
        if self._zip is None:
            self._zip = zipfile.ZipFile(self.path)
        return self._zip

    def close(self) -> None:
        """Blocking."""
        if self._zip is not None:
            self._zip.close()
            self._zip = None


def extract_member(
    archive: Path,
    member: str,
    destination: Path,
    *,
    cache_dir: Path,
    opened: OpenArchive | None = None,
) -> int:
    """Write one member out, re-checking its name and measuring it; a liar is deleted. `opened`
    reads it through an archive already open, and leaves it open."""
    _safe_name(member, archive)
    target = _cache_place(destination, cache_dir)
    part = _part_of(target)
    written = 0
    try:
        with contextlib.ExitStack() as closing:
            if opened is None:
                zipped = closing.enter_context(zipfile.ZipFile(archive))
            else:
                zipped = opened.zip()
            declared = zipped.getinfo(member).file_size
            with zipped.open(member) as inside, part.open("wb") as out:
                for chunk in iter(lambda: inside.read(_CHUNK), b""):
                    written += len(chunk)
                    if written > MAX_MEMBER_BYTES:
                        raise ArchiveRefused(
                            "that file inside the archive is bigger than it claimed"
                        )
                    out.write(chunk)
        if written != declared:
            raise ArchiveRefused("that file inside the archive is not the size the archive claimed")
        # The cache's own part file into its place.
        os.replace(part, target)  # nosemgrep: sift-no-file-removal-outside-delete-trash
    except (KeyError, zipfile.BadZipFile, OSError) as broken:
        # Only the half-written file this call created, never anything indexed.
        part.unlink(missing_ok=True)  # nosemgrep: sift-no-file-removal-outside-delete-trash
        raise ArchiveRefused("that file could not be read out of the archive") from broken
    except ArchiveRefused:
        part.unlink(missing_ok=True)  # nosemgrep: sift-no-file-removal-outside-delete-trash
        raise
    return written


class CopyChanged(OSError):
    """The file changed while it was being copied, so the copy describes nothing."""


def copy_settled(source: Path, destination: Path, *, size: int) -> int:
    """Copy a file whole in the share's own read size, refusing one that is not the size the walk
    saw or that moves while it is read, as the identity read does. Blocking. Bytes copied.

    Opened non-blocking and checked on the descriptor, as the gate does, so a named pipe cannot
    hang it. `destination` is written under its own name and renamed into place.
    """
    fd = os.open(source, os.O_RDONLY | O_NONBLOCK | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise CopyChanged(f"{source.name} is not a regular file")
        if before.st_size != size:
            raise CopyChanged(f"{source.name} changed since it was listed")
        part = _part_of(destination)
        copied = 0
        try:
            with part.open("wb") as out:
                while copied < size:
                    chunk = handle.read(min(COPY_CHUNK, size - copied))
                    if not chunk:
                        break
                    out.write(chunk)
                    copied += len(chunk)
            after = os.fstat(handle.fileno())
            if (
                copied != size
                or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns
            ):
                raise CopyChanged(f"{source.name} is still being written to")
            # The cache's own part file into its place.
            os.replace(part, destination)  # nosemgrep: sift-no-file-removal-outside-delete-trash
        except BaseException:
            part.unlink(missing_ok=True)  # nosemgrep: sift-no-file-removal-outside-delete-trash
            raise
    return copied


def _safe_name(raw: str, archive: Path) -> str:
    """A member's name as Sift will store it, or a refusal; backslashes normalised first."""
    name = raw.replace("\\", "/")
    if name.startswith("/") or ":" in name.split("/")[0]:
        raise ArchiveRefused(f"{archive.name} names a file outside itself")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ArchiveRefused(f"{archive.name} names a file outside itself")
    if not parts:
        raise ArchiveRefused(f"{archive.name} holds a file with no name")
    return "/".join(parts)


def _extension_of(name: str) -> str:
    _, dot, extension = name.rpartition(".")
    return f".{extension.lower()}" if dot else ""
