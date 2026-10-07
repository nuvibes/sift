# SPDX-License-Identifier: AGPL-3.0-or-later
"""One file, start to finish: pictures in, appearances out.

The order of the stages is the design, and it is chosen so that each one only ever hands the next
one work worth doing:

1. **Read a spread of moments.** Cheap for a still, one read for a GIF, a jump per moment for a
   video, and every planned moment is read: only the time limit stops a pass before the end.
2. **Find the faces.** Cheap now that a small detector does it.
3. **Follow each face across the moments.** Nearly free, and it is what stops the same person
   being described from scratch in every frame they appear in.
4. **Measure quality, and refuse the bad ones here.** The cheapest step, placed immediately before
   the expensive one. A face that is tiny, blurred or turned away does not merely fail to match:
   it lands near every other bad face, and a handful of them will pull two people who look nothing
   alike into one group.
5. **Look again at the few that survived**, close up, so their features are marked precisely. Only
   for faces about to be described, never for every detection.
6. **Describe them**, and keep the picture.
7. **Join the runs whose descriptions agree.** Sampling is seconds apart and a person moves, so one
   person routinely produces three runs that never overlapped each other. Without this a file with
   one person in it reports three appearances and, once one is named, reads as *partly* identified
   for ever.

The unit throughout is a **face**, never a file. Three people in a photograph are three faces, in
three runs, attributed to three people independently.
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

        **The depth is deliberately not an input here.** A deep pass that also lowered the bar
        would admit faces at half the size the recognizer reads, upscale them, and describe detail
        that was never in the picture, and faces that small do not produce weak numbers, they
        produce wrong ones that resemble each other rather than the people they came from.

        So depth means one thing: look at more moments. The quality bar is the quality setting,
        and "look harder" does not also mean "believe more".
        """
        pixels, sharpness, frontality = level
        return cls(min_pixels=pixels, min_sharpness=sharpness, min_frontality=frontality)


@dataclass(slots=True)
class Spent:
    """Where the seconds of one pass went, in seconds.

    Three stages, and the split between them is the whole of what decides how to make scanning a
    library faster. Reading video and running the two models are different problems with different
    answers: one is helped by decoding smaller frames and by seeking less, the other by a faster
    processor or a graphics card. Estimating the split from the outside is guesswork, so it is
    measured in every pass and written to the log.
    """

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

        Counts what an earlier pass covered as well as what this one did, because that is the
        question every caller is asking: a resumed pass that reads the last tenth of a long video
        has covered the whole of it, not a tenth of it.

        **A tail nothing can read is covered, because there is nothing else that can ever happen to
        it.** A video whose length is not a whole number of sampling steps has a last moment inside
        the file that the decoder will not return a frame for. The pass plans it, reads nothing,
        and reports a coverage a fraction below one, so without this the file would never be
        settled, and every sweep would offer it again to plan the same unreadable moment.

        So a pass that had moments to look at and was not cut short has gone as far as this file
        goes, whether the moments it got nothing back from are a tail or most of the file (an
        GIF only part of which decodes). Being cut short is the case that must NOT count:
        that is a pass with work left which was told to stop, and it is the reason resuming exists.
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

        `after_ms` is where the last pass gave up. The plan is worked out in full either way, so
        the moments before it are counted as covered rather than forgotten. Otherwise a resumed
        pass reads the tail of a video and reports that it has looked at the tail's worth of it.

        **Every model call goes to a thread, and the reason is that this is not the only thing
        running.** Sift is one process with one event loop: the API, the live job feed and every
        video anybody is watching share it with this. Detection and description are ONNX, which is
        C++ holding the interpreter lock for the whole inference, so run on the loop they would
        stop all of it, in bursts, for as long as a scan of one file takes (tens of seconds): video
        pausing and the interface stuttering, with nothing in the logs but ordinary queries taking a
        hundred milliseconds.

        ONNX Runtime sessions are safe to call from several threads, and how many run at the same
        time is already capped by the machine-budget setting, so the hop costs nothing but the hop.
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
        # Where this pass got to. Never backwards: see `Outcome.reached_ms` below.
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

        # **A video in which every face was refused, one of them within reach of the floor, is
        # looked at again around that face, and only there.** Moments are seconds apart, and a
        # person walking towards the camera is under the floor at one moment and over it a second
        # later: a short clip of somebody walking forward, read at a handful of moments, can refuse
        # every face it sees (the largest just under the floor) while dozens of the frames between
        # two of those moments hold the same face over every floor. The plan is not
        # made denser for every file to catch that; this file alone gets a few more moments, in
        # the two stretches either side of the moment the nearest face was seen. See
        # `again_moments` for the bound.
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
                # A moment the reader answered with a picture already held adds nothing, and two
                # copies of one moment would read as a face standing still for no time at all.
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
        tally = found.tally

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

        appearances = tuple(_appearance(segment) for segment in found.merged)
        return Outcome(
            appearances=appearances,
            spent=spent,
            frames_examined=examined,
            frames_planned=len(planned),
            stopped_early=stopped_early,
            frames_skipped=len(planned) - len(timestamps),
            frames_attempted=len(timestamps),
            # Where this pass got to, or where the last one did when this one read nothing at all.
            # Never backwards: a resumed pass that finds its tail empty must not report that the
            # file is less covered than it already was.
            reached_ms=reached if reached is not None else after_ms,
            refused_small=tally.small,
            refused_closer=tally.closer,
            refused_largest=tally.largest,
            refused_blurred=tally.reasons[Finding.TOO_BLURRED],
            refused_turned=tally.reasons[Finding.TURNED_AWAY],
            refused_edge=tally.reasons[Finding.RUNS_OFF_EDGE],
            looked_again=looked_again,
        )

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
        """Read the moments asked for and find the faces in each: every moment that came back,
        in the order read, with what the detector found in it, and whether the time limit ended
        the reading."""
        seen: list[tuple[Frame, list[Detection]]] = []
        stopped_early = False
        # Where the time goes, measured rather than guessed. Reading video and running the models
        # are the two halves of a scan, and which of them dominates decides everything about how
        # to make a library scan faster: a graphics card is worth a great deal if the models are
        # most of it and very little if the decoding is. Three `monotonic` calls per frame.
        waiting = time.monotonic()
        async for frame in self._reader.stream(
            path,
            media_type=media_type,
            width=width,
            height=height,
            timestamps=timestamps,
        ):
            spent.decoding += time.monotonic() - waiting
            # Off the event loop. See `_describe`: same reason, and this is the hotter of the two.
            mark = time.monotonic()
            found = await asyncio.to_thread(
                self._detector.detect, frame.pixels, timestamp_ms=frame.timestamp_ms
            )
            spent.detecting += time.monotonic() - mark
            seen.append((frame, found))

            # **A quiet stretch is never a reason to stop, at any effort.** Somebody who walks on
            # after the first third of a video is only found by reading the rest of it, and the
            # plan is already capped at a few dozen moments per file. The time limit is the one
            # stop: it leaves a position behind, and a later pass carries on from there.
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
        """From the faces found in each moment to the appearances worth keeping: follow them across
        the moments, measure, look closely at the few that clear the size floor, describe the ones
        that clear the rest, and join the runs whose descriptions agree."""
        # Followed in time order and a moment at a time, an empty moment included: a moment with
        # nobody in it is what ends a run.
        linker = tracking.Linker()
        for _, found in seen:
            linker.add(found)

        # Also off the loop, and one hop per run rather than per call: describing a run is a short
        # burst of the same arithmetic, and handing each individual `embed` over separately would
        # pay the hop more often than it saves anything.
        mark = time.monotonic()
        by_time = {frame.timestamp_ms: frame for frame, _ in seen}
        tally = _Tally()
        chosen = [
            await asyncio.to_thread(self._choose, run, by_time, lift, tally) for run in linker.runs
        ]
        spent.describing += time.monotonic() - mark

        # The faces about to be described, read again at the file's own size. See
        # `Reader.windows` for why. Counted as decoding, because it is.
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
        # Includes measuring quality, which is deliberately a thousandth of the cost of describing,
        # so this is the recognizer's time to within the accuracy anybody would act on.
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

        Measuring is done on every frame because it costs almost nothing and choosing the best
        needs a score for each. Describing (the expensive step) happens only for the two best,
        and only after the second, close-up look at where the features are.

        **The bar is applied AFTER that second look, not before it, and the difference is whole
        files.** Blur, angle and containment are all read off the aligned square, and the square is
        cut by warping the frame onto the landmarks, so all three are measurements of the
        landmarks as much as of the face. The first look's landmarks are the coarse ones, pinned to
        the grid of a frame reduced to a 640-pixel square, which is exactly why `refine` exists.
        Judging a face on them and discarding it there means the careful measurement is never taken.

        It is not a small effect. A face looking three quarters towards the camera can read as
        turned too far away on the coarse landmarks and comfortably inside the line on the refined
        ones, and a photograph is a single frame, so a single coarse reading would decide the
        whole file and record it as having no faces in it at all.

        What remains here is the size floor, and only that. It is read off the detector's box
        rather than off any square, so `refine` barely moves it; it is the check that rules out the
        stranger in the background of a photograph, which is most of what a run of nothing is made
        of; and leaving it here keeps the cost of a worthless run at the two forward passes it
        already was rather than paying for a close-up look at every face too small to use.

        Synchronous on purpose, and called from a thread. See the note on `run`.

        **The size floor is read in the FILE'S pixels, not the reduced frame's.** `lift` is how many
        of the file's pixels one pixel of the frame the pass read stands for, and the face is cut
        from the file's own pixels afterwards. See `Reader.windows`. Read in the reduced frame,
        this floor would refuse every face of a 1440p clip at half its real size.

        The first half of the pass over a run: which frames of it are worth the closer look. The
        second half is `_describe`, after those frames have been read again at full size.
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
            # Ranks the frames of this run against each other; it decides which two get the closer
            # look, never whether any of them is worth keeping. That verdict is taken below.
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
        """Where each chosen face is looked at closely: a piece of the file at its own size, or
        the reduced frame when the file is no bigger than that or the moment would not read again.
        """
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
                    # Read the first time and not the second: a truncated tail, a share gone
                    # quiet. The reduced frame is still a picture of this face, and the floor
                    # judges what it holds, so a face too small there is refused honestly
                    # rather than described from pixels nobody read.
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
        """Look closely at the chosen frames of one run, keep those that clear the bar, and
        describe them. The second half of `_choose`; synchronous on purpose, called from a thread.

        Measured and cut in whatever picture the look holds (the file's own pixels where the file
        is larger than the reduced frame) and handed back in the reduced frame's pixels, which is
        what every stored box is in and what cutting a cover reads it as.
        """
        described: list[Described] = []
        turned: list[tuple[_Look, Detection, cropping.Aligned, Quality]] = []
        for look in looks:
            sharpened = self._detector.refine(look.picture, look.detection)
            aligned = cropping.align(look.picture, sharpened.landmarks)
            # **Measured again, and refused again.** The bar above was applied to the first look;
            # this is a different picture, cut with different landmarks, and the closer look is
            # exactly the step that can go wrong: `align` warps the frame onto the landmarks it
            # is given, so landmarks that come back degenerate produce a chip that is a smear
            # rather than a face.
            # Checked only on the way in, those would go straight through: described, stored, and
            # put into a group, carrying a frontality of 0.0 against a floor of 0.15. They are the
            # streaked crops: not bad source material but a good frame cut with bad landmarks.
            # And they do not merely look wrong. A description taken from a warped chip lands near
            # every other warped chip rather than near the person it came from, so they pull
            # strangers together and keep one person apart.
            #
            # The same is true of the OTHER way a square stops being a picture of a face: cut at
            # the side of a shot, part of it is the edge pixel repeated. That has its own floor,
            # and it is measured by the alignment rather than read off the square, because
            # once the repetition has happened nothing can tell it from a flat background.
            refined = self._measure(sharpened, aligned)
            if not refined.accepted:
                if quality_module.asked_only(refined):
                    turned.append((look, sharpened, aligned, refined))
                    continue
                # The look's picture is the file's own pixels (or the reduced frame, where the
                # file is no larger), so the box's long side is already the size to report.
                tally.refuse_closer(refined, at=look.detection.timestamp_ms)
                continue
            described.append(self._described(look, sharpened, aligned))

        # **A run whose only faces are turned past the line keeps them, and they are matched like
        # any other.** Large, sharp and whole, refused for the angle alone, it is a face a person
        # recognizes, and refusing it left a file with somebody plainly in it reading as "turned
        # too far away" with nothing anybody could do. Kept, it is described, stored and matched
        # at the bar every face meets; it joins no group and never becomes a reference: see
        # `quality.asked_only`. Where the run has a face over the line, that face stands for it
        # and the turned ones are refused as before, so an appearance is turned only when all of
        # it is.
        if described:
            for look, _, _, refined in turned:
                tally.refuse_closer(refined, at=look.detection.timestamp_ms)
        else:
            described = [
                self._described(look, sharpened, aligned) for look, sharpened, aligned, _ in turned
            ]
            tally.turned_kept += len(described)

        # Every one refused on the second look. That is an appearance with no describable face in
        # it, which is the same answer as no appearance, and the shape everything downstream
        # expects, since a segment with no faces has nothing to compare, group or show.
        if not described:
            return None

        return tracking.Segment(
            started_ms=run.started_ms,
            ended_ms=run.ended_ms,
            seen_in=len(run.detections),
            faces=tuple(described),
        )

    def _described(self, look: _Look, sharpened: Detection, aligned: cropping.Aligned) -> Described:
        """One face described, and measured a second time with how firmly the recognizer answered.

        The verdict that let it this far is what decides whether to pay for the description at
        all, so it cannot use anything the description produced, and how firmly the recognizer
        answered is exactly that. It ranks the frames afterwards; it never decides which ones are
        worth describing, because by the time it exists they all have been.
        """
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

    #: The biggest face refused for any reason, in the file's own pixels, and the moment it was
    #: seen at: where a look again starts from. None when nothing was refused.
    nearest: tuple[int, int] | None = None
    #: Faces kept although turned past the line, matched like any other (`quality.asked_only`).
    turned_kept: int = 0

    def refuse_small(self, long_side: float, *, at: int = 0) -> None:
        self.small += 1
        self.largest = max(self.largest, round(long_side))
        self._seen(round(long_side), at)

    def refuse_closer(self, measured: Quality, *, at: int = 0) -> None:
        """One face the closer look refused, counted under the reason it gave.

        A face the refined box shows under the size floor is a size refusal, whichever look
        caught it: the reason a person reads is the same, and so is the number they need.
        """
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
    """The moments a video is read at again when every face in it was refused: none, or a few
    either side of the moment the biggest refused face was seen at.

    Only for a face within reach of the floor (`tuning.LOOK_AGAIN_REACH` of it), or over it and
    refused on the closer look, which is a blurred frame of somebody moving; a crowd of faces far
    under the floor is not a person about to step forward. The stretches are the ones between that
    moment and the planned moments either side of it (the end of the file after the last), since a
    face growing towards the floor is growing towards one of them, and each gets
    `tuning.LOOK_AGAIN_EACH_SIDE` moments evenly spaced across it.

    **Bounded.** At most twice `LOOK_AGAIN_EACH_SIDE` moments per file, once per pass, and never
    for a file in which anything was kept: the cost is one more read of that many moments and the
    detector on each, about what the plan itself spends on a short clip at the fast effort.
    """
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
    """One chosen face and the picture it is examined in, with the way back to the reduced frame.

    `detection` is in `picture`'s pixels. For a piece of the file at its own size that is the
    file's pixels less the piece's corner; for the reduced frame it is the frame's own, and the
    defaults below make the way back nothing at all.
    """

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
    """One appearance, with each of its frames told how well it matches the others.

    Here rather than in `_describe` because here is the first place the whole appearance exists: a
    segment is one unbroken run of a face, capped at two described frames, and the runs are joined
    into an appearance afterwards. Asked a run at a time, the question has almost nothing to work
    with; asked here, an appearance can hold a dozen frames of the same person.
    """
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
