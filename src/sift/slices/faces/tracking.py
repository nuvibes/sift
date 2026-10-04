# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deciding that a face in one sampled moment is the same face as in the next.

This is the module that makes the feature affordable and the one that makes its counting correct,
and those are two different jobs done in two stages.

**Linking by position** is free: two boxes in consecutive samples that overlap, or whose centres
are close relative to how big the face is, are one face carrying on. That alone halves the work,
because only the best couple of frames of each run are ever described in numbers.

**Joining by appearance** is the second stage and it exists because the first one is not enough
here. Sift samples a moment every one to ten seconds, not every frame, and a person walks a long
way in ten seconds, so one person often produces three or four runs that never overlap each
other, and a run is commonly only a frame or two long. Left there, a file with one person
in it would report four appearances and, once one of them was named, read as *partly* identified
for ever. So after each run has been described once, runs whose numbers agree are joined.

The order matters and is the whole design: link by position first because it is free, then join by
appearance using descriptions that had to be computed anyway.
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
    """Whether one detection is the previous one carrying on.

    Overlap, and nothing else. A "centres are close enough" fallback would add nothing: for two
    boxes of a similar size it fires almost exactly where overlap already does, and where it reaches
    further, it reaches far enough to join two DIFFERENT people standing near each other, which is the one mistake this whole feature is shaped to avoid.

    A face that really has moved out of its own box between two samples is not abandoned. It starts
    a second run, and the two runs are joined afterwards by comparing their descriptions, which is
    evidence about who the face is rather than about where it was.
    """
    return earlier.box.overlap(later.box) >= tuning.TRACK_OVERLAP


class Linker:
    """Follows faces from one sampled moment to the next, a frame at a time.

    A frame at a time rather than over a whole file, because the caller decides when to stop
    reading and needs to know, as it goes, whether the last several frames turned up anybody new.
    A file where the same person stands in the same place for an hour is worth stopping early, and
    that judgement cannot be made after all the frames have already been read.
    """

    def __init__(self) -> None:
        self.runs: list[Run] = []
        self._open: list[int] = []

    def add(self, detections: list[Detection]) -> int:
        """Take one frame's faces. Hands back how many of them started something new.

        Compared only with the frame before, deliberately. Reaching further back would join a face
        to one that left the shot and came back (a separate appearance), and would join across a
        cut, where the same position means nothing at all.

        Each face may continue at most one run and each run may be continued by at most one face,
        so two people crossing produce two runs rather than a tangle.
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
    """A run, after the best of it has been described.

    The time range is the range the face was **seen**, not the range of the pictures that were
    kept. Only two frames of a long run are described, and reporting their span would say a person
    who is on screen for a minute appears for four seconds, which is what a viewer would be shown
    and would be wrong.
    """

    started_ms: int
    ended_ms: int
    seen_in: int
    faces: tuple[Described, ...]


def merge(segments: list[Segment]) -> list[Segment]:
    """Join runs of the same face that position could not connect.

    Compares the best description of each run against the best of every other and joins the pairs
    that agree. Repeated until nothing more joins, so three runs of one person collapse to one even
    when only two of the three pairs are alike enough on their own.

    The threshold is the one measured for comparing two individual faces, which is a lower scale
    than comparing a face against a person's whole gallery: two faces of the same person sit
    around the middle of the range, two faces of different people almost never reach it.
    """
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
    """Best against best. The strongest evidence that two runs are one face is the two clearest
    views of it agreeing: averaging in the poor frames of a run buries exactly that."""
    best_first = max(first, key=lambda item: item.quality.score)
    best_second = max(second, key=lambda item: item.quality.score)
    return similarity(best_first.vector, best_second.vector) >= tuning.TRACKLET_MERGE


def best_frames(
    scored: list[tuple[float, Detection]], count: int = tuning.FRAMES_PER_TRACK
) -> list[Detection]:
    """Which frames of a run are worth the expensive step.

    Handed every frame of the run that cleared the quality bar, with its score. Takes the best
    `count` of them and returns those in the order they happened, so the pictures kept for an
    appearance read as a sequence rather than in quality order.

    Two rather than one because a single frame is a single point of failure: if the sharpest
    view of somebody is the one where they blinked, that appearance is described by a blink. Past
    two, the extra frames of a run that averages two frames long are the same picture again.
    """
    chosen = sorted(scored, key=lambda item: -item[0])[:count]
    return [detection for _, detection in sorted(chosen, key=lambda item: item[1].timestamp_ms)]


def agreements(faces: tuple[Described, ...]) -> tuple[float, ...]:
    """For each face of one appearance, how much it looks like the OTHERS.

    Each face is compared against the middle of its neighbours rather than against the middle of
    everything, so a face cannot vouch for itself: with its own description in the average, the
    frame that is most unlike the rest still pulls the target it is measured against towards
    itself, and the worse it is the more it hides.

    **A single face agrees with nothing and gets 1.0**, which is not a compliment: it is the
    absence of evidence, and three quarters of appearances are in exactly that position because a
    still photograph can only ever have one frame.

    **Two faces get the SAME number as each other**, necessarily: each is compared against the
    other, so the comparison is one number written twice. They cannot be reordered by it, and that
    is right rather than a shortfall: two frames disagreeing tells you one of them is wrong and
    says nothing at all about which.
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
