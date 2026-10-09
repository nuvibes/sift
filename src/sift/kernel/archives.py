# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a ZIP of pictures in place, refusing an unsafe archive from its index alone.

Nothing is decompressed until the central directory passes every check; videos are skipped."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from sift.kernel.paths import PathEscape, confine

__all__ = [
    "ARCHIVE_EXTENSIONS",
    "ArchiveRefused",
    "Member",
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


def extract_member(archive: Path, member: str, destination: Path, *, cache_dir: Path) -> int:
    """Write one member out, re-checking its name and measuring it; a liar is deleted."""
    _safe_name(member, archive)
    try:
        target = confine(cache_dir, destination)
    except PathEscape as escaped:
        raise ArchiveRefused("that is not a place inside the cache") from escaped

    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with zipfile.ZipFile(archive) as opened:
            declared = opened.getinfo(member).file_size
            with opened.open(member) as inside, target.open("wb") as out:
                for chunk in iter(lambda: inside.read(_CHUNK), b""):
                    written += len(chunk)
                    if written > MAX_MEMBER_BYTES:
                        raise ArchiveRefused(
                            "that file inside the archive is bigger than it claimed"
                        )
                    out.write(chunk)
        if written != declared:
            raise ArchiveRefused("that file inside the archive is not the size the archive claimed")
    except (KeyError, zipfile.BadZipFile, OSError) as broken:
        # Only the half-written file this call created, never anything indexed.
        target.unlink(missing_ok=True)  # nosemgrep: sift-no-file-removal-outside-delete-trash
        raise ArchiveRefused("that file could not be read out of the archive") from broken
    except ArchiveRefused:
        target.unlink(missing_ok=True)  # nosemgrep: sift-no-file-removal-outside-delete-trash
        raise
    return written


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
