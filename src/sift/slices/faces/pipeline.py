# SPDX-License-Identifier: AGPL-3.0-or-later
"""One file, start to finish: read moments, find and follow faces, refuse poor ones, describe.

The unit is a face, never a file: three people in a photograph are three faces.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from sift.kernel.log import get_logger
from sift.kernel.sampling import face_frames
from sift.slices.faces import crop as cropping
from sift.slices.faces import frames as framing
from sift.slices.faces import quality as quality_module
from sift.slices.faces import tracking, tuning
from sift.slices.faces.detect import Detector
from sift.slices.faces.frames import Frame, Reader
from sift.slices.faces.models import Appearance, Box, Described, Detection, Finding, Quality
from sift.slices.faces.recognize import Recognizer

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Bar:
    """The quality a face has to reach to be worth describing."""

    min_pixels: int
    min_sharpness: float
    min_frontality: float

    @classmethod
    def of(cls, level: tuple[int, float, float]) -> Bar:
        """The bar a pass measures faces against: the quality preset, whatever the depth.

        Depth only means more moments; a lower bar would describe detail that was never there.
        """
        pixels, sharpness, frontality = level
        return cls(min_pixels=pixels, min_sharpness=sharpness, min_frontality=frontality)


@dataclass(slots=True)
class Spent:
    """Where the seconds of one pass went: decoding, detecting and describing, logged per pass."""

    decoding: float = 0.0
    detecting: float = 0.0
    describing: float = 0.0


@dataclass(frozen=True, slots=True)
class Outcome:
    """What a pass over one file found, and how much of the file it managed to look at."""

    appearances: tuple[Appearance, ...]
    frames_examined: int
    frames_planned: int
    stopped_early: bool
    spent: Spent = field(default_factory=Spent)
    frames_skipped: int = 0
    """Moments an earlier pass had already covered, which this one started after."""
    frames_attempted: int = 0
    """Moments this pass set out to look at, before anything was read.

    Not the same as `frames_examined`, and the difference matters below: a pass can
    have moments left and get nothing back from any of them.
    """
    reached_ms: int | None = None
    """The last moment actually looked at, so a later pass can carry on from it."""
    refused_small: int = 0
    """Faces found and then refused for size, at either look.

    Kept because it is the whole difference between two files that both say "no faces": one with
    nobody in it, and one full of people too far from the camera to describe. Written to the log
    with the pass; see `Pipeline.run`."""
    refused_closer: int = 0
    """Faces that cleared the size floor and were refused on the closer look: blur, angle, or a
    square cut partly from outside the picture. The sum of the three below."""
    refused_largest: int = 0
    """The long side, in the file's own pixels, of the biggest face refused for size; 0 when none
    was. The number a person needs to judge "too small" for themselves."""
    refused_blurred: int = 0
    """Of `refused_closer`, the faces too blurred to recognize."""
    refused_turned: int = 0
    """Of `refused_closer`, the faces turned too far from the camera."""
    refused_edge: int = 0
    """Of `refused_closer`, the faces running off the edge of the picture."""
    looked_again: int = 0
    """Moments read after the planned ones, around a face that came close to the floor. See
    `again_moments`. Not in `frames_examined`, which counts the plan's moments: coverage is a
    measure of the plan, and these are inside it."""

    @property
    def coverage(self) -> float:
        """How much of the file has been looked at in total, from 0 to 1.

        A pass that had moments to read and was not cut short is complete: an unreadable tail
        would otherwise never let the file settle.
        """
        if self.frames_planned <= 0:
            return 1.0
        if self.frames_attempted > 0 and not self.stopped_early:
            return 1.0
        return min(1.0, (self.frames_skipped + self.frames_examined) / self.frames_planned)


class Pipeline:
    """Runs one file through the stages. Holds no state between files."""

    def __init__(
        self,
        reader: Reader,
        detector: Detector,
        recognizer: Recognizer,
        *,
        bar: Bar,
        density: float = 1.0,
        budget_seconds: float | None = None,
    ) -> None:
        self._reader = reader
        self._detector = detector
        self._recognizer = recognizer
        self._bar = bar
        self._density = density
        self._budget = budget_seconds

    async def run(
        self,
        path: Path,
        *,
        media_type: str,
        width: int,
        height: int,
        duration_ms: int | None,
        after_ms: int | None = None,
    ) -> Outcome:
        """One pass over one file, or the rest of one an earlier pass did not finish.

        Every model call runs in a thread: ONNX holds the interpreter lock, and the event loop
        serves the API and playback too.
        """
        planned = face_frames(duration_ms or 0, density=self._density)
        timestamps = planned if after_ms is None else tuple(t for t in planned if t > after_ms)
        started = time.monotonic()

        spent = Spent()
        seen, stopped_early = await self._read(
            path,
            media_type=media_type,
            width=width,
            height=height,
            timestamps=timestamps,
            started=started,
            spent=spent,
        )
        examined = len(seen)
        reached = seen[-1][0].timestamp_ms if seen else None
        lift = framing.enlargement(width, height) if width > 0 and height > 0 else (1.0, 1.0)
        found = await self._faces(
            path,
            media_type=media_type,
            width=width,
            height=height,
            seen=seen,
            lift=lift,
            spent=spent,
        )

        found, looked_again = await self._look_again(
            path,
            media_type=media_type,
            width=width,
            height=height,
            planned=planned,
            duration_ms=duration_ms,
            seen=seen,
            found=found,
            lift=lift,
            started=started,
            spent=spent,
            stopped_early=stopped_early,
        )
        tally = found.tally
        _log_spent(path, examined, looked_again, spent, tally)

        appearances = tuple(_appearance(segment) for segment in found.merged)
        return Outcome(
            appearances=appearances,
            spent=spent,
            frames_examined=examined,
            frames_planned=len(planned),
            stopped_early=stopped_early,
            frames_skipped=len(planned) - len(timestamps),
            frames_attempted=len(timestamps),
            # Never backwards: an empty resumed tail must not lower the coverage.
            reached_ms=reached if reached is not None else after_ms,
            refused_small=tally.small,
            refused_closer=tally.closer,
            refused_largest=tally.largest,
            refused_blurred=tally.reasons[Finding.TOO_BLURRED],
            refused_turned=tally.reasons[Finding.TURNED_AWAY],
            refused_edge=tally.reasons[Finding.RUNS_OFF_EDGE],
            looked_again=looked_again,
        )

    async def _look_again(
        self,
        path: Path,
        *,
        media_type: str,
        width: int,
        height: int,
        planned: tuple[int, ...],
        duration_ms: int | None,
        seen: list[tuple[Frame, list[Detection]]],
        found: _Found,
        lift: tuple[float, float],
        started: float,
        spent: Spent,
        stopped_early: bool,
    ) -> tuple[_Found, int]:
        """`run`'s faces after any second read, and how many moments that read added."""
        # A video whose faces were all refused, one near the floor, is read again around that face.
        looked_again = 0
        if not stopped_early and media_type == "video" and not found.merged:
            again = again_moments(
                found.tally.nearest,
                planned,
                duration_ms or 0,
                floor=self._bar.min_pixels,
            )
            have = {frame.timestamp_ms for frame, _ in seen}
            if again:
                more, _ = await self._read(
                    path,
                    media_type=media_type,
                    width=width,
                    height=height,
                    timestamps=again,
                    started=started,
                    spent=spent,
                )
                # A moment already held adds nothing and would read as a face standing still.
                more = [item for item in more if item[0].timestamp_ms not in have]
                looked_again = len(more)
                if more:
                    seen = sorted([*seen, *more], key=lambda item: item[0].timestamp_ms)
                    found = await self._faces(
                        path,
                        media_type=media_type,
                        width=width,
                        height=height,
                        seen=seen,
                        lift=lift,
                        spent=spent,
                    )
        return found, looked_again

    async def _read(
        self,
        path: Path,
        *,
        media_type: str,
        width: int,
        height: int,
        timestamps: tuple[int, ...],
        started: float,
        spent: Spent,
    ) -> tuple[list[tuple[Frame, list[Detection]]], bool]:
        """The moments asked for, each with the faces found in it, and whether time ran out."""
        seen: list[tuple[Frame, list[Detection]]] = []
        stopped_early = False
        waiting = time.monotonic()
        async for frame in self._reader.stream(
            path,
            media_type=media_type,
            width=width,
            height=height,
            timestamps=timestamps,
        ):
            spent.decoding += time.monotonic() - waiting
            # Off the event loop; see `run`.
            mark = time.monotonic()
            found = await asyncio.to_thread(
                self._detector.detect, frame.pixels, timestamp_ms=frame.timestamp_ms
            )
            spent.detecting += time.monotonic() - mark
            seen.append((frame, found))

            # Only the time limit stops a pass; a later pass carries on from where it stopped.
            if self._budget is not None and time.monotonic() - started >= self._budget:
                stopped_early = True
                log.info("faces.pass.budget_reached", path=str(path), frames=len(seen))
                break
            waiting = time.monotonic()
        return seen, stopped_early

    async def _faces(
        self,
        path: Path,
        *,
        media_type: str,
        width: int,
        height: int,
        seen: list[tuple[Frame, list[Detection]]],
        lift: tuple[float, float],
        spent: Spent,
    ) -> _Found:
        """Follow the faces across moments, choose, look closer, describe and join the runs."""
        # A moment with nobody in it is what ends a run.
        linker = tracking.Linker()
        for _, found in seen:
            linker.add(found)

        # One thread hop per run rather than per call.
        mark = time.monotonic()
        by_time = {frame.timestamp_ms: frame for frame, _ in seen}
        tally = _Tally()
        chosen = [
            await asyncio.to_thread(self._choose, run, by_time, lift, tally) for run in linker.runs
        ]
        spent.describing += time.monotonic() - mark

        # The chosen faces read again at the file's own size; counted as decoding.
        mark = time.monotonic()
        looks = await self._looks(
            path,
            media_type=media_type,
            width=width,
            height=height,
            chosen=chosen,
            lift=lift,
            by_time=by_time,
        )
        spent.decoding += time.monotonic() - mark

        mark = time.monotonic()
        segments = [
            await asyncio.to_thread(self._describe, run, looks_of_run, tally)
            for run, looks_of_run in zip(linker.runs, looks, strict=True)
        ]
        spent.describing += time.monotonic() - mark
        merged = tracking.merge([item for item in segments if item is not None])
        return _Found(merged=merged, tally=tally)

    def _choose(
        self,
        run: tracking.Run,
        by_time: dict[int, Frame],
        lift: tuple[float, float],
        tally: _Tally,
    ) -> list[Detection]:
        """Measure every frame of a run and choose the best that clear the size floor.

        Only the size floor is applied here, in the file's own pixels (`lift`); blur, angle and
        edge are judged after the closer look, on refined landmarks. Runs in a thread.
        """
        scored: list[tuple[float, Detection]] = []
        for detection in run.detections:
            frame = by_time.get(detection.timestamp_ms)
            if frame is None:
                continue
            own = _own_long_side(detection.box, lift)
            if own < self._bar.min_pixels:
                tally.refuse_small(own, at=detection.timestamp_ms)
                continue
            aligned = cropping.align(frame.pixels, detection.landmarks)
            # Ranks frames for the closer look; the verdict is taken in `_describe`.
            scored.append((self._measure(detection, aligned).score, detection))
        return tracking.best_frames(scored) if scored else []

    async def _looks(
        self,
        path: Path,
        *,
        media_type: str,
        width: int,
        height: int,
        chosen: list[list[Detection]],
        lift: tuple[float, float],
        by_time: dict[int, Frame],
    ) -> list[list[_Look]]:
        """Where each chosen face is looked at closely: a piece of the file at its own size,
        or the reduced frame when the file is no bigger or the moment would not read again."""
        plain = [
            [_Look(picture=by_time[item.timestamp_ms].pixels, detection=item) for item in picks]
            for picks in chosen
        ]
        if lift == (1.0, 1.0) or not any(chosen):
            return plain
        flat = [item for picks in chosen for item in picks]
        pieces = await self._reader.windows(
            path,
            media_type=media_type,
            width=width,
            height=height,
            wanted=[(item.timestamp_ms, _scaled(item, lift).box) for item in flat],
        )
        found = iter(pieces)
        looks: list[list[_Look]] = []
        for picks, fallback in zip(chosen, plain, strict=True):
            row: list[_Look] = []
            for item, reduced in zip(picks, fallback, strict=True):
                piece = next(found)
                if piece is None:
                    # Not read again: the reduced frame is still this face, judged by the floor.
                    row.append(reduced)
                    continue
                row.append(
                    _Look(
                        picture=piece.pixels,
                        detection=_moved(_scaled(item, lift), -piece.left, -piece.top),
                        left=piece.left,
                        top=piece.top,
                        lift=lift,
                    )
                )
            looks.append(row)
        return looks

    def _describe(
        self, run: tracking.Run, looks: list[_Look], tally: _Tally
    ) -> tracking.Segment | None:
        """Look closely at a run's chosen frames, keep those that clear the bar, and describe them.

        Runs in a thread; boxes come back in the reduced frame's pixels.
        """
        described: list[Described] = []
        turned: list[tuple[_Look, Detection, cropping.Aligned, Quality]] = []
        for look in looks:
            sharpened = self._detector.refine(look.picture, look.detection)
            aligned = cropping.align(look.picture, sharpened.landmarks)
            # Measured again: degenerate landmarks or a cut off the picture's edge make a smear.
            refined = self._measure(sharpened, aligned)
            if not refined.accepted:
                if quality_module.asked_only(refined):
                    turned.append((look, sharpened, aligned, refined))
                    continue
                tally.refuse_closer(refined, at=look.detection.timestamp_ms)
                continue
            described.append(self._described(look, sharpened, aligned))

        # A run whose only faces are turned past the line keeps them (`quality.asked_only`).
        if described:
            for look, _, _, refined in turned:
                tally.refuse_closer(refined, at=look.detection.timestamp_ms)
        else:
            described = [
                self._described(look, sharpened, aligned) for look, sharpened, aligned, _ in turned
            ]
            tally.turned_kept += len(described)

        # Every one refused: the same answer as no appearance.
        if not described:
            return None

        return tracking.Segment(
            started_ms=run.started_ms,
            ended_ms=run.ended_ms,
            seen_in=len(run.detections),
            faces=tuple(described),
        )

    def _described(self, look: _Look, sharpened: Detection, aligned: cropping.Aligned) -> Described:
        """One face described, and measured again with how firmly the recognizer answered."""
        description = self._recognizer.embed(aligned.chip)
        return Described(
            detection=look.back(sharpened),
            quality=self._measure(
                sharpened,
                aligned,
                strength=description.strength,
                recognisability=self._recognizer.recognisability(description.strength),
            ),
            vector=description.vector,
            chip=aligned.chip,
        )

    def _measure(
        self,
        detection: Detection,
        aligned: cropping.Aligned,
        *,
        strength: float = 0.0,
        recognisability: float = 1.0,
    ) -> Quality:
        return quality_module.assess(
            detection.box,
            detection.landmarks,
            aligned.chip,
            containment=aligned.containment,
            strength=strength,
            recognisability=recognisability,
            min_pixels=self._bar.min_pixels,
            min_sharpness=self._bar.min_sharpness,
            min_frontality=self._bar.min_frontality,
        )


@dataclass(slots=True)
class _Tally:
    """How many faces one pass refused, at which gate and for which reason."""

    small: int = 0
    closer: int = 0
    #: The long side of the biggest face refused for size, in the file's own pixels.
    largest: int = 0
    reasons: dict[Finding, int] = field(
        default_factory=lambda: dict.fromkeys(
            (Finding.TOO_BLURRED, Finding.TURNED_AWAY, Finding.RUNS_OFF_EDGE), 0
        )
    )

    #: The biggest refused face and its moment: where a look again starts. None when none.
    nearest: tuple[int, int] | None = None
    #: Faces kept although turned past the line, matched like any other (`quality.asked_only`).
    turned_kept: int = 0

    def refuse_small(self, long_side: float, *, at: int = 0) -> None:
        self.small += 1
        self.largest = max(self.largest, round(long_side))
        self._seen(round(long_side), at)

    def refuse_closer(self, measured: Quality, *, at: int = 0) -> None:
        """One face the closer look refused; under the size floor it counts as a size refusal."""
        if measured.failed is Finding.TOO_SMALL:
            self.refuse_small(measured.pixels, at=at)
            return
        self.closer += 1
        if measured.failed in self.reasons:  # pragma: no branch (assess refuses for no other)
            self.reasons[measured.failed] += 1
        self._seen(measured.pixels, at)

    def _seen(self, long_side: int, at: int) -> None:
        if self.nearest is None or long_side > self.nearest[0]:
            self.nearest = (long_side, at)


def _log_spent(path: Path, examined: int, looked_again: int, spent: Spent, tally: _Tally) -> None:
    log.info(
        "faces.pass.spent",
        path=str(path),
        frames=examined,
        looked_again=looked_again,
        decoding_ms=round(spent.decoding * 1000),
        detecting_ms=round(spent.detecting * 1000),
        describing_ms=round(spent.describing * 1000),
        refused_small=tally.small,
        refused_closer=tally.closer,
        asked_only=tally.turned_kept,
    )


@dataclass(frozen=True, slots=True)
class _Found:
    """What one pass over the moments read came to: the joined runs, and what it refused."""

    merged: list[tracking.Segment]
    tally: _Tally


def again_moments(
    nearest: tuple[int, int] | None,
    planned: tuple[int, ...],
    duration_ms: int,
    *,
    floor: int,
) -> tuple[int, ...]:
    """The moments a video is read at again when every face was refused: none, or a few
    either side of the biggest refused face, bounded by `tuning.LOOK_AGAIN_EACH_SIDE`."""
    if nearest is None or not planned or duration_ms <= 0:
        return ()
    size, at = nearest
    if size < tuning.LOOK_AGAIN_REACH * floor:
        return ()
    ordered = sorted(planned)
    index = min(range(len(ordered)), key=lambda position: abs(ordered[position] - at))
    stretches: list[tuple[int, int]] = []
    if index > 0:
        stretches.append((ordered[index - 1], ordered[index]))
    after = ordered[index + 1] if index + 1 < len(ordered) else duration_ms
    stretches.append((ordered[index], after))
    known = set(ordered)
    wanted: set[int] = set()
    parts = tuning.LOOK_AGAIN_EACH_SIDE + 1
    for start, end in stretches:
        for step in range(1, parts):
            moment = round(start + (end - start) * step / parts)
            if 0 < moment < duration_ms and moment not in known:
                wanted.add(moment)
    return tuple(sorted(wanted))


@dataclass(frozen=True, slots=True)
class _Look:
    """One chosen face and the picture it is examined in, with the way back to the reduced frame."""

    picture: np.ndarray
    detection: Detection
    left: int = 0
    top: int = 0
    lift: tuple[float, float] = (1.0, 1.0)

    def back(self, detection: Detection) -> Detection:
        """A detection in this look's picture, in the reduced frame's pixels."""
        if self.lift == (1.0, 1.0) and self.left == 0 and self.top == 0:
            return detection
        scale_x, scale_y = self.lift
        box = detection.box
        return Detection(
            box=Box(
                x=round((box.x + self.left) / scale_x),
                y=round((box.y + self.top) / scale_y),
                width=max(1, round(box.width / scale_x)),
                height=max(1, round(box.height / scale_y)),
            ),
            score=detection.score,
            landmarks=tuple(
                ((x + self.left) / scale_x, (y + self.top) / scale_y)
                for x, y in detection.landmarks
            ),
            timestamp_ms=detection.timestamp_ms,
        )


def _own_long_side(box: Box, lift: tuple[float, float]) -> float:
    """A box's long side in the file's own pixels."""
    return max(box.width * lift[0], box.height * lift[1])


def _scaled(detection: Detection, lift: tuple[float, float]) -> Detection:
    """A detection in the reduced frame, carried into the file's own pixels."""
    scale_x, scale_y = lift
    box = detection.box
    return Detection(
        box=Box(
            x=round(box.x * scale_x),
            y=round(box.y * scale_y),
            width=max(1, round(box.width * scale_x)),
            height=max(1, round(box.height * scale_y)),
        ),
        score=detection.score,
        landmarks=tuple((x * scale_x, y * scale_y) for x, y in detection.landmarks),
        timestamp_ms=detection.timestamp_ms,
    )


def _moved(detection: Detection, dx: int, dy: int) -> Detection:
    box = detection.box
    return Detection(
        box=Box(x=box.x + dx, y=box.y + dy, width=box.width, height=box.height),
        score=detection.score,
        landmarks=tuple((x + dx, y + dy) for x, y in detection.landmarks),
        timestamp_ms=detection.timestamp_ms,
    )


def _appearance(segment: tracking.Segment) -> Appearance:
    """One appearance, each frame told how well it matches the others in it."""
    faces = tuple(
        replace(face, quality=quality_module.with_agreement(face.quality, agreed))
        for face, agreed in zip(segment.faces, tracking.agreements(segment.faces), strict=True)
    )
    return Appearance(
        started_ms=segment.started_ms,
        ended_ms=segment.ended_ms,
        seen_in=segment.seen_in,
        quality=max(item.quality.score for item in faces),
        faces=faces,
    )
