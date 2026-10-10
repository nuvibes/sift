# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a pass over one file does, and (more importantly) what it refuses to do.

Three of the tests here are about work NOT happening. They are the ones worth reading: the
guarantees this feature makes about cost are all of the form "the expensive step runs a small fixed
number of times", and the only honest way to check that is to ask the expensive step how often it
was called.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

from sift.slices.faces import frames as framing
from sift.slices.faces import quality as quality_module
from sift.slices.faces import settings as face_settings
from sift.slices.faces import tracking, tuning
from sift.slices.faces.frames import Frame
from sift.slices.faces.models import Box, Described, Detection, Finding
from sift.slices.faces.pipeline import Bar, Outcome, Pipeline, _Tally, again_moments
from sift.slices.faces.tests.conftest import (
    FakeDetector,
    FakeRecognizer,
    draw_face,
    landmarks_for,
    noisy_frame,
    person_vector,
    unit,
)

pytestmark = pytest.mark.unit

FRAME_WIDTH = 640
FRAME_HEIGHT = 480


@dataclass
class ScriptedReader:
    """Hands back frames a test wrote out, and counts how many times a file was opened."""

    frames: list[Frame]
    opens: int = 0
    asked: tuple[int, ...] = ()
    """The moments the pass planned to look at. What a resumed pass trims is exactly this."""

    async def stream(self, path, **kwargs: object):  # type: ignore[no-untyped-def]
        self.opens += 1
        planned = kwargs.get("timestamps")
        self.asked = tuple(planned) if isinstance(planned, tuple | list) else ()
        for frame in self.frames:
            yield frame


def moving_face(
    count: int, *, step_ms: int = 1000, drift: int = 4
) -> tuple[list[Frame], dict[int, list[tuple[Box, float]]]]:
    """One person, present throughout, moving a little between each sampled moment."""
    frames: list[Frame] = []
    placed: dict[int, list[tuple[Box, float]]] = {}
    for index in range(count):
        stamp = index * step_ms
        frame = noisy_frame(FRAME_WIDTH, FRAME_HEIGHT, seed=index)
        box = draw_face(frame, x=100 + index * drift, y=90, size=160)
        frames.append(Frame(pixels=frame, timestamp_ms=stamp))
        placed[stamp] = [(box, 0.9)]
    return frames, placed


def build(
    frames: list[Frame],
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    *,
    bar: Bar | None = None,
    budget_seconds: float | None = None,
    batched: bool = False,
) -> tuple[Pipeline, ScriptedReader]:
    reader = ScriptedReader(frames=frames)
    pipeline = Pipeline(
        reader,  # type: ignore[arg-type]
        detector,  # type: ignore[arg-type]
        recognizer,  # type: ignore[arg-type]
        bar=bar or Bar(min_pixels=48, min_sharpness=1.0, min_frontality=0.05),
        budget_seconds=budget_seconds,
        batched=batched,
    )
    return pipeline, reader


async def run(
    pipeline: Pipeline,
    *,
    media_type: str = "video",
    duration_ms: int = 30_000,
    after_ms: int | None = None,
) -> Outcome:
    return await pipeline.run(
        Path("clip.mp4"),
        media_type=media_type,
        width=FRAME_WIDTH,
        height=FRAME_HEIGHT,
        duration_ms=duration_ms,
        after_ms=after_ms,
    )


# --- the tracker is what makes this affordable ---------------------------------------------------


async def test_following_one_face_across_frames_describes_it_a_few_times_not_every_frame(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """The saving the whole feature rests on.

    One person, on screen for every sampled moment of a clip. Following them across the moments
    means describing the best couple of views, not one per frame. Break the linking and this count
    becomes the number of frames.
    """
    frames, placed = moving_face(8)
    detector.placed = placed
    pipeline, _ = build(frames, detector, recognizer)

    outcome = await run(pipeline)

    assert len(outcome.appearances) == 1
    assert outcome.frames_examined == len(frames)
    assert recognizer.calls == tuning.FRAMES_PER_TRACK
    assert recognizer.calls < len(frames) / 3


async def test_on_a_card_a_files_faces_are_described_in_one_run_of_the_model(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """Every face kept across every run of the file goes to the recognizer together, and comes
    back as the same appearances one face at a time gives; the processor keeps one per call."""
    frames, placed = moving_face(8)
    detector.placed = placed
    one_at_a_time = await run(build(frames, detector, recognizer)[0])
    assert recognizer.many_calls == 0 and recognizer.calls == tuning.FRAMES_PER_TRACK

    recognizer.calls = recognizer.many_calls = 0
    together = await run(build(frames, detector, recognizer, batched=True)[0])

    assert recognizer.many_calls == 1 and recognizer.calls == tuning.FRAMES_PER_TRACK

    def described(outcome: Outcome) -> list[tuple[int, int, tuple[float, ...], float]]:
        return [
            (one.started_ms, one.ended_ms, face.vector, face.quality.score)
            for one in outcome.appearances
            for face in one.faces
        ]

    assert described(together) == described(one_at_a_time)


async def test_a_face_that_jumps_across_the_frame_is_still_one_appearance(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """The second stage of following, and the reason it exists.

    Moments are seconds apart, so a person who has walked across the shot does not overlap
    themselves at all. Position alone reports two appearances; comparing the descriptions (which
    had to be computed anyway) puts them back together. Without this, a file with one person in
    it reads as partly identified for ever once one half of them is named.
    """
    frames: list[Frame] = []
    placed: dict[int, list[tuple[Box, float]]] = {}
    for index in range(6):
        stamp = index * 1000
        frame = noisy_frame(FRAME_WIDTH, FRAME_HEIGHT, seed=index)
        # Jumps from one side to the other half way through: no overlap between the two runs.
        box = draw_face(frame, x=40 if index < 3 else 420, y=90, size=150)
        frames.append(Frame(pixels=frame, timestamp_ms=stamp))
        placed[stamp] = [(box, 0.9)]

    detector.placed = placed
    recognizer.rule = lambda chip: person_vector(0)
    pipeline, _ = build(frames, detector, recognizer)

    outcome = await run(pipeline)

    assert len(outcome.appearances) == 1
    assert outcome.appearances[0].started_ms == 0
    assert outcome.appearances[0].ended_ms == 5000


async def test_two_different_people_are_two_appearances(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """The other side of joining runs: it must not join two people who never overlapped."""
    frames: list[Frame] = []
    placed: dict[int, list[tuple[Box, float]]] = {}
    for index in range(6):
        stamp = index * 1000
        frame = noisy_frame(FRAME_WIDTH, FRAME_HEIGHT, seed=index)
        box = draw_face(frame, x=40 if index < 3 else 420, y=90, size=150)
        frames.append(Frame(pixels=frame, timestamp_ms=stamp))
        placed[stamp] = [(box, 0.9)]

    detector.placed = placed
    # Each half of the clip is a different person.
    order: list[int] = []

    def rule(chip: np.ndarray) -> list[float]:
        order.append(len(order))
        return list(person_vector(0 if len(order) <= 2 else 3))

    recognizer.rule = rule
    pipeline, _ = build(frames, detector, recognizer)

    outcome = await run(pipeline)

    assert len(outcome.appearances) == 2


# --- the quality bar comes BEFORE the expensive step ----------------------------------------------


async def test_a_face_below_the_bar_is_never_described(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """The cost saving IS the feature, so the ordering is what gets asserted.

    A face too small to be worth anything must not merely fail to match: it must never reach the
    recognizer at all. Move the bar after the description and this count stops being zero.
    """
    frames: list[Frame] = []
    placed: dict[int, list[tuple[Box, float]]] = {}
    for index in range(4):
        stamp = index * 1000
        frame = noisy_frame(FRAME_WIDTH, FRAME_HEIGHT, seed=index)
        box = draw_face(frame, x=100, y=90, size=20)
        frames.append(Frame(pixels=frame, timestamp_ms=stamp))
        placed[stamp] = [(box, 0.9)]

    detector.placed = placed
    pipeline, _ = build(
        frames, detector, recognizer, bar=Bar(min_pixels=64, min_sharpness=1.0, min_frontality=0.05)
    )

    outcome = await run(pipeline)

    assert recognizer.calls == 0
    assert outcome.appearances == ()


async def test_the_closer_second_look_happens_only_for_faces_about_to_be_described(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """Looking again is cheap but not free, so it is spent only where it changes the answer."""
    frames, placed = moving_face(8)
    detector.placed = placed
    pipeline, _ = build(frames, detector, recognizer)

    outcome = await run(pipeline)

    assert detector.detect_calls == outcome.frames_examined
    assert detector.refine_calls == tuning.FRAMES_PER_TRACK


# --- several people in one file --------------------------------------------------------------------


async def test_three_people_in_one_picture_are_three_separate_faces(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """The rule the whole feature is shaped around: the unit is a face, never a file."""
    frame = noisy_frame(FRAME_WIDTH, FRAME_HEIGHT, seed=1)
    boxes = [
        draw_face(frame, x=20, y=100, size=120, shade=200),
        draw_face(frame, x=250, y=100, size=120, shade=170),
        draw_face(frame, x=480, y=100, size=120, shade=140),
    ]
    detector.placed = {0: [(box, 0.9) for box in boxes]}
    seen: list[int] = []

    def rule(chip: np.ndarray) -> list[float]:
        seen.append(len(seen))
        return list(person_vector(len(seen)))

    recognizer.rule = rule
    pipeline, _ = build([Frame(pixels=frame, timestamp_ms=0)], detector, recognizer)

    outcome = await run(pipeline, media_type="image", duration_ms=0)

    assert len(outcome.appearances) == 3


# --- GIFs ------------------------------------------------------------------------------------


async def test_a_gif_yields_faces_from_more_than_one_frame(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """A GIF is read once and every frame of it is available.

    Treated as a still (which is the obvious shortcut, because it has no timeline worth the name),
    a GIF contributes one face. Read whole, it is the cheapest kind of file there is.
    """
    frames: list[Frame] = []
    placed: dict[int, list[tuple[Box, float]]] = {}
    for index in range(12):
        frame = noisy_frame(320, 240, seed=index)
        box = draw_face(frame, x=20 + index * 12, y=40, size=110)
        frames.append(Frame(pixels=frame, timestamp_ms=index))
        placed[index] = [(box, 0.9)]

    detector.placed = placed
    recognizer.rule = lambda chip: person_vector(0)
    pipeline, reader = build(frames, detector, recognizer)

    outcome = await pipeline.run(
        Path("loop.gif"), media_type="gif", width=320, height=240, duration_ms=0
    )

    assert reader.opens == 1
    assert detector.detect_calls == 12
    assert len(outcome.appearances) == 1
    assert outcome.appearances[0].seen_in >= 1


# --- reading a file through, and the time limit ----------------------------------------------------


async def test_a_long_stretch_with_nothing_new_is_read_to_the_end(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """A long quiet stretch is no reason to stop: somebody who walks on in the last third is only
    found by reading the last third. Every effort reads its plan out."""
    frames, placed = moving_face(40)
    detector.placed = placed
    pipeline, _ = build(frames, detector, recognizer)

    outcome = await run(pipeline, duration_ms=600_000)

    assert not outcome.stopped_early
    assert outcome.frames_examined == len(frames)
    assert detector.detect_calls == len(frames)
    assert outcome.coverage == 1.0


async def test_the_time_limit_still_stops_a_pass_and_leaves_it_unfinished(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """The one early stop left. It keeps a position so a later pass carries on from it."""
    frames, placed = moving_face(40)
    detector.placed = placed
    pipeline, _ = build(frames, detector, recognizer, budget_seconds=0.0)

    outcome = await run(pipeline, duration_ms=600_000)

    assert outcome.stopped_early
    assert outcome.frames_examined == 1
    assert outcome.coverage < 1.0


# --- carrying on from where a pass gave up ----------------------------------------------------------


async def test_a_resumed_pass_only_looks_at_the_moments_after_the_last_one_stopped(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """The point of recording where a pass reached: the next one does not read it again."""
    frames, placed = moving_face(4)
    detector.placed = placed
    pipeline, reader = build(frames, detector, recognizer)

    await run(pipeline, duration_ms=600_000, after_ms=300_000)

    assert reader.asked
    assert min(reader.asked) > 300_000


async def test_a_resumed_pass_counts_what_the_earlier_one_covered(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """A pass that reads the last tenth of a video has covered the whole of it, not a tenth.

    Coverage decides whether a file is finished, so counting only this pass would leave a file
    that has been looked at end to end reporting that it has barely been started, and being
    offered again forever.
    """
    frames, placed = moving_face(4)
    detector.placed = placed
    pipeline, reader = build(frames, detector, recognizer)

    outcome = await run(pipeline, duration_ms=600_000, after_ms=300_000)

    assert outcome.frames_skipped == outcome.frames_planned - len(reader.asked)
    assert outcome.coverage > outcome.frames_examined / outcome.frames_planned


async def test_a_resumed_pass_that_reads_nothing_does_not_lose_where_the_last_one_got_to(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """Otherwise a file whose tail turns out to be empty starts again from the beginning."""
    pipeline, _ = build([], detector, recognizer)

    outcome = await run(pipeline, duration_ms=600_000, after_ms=300_000)

    assert outcome.reached_ms == 300_000


async def test_a_pass_records_the_last_moment_it_actually_looked_at(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    frames, placed = moving_face(40)
    detector.placed = placed
    # Stopped by the time limit, the one stop there is: without one this pass reads to the end.
    pipeline, _ = build(frames, detector, recognizer, budget_seconds=0.0)

    outcome = await run(pipeline, duration_ms=600_000)

    assert outcome.stopped_early
    assert outcome.reached_ms == frames[outcome.frames_examined - 1].timestamp_ms


async def test_a_file_that_was_looked_at_completely_reports_full_coverage(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    frame = noisy_frame(FRAME_WIDTH, FRAME_HEIGHT, seed=1)
    box = draw_face(frame, x=100, y=90, size=160)
    detector.placed = {0: [(box, 0.9)]}
    pipeline, _ = build([Frame(pixels=frame, timestamp_ms=0)], detector, recognizer)

    outcome = await run(pipeline, media_type="image", duration_ms=0)

    assert outcome.coverage == 1.0
    assert not outcome.stopped_early


@pytest.mark.parametrize("preset", sorted(face_settings.QUALITY_BARS))
async def test_the_quality_bar_is_the_quality_setting_and_nothing_else(preset: str) -> None:
    """A deep pass measures a face against exactly the bar a fast one does.

    A deep pass that halved the measurements would let a face through at half the size the
    recognizer reads, and faces that small describe invented detail. Depth is about how many moments
    get looked at.

    Read from the real presets rather than numbers written out here, so moving a floor does not fail
    this for the wrong reason.
    """
    level = face_settings.QUALITY_BARS[preset]
    bar = Bar.of(level)

    assert (bar.min_pixels, bar.min_sharpness, bar.min_frontality) == level


async def test_no_preset_admits_a_face_smaller_than_it_can_be_described_from() -> None:
    """Every accepted face is warped to a 112-pixel square and described from it.

    So a preset below that is one whose faces are upscaled before being read, whatever the depth.
    The loosest sits at 96: a stretch of 1.17, and nothing lower is admitted.
    """
    for level in face_settings.QUALITY_BARS.values():
        assert Bar.of(level).min_pixels >= tuning.MIN_PIXELS_ACCEPTED


async def test_a_face_the_closer_look_redeems_is_kept_rather_than_refused_on_the_coarse_reading(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """The other direction of the same seam, and it can cost whole files.

    Blur, angle and containment are all read off the aligned square, and the square is cut by
    warping the frame onto the landmarks, so all three measure the landmarks as much as the face.
    The first look's are the coarse ones, pinned to the grid of a frame reduced to a 640-pixel
    square, which is the entire reason `refine` exists. Judging a face on them and discarding it
    there means the careful measurement is never taken.

    A photograph of somebody looking three quarters towards the camera can read as turned too far
    on the coarse landmarks and well inside the line on the refined ones, and a photograph is one
    frame, so one coarse reading would decide everything and record it as having no faces.
    """
    frames, placed = moving_face(3)
    detector.placed = placed
    detector.coarse_first_look = True
    pipeline, _ = build(frames, detector, recognizer)

    outcome = await run(pipeline)

    assert detector.refine_calls > 0, "the closer look never happened"
    assert outcome.appearances != (), "a face refused on the coarse reading was never looked at"


async def test_a_face_too_small_to_use_is_dropped_before_the_closer_look(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """What is left of the first gate, and why it is left.

    Size is read off the detector's box rather than off any square, so the closer look barely moves
    it, and it is the check that rules out the stranger in the background of a photograph, which
    is most of what a worthless run is made of. Keeping it here holds the cost of such a run at the
    forward passes it already was.
    """
    frames, placed = moving_face(3)
    detector.placed = placed
    pipeline, _ = build(
        frames,
        detector,
        recognizer,
        bar=Bar(min_pixels=4096, min_sharpness=1.0, min_frontality=0.05),
    )

    outcome = await run(pipeline)

    assert outcome.appearances == ()
    assert detector.refine_calls == 0, "a face below the size floor was looked at closely anyway"
    assert recognizer.calls == 0


async def test_a_face_the_closer_look_ruins_is_thrown_away_rather_than_stored(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """The bar has to survive the second look.

    A face is measured, accepted, and then looked at again more closely to place its features
    precisely, and the chip is cut from those landmarks. When they come back degenerate the chip
    is a smear rather than a face, with a frontality of 0.0 against a floor of 0.15, so the bar is
    applied after that step too.

    They do not only look wrong: a description taken from a warped chip sits near every other
    warped chip rather than near the person it came from, so they pull strangers together and keep
    one person apart.
    """
    frames, placed = moving_face(3)
    detector.placed = placed
    # The closer look hands back landmarks with no spread between the eyes, which is what a failed
    # refinement produces and what `frontality` reads as zero.
    detector.ruin_refine = True
    pipeline, _ = build(frames, detector, recognizer)

    outcome = await run(pipeline)

    assert outcome.appearances == ()
    assert recognizer.calls == 0, "a chip nothing could be recognized from was described anyway"


async def test_a_face_cut_mostly_from_outside_the_frame_is_thrown_away_rather_than_stored(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """The second way a square stops being a picture of a face.

    A face at the side of a shot is resampled partly from beyond the edge of the frame. Such a band
    can be half the square deep and still clear the size, sharpness and frontality floors:
    sharpness is the variance of the whole square, and a band beside a sharp face still averages
    well above the floor.

    Nor can this be a bounds check on the detector box, which usually lies wholly inside the frame:
    the square is rotated and scaled onto the landmarks and reaches further than the box does.
    """
    frames: list[Frame] = []
    placed: dict[int, list[tuple[Box, float]]] = {}
    for index in range(3):
        stamp = index * 1000
        frame = noisy_frame(FRAME_WIDTH, FRAME_HEIGHT, seed=index)
        draw_face(frame, x=0, y=90, size=160)
        # Almost all of it beyond the left edge. The box says where the face is, not where the
        # pixels are, which is exactly the case a bounds check on the box would wave through.
        placed[stamp] = [(Box(x=-150, y=90, width=160, height=160), 0.9)]
        frames.append(Frame(pixels=frame, timestamp_ms=stamp))
    detector.placed = placed
    pipeline, _ = build(frames, detector, recognizer)

    outcome = await run(pipeline)

    assert outcome.appearances == ()
    assert recognizer.calls == 0, "a square that was mostly one repeated pixel was described anyway"


async def test_a_file_with_no_faces_in_it_produces_nothing_and_costs_nothing(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    frames = [
        Frame(pixels=noisy_frame(320, 240, seed=index), timestamp_ms=index) for index in range(3)
    ]
    pipeline, _ = build(frames, detector, recognizer)

    outcome = await run(pipeline)

    assert outcome.appearances == ()
    assert recognizer.calls == 0


def test_a_tail_nothing_can_read_counts_as_covered() -> None:
    """A file that would otherwise be re-queued on every sweep, forever.

    A video whose length is not a whole number of sampling steps has a last moment inside the file
    that the decoder will not return a frame for. The pass plans it, reads nothing, and reports a
    coverage a fraction below one, so the file is never settled, the next sweep offers it again,
    and the pass after that plans the same unreadable moment.
    """
    outcome = Outcome(
        appearances=(),
        frames_examined=0,
        frames_planned=8,
        stopped_early=False,
        frames_skipped=7,
        frames_attempted=1,
        reached_ms=6000,
    )

    assert outcome.coverage == 1.0


def test_a_pass_that_ran_its_plan_out_is_covered_however_little_decoded() -> None:
    """A GIF only part of which decodes: every planned moment was asked for, the pass was
    not cut short, and a quarter of them came back. No later pass gets more out of it, so the file
    is settled rather than offered again by every run."""
    outcome = Outcome(
        appearances=(),
        frames_examined=2,
        frames_planned=8,
        stopped_early=False,
        frames_attempted=8,
        reached_ms=1000,
    )

    assert outcome.coverage == 1.0


def test_a_pass_that_was_cut_short_is_still_unfinished() -> None:
    """The case that must NOT be swallowed by the rule above. A pass told to stop has work left,
    and resuming exists for exactly this: treating it as covered would abandon the rest of a
    long video the first time a budget fired."""
    outcome = Outcome(
        appearances=(),
        frames_examined=0,
        frames_planned=8,
        stopped_early=True,
        frames_skipped=4,
        frames_attempted=4,
        reached_ms=4000,
    )

    assert outcome.coverage < 1.0


def test_a_pass_with_nothing_left_to_look_at_is_unchanged() -> None:
    """No moments attempted at all means the plan was already run out, and the arithmetic answers
    that on its own. The rule above must not reach it."""
    outcome = Outcome(
        appearances=(),
        frames_examined=0,
        frames_planned=8,
        stopped_early=False,
        frames_skipped=8,
        frames_attempted=0,
        reached_ms=8000,
    )

    assert outcome.coverage == 1.0


# --- the two terms that rank a face after it has been described ------------------------------------
#
# Both exist because the four measurements taken BEFORE the description cannot see two things
# that a person looking at the pile screen sees immediately: a picture that is not really of a face,
# and a frame that does not belong with the rest of its appearance.


def _described(vectors: list[list[float]]) -> tuple[Described, ...]:
    """Faces of one appearance, made by hand. Only the descriptions matter here."""
    from sift.slices.faces.models import Detection, Quality

    box = Box(x=0, y=0, width=120, height=120)
    return tuple(
        Described(
            detection=Detection(box=box, score=0.9, landmarks=(), timestamp_ms=index * 1000),
            quality=Quality(pixels=120, sharpness=500.0, frontality=1.0, score=0.5, accepted=True),
            vector=unit(values),
            chip=np.zeros((1, 1, 3), dtype=np.uint8),
        )
        for index, values in enumerate(vectors)
    )


def test_one_frame_agrees_with_nothing_and_is_given_the_benefit_of_it() -> None:
    """Three quarters of appearances are a single frame and a still can never be more, so the
    no-evidence case is the common one, and it must read as no evidence rather than as
    disagreement, or the term would quietly demote most of a library."""
    assert tracking.agreements(_described([[1.0, 0.0, 0.0]])) == (1.0,)


def test_two_frames_are_given_the_same_number_and_so_cannot_be_reordered_by_it() -> None:
    """Each is compared against the other, so it is one comparison written down twice. Two frames
    disagreeing says one of them is wrong; it does not say which, and this term does not pretend
    to know."""
    first, second = tracking.agreements(_described([[1.0, 0.0, 0.0], [0.6, 0.8, 0.0]]))

    assert first == pytest.approx(second)


def test_the_frame_unlike_the_rest_is_the_one_marked_down() -> None:
    values = [[1.0, 0.0, 0.0], [1.0, 0.05, 0.0], [1.0, -0.05, 0.0], [0.0, 1.0, 0.0]]

    scored = tracking.agreements(_described(values))

    assert scored.index(min(scored)) == 3
    assert min(scored) < 0.5 < max(scored)


def test_a_face_is_never_compared_against_a_group_that_includes_itself() -> None:
    """With its own description in the average, the frame least like the rest drags the very
    target it is measured against towards itself, so the worse it is, the better it scores. The
    odd one out here would sit at 0.71 against its own group and 0.0 against the others."""
    scored = tracking.agreements(_described([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]))

    assert scored[2] == pytest.approx(0.0, abs=1e-6)


async def test_the_frame_that_matches_its_appearance_least_is_ranked_last_by_a_real_pass(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """The wiring, end to end. Four described frames of one person (two runs, joined), with one
    of them describing somebody slightly different."""
    frames: list[Frame] = []
    placed: dict[int, list[tuple[Box, float]]] = {}
    for index in range(6):
        stamp = index * 1000
        frame = noisy_frame(FRAME_WIDTH, FRAME_HEIGHT, seed=index)
        box = draw_face(frame, x=40 if index < 3 else 420, y=90, size=150)
        frames.append(Frame(pixels=frame, timestamp_ms=stamp))
        placed[stamp] = [(box, 0.9)]
    detector.placed = placed

    # Far enough from the rest to stand out, near enough that the two runs still join. Otherwise
    # this would be a test about the tracker refusing to merge rather than about the term.
    odd = [a * 0.75 + b * 0.25 for a, b in zip(person_vector(0), person_vector(1), strict=True)]
    seen: list[int] = []

    def rule(chip: np.ndarray) -> list[float]:
        seen.append(1)
        return odd if len(seen) == 3 else list(person_vector(0))

    recognizer.rule = rule
    pipeline, _ = build(frames, detector, recognizer)

    outcome = await run(pipeline)

    assert len(outcome.appearances) == 1
    faces = outcome.appearances[0].faces
    assert len(faces) == 4
    strange = min(faces, key=lambda face: face.quality.agreement)
    assert strange.vector == pytest.approx(unit(odd))
    assert strange.quality.agreement < min(
        face.quality.agreement for face in faces if face is not strange
    )
    # It is ranked, not refused. Every one of the four is still kept.
    assert all(face.quality.accepted for face in faces)


async def test_a_recognizer_that_answers_weakly_ranks_the_whole_appearance_lower(
    detector: FakeDetector,
) -> None:
    """The other term, end to end, and the reason it exists: the pictures it marks down are large,
    sharp and apparently front-on, so all four of the earlier measurements rate them well."""
    frames, placed = moving_face(4)
    detector.placed = placed
    ramp = (14.0, 21.0)

    firm, _ = build(frames, detector, FakeRecognizer(ramp=ramp, strength_rule=lambda chip: 21.0))
    confident = await run(firm)

    weak, _ = build(frames, detector, FakeRecognizer(ramp=ramp, strength_rule=lambda chip: 15.0))
    hesitant = await run(weak)

    assert hesitant.appearances[0].quality < confident.appearances[0].quality
    # Ranked lower, not thrown away: the same faces come back either way.
    assert len(hesitant.appearances[0].faces) == len(confident.appearances[0].faces)
    assert all(face.quality.accepted for face in hesitant.appearances[0].faces)


async def test_the_raw_length_is_kept_beside_the_face_not_only_the_term_taken_from_it(
    detector: FakeDetector,
) -> None:
    """So that the second model family's ramp can be set from evidence later without re-reading a
    single file. A term cannot be turned back into the measurement behind it."""
    frames, placed = moving_face(2)
    detector.placed = placed

    pipeline, _ = build(frames, detector, FakeRecognizer(strength_rule=lambda chip: 17.25))
    outcome = await run(pipeline)

    assert all(
        face.quality.strength == pytest.approx(17.25) for face in outcome.appearances[0].faces
    )


# --- a face is measured in the file's own pixels -------------------------------------------------


@dataclass
class WideReader(ScriptedReader):
    """A reader for a file larger than the frames a pass reads: the reduced frames are what
    `stream` hands over, and `own` is each moment at the file's full size, for `windows`."""

    own: dict[int, np.ndarray] = field(default_factory=dict)
    pieces_asked: int = 0

    async def windows(self, path, *, media_type, width, height, wanted):  # type: ignore[no-untyped-def]
        self.pieces_asked += len(wanted)
        return [
            None if at not in self.own else framing.cut(self.own[at], box) for at, box in wanted
        ]


def wide_runway(
    count: int,
) -> tuple[list[Frame], dict[int, np.ndarray], dict[int, list[tuple[Box, float]]]]:
    """One person at 1440p, 120 pixels tall in the file and so 60 in the frame the pass reads:
    the shape of a large clip that would otherwise record no faces at all."""
    reduced: list[Frame] = []
    own: dict[int, np.ndarray] = {}
    placed: dict[int, list[tuple[Box, float]]] = {}
    for index in range(count):
        stamp = index * 1000
        full = noisy_frame(2560, 1440, seed=index)
        draw_face(full, x=1000 + index * 8, y=500, size=120)
        own[stamp] = full
        reduced.append(Frame(pixels=np.ascontiguousarray(full[::2, ::2]), timestamp_ms=stamp))
        placed[stamp] = [(Box(x=500 + index * 4, y=250, width=60, height=60), 0.9)]
    return reduced, own, placed


def wide_pipeline(
    reader: WideReader, detector: FakeDetector, recognizer: FakeRecognizer
) -> Pipeline:
    return Pipeline(
        reader,  # type: ignore[arg-type]
        detector,  # type: ignore[arg-type]
        recognizer,  # type: ignore[arg-type]
        bar=Bar(min_pixels=tuning.MIN_PIXELS, min_sharpness=1.0, min_frontality=0.05),
    )


async def wide_run(pipeline: Pipeline) -> Outcome:
    return await pipeline.run(
        Path("runway.mp4"), media_type="video", width=2560, height=1440, duration_ms=3000
    )


async def test_a_face_small_in_the_reduced_frame_but_large_in_the_file_is_described(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """A large file, reduced to its shape.

    Faces 89 to 142 pixels tall in the file are 47 to 68 in the frame the pass reads, and read there
    every one would be refused and the file recorded as having nobody in it. Measured in the file's
    own pixels, and cut from them, they are faces the recognizer can read.
    """
    reduced, own, placed = wide_runway(3)
    detector.placed = placed
    reader = WideReader(frames=reduced, own=own)

    outcome = await wide_run(wide_pipeline(reader, detector, recognizer))

    assert len(outcome.appearances) == 1, "a face large enough in the file was refused"
    assert reader.pieces_asked > 0, "the face was not read again at the file's size"
    faces = outcome.appearances[0].faces
    assert all(face.quality.pixels >= tuning.MIN_PIXELS for face in faces)
    # Stored in the reduced frame's pixels, which is what cutting a cover reads a box as.
    assert all(face.detection.box.long_side == 60 for face in faces)
    assert all(500 <= face.detection.box.x <= 520 for face in faces)
    assert outcome.refused_small == 0


async def test_a_moment_that_will_not_read_again_is_judged_on_the_reduced_frame(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """No piece means the reduced frame, and the floor then judges what IT holds: a face too
    small there is refused rather than described from pixels nobody read."""
    reduced, _, placed = wide_runway(3)
    detector.placed = placed
    reader = WideReader(frames=reduced, own={})

    outcome = await wide_run(wide_pipeline(reader, detector, recognizer))

    assert outcome.appearances == ()
    assert recognizer.calls == 0
    # A size refusal whichever look caught it: the reason a person reads is the same.
    assert outcome.refused_small > 0
    assert outcome.refused_closer == 0


async def test_a_pass_that_finds_only_faces_too_small_says_so(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """Two files that both come to "no faces" (one empty, one full of people too far away to
    describe) are told apart by what the pass refused."""
    frames, placed = moving_face(3)
    detector.placed = placed
    pipeline, _ = build(
        frames,
        detector,
        recognizer,
        bar=Bar(min_pixels=4096, min_sharpness=1.0, min_frontality=0.05),
    )

    outcome = await run(pipeline)

    assert outcome.appearances == ()
    assert outcome.refused_small == 3
    # The size a person can judge "too small" by: the biggest face refused, in the file's pixels.
    assert outcome.refused_largest == max(box.long_side for [(box, _)] in placed.values())


@pytest.mark.parametrize(
    ("bar", "ruined", "reason"),
    [
        (Bar(min_pixels=48, min_sharpness=1e12, min_frontality=0.05), False, "refused_blurred"),
        # A face read as fully side-on: one turned less than that is kept to be asked about.
        (Bar(min_pixels=48, min_sharpness=1.0, min_frontality=0.05), True, "refused_turned"),
    ],
)
async def test_the_closer_look_counts_each_refusal_under_its_reason(
    detector: FakeDetector, recognizer: FakeRecognizer, bar: Bar, ruined: bool, reason: str
) -> None:
    """Blur and angle are told apart, so the History line can say which one it was."""
    frames, placed = moving_face(3)
    detector.placed = placed
    detector.ruin_refine = ruined
    pipeline, _ = build(frames, detector, recognizer, bar=bar)

    outcome = await run(pipeline)

    assert outcome.appearances == ()
    assert outcome.refused_closer > 0
    split = {
        "refused_blurred": outcome.refused_blurred,
        "refused_turned": outcome.refused_turned,
        "refused_edge": outcome.refused_edge,
    }
    assert split.pop(reason) == outcome.refused_closer
    assert set(split.values()) == {0}
    assert (outcome.refused_small, outcome.refused_largest) == (0, 0)


def test_a_face_off_the_edge_is_counted_as_that_and_a_small_one_keeps_the_largest() -> None:
    tally = _Tally()
    edge = quality_module.assess(
        Box(0, 0, 200, 200),
        ((70.0, 80.0), (130.0, 80.0), (100.0, 110.0), (75.0, 140.0), (125.0, 140.0)),
        np.full((112, 112, 3), 128, dtype=np.uint8),
        containment=0.5,
        min_sharpness=0.0,
    )
    tally.refuse_closer(edge)
    tally.refuse_small(90.4)
    tally.refuse_small(77.0)

    assert edge.failed is Finding.RUNS_OFF_EDGE
    assert tally.reasons[Finding.RUNS_OFF_EDGE] == 1
    assert (tally.closer, tally.small, tally.largest) == (1, 2, 90)


# --- a face turned past the line ------------------------------------------------------------------


async def test_a_run_turned_past_the_line_is_kept_as_a_face_sift_may_only_ask_about(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """Large, sharp, whole, and refused for its angle alone: described and kept, not refused.

    The bar's angle is set just over a square-on face, so every face here is "turned" without a
    landmark moving. Undo `quality.asked_only` and the file reads as having nobody in it.
    """
    frames, placed = moving_face(3)
    detector.placed = placed
    pipeline, _ = build(
        frames, detector, recognizer, bar=Bar(min_pixels=48, min_sharpness=1.0, min_frontality=1.01)
    )

    outcome = await run(pipeline)

    assert len(outcome.appearances) == 1
    faces = outcome.appearances[0].faces
    assert faces and all(face.quality.failed is Finding.TURNED_AWAY for face in faces)
    assert (outcome.refused_turned, outcome.refused_closer) == (0, 0)


@dataclass
class _Angled(FakeDetector):
    """Places one face whose nose sits `offset` of the eye-to-mouth drop off centre at the moments
    listed in `turned`, and square-on everywhere else."""

    turned: frozenset[int] = frozenset()
    offset: float = 0.3

    def detect(self, frame, *, timestamp_ms=0, threshold=0.5, retry=True):  # type: ignore[no-untyped-def]
        found = super().detect(frame, timestamp_ms=timestamp_ms)
        if timestamp_ms not in self.turned:
            return found
        moved = []
        for one in found:
            points = list(one.landmarks)
            drop = one.box.height * (0.75 - 0.38)
            points[2] = (points[2][0] + self.offset * drop, points[2][1])
            moved.append(
                Detection(
                    box=one.box,
                    score=one.score,
                    landmarks=tuple(points),
                    timestamp_ms=one.timestamp_ms,
                )
            )
        return moved


async def test_a_turned_face_beside_one_over_the_line_is_refused_as_before(
    recognizer: FakeRecognizer,
) -> None:
    """An appearance is turned only when all of it is: a square-on frame stands for the run and
    the turned one is refused and counted, as it always was."""
    frames, placed = moving_face(2)
    detector = _Angled(placed=placed, turned=frozenset({1000}))
    pipeline, _ = build(
        frames, detector, recognizer, bar=Bar(min_pixels=48, min_sharpness=1.0, min_frontality=0.5)
    )

    outcome = await run(pipeline)

    assert len(outcome.appearances) == 1
    assert all(face.quality.accepted for face in outcome.appearances[0].faces)
    assert outcome.refused_turned == 1


# --- a video looked at again around a face near the floor ----------------------------------------


def test_the_moments_looked_at_again_are_either_side_of_the_nearest_face_and_bounded() -> None:
    planned = (0, 1000, 2000, 3000)

    again = again_moments((44, 2000), planned, 4000, floor=48)

    assert again == (1250, 1500, 1750, 2250, 2500, 2750)
    assert len(again) <= 2 * tuning.LOOK_AGAIN_EACH_SIDE


def test_the_last_moment_looks_on_to_the_end_and_the_first_looks_only_forwards() -> None:
    planned = (0, 1000, 2000, 3000)

    assert again_moments((44, 3000), planned, 4000, floor=48) == (
        2250,
        2500,
        2750,
        3250,
        3500,
        3750,
    )
    assert again_moments((44, 0), planned, 4000, floor=48) == (250, 500, 750)


def test_nothing_is_looked_at_again_for_a_face_out_of_reach_or_no_face_at_all() -> None:
    planned = (0, 1000, 2000)
    out_of_reach = round(tuning.LOOK_AGAIN_REACH * 48) - 1

    assert again_moments((out_of_reach, 1000), planned, 3000, floor=48) == ()
    assert again_moments(None, planned, 3000, floor=48) == ()
    assert again_moments((44, 1000), (), 3000, floor=48) == ()


def test_a_moment_already_read_or_off_the_ends_of_the_file_is_not_read_again() -> None:
    """Planned moments a few milliseconds apart leave stretches too short to split evenly: the
    spaced moments land back on the planned ones and on the start, and are left out."""
    assert again_moments((44, 2), (0, 2, 4), 6, floor=48) == (1, 3)


@dataclass
class MomentReader:
    """Draws whatever moment is asked for, and records every list of moments asked."""

    asked: list[tuple[int, ...]] = field(default_factory=list)

    async def stream(self, path, **kwargs: object):  # type: ignore[no-untyped-def]
        planned = kwargs.get("timestamps")
        moments = tuple(planned) if isinstance(planned, tuple | list) else ()
        self.asked.append(moments)
        for stamp in moments:
            yield Frame(
                pixels=noisy_frame(FRAME_WIDTH, FRAME_HEIGHT, seed=stamp), timestamp_ms=stamp
            )


def _walking_closer(sizes: dict[int, int], default: int = 30) -> FakeDetector:
    """One person whose face is `sizes[moment]` across at the moments listed, `default` elsewhere,
    standing in one place so every moment continues the same run."""

    class Walking(FakeDetector):
        def detect(self, frame, *, timestamp_ms=0, threshold=0.5, retry=True):  # type: ignore[no-untyped-def]
            self.detect_calls += 1
            size = sizes.get(timestamp_ms, default)
            box = draw_face(frame, x=100, y=90, size=size)
            return [
                Detection(
                    box=box, score=0.9, landmarks=landmarks_for(box), timestamp_ms=timestamp_ms
                )
            ]

    return Walking()


def _looking(detector: FakeDetector, recognizer: FakeRecognizer) -> tuple[Pipeline, MomentReader]:
    reader = MomentReader()
    pipeline = Pipeline(
        reader,  # type: ignore[arg-type]
        detector,  # type: ignore[arg-type]
        recognizer,  # type: ignore[arg-type]
        bar=Bar(min_pixels=48, min_sharpness=1.0, min_frontality=0.05),
    )
    return pipeline, reader


async def test_a_video_whose_faces_were_all_too_small_is_read_again_around_the_nearest(
    recognizer: FakeRecognizer,
) -> None:
    """Somebody walking closer: the face is under the floor at every moment of the plan and over
    it at a moment between two of them. Undo the look again and the file reads as nobody in it."""
    detector = _walking_closer({2000: 44, 2250: 60})
    pipeline, reader = _looking(detector, recognizer)

    outcome = await run(pipeline, duration_ms=6000)

    assert len(outcome.appearances) == 1
    assert outcome.looked_again == 6
    assert reader.asked[1] == (1250, 1500, 1750, 2250, 2500, 2750)
    # The plan's moments are what coverage counts; the look again is inside them.
    assert outcome.frames_examined == len(reader.asked[0])


@pytest.mark.parametrize(
    ("sizes", "media_type"),
    [
        ({2000: 20}, "video"),  # every face far under the floor: nobody about to step forward
        ({2000: 44, 3000: 60}, "video"),  # something kept: the plan found her
        ({2000: 44}, "image"),  # a still has no moment either side
    ],
)
async def test_nothing_is_read_again_unless_a_video_kept_nothing_and_came_close(
    recognizer: FakeRecognizer, sizes: dict[int, int], media_type: str
) -> None:
    detector = _walking_closer(sizes, default=20)
    pipeline, reader = _looking(detector, recognizer)

    outcome = await run(pipeline, media_type=media_type, duration_ms=6000)

    assert outcome.looked_again == 0
    assert len(reader.asked) == 1
