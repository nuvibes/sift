# SPDX-License-Identifier: AGPL-3.0-or-later
"""Find near-duplicate pairs through a block index, never all-pairs: a 120x cut, complete within
`NEAR_FRAME_BITS`."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum

from sift.kernel.content import perceptual
from sift.kernel.content.duplicates import Fingerprint
from sift.kernel.content.perceptual import distance
from sift.kernel.media import MAX_DURATION_GAP_MS

FRAME_HEX = 16

VIDEO_FRAMES = 30

#: A value of any other width holds no thirty frames and is skipped.
VIDEO_HEX = FRAME_HEX * VIDEO_FRAMES

#: Matched on the kind, never on which fingerprint column is filled in.
_IMAGE = "image"
_GIF = "gif"
_VIDEO = "video"

#: Eleven, where the block search is complete; real non-duplicates sit at 26 bits or more.
NEAR_FRAME_BITS = perceptual.NEAR_FRAME_BITS

#: A fifth may differ: about what a caption or a trimmed end costs.
NEAR_VIDEO_FRAMES = 24

#: Four blocks, each looked up within two bits: complete for any pair within `NEAR_FRAME_BITS`.
_BLOCKS = 4
_BLOCK_HEX = FRAME_HEX // _BLOCKS
_BLOCK_BITS = _BLOCK_HEX * 4
_BLOCK_RADIUS = 2


class Accuracy(StrEnum):
    """How alike two videos must look before Sift asks; named, since a bit count says nothing."""

    EXACT = "exact"
    """Bit for bit. The same video, encoded the same way, which after re-encoding is rare."""

    HIGH = "high"
    """Two bits. Where two encodes of one video submitted by two people were measured to sit."""

    MEDIUM = "medium"
    """Four bits. The default: it reaches a distant encode without reaching the collision edge."""

    LOW = "low"
    """Six bits. The outer edge, where unrelated videos begin to collide. Expect false alarms."""


#: One table per kind: bits for stills and videos, frames for a GIF.
LEVELS: dict[str, dict[Accuracy, int]] = {
    "phash": {
        Accuracy.EXACT: 0,
        Accuracy.HIGH: 2,
        Accuracy.MEDIUM: 4,
        Accuracy.LOW: NEAR_FRAME_BITS,
    },
    "video_phash": {
        Accuracy.EXACT: 0,
        Accuracy.HIGH: 2,
        Accuracy.MEDIUM: 4,
        Accuracy.LOW: 6,
    },
    "videohash": {
        Accuracy.EXACT: 0,
        Accuracy.HIGH: 2,
        Accuracy.MEDIUM: 4,
        Accuracy.LOW: VIDEO_FRAMES - NEAR_VIDEO_FRAMES,
    },
}

#: A scan files at the widest level so a narrower setting filters stored rows without a rescan.
WIDEST: dict[str, int] = {kind: table[Accuracy.LOW] for kind, table in LEVELS.items()}

DEFAULT_ACCURACY = Accuracy.MEDIUM

#: Applied when the queue is read, for the reason `WIDEST` gives; the number lives in the kernel.
DEFAULT_MAX_DURATION_GAP_MS = MAX_DURATION_GAP_MS


def _variants(value: int) -> Iterator[int]:
    """Every block value within `_BLOCK_RADIUS` bits of this one, including it: 137 for 16 bits."""
    yield value
    for first in range(_BLOCK_BITS):
        yield value ^ (1 << first)
        for second in range(first + 1, _BLOCK_BITS):
            yield value ^ (1 << first) ^ (1 << second)


def _blocks(fingerprint: str) -> tuple[int, ...]:
    return tuple(
        int(fingerprint[at : at + _BLOCK_HEX], 16) for at in range(0, FRAME_HEX, _BLOCK_HEX)
    )


def _frames(videohash: str) -> list[str]:
    return [videohash[at : at + FRAME_HEX] for at in range(0, len(videohash), FRAME_HEX)]


@dataclass(frozen=True, slots=True)
class Pair:
    """Two assets that look alike; `distance` is bits, or frames for `videohash`, per `method`."""

    asset_a: str
    asset_b: str
    method: str
    distance: int
    #: None means unknown, never agreement.
    duration_gap_ms: int | None = None


class _Index:
    def __init__(self) -> None:
        self._buckets: list[dict[int, list[int]]] = [{} for _ in range(_BLOCKS)]

    def add(self, fingerprint: str, owner: int) -> None:
        for position, value in enumerate(_blocks(fingerprint)):
            self._buckets[position].setdefault(value, []).append(owner)

    def near(self, fingerprint: str) -> set[int]:
        found: set[int] = set()
        for position, value in enumerate(_blocks(fingerprint)):
            bucket = self._buckets[position]
            for variant in _variants(value):
                owners = bucket.get(variant)
                if owners is not None:
                    found.update(owners)
        return found


class Matcher:
    """Compares fingerprints and counts the comparisons, which a test holds down."""

    def __init__(self) -> None:
        self.comparisons = 0

    def _distance(self, first: str, second: str) -> int | None:
        """`distance`, counted; None means not comparable, never zero."""
        self.comparisons += 1
        return distance(first, second)

    def find(self, fingerprints: Sequence[Fingerprint]) -> list[Pair]:
        """Every near-duplicate pair, exact duplicates excluded; each kind searched on its own."""
        # Dropped here so the loops below need no check for it.
        stills = [
            row
            for row in fingerprints
            if row.media_type == _IMAGE and _usable(row.phash, FRAME_HEX)
        ]
        loops = [
            row
            for row in fingerprints
            if row.media_type == _GIF and _usable(row.videohash, VIDEO_HEX)
        ]
        scenes = [
            row
            for row in fingerprints
            if row.media_type == _VIDEO and _usable(row.video_phash, FRAME_HEX)
        ]
        return self._still_pairs(stills) + self._loop_pairs(loops) + self._scene_pairs(scenes)

    def _scene_pairs(self, scenes: Sequence[Fingerprint]) -> list[Pair]:
        """Videos that look like the same video, filed at the widest level (see `WIDEST`)."""
        index = _Index()
        found: list[Pair] = []
        widest = WIDEST["video_phash"]

        for position, row in enumerate(scenes):
            mine = row.video_phash or ""
            for other in sorted(index.near(mine)):
                candidate = scenes[other]
                apart = self._distance(mine, candidate.video_phash or "")
                if apart is None or apart > widest:
                    continue
                pair = _pair(candidate, row, "video_phash", apart, gap=_length_gap(row, candidate))
                if pair is not None:
                    found.append(pair)
            index.add(mine, position)

        return found

    def _still_pairs(self, stills: Sequence[Fingerprint]) -> list[Pair]:
        index = _Index()
        found: list[Pair] = []

        for position, row in enumerate(stills):
            mine = row.phash or ""
            for other in sorted(index.near(mine)):
                candidate = stills[other]
                apart = self._distance(mine, candidate.phash or "")
                if apart is None or apart > WIDEST["phash"]:
                    continue
                pair = _pair(candidate, row, "phash", apart)
                if pair is not None:
                    found.append(pair)
            index.add(mine, position)

        return found

    def _loop_pairs(self, videos: Sequence[Fingerprint]) -> list[Pair]:
        index = _Index()
        found: list[Pair] = []

        for position, row in enumerate(videos):
            mine = row.videohash or ""
            frames = _frames(mine)

            for other in sorted(self._video_candidates(index, frames)):
                candidate = videos[other]
                differing = self._frames_apart(candidate.videohash or "", mine)
                if differing is None or differing > WIDEST["videohash"]:
                    continue
                pair = _pair(candidate, row, "videohash", differing)
                if pair is not None:
                    found.append(pair)

            for frame in frames:
                index.add(frame, position)

        return found

    def _video_candidates(self, index: _Index, frames: Iterable[str]) -> set[int]:
        """Videos that enough of these frames landed near; one shared frame is nothing."""
        hits: dict[int, int] = {}
        for frame in frames:
            for owner in index.near(frame):
                hits[owner] = hits.get(owner, 0) + 1
        return {owner for owner, count in hits.items() if count >= NEAR_VIDEO_FRAMES}

    def _frames_apart(self, first: str, second: str) -> int | None:
        """How many of thirty frames differ, in place or as a set; None if not comparable."""
        if len(first) != VIDEO_HEX or len(second) != VIDEO_HEX:
            return None

        mine = _frames(first)
        theirs = _frames(second)

        in_place = sum(1 for a, b in zip(mine, theirs, strict=True) if self._agree(a, b))
        if in_place >= NEAR_VIDEO_FRAMES:
            return VIDEO_FRAMES - in_place

        # The shifted case: ask whether they are the same frames in any order.
        as_a_set = min(self._paired_off(mine, theirs), self._paired_off(theirs, mine))
        return VIDEO_FRAMES - max(in_place, as_a_set)

    def _paired_off(self, mine: list[str], theirs: list[str]) -> int:
        """How many frames pair up one-to-one, so one static frame cannot match all thirty."""
        spare = list(theirs)
        paired = 0
        for frame in mine:
            for at, other in enumerate(spare):
                if self._agree(frame, other):
                    spare.pop(at)
                    paired += 1
                    break
        return paired

    def _agree(self, first: str, second: str) -> bool:
        apart = self._distance(first, second)
        return apart is not None and apart <= NEAR_FRAME_BITS


def _usable(fingerprint: str | None, width: int) -> bool:
    return fingerprint is not None and len(fingerprint) == width


def _length_gap(first: Fingerprint, second: Fingerprint) -> int | None:
    """How far apart these two run, in milliseconds; None, never zero, if a duration is missing."""
    if first.duration_ms is None or second.duration_ms is None:
        return None
    return abs(first.duration_ms - second.duration_ms)


def _pair(
    first: Fingerprint,
    second: Fingerprint,
    method: str,
    apart: int,
    *,
    gap: int | None = None,
) -> Pair | None:
    """A pair with its ids in order, or None for the same bytes (the reclaim view's)."""
    if first.identity == second.identity:
        return None
    asset_a, asset_b = sorted((first.asset_id, second.asset_id))
    return Pair(
        asset_a=asset_a, asset_b=asset_b, method=method, distance=apart, duration_gap_ms=gap
    )
