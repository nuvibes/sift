# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deciding that a face in one sampled moment is the same face as in the next.

Runs are linked by position first, which is free, then joined by appearance, since samples are
seconds apart and one person otherwise reads as several appearances.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sift.slices.faces import tuning
from sift.slices.faces.models import Described, Detection, Vector
from sift.slices.faces.recognize import similarity


@dataclass(slots=True)
class Run:
    """One face followed across consecutive samples, before anything expensive has happened."""

    detections: list[Detection] = field(default_factory=list)

    @property
    def started_ms(self) -> int:
        return min(item.timestamp_ms for item in self.detections)

    @property
    def ended_ms(self) -> int:
        return max(item.timestamp_ms for item in self.detections)


def continues(earlier: Detection, later: Detection) -> bool:
    """Whether one detection is the previous one carrying on: overlap alone, since nearness would
    join two people standing close; a face that moved is joined later by description."""
    return earlier.box.overlap(later.box) >= tuning.TRACK_OVERLAP


class Linker:
    """Follows faces from one sampled moment to the next, a frame at a time."""

    def __init__(self) -> None:
        self.runs: list[Run] = []
        self._open: list[int] = []

    def add(self, detections: list[Detection]) -> int:
        """Take one frame's faces, compared with the frame before only; how many started anew.

        Each face continues one run at most and each run one face, so crossings stay apart.
        """
        claimed: set[int] = set()
        still_open: list[int] = []
        started = 0
        for detection in detections:
            joined = None
            for index in self._open:
                if index in claimed:
                    continue
                if continues(self.runs[index].detections[-1], detection):
                    joined = index
                    break
            if joined is None:
                self.runs.append(Run(detections=[detection]))
                joined = len(self.runs) - 1
                started += 1
            else:
                self.runs[joined].detections.append(detection)
            claimed.add(joined)
            still_open.append(joined)
        self._open = still_open
        return started


@dataclass(frozen=True, slots=True)
class Segment:
    """A run after its best frames were described; its range is when the face was seen."""

    started_ms: int
    ended_ms: int
    seen_in: int
    faces: tuple[Described, ...]


def merge(segments: list[Segment]) -> list[Segment]:
    """Join runs of the same face that position could not connect, until nothing more joins."""
    groups = [item for item in segments if item.faces]
    while True:
        joined = _join_once(groups)
        if joined is None:
            return groups
        groups = joined


def _join_once(groups: list[Segment]) -> list[Segment] | None:
    for first in range(len(groups)):
        for second in range(first + 1, len(groups)):
            if _alike(groups[first].faces, groups[second].faces):
                merged = list(groups)
                merged[first] = _joined(merged[first], merged[second])
                merged.pop(second)
                return merged
    return None


def _joined(first: Segment, second: Segment) -> Segment:
    faces = tuple(sorted(first.faces + second.faces, key=lambda item: item.detection.timestamp_ms))
    return Segment(
        started_ms=min(first.started_ms, second.started_ms),
        ended_ms=max(first.ended_ms, second.ended_ms),
        seen_in=first.seen_in + second.seen_in,
        faces=faces,
    )


def _alike(first: tuple[Described, ...], second: tuple[Described, ...]) -> bool:
    """Best against best: averaging in a run's poor frames buries the clearest agreement."""
    best_first = max(first, key=lambda item: item.quality.score)
    best_second = max(second, key=lambda item: item.quality.score)
    return similarity(best_first.vector, best_second.vector) >= tuning.TRACKLET_MERGE


def best_frames(
    scored: list[tuple[float, Detection]], count: int = tuning.FRAMES_PER_TRACK
) -> list[Detection]:
    """The best `count` frames of a run, in the order they happened; two, as one may blink."""
    chosen = sorted(scored, key=lambda item: -item[0])[:count]
    return [detection for _, detection in sorted(chosen, key=lambda item: item[1].timestamp_ms)]


def agreements(faces: tuple[Described, ...]) -> tuple[float, ...]:
    """For each face of one appearance, how much it looks like the others, never itself.

    A single face gets 1.0, which is no evidence; two faces get the same number.
    """
    if len(faces) < 2:
        return (1.0,) * len(faces)
    return tuple(
        similarity(
            face.vector,
            centroid([other.vector for index, other in enumerate(faces) if index != position]),
        )
        for position, face in enumerate(faces)
    )


def centroid(vectors: list[Vector]) -> Vector:
    """The middle of a set of descriptions, scaled back to unit length."""
    if not vectors:
        raise ValueError("there is no middle of nothing")
    width = len(vectors[0])
    total = [0.0] * width
    for vector in vectors:
        for index, value in enumerate(vector):
            total[index] += value
    length = sum(value * value for value in total) ** 0.5
    if length == 0:
        return tuple(total)
    return tuple(value / length for value in total)
