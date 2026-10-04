# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a ZIP of pictures without unpacking it.

A gallery arrives as a `.zip` far more often than as a folder, and the obvious way to support that
(unpack it beside itself) is not taken. It would mean a second copy
of every picture on somebody's disk, permanently, and Sift writing into a library it has always
promised only to read. So an archive is INDEXED IN PLACE: each picture inside it becomes an ordinary
asset whose location happens to say "inside that archive, under that name", and the bytes stay where
they are.

## This module is a gate, not a convenience

Everything Sift ingests today it can verify by DECODING: the bytes are what the extension claims
and ffmpeg can read them. An archive is the first thing that is not like that: what arrives is a
directory of promises about files nobody has looked at yet, written by whoever made the archive.
So the shape here is deliberately the same as the ingress gate's: a validation that returns proof,
and readers downstream that take the proof rather than a path.

`inspect` reads the central directory ONLY (the index at the end of the file, which says what is
inside and how big each thing claims to be) and refuses the whole archive before a single byte of
content is decompressed. That ordering is the point. A decompression bomb is only dangerous once you
start decompressing it, and every check that matters can be made from the index.

## What it refuses, and why each one

- **A member whose name escapes.** `../../.ssh/authorized_keys` is a real attack with a name
  (zip-slip), and it works because the obvious extraction loop joins the member's name to a
  directory. Refused at the index, and the destination is confined again at extraction, because one
  guard that has to be remembered is not a guard.
- **A bomb.** A cap on how many members, on how big the archive claims to expand to, on any single
  member, and on the ratio between packed and unpacked. All four are read off the index.
- **A lying index.** What was written is measured against what the index promised, and a member
  that does not match is thrown away rather than kept. Without this the caps above are advisory:
  an index can claim 4 KB and stream forever.
- **Encryption.** Refused with a sentence. Sift has nowhere to ask for a password and a member it
  cannot read is not a picture.

Nested archives are neither refused nor opened: they are skipped, the same as a `.txt` sitting
beside the pictures. That is what keeps this to one level, which is what makes the caps mean
something: a cap you can nest under is not a cap.

## Only still pictures

A video inside an archive is skipped too, and that is a product decision rather than a limitation.
Playing one means materialising the whole file to seek around in, which is the second copy this
whole design exists to avoid, and it would happen silently, the first time somebody pressed play
on what looked like an ordinary video.
"""

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

#: What Sift will open. One family, deliberately: every format added here is another parser reading
#: a stranger's bytes, and a `.zip` is what galleries actually arrive as.
ARCHIVE_EXTENSIONS = frozenset({".zip"})

#: How many members an archive may declare. A shoot is hundreds of pictures; ten thousand is far
#: past anything anybody photographs and well short of what it costs to walk an index.
MAX_MEMBERS = 10_000

#: How big everything inside may claim to be, added up. Generous enough for a raw shoot and small
#: enough that a bomb never gets as far as being read.
MAX_TOTAL_BYTES = 32 * 1024**3

#: How big any ONE member may claim to be. A picture is megabytes; a gigabyte of "picture" is not.
MAX_MEMBER_BYTES = 1024**3

#: How much bigger the contents may be than the archive. Pictures are already compressed, so a real
#: gallery sits near 1; the classic bomb is thousands. Measured over the whole archive rather than
#: per member, because a single tiny text file legitimately compresses very well.
MAX_RATIO = 200

#: Small enough that a member is never held in memory whole, big enough not to syscall per kilobyte.
_CHUNK = 1 << 20


class ArchiveRefused(Exception):
    """This archive will not be indexed, and the sentence says why.

    One exception for every refusal rather than one per rule, because the caller does the same
    thing with all of them: leave the file alone and record the reason where somebody can read it.
    """


@dataclass(frozen=True, slots=True)
class Member:
    """One file inside an archive, as the index describes it.

    `path` is the name as it will be stored and asked for again later, normalised to forward slashes
    and already proved not to escape. `size_bytes` is what the index CLAIMS: it is a promise, and
    `extract_member` is where the promise is checked against what actually came out.
    """

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
    """Everything worth indexing inside an archive, having refused the archive if it is not safe.

    `wanted` is the set of lowercase extensions to keep, handed in rather than known here, so this
    module has no opinion about what Sift accepts and there is no second media allowlist to drift
    from the real one.

    Refusals are about the ARCHIVE and skips are about a member: an unsafe name or a bomb means
    nothing in this file is indexed, while a `.txt`, a nested `.zip` or a video is simply not one of
    the pictures. That asymmetry is deliberate. A stray file beside the photographs is ordinary; a
    member named `../..` is somebody trying something, and indexing the rest of that archive as
    though nothing happened would be answering an attack with a shrug.

    The order is: open the index, refuse on the index, and only then hand anything back. Nothing in
    here decompresses a byte.
    """
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

    # Checked last, once, over the whole archive: per member it would refuse an ordinary readme
    # sitting beside the pictures. `packed` can be nought for an archive of empty files, which is
    # not a bomb and must not divide.
    if packed > 0 and unpacked / packed > MAX_RATIO:
        raise ArchiveRefused(
            "this archive unpacks to far more than its size, so Sift will not open it"
        )

    return found


def extract_member(archive: Path, member: str, destination: Path, *, cache_dir: Path) -> int:
    """Write one member out to `destination`, and return how many bytes it really was.

    Two guards, and both are needed even though either alone looks sufficient. The name is proved
    safe again: this is called with a name off a database row, which may have been written by an
    older version of Sift or restored from somebody's backup, exactly the case where a row outlives
    the code that checked it. And the destination is confined to the cache, so a caller that builds
    a path wrongly cannot write outside it.

    The size is measured while it is written, not read off the index. An index that claims four
    kilobytes and then streams for ever is the whole of the bomb attack that survives every check
    made before decompression starts, so the stream is cut at the cap and a member whose real size
    disagrees with its promise is deleted rather than kept. Deleted rather than left: a half-written
    picture that stays on disk is a file the rest of Sift will treat as real.
    """
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
        # Removes only the half-written file this call created a moment ago, never anything
        # indexed. A refusal that left one behind would look to every later read like a whole one.
        target.unlink(missing_ok=True)  # nosemgrep: sift-no-file-removal-outside-delete-trash
        raise ArchiveRefused("that file could not be read out of the archive") from broken
    except ArchiveRefused:
        target.unlink(missing_ok=True)  # nosemgrep: sift-no-file-removal-outside-delete-trash
        raise
    return written


def _safe_name(raw: str, archive: Path) -> str:
    """A member's name as Sift will store it, or a refusal.

    Backslashes first, because an archive written on Windows can carry them as separators and a
    name checked before they are normalised is a name checked in the wrong shape: `..\\..\\x`
    has no `..` component until it does.
    """
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
