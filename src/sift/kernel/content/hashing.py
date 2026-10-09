# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file's identity (BLAKE3 over a sample of its bytes), the whole-file digest and oshash.

The sample layout below is part of every stored identity: changing it is a schema change."""

from __future__ import annotations

import asyncio
import os
import stat
import struct
from pathlib import Path

from blake3 import blake3

from sift.kernel import lanes
from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.kernel.ingress import IngressResult
from sift.kernel.landing import register_landing
from sift.kernel.log import get_logger, timing_hook
from sift.kernel.paths import O_NONBLOCK

log = get_logger(__name__)

# Bounds memory: a worker per core holds one chunk.
CHUNK_BYTES = 4 * 1024 * 1024

# Streamed on one thread, never mmap: a file truncated while mapped raises SIGBUS and kills the
# server, and the disk, not the hash, is the bottleneck.
_THREADS = 1


class FileStillChanging(Exception):
    """The file changed while it was being read, so the digest describes nothing."""


def _digest_settled(path: Path, expected_size: int) -> str:
    """Hash a file, refusing a non-regular file or one that moved underneath the read."""
    fd = os.open(path, os.O_RDONLY | O_NONBLOCK)
    before = os.fstat(fd)

    if not stat.S_ISREG(before.st_mode):
        # A named pipe would block the read for ever, and a library can hold anything.
        os.close(fd)
        raise FileStillChanging(f"{path.name} is not a regular file")

    if before.st_size != expected_size:
        # It changed between the gate and here.
        os.close(fd)
        raise FileStillChanging(
            f"{path.name} was {expected_size} bytes when it was checked and is {before.st_size} now"
        )

    hasher = blake3(max_threads=_THREADS)
    read = 0

    # Read exactly the measured size, so a file still growing cannot keep the loop going.
    with os.fdopen(fd, "rb") as handle:
        while read < expected_size:
            chunk = handle.read(min(CHUNK_BYTES, expected_size - read))
            if not chunk:
                break
            hasher.update(chunk)
            read += len(chunk)
        after = os.fstat(fd)

    if (
        read != expected_size
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        raise FileStillChanging(f"{path.name} is still being written to")

    return hasher.hexdigest()


#: Written first, so an identity never equals any other digest; a new version is a migration.
IDENTITY_VERSION = b"sift-identity-v1"

#: The head holds containers' headers; the tail holds the index most tools write last.
IDENTITY_END_BYTES = 1024 * 1024

IDENTITY_SAMPLES = 12
IDENTITY_SAMPLE_BYTES = 128 * 1024

IDENTITY_WHOLE_BELOW = 2 * IDENTITY_END_BYTES + IDENTITY_SAMPLES * IDENTITY_SAMPLE_BYTES


def identity_ranges(size: int) -> list[tuple[int, int]]:
    """The (offset, length) pairs the identity reads; integer arithmetic, as they are identity."""
    if size <= IDENTITY_WHOLE_BELOW:
        return [(0, size)]
    middle_start = IDENTITY_END_BYTES
    middle_end = size - IDENTITY_END_BYTES
    room = middle_end - middle_start - IDENTITY_SAMPLE_BYTES
    ranges = [(0, IDENTITY_END_BYTES)]
    for index in range(IDENTITY_SAMPLES):
        ranges.append(
            (middle_start + room * index // (IDENTITY_SAMPLES - 1), IDENTITY_SAMPLE_BYTES)
        )
    ranges.append((middle_end, IDENTITY_END_BYTES))
    return ranges


def _identity_settled(path: Path, expected_size: int) -> str:
    """Digest a file's identity ranges, refusing a file that moved underneath the read."""
    fd = os.open(path, os.O_RDONLY | O_NONBLOCK)
    before = os.fstat(fd)

    if not stat.S_ISREG(before.st_mode):
        os.close(fd)
        raise FileStillChanging(f"{path.name} is not a regular file")

    if before.st_size != expected_size:
        os.close(fd)
        raise FileStillChanging(
            f"{path.name} was {expected_size} bytes when it was checked and is {before.st_size} now"
        )

    hasher = blake3(max_threads=_THREADS)
    hasher.update(IDENTITY_VERSION)
    hasher.update(struct.pack("<Q", expected_size))

    with os.fdopen(fd, "rb") as handle:
        for offset, length in identity_ranges(expected_size):
            handle.seek(offset)
            read = 0
            while read < length:
                chunk = handle.read(min(CHUNK_BYTES, length - read))
                if not chunk:
                    raise FileStillChanging(f"{path.name} is still being written to")
                hasher.update(chunk)
                read += len(chunk)
        after = os.fstat(fd)

    if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
        raise FileStillChanging(f"{path.name} is still being written to")

    return hasher.hexdigest()


async def identity_file(checked: IngressResult) -> str:
    """A verified file's identity as lowercase hex, read in the storage's lane."""
    with timing_hook("content.identity", file_size=checked.size, file_type=checked.media.name):
        async with lanes.reading(checked.path):
            return await asyncio.to_thread(_identity_settled, checked.path, checked.size)


async def hash_file(checked: IngressResult) -> str:
    """The whole-file BLAKE3 digest of a verified file; not the identity."""
    with timing_hook("content.hash", file_size=checked.size, file_type=checked.media.name):
        # In the storage's lane: a share serving a dozen whole-file reads collapses.
        async with lanes.reading(checked.path):
            return await asyncio.to_thread(_digest_settled, checked.path, checked.size)


#: 64 bits; a cache key, not a security boundary, so chosen against accident only.
CACHE_DIGEST_CHARS = 16


def _digest_cache_file(path: Path) -> str | None:
    """Hash a file in Sift's own cache, or None if nothing regular and readable is there."""
    try:
        fd = os.open(path, os.O_RDONLY | O_NONBLOCK)
    except OSError:
        return None

    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        return None

    hasher = blake3(max_threads=_THREADS)
    try:
        with os.fdopen(fd, "rb") as handle:
            while chunk := handle.read(CHUNK_BYTES):
                hasher.update(chunk)
    except OSError:
        return None
    return hasher.hexdigest()[:CACHE_DIGEST_CHARS]


async def digest_cache_file(path: Path) -> str | None:
    """The short digest of a file Sift generated, as lowercase hex, or None."""
    with timing_hook("content.cache_digest"):
        return await asyncio.to_thread(_digest_cache_file, path)


#: Fixed by the algorithm: the value lies in everybody computing the same one.
OSHASH_CHUNK = 64 * 1024


def oshash(head: bytes, tail: bytes, size: int) -> str:
    """The OpenSubtitles hash stash-boxes key on: length plus both ends summed as 64-bit words."""
    if size <= 8:
        raise ValueError("a file of eight bytes or fewer has no oshash")
    if len(head) % 8 or len(tail) % 8:
        raise ValueError("each end must be a whole number of 8-byte words")

    total = size
    for buffer in (head, tail):
        for at in range(0, len(buffer), 8):
            total += struct.unpack_from("<Q", buffer, at)[0]
    return f"{total & 0xFFFFFFFFFFFFFFFF:016x}"


def _read_both_ends(path: Path) -> str | None:
    """Hash both ends from one descriptor, measured there, as the length is part of the hash."""
    with open(path, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        if size <= 8:
            return None
        chunk = OSHASH_CHUNK if size >= OSHASH_CHUNK else (size // 8) * 8
        head = handle.read(chunk)
        handle.seek(-chunk, os.SEEK_END)
        tail = handle.read(chunk)
    return oshash(head, tail, size)


async def oshash_file(path: Path) -> str | None:
    """The exact-file hash of a file on disk, or None if it is too small to have one."""
    with timing_hook("content.oshash"):
        async with lanes.reading(path):
            return await asyncio.to_thread(_read_both_ends, path)


def fingerprint(text: str) -> str:
    """A short, stable tag naming a derivative's settings in Sift's cache; not an identity."""
    return blake3(text.encode("utf-8")).hexdigest()[:8]


#: Only onto a row with none, keyed by identity, so a second landing writes nothing.
_RECORD_WHOLE_DIGEST = (
    "UPDATE assets SET whole_digest = ? WHERE identity = ? AND whole_digest IS NULL"
)


def _digest_of_whole(path: Path) -> str:
    """The digest of every byte of a file on disk, measuring the file first."""
    return _digest_settled(path, os.stat(path).st_size)


async def record_whole_digest(database: Database, path: Path, identity: str) -> str:
    """Digest a file end to end and write it against the identity those bytes have."""
    with timing_hook("content.whole_digest"):
        async with lanes.reading(path):
            digest = await asyncio.to_thread(_digest_of_whole, path)
    async with database.write() as connection:
        await connection.execute(_RECORD_WHOLE_DIGEST, (digest, identity))
    return digest


class WholeDigestLanding:
    """The whole-file digest, taken while a landing's bytes are still on the local disk."""

    name = "whole-digest"

    def __init__(self, database: Database) -> None:
        self._database = database

    async def landed(
        self, path: Path, identity: str, settings: Settings, *, root_id: str | None = None
    ) -> None:
        # `root_id` is not asked: every file that lands is digested, whichever folder it goes to.
        digest = await record_whole_digest(self._database, path, identity)
        log.info("content.whole_digest.recorded", identity=identity, digest=digest[:16])


register_landing(WholeDigestLanding.name, WholeDigestLanding)
