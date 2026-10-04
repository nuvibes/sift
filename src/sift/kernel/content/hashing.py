# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file's identity (a BLAKE3 digest of a SAMPLE of its bytes) and, separately, the name the
world uses.

Three hashes here. The identity digest at the top is Sift's own answer to "is this the same file"
and the one everything internal is keyed on: the file's size, its two ends and a fixed number of
samples spaced through the middle, about 3.5 MiB however long the file is. The whole-file digest
below it is kept for a check-for-damage pass that wants every byte; nothing reads it as
identity. The OpenSubtitles hash at the
bottom is nobody's idea of a good digest, and it is here because it is the one a public stash-box
understands: it reads 128 KB instead of the whole file, and it is the only way to ask somebody else
"do you know this exact file". None of the three replaces another.

Why a sample and not the whole file: the cost of a digest is the storage, not the arithmetic.
BLAKE3 outruns a network share by a hundred times, so a whole-file identity spends ten hours
reading a terabyte over one before Sift can do anything else with it, and nothing in the tree
consumes the exactness that buys: a scan skips unchanged files and never re-hashes them. What
identity is for (a second copy is a location and not a file; a moved file keeps its tags; copies
share pictures) needs stability and no collisions in practice, which the size plus fourteen
positions gives: two different media files collide only if they are the same length and identical
at every sampled byte.

The digest *is* the identity, so it ends up in the database next to every asset, and a change to
what it computes would not look like a bug. It would look like an empty library: every file
re-hashed to a value matching nothing, re-imported as a new asset, and every tag and rating left
behind on a row nothing points at any more. A checked-in digest of a checked-in file is the alarm
on that, and it is why the dependency is pinned, and why the sample's layout below is a set of
constants a change to which is a schema change, never a tuning.

Hashing runs after the ingress gate and never before it. The gate takes a path and returns proof
that the bytes behind it were checked; this takes the proof. Nothing hashes a file Sift has
refused, and it is the type of the argument that says so rather than a convention.
"""

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

# Big enough that the per-call overhead disappears, small enough that a 4K video does not arrive
# in memory all at once. A worker per core, each holding one of these, is the memory ceiling.
CHUNK_BYTES = 4 * 1024 * 1024

# Read in chunks rather than mapped into memory, and hashed on one thread rather than several.
# Both are deliberate, and both look like the slower choice until you measure them.
#
# `update_mmap` is genuinely faster in a benchmark, several times faster, because it can hash
# the pages in parallel. But it maps a file Sift does not own. These files are the user's, sitting
# in the user's folders, and a scan of a big library runs for a long time; if something truncates
# a file while it is mapped, touching a page past the new end of it raises SIGBUS. That is not an
# exception. It is a signal, it cannot be caught in Python, and it takes the whole server down,
# in exchange for speed in the one place that has none to spare.
#
# Because the speed is not real. A single thread hashes many times faster than an NVMe drive can
# feed it and far faster again than a NAS over gigabit ethernet. The disk is the bottleneck and
# always will be; the hash is not.
#
# And multithreading a *streamed* hash actively costs: the thread pool is spun up per `update()`
# call, so 4 MiB chunks with `max_threads=AUTO` hash several times slower than one thread does.
# The parallelism only pays for itself when the whole file is one call, which is exactly the case
# that requires the mmap.
_THREADS = 1


class FileStillChanging(Exception):
    """The file changed while it was being read, so the digest describes nothing.

    A half-written file hashes perfectly happily. The digest is of the half, and it is wrong the
    moment the writer finishes, which is how a download in progress gets indexed as an asset
    that will never be seen again.
    """


def _digest_settled(path: Path, expected_size: int) -> str:
    """Hash a file, and refuse to return a digest of one that moved underneath us.

    Opened non-blocking, and its regular-file-ness checked on the open descriptor. A named pipe
    left in a library (opened the ordinary blocking way) would hang this read until someone
    wrote to it, and nobody will, so the worker would never come back. `O_NONBLOCK` makes the open
    of a pipe return instead of wait, and the `S_ISREG` check on the descriptor rejects it before a
    byte is read. Checking and reading the *same* descriptor means a swap between the check and the
    read cannot slip a special file through: this is the same guard the ingress gate opens with.

    Size and modification time are read off that descriptor before and after. A file being appended
    to fails on size; one rewritten in place fails on mtime, in nanoseconds, so a rewrite inside the
    same second does not slip through. The number of bytes actually read is checked too, which is
    the case neither stat catches: a file shrinking back to its original length between the reads.

    The watcher already waits for a file to settle before it gets here, and this is not a second
    opinion about that: it is the check that the wait was long enough. There is no way to ask a
    filesystem whether someone still has the file open for writing, so the only honest test is to
    read it and see whether it held still.
    """
    fd = os.open(path, os.O_RDONLY | O_NONBLOCK)
    before = os.fstat(fd)

    if not stat.S_ISREG(before.st_mode):
        # A named pipe read like a file blocks until somebody writes to it, and nobody is going
        # to: the read never returns, and the worker never comes back. A library is a directory
        # someone else assembled, and it can have anything in it.
        os.close(fd)
        raise FileStillChanging(f"{path.name} is not a regular file")

    if before.st_size != expected_size:
        # It changed between the gate and here, which is already the answer.
        os.close(fd)
        raise FileStillChanging(
            f"{path.name} was {expected_size} bytes when it was checked and is {before.st_size} now"
        )

    hasher = blake3(max_threads=_THREADS)
    read = 0

    # Reads exactly the number of bytes the file was measured to have, and not one more. A plain
    # read-until-empty loop on a file that is still growing does not finish while the writer keeps
    # up with the reader, which is not a hypothetical: a download in progress grows at the speed
    # of the disk, and so does the reader. The size check afterwards is what notices it grew; this
    # is what makes sure there is an afterwards. `os.fdopen` takes ownership of the descriptor, so
    # leaving the block closes it.
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


# --- the identity: a sample of the file -------------------------------------------------------

#: Written into the digest first, so an identity can never equal a whole-file digest of the same
#: bytes or a digest by any other layout. Bump the version and every file re-identifies; that is
#: a migration, and the name says so.
IDENTITY_VERSION = b"sift-identity-v1"

#: How much of each end is read whole. The head carries every container's index and headers; the
#: tail carries the index of a file written by a tool that puts it last, which is most of them.
IDENTITY_END_BYTES = 1024 * 1024

#: The samples between the ends: how many, and how long each is.
IDENTITY_SAMPLES = 12
IDENTITY_SAMPLE_BYTES = 128 * 1024

#: A file this size or smaller is digested whole: sampling it would read every byte anyway, and a
#: whole-file read is one range instead of fourteen.
IDENTITY_WHOLE_BELOW = 2 * IDENTITY_END_BYTES + IDENTITY_SAMPLES * IDENTITY_SAMPLE_BYTES


def identity_ranges(size: int) -> list[tuple[int, int]]:
    """The (offset, length) pairs the identity reads, in file order, for a file of this size.

    A file under the budget is one range, the whole of it. Above it: the head, the samples spaced
    evenly through the middle so that the first begins where the head ends and the last ends where
    the tail begins, then the tail. Integer arithmetic throughout, so the same size always gives
    the same offsets on every machine: the offsets are part of the identity.
    """
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
    """Digest a file's identity ranges, refusing a file that moved underneath the read.

    The same discipline as `_digest_settled` (opened non-blocking, checked to be a regular file
    on the descriptor, measured before and after), because a file being written is as wrong a
    thing to identify by a sample as by the whole. Each range is read exactly, and a short read is
    the file having shrunk, which is refused rather than digested.
    """
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
    """A verified file's identity, as lowercase hex.

    Off the event loop and in the storage's lane like the whole-file digest, though it reads about
    3.5 MiB at most: fourteen seeks on a share are fourteen round trips, and the lane is what keeps
    a scan of a thousand new files from asking for them all at once.
    """
    with timing_hook("content.identity", file_size=checked.size, file_type=checked.media.name):
        async with lanes.reading(checked.path):
            return await asyncio.to_thread(_identity_settled, checked.path, checked.size)


async def hash_file(checked: IngressResult) -> str:
    """The whole-file BLAKE3 digest of a verified file, as lowercase hex.

    Not the identity; kept for a pass that wants every byte, such as checking
    a library for damage against what it hashed to before.

    Off the event loop: this reads the whole file, and a big one over a network share can take
    minutes. Blocking the loop for that would stall every request the server is serving.
    """
    with timing_hook("content.hash", file_size=checked.size, file_type=checked.media.name):
        # In the storage's lane: this is the heaviest read Sift makes of a file, the whole of it
        # end to end, and a share asked to serve a dozen of them at once collapses.
        async with lanes.reading(checked.path):
            return await asyncio.to_thread(_digest_settled, checked.path, checked.size)


# --- naming a file Sift built itself ------------------------------------------------------------

#: How much of the digest goes into an address. Sixteen hex characters is 64 bits.
#:
#: Not a security boundary, so the length is chosen against accident rather than against an
#: attacker: a collision would have to be between two versions of the SAME picture, since the rest
#: of the address already names the asset and the kind. Guessing one changes nothing either, because
#: the digest is a cache key and the permission check runs on every request that reaches the server.
CACHE_DIGEST_CHARS = 16


def _digest_cache_file(path: Path) -> str | None:
    """Hash a file out of Sift's own cache, or None if there is nothing readable there.

    Unlike a library file this one was written by Sift a moment ago and nothing else is touching
    it, so there is no settling to check for. What is still checked is that it is a regular file,
    on the descriptor rather than on the path: the cache directory is a directory on somebody's
    machine, and a named pipe left where a thumbnail should be would otherwise hang this read
    until a writer that is never coming turns up.

    None rather than a raise. A cache file that has been deleted or cannot be read is an ordinary
    thing to meet (the whole directory is disposable and rebuildable), and the caller records
    no digest, which reads as "serve this the careful way".
    """
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
    """The short digest of a file Sift generated, as lowercase hex, or None.

    Off the event loop: a re-muxed copy of a video is a whole file, and reading one on the loop
    would stall every request the server is answering.
    """
    with timing_hook("content.cache_digest"):
        return await asyncio.to_thread(_digest_cache_file, path)


# --- the exact-file hash the public stash-boxes key on ----------------------------------------

#: How much of each end goes into it. Fixed by the algorithm rather than chosen here: a different
#: number is a different hash, and the whole value of this one is that everybody computes the same
#: one. Also why it is not a setting.
OSHASH_CHUNK = 64 * 1024


def oshash(head: bytes, tail: bytes, size: int) -> str:
    """The OpenSubtitles hash: the file's length, plus both of its ends added up as numbers.

    Sift already has BLAKE3, which is a better answer to "are these the same bytes" in every way
    that matters locally: it reads the whole file and it cannot collide by accident. This exists
    for a different reason: it is the name the rest of the world files a video under. A stash-box
    that has never seen Sift can be asked "do you know this file", and this is the only question it
    understands about a file's exact identity.

    It is cheap in a way BLAKE3 is not: two reads of 64 KB, wherever the file sits, against a full
    pass over what may be twenty gigabytes on a network share. That is what makes it affordable to
    have on every video rather than on the ones somebody asks about.

    It is also weak, and it is used as though it were: an agreement here is strong evidence and a
    disagreement is no evidence at all, because every re-encode of one video is a different file
    with a different value. The perceptual hash next door is what answers the general question.

    The arithmetic is deliberately plain: 64-bit little-endian words summed and allowed to wrap,
    which is what every other implementation does. Both ends must be whole words; a file shorter
    than 64 KB uses as much of itself as divides by eight, and a file of eight bytes or fewer has
    no hash at all rather than a misleading one.
    """
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
    """Measure the file, read its two ends, and hash them. Blocking; callers use `oshash_file`.

    The length is taken from the open descriptor rather than from anything remembered elsewhere,
    and that is not tidiness. The length is PART OF THE HASH, so a stored size that is a version
    out of date does not produce a slightly stale answer. It produces a confidently wrong one,
    which no stash-box will match and nothing downstream can tell from a file nobody has heard of.

    The file is opened once and both ends are read from the same descriptor, so a file replaced
    between the two reads cannot contribute one end of the old bytes and one of the new. It is not
    re-checked for having settled the way the digest is: this reads 128 KB rather than the whole
    file, so there is no long window to be wrong about, and a file still being written is refused
    upstream in any case.
    """
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
    """The exact-file hash of a file on disk, or None if it is too small to have one.

    None rather than a raise: a file below the floor is an ordinary thing to meet in a library, the
    caller is a probe that has plenty else to record about it, and there is nothing to retry.
    """
    with timing_hook("content.oshash"):
        async with lanes.reading(path):
            return await asyncio.to_thread(_read_both_ends, path)


def fingerprint(text: str) -> str:
    """A short, stable tag for a string, used to tell one derivative's settings from another.

    Not a security boundary and not an identity: it names a file in Sift's own cache directory,
    where the inputs are Sift's own parameter dictionaries and the worst a collision costs is a
    thumbnail regenerated once.
    """
    return blake3(text.encode("utf-8")).hexdigest()[:8]


# --- the whole-file digest, taken at the one moment it is affordable -----------------------------

#: What a landing writes the whole-file digest onto.
#:
#: Filtered to a row that has none, so a second landing of bytes the library already holds writes
#: nothing and a digest already there is never argued with. Keyed by IDENTITY rather than by an
#: asset id for the reason the registry is: a landing knows which bytes it read, and the asset row
#: those bytes belong to is settled by whoever wrote it.
_RECORD_WHOLE_DIGEST = (
    "UPDATE assets SET whole_digest = ? WHERE identity = ? AND whole_digest IS NULL"
)


def _digest_of_whole(path: Path) -> str:
    """The digest of every byte of a file on disk, measuring the file first.

    The size is read in this thread rather than handed in, because a landing is given a path and
    nothing else. It is the same descriptor-based settling check `_digest_settled` makes: what this
    adds is only where the expected size comes from.
    """
    return _digest_settled(path, os.stat(path).st_size)


async def record_whole_digest(database: Database, path: Path, identity: str) -> str:
    """Digest a file end to end and write it against the identity those bytes have.

    Off the event loop and in the storage's lane, like every other whole-file read here.
    """
    with timing_hook("content.whole_digest"):
        async with lanes.reading(path):
            digest = await asyncio.to_thread(_digest_of_whole, path)
    async with database.write() as connection:
        await connection.execute(_RECORD_WHOLE_DIGEST, (digest, identity))
    return digest


class WholeDigestLanding:
    """The whole-file digest, taken while the bytes are still on the local disk.

    `whole_digest` is NULL on every row of a library built since the identity became a sample of a
    file rather than the whole of it, and filling it afterwards is the most expensive sweep there
    is: every byte of every file, over whatever the library sits on. On a share that is days. So it
    is taken at the one moment it is not: a download, a drop, a paste or an upload, all of which
    wait in staging on this machine's own disk before they are copied into a library folder.

    **It is not free, and the registry's own wording is what makes that easy to miss.** Nothing at
    the landing has already streamed these bytes through Python: the copy into the library is
    `shutil.copyfile`, which hands the work to the operating system wherever it can. This is a
    second read of the staged file. What makes it worth making is where that read happens (local
    disk at gigabytes a second against a share at tens of megabytes), not that somebody else was
    paying for it.

    A scanned file is deliberately left alone. Nothing in Sift reads this column yet; what it is
    for is a pass that checks a library against what it hashed to before, and such a pass can only
    ever compare the rows that have one. Filling it for the files that arrive costs a local read
    each and fills it for every file Sift ever takes in; filling it for the files already in a
    library is the days-long sweep, and that stays a decision for the day something needs it.
    """

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
