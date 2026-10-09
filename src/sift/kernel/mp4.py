# SPDX-License-Identifier: AGPL-3.0-or-later
"""How far an MP4 stores its audio from the video it belongs with, read from the `moov` index.

A wide gap makes browsers storm on seeking; ffprobe would read the whole file."""

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

#: A box header; size 1 means a 64-bit size follows, size 0 runs to the end of the file.
_HEADER = 8
_LARGE_HEADER = 16

#: The most index read into memory; past it the file is unmeasurable rather than guessed at.
MAX_INDEX_BYTES = 64 * 1024 * 1024

#: Boxes walked into on the way to a sample table; nothing else is.
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

#: The gap at which a file needs repairing, shared by the repair job and the screen explaining it.
NEEDS_REPAIR_BYTES = 20 * 1024 * 1024

#: The two handler types measured; a file carrying neither cannot be.
_VIDEO = b"vide"
_AUDIO = b"soun"


class Malformed(Exception):
    """The file does not parse as a box structure."""


@dataclass(frozen=True, slots=True)
class Box:
    """One box: its kind, start, size, and the length of its own header."""

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
    """One track, with a factory for its samples so a long video's need not be held."""

    kind: bytes
    samples: Callable[[], Iterator[tuple[int, float]]]


def _measure(blob: bytes, at: int, limit: int) -> Box:
    """One box header at `at`, refusing anything that runs past `limit`, the bytes truly held."""
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
    """The boxes laid end to end between two points; raises `Malformed` on anything else."""
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
    """The file's `moov`, read by seeking past the media, relative to the blob; or None."""
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
    """Where a full box's entries start and how many there are."""
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
    """Samples per chunk, expanded from `stsc`'s run-length form with its 1-based chunk numbers."""
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
    """Each sample's duration in the track's ticks, expanded from run-length `stts`."""
    table = find(blob, stbl.body, stbl.end, _STTS)
    if table is None:
        raise Malformed("a track has no time-to-sample table")
    at, count = _table(blob, table, 8)
    for i in range(count):
        run, delta = struct.unpack_from(">II", blob, at + i * 8)
        for _ in range(run):
            yield delta


def _sizes(blob: bytes, stbl: Box) -> Iterator[int]:
    """Each sample's byte size from `stsz`; a non-zero `sample_size` means all are that size."""
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
    """Every sample as (byte position, second it plays at), samples sitting end to end in chunks."""
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
    """Every video and audio track in an index, as sample positions against sample times."""
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
    """The largest byte distance from a video sample to the nearest-in-time audio sample."""
    ordered = sorted(audio, key=lambda pair: pair[1])
    when = [moment for _, moment in ordered]
    worst = 0
    for where, moment in video:
        near = min(bisect_left(when, moment), len(ordered) - 1)
        worst = max(worst, abs(ordered[near][0] - where))
    return worst


def worst_gap(path: Path) -> int | None:
    """The worst video-to-audio byte gap; zero with no audio, None when the file cannot be read."""
    # The whole walk is guarded, as the lazy tables raise only when walked.
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
        # A track that walks to nothing is unknown (None), never a perfect zero.
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
