# SPDX-License-Identifier: AGPL-3.0-or-later
"""How far an MP4 stores its audio from the video it belongs with.

In a well-made MP4 the audio and video for the same moment sit near each other on disk. In a badly
muxed one they do not, and a browser that seeks has to hold two read positions hundreds of megabytes
apart. Chrome cannot, so it falls into a loop of cancelled range requests: ten to fifty times the
bytes the video needs, sustained, for as long as it plays. Playing forward from the
start never notices, which is why such a file looks fine until somebody drags the scrubber.

`worst_gap` answers it as one number: the largest byte distance between a moment's video and the
audio nearest it in time.

## Why this reads the index rather than asking ffprobe

ffprobe can list packet positions, but it demuxes to do it, **reading more than the whole file**.
Import already pays one full read for the content hash and a second is not free on a large
library over a network share.

Everything needed is in the `moov` index, which is a few megabytes at the end (or the start) of the
file: `stco`/`co64` say where each chunk of a track lives, `stsc` how many samples are in it, `stsz`
how big each one is, `stts` how long each lasts, `mdhd` the timescale, and `hdlr` which track is the
video and which the audio. Samples sit end to end inside a chunk, so that is enough to place every
frame exactly, which is what a packet-level walk reports, arrived at without demuxing.

Measuring per chunk rather than per sample is not good enough: it understates a drifting file by
about a chunk's worth, which can be half the gap. Per sample it agrees with a packet walk to within
a fraction of a megabyte.

Which track is which comes from `hdlr`, never from track order. A file can carry its audio first,
and reading position instead of handler compares the streams inverted.

Edit lists are ignored. They shift presentation times a little; they do not move bytes, and this
measures a distance in bytes.
"""

from __future__ import annotations

import struct
from bisect import bisect_left
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from itertools import chain
from pathlib import Path

from sift.kernel.log import get_logger

log = get_logger(__name__)

#: A box header is a 32-bit size and a four-character type. A size of 1 means the real size is a
#: 64-bit value that follows it, and a size of 0 means the box runs to the end of the file.
_HEADER = 8
_LARGE_HEADER = 16

#: The most index this will read into memory. Two of these is a feature-length video at high
#: bitrate; past it the file is not one to guess about, so it is reported as unmeasurable rather
#: than parsed.
MAX_INDEX_BYTES = 64 * 1024 * 1024

#: Boxes that contain other boxes on the path down to a sample table. Walked into; nothing else is.
_CONTAINERS = frozenset({b"moov", b"trak", b"mdia", b"minf", b"stbl"})

_MOOV = b"moov"
_TRAK = b"trak"
_MDIA = b"mdia"
_MINF = b"minf"
_STBL = b"stbl"
_MDHD = b"mdhd"
_HDLR = b"hdlr"
_STCO = b"stco"
_CO64 = b"co64"
_STSC = b"stsc"
_STSZ = b"stsz"
_STTS = b"stts"

#: How far a file may store its audio from the video for the same moment before it needs
#: repairing, in bytes.
#:
#: Here rather than in the feature that acts on it, because two features compare against it (the
#: job that repairs a file and the screen that explains why) and a slice may not import a slice.
#: Two copies of this number would eventually disagree, and the screen would describe a file the
#: job had not touched.
#:
#: Twenty megabytes: seeking a file to its own worst moment in a browser, gaps under ten megabytes
#: behave and gaps of about fifty storm every time. The margin is deliberate: a browser sizes its
#: buffer from system memory, so a phone breaks earlier than a desktop.
NEEDS_REPAIR_BYTES = 20 * 1024 * 1024

#: The two handler types this cares about. A file carrying neither cannot be measured.
_VIDEO = b"vide"
_AUDIO = b"soun"


class Malformed(Exception):
    """The file does not parse as a box structure."""


@dataclass(frozen=True, slots=True)
class Box:
    """One box: what it is, where it starts, how long it is, and how long its own header is."""

    kind: bytes
    start: int
    size: int
    header: int

    @property
    def end(self) -> int:
        return self.start + self.size

    @property
    def body(self) -> int:
        return self.start + self.header


@dataclass(frozen=True, slots=True)
class Track:
    """One track, and a way to walk its samples without holding them all.

    `samples` is a factory rather than a list: the audio side has to be materialised for lookup, the
    video side does not, and a long video has hundreds of thousands of either.
    """

    kind: bytes
    samples: Callable[[], Iterator[tuple[int, float]]]


def _measure(blob: bytes, at: int, limit: int) -> Box:
    """Read one box header out of `blob` at `at`, given that nothing may run past `limit`.

    `limit` is what stops a short read: every caller passes a limit no larger than the bytes it
    really has, so a header that does not fit inside it does not fit at all.
    """
    if at + _HEADER > limit:
        raise Malformed("a box header runs past the end")
    size = struct.unpack_from(">I", blob, at)[0]
    kind = bytes(blob[at + 4 : at + 8])
    header = _HEADER
    if size == 1:
        if at + _LARGE_HEADER > limit:
            raise Malformed("a 64-bit box header runs past the end")
        size = struct.unpack_from(">Q", blob, at + 8)[0]
        header = _LARGE_HEADER
    elif size == 0:
        size = limit - at
    if size < header or at + size > limit:
        raise Malformed(f"box {kind!r} does not fit where it says it does")
    return Box(kind=kind, start=at, size=size, header=header)


def boxes(blob: bytes, start: int, end: int) -> Iterator[Box]:
    """The boxes laid end to end between two points. Raises `Malformed` on anything else."""
    at = start
    while at < end:
        box = _measure(blob, at, end)
        yield box
        at = box.end


def find(blob: bytes, start: int, end: int, kind: bytes) -> Box | None:
    """The first box of one type directly inside this range."""
    for box in boxes(blob, start, end):
        if box.kind == kind:
            return box
    return None


def index_box(path: Path, size: int) -> tuple[bytes, Box] | None:
    """The file's `moov`, read by seeking past the media rather than through it.

    Returns the index bytes and the box describing them, both offset so that box positions inside
    the returned blob are relative to it. None when there is no index, or it is larger than
    `MAX_INDEX_BYTES`, or the file does not parse.
    """
    with path.open("rb") as handle:
        at = 0
        while at < size:
            handle.seek(at)
            head = handle.read(_LARGE_HEADER)
            if len(head) < _HEADER:
                raise Malformed("a box header runs past the end")
            box = _measure(head, 0, size - at)
            if box.kind == _MOOV:
                if box.size > MAX_INDEX_BYTES:
                    return None
                handle.seek(at)
                blob = handle.read(box.size)
                if len(blob) < box.size:
                    raise Malformed("the index is shorter than it says it is")
                return blob, Box(kind=_MOOV, start=0, size=box.size, header=box.header)
            at += box.size
    return None


def _table(blob: bytes, box: Box, width: int) -> tuple[int, int]:
    """Where a full box's entries start and how many there are.

    Past the box header comes a version-and-flags word and then the count, which is the layout every
    table here has always had.
    """
    at = box.body + 8
    if at > box.end:
        raise Malformed(f"table {box.kind!r} has no count")
    count = struct.unpack_from(">I", blob, box.body + 4)[0]
    if at + count * width > box.end:
        raise Malformed(f"table {box.kind!r} claims more entries than it holds")
    return at, count


def _offsets(blob: bytes, stbl: Box) -> list[int]:
    """Where every chunk of this track begins, absolute in the file."""
    table = find(blob, stbl.body, stbl.end, _STCO) or find(blob, stbl.body, stbl.end, _CO64)
    if table is None:
        raise Malformed("a track has no chunk offsets")
    wide = table.kind == _CO64
    at, count = _table(blob, table, 8 if wide else 4)
    form = f">{count}{'Q' if wide else 'I'}"
    return list(struct.unpack_from(form, blob, at))


def _samples_per_chunk(blob: bytes, stbl: Box, chunks: int) -> list[int]:
    """How many samples each chunk holds, expanded from the run-length form `stsc` stores.

    `stsc` names only the chunks where the count *changes*: an entry `(first_chunk, samples, ...)`
    holds until the next entry's `first_chunk`. Chunk numbers in it are 1-based.
    """
    table = find(blob, stbl.body, stbl.end, _STSC)
    if table is None:
        raise Malformed("a track has no sample-to-chunk table")
    at, count = _table(blob, table, 12)
    runs = [
        (
            struct.unpack_from(">I", blob, at + i * 12)[0],
            struct.unpack_from(">I", blob, at + i * 12 + 4)[0],
        )
        for i in range(count)
    ]
    if not runs or runs[0][0] != 1:
        raise Malformed("a sample-to-chunk table does not start at the first chunk")
    filled = [0] * chunks
    for i, (first, per) in enumerate(runs):
        last = runs[i + 1][0] - 1 if i + 1 < len(runs) else chunks
        for chunk in range(first - 1, min(last, chunks)):
            filled[chunk] = per
    return filled


def _durations(blob: bytes, stbl: Box) -> Iterator[int]:
    """How long each sample lasts, in this track's own ticks, expanded from `stts`.

    `stts` is run-length: `(count, delta)` means `count` consecutive samples each lasting `delta`.
    """
    table = find(blob, stbl.body, stbl.end, _STTS)
    if table is None:
        raise Malformed("a track has no time-to-sample table")
    at, count = _table(blob, table, 8)
    for i in range(count):
        run, delta = struct.unpack_from(">II", blob, at + i * 8)
        for _ in range(run):
            yield delta


def _sizes(blob: bytes, stbl: Box) -> Iterator[int]:
    """How many bytes each sample takes, from `stsz`.

    A non-zero `sample_size` means every sample is that big and no table follows, which is how
    constant-bitrate audio is usually stored.
    """
    table = find(blob, stbl.body, stbl.end, _STSZ)
    if table is None:
        raise Malformed("a track has no sample size table")
    if table.body + 12 > table.end:
        raise Malformed("a sample size table is too short")
    uniform, total = struct.unpack_from(">II", blob, table.body + 4)
    if uniform:
        yield from (uniform for _ in range(total))
        return
    at = table.body + 12
    if at + total * 4 > table.end:
        raise Malformed("a sample size table claims more sizes than it holds")
    for i in range(total):
        yield struct.unpack_from(">I", blob, at + i * 4)[0]


def _samples(blob: bytes, stbl: Box, timescale: int) -> Iterator[tuple[int, float]]:
    """Every sample in this track as (byte position, the second it plays at).

    Samples sit end to end inside a chunk, so a sample's position is its chunk's start plus the
    sizes of the samples before it in that chunk. This is what a packet-level walk reports, arrived
    at from the index instead of by demuxing the media.
    """
    offsets = _offsets(blob, stbl)
    per_chunk = _samples_per_chunk(blob, stbl, len(offsets))
    sizes = _sizes(blob, stbl)
    ticks = _durations(blob, stbl)
    elapsed = 0
    for chunk, held in zip(offsets, per_chunk, strict=True):
        at = chunk
        for _ in range(held):
            size = next(sizes, None)
            step = next(ticks, None)
            if size is None or step is None:
                return
            yield at, elapsed / timescale
            at += size
            elapsed += step


def _timescale(blob: bytes, mdia: Box) -> int:
    """Ticks per second for this track, from `mdhd`. Version 1 widens two fields, not this one."""
    box = find(blob, mdia.body, mdia.end, _MDHD)
    if box is None:
        raise Malformed("a track has no media header")
    version = blob[box.body]
    at = box.body + (20 if version == 1 else 12)
    if at + 4 > box.end:
        raise Malformed("a media header is too short to hold a timescale")
    scale = int(struct.unpack_from(">I", blob, at)[0])
    if scale == 0:
        raise Malformed("a track has a zero timescale")
    return scale


def _handler(blob: bytes, mdia: Box) -> bytes:
    """What kind of track this is: `vide`, `soun`, or something this does not care about."""
    box = find(blob, mdia.body, mdia.end, _HDLR)
    if box is None:
        raise Malformed("a track has no handler")
    at = box.body + 8
    if at + 4 > box.end:
        raise Malformed("a handler box is too short to name a type")
    return bytes(blob[at : at + 4])


def tracks(blob: bytes, moov: Box) -> list[Track]:
    """Every video and audio track in an index, as chunk positions against chunk times."""
    found: list[Track] = []
    for trak in boxes(blob, moov.body, moov.end):
        if trak.kind != _TRAK:
            continue
        mdia = find(blob, trak.body, trak.end, _MDIA)
        if mdia is None:
            continue
        kind = _handler(blob, mdia)
        if kind not in (_VIDEO, _AUDIO):
            continue
        minf = find(blob, mdia.body, mdia.end, _MINF)
        stbl = find(blob, minf.body, minf.end, _STBL) if minf is not None else None
        if stbl is None:
            raise Malformed("a track has no sample table")
        scale = _timescale(blob, mdia)
        found.append(
            Track(kind=kind, samples=partial(_samples, blob, stbl, scale)),
        )
    return found


def gap_between(video: Iterator[tuple[int, float]], audio: list[tuple[int, float]]) -> int:
    """The largest byte distance between a video sample and the audio sample nearest it in time.

    The video side is consumed as it is produced rather than held, because a long video has hundreds
    of thousands of samples and only the audio side needs to be indexed for lookup.
    """
    ordered = sorted(audio, key=lambda pair: pair[1])
    when = [moment for _, moment in ordered]
    worst = 0
    for where, moment in video:
        near = min(bisect_left(when, moment), len(ordered) - 1)
        worst = max(worst, abs(ordered[near][0] - where))
    return worst


def worst_gap(path: Path) -> int | None:
    """The largest byte distance between a moment's video and the audio nearest it in time.

    Zero for a video with no audio track at all. There is no distance for such a file to get wrong,
    so it is answered rather than left unknown: a silent video cannot make a browser hold two read
    positions apart.

    None when the file cannot be *read*: no index, an index too large to hold, no video track, a
    track whose tables yield nothing, a container that is not MP4 at all (Matroska keeps none of
    this), or anything that does not parse as boxes. A file that cannot be measured is not a file
    that is known to be fine, and the caller decides what to do about that.
    """
    # The whole measurement is inside the guard, not just the parse. `Track.samples` is lazy, so a
    # table that lies about its length raises when it is walked rather than when it is found, and
    # a guard that stops at `tracks()` lets exactly those files throw.
    try:
        size = path.stat().st_size
        held = index_box(path, size)
        if held is None:
            return None
        blob, moov = held
        found = tracks(blob, moov)
        video = [track for track in found if track.kind == _VIDEO]
        audio = [track for track in found if track.kind == _AUDIO]
        if video and not audio:
            return 0
        # A track that walks to nothing must not read as a gap of zero, which is the answer meaning
        # "perfectly interleaved". Nothing measured is None, and the caller treats that as unknown.
        worst: int | None = None
        for sound in audio:
            listened = list(sound.samples())
            if not listened:
                continue
            for picture in video:
                frames = picture.samples()
                first = next(frames, None)
                if first is None:
                    continue
                worst = max(worst or 0, gap_between(chain([first], frames), listened))
        return worst
    except (Malformed, OSError, struct.error, ValueError):
        log.debug("interleave.unreadable", path=str(path))
        return None


__all__ = ["MAX_INDEX_BYTES", "NEEDS_REPAIR_BYTES", "Box", "Malformed", "Track", "worst_gap"]
