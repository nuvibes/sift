# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measuring whether a face is worth recognizing, before anything expensive happens to it.

The reason a face is refused is carried alongside the verdict, because the one place this is shown
to a person is a reference image they chose and Sift would not take, and "rejected" on its own is
not something anybody can act on.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from sift.slices.faces import quality
from sift.slices.faces.models import Box, Finding, Quality
from sift.slices.faces.tests.conftest import noisy_frame

pytestmark = pytest.mark.unit

FRONT_ON = ((30.0, 40.0), (70.0, 40.0), (50.0, 60.0), (35.0, 80.0), (65.0, 80.0))
IN_PROFILE = ((30.0, 40.0), (70.0, 40.0), (70.0, 60.0), (35.0, 80.0), (65.0, 80.0))


def sharp_chip() -> np.ndarray:
    return noisy_frame(112, 112, seed=1)


def flat_chip() -> np.ndarray:
    return np.full((112, 112, 3), 128, dtype=np.uint8)


# --- the three measurements -------------------------------------------------------------------------


def test_a_picture_with_fine_detail_in_it_scores_far_above_a_flat_one() -> None:
    """Motion blur and a soft focus both destroy exactly the fine structure a recognizer reads."""
    assert quality.sharpness(sharp_chip()) > quality.MIN_SHARPNESS
    assert quality.sharpness(flat_chip()) == 0.0


def test_a_picture_too_small_to_measure_scores_nothing_rather_than_failing() -> None:
    assert quality.sharpness(np.zeros((2, 2, 3), dtype=np.uint8)) == 0.0


def turned(degrees: float) -> tuple[tuple[float, float], ...]:
    """`FRONT_ON` with the head yawed, projected the way a camera would see it.

    Two things move and both matter. The nose swings out from the midpoint of the eyes by its own
    protrusion times the sine of the turn, and the eyes themselves draw CLOSER TOGETHER, by the
    cosine, because that is what a rotation looks like flattened onto a picture. The eye-to-mouth
    drop lies along the axis being turned about, so it does not move at all, which is the whole
    reason it is what the measure divides by.
    """
    angle = np.radians(degrees)
    half = 20.0 * np.cos(angle)
    nose = 50.0 + 0.5 * 40.0 * np.sin(angle)
    return (
        (50.0 - half, 40.0),
        (50.0 + half, 40.0),
        (nose, 60.0),
        (50.0 - half / 2, 80.0),
        (50.0 + half / 2, 80.0),
    )


def test_a_face_looking_straight_ahead_scores_full_and_one_in_profile_scores_nothing() -> None:
    assert quality.frontality(FRONT_ON) == pytest.approx(1.0)
    assert quality.frontality(IN_PROFILE) == pytest.approx(0.0)


def test_a_head_turned_past_a_three_quarter_view_is_measured_rather_than_written_off() -> None:
    """The commonest pose anybody photographs themselves in does not score zero.

    Dividing the nose's offset by the eye span would count the turn twice, because the span is
    itself foreshortened by that turn: `sin` over `cos` is `tan`, which reaches 1 at 45 degrees and
    passes it. Every head turned further than that would score 0.000: refused at every quality
    level, lenient included. A photograph of somebody looking half away from the camera is still a
    picture of them.
    """
    assert quality.frontality(turned(40.0)) > quality.MIN_FRONTALITY
    # Measured against the eye span both would be exactly 0.000, and no floor can be set below that.
    assert quality.frontality(turned(50.0)) > 0.0
    assert quality.frontality(turned(65.0)) > 0.0


def test_the_angle_measure_keeps_ranking_past_the_point_the_old_one_flattened() -> None:
    """A measure that saturates cannot rank the faces it exists to rank.

    Measured against the eye span, everything past 45 degrees would be zero, so a head turned a
    little too far and one facing directly away would be the same number, and a good share of the
    faces over the size floor would share that one value.

    Ranking matters beyond the refusal: the score picks which frames of an appearance are kept and
    described, so a flat measure hands that choice to blur and size alone.
    """
    readings = [quality.frontality(turned(d)) for d in (45.0, 60.0, 75.0, 89.0)]

    assert readings == sorted(readings, reverse=True)
    assert all(nearer > further for nearer, further in itertools.pairwise(readings))
    assert readings[-1] == pytest.approx(0.0, abs=0.02)


def test_a_tilted_head_is_not_mistaken_for_a_turned_one() -> None:
    """Measured along the line between the eyes, so tilting (which changes nothing about how
    recognizable a face is) does not register as turning."""
    angle = np.radians(25.0)
    turn = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    tilted = tuple(tuple(turn @ np.array(point)) for point in FRONT_ON)

    assert quality.frontality(tilted) == pytest.approx(1.0, abs=1e-6)


def test_landmarks_that_cannot_describe_an_arrangement_score_nothing() -> None:
    assert quality.frontality(((0.0, 0.0), (0.0, 0.0))) == 0.0
    assert quality.frontality(((1.0, 1.0), (1.0, 1.0), (1.0, 1.0))) == 0.0
    # All five, and still nothing to measure: the mouth is on the eyes, so there is no drop to
    # divide by. Zero rather than an exception, and zero rather than one: a face nothing can be
    # read from is refused, not waved through.
    assert quality.frontality(((0.0, 0.0),) * 5) == 0.0
    flat = ((30.0, 40.0), (70.0, 40.0), (50.0, 40.0), (35.0, 40.0), (65.0, 40.0))
    assert quality.frontality(flat) == 0.0


def test_a_face_the_detector_gave_only_three_points_for_is_refused_rather_than_guessed_at() -> None:
    """The measure needs the mouth now, and a caller that cannot supply it gets the safe answer.

    Every detector here returns five points, so this is the shape of the contract rather than a
    case that arises, but the safe answer is what makes it safe to tighten: refused, never a
    confident 1.0 taken from landmarks that were never read.
    """
    assert quality.frontality(FRONT_ON[:3]) == 0.0


# --- the verdict ----------------------------------------------------------------------------------------


def test_a_good_face_is_accepted_with_no_reason_to_give() -> None:
    verdict = quality.assess(Box(x=0, y=0, width=120, height=120), FRONT_ON, sharp_chip())

    assert verdict.accepted is True
    assert verdict.reason is None
    assert 0.0 < verdict.score <= 1.0


def test_a_face_too_few_pixels_across_is_refused_and_the_message_says_how_few() -> None:
    verdict = quality.assess(Box(x=0, y=0, width=20, height=20), FRONT_ON, sharp_chip())

    assert verdict.accepted is False
    assert verdict.reason is not None
    assert "20 pixels" in verdict.reason


def test_a_blurred_face_is_refused_and_says_so() -> None:
    verdict = quality.assess(Box(x=0, y=0, width=120, height=120), FRONT_ON, flat_chip())

    assert verdict.accepted is False
    assert verdict.reason == "the face is too blurred to recognize"


def test_a_face_turned_away_from_the_camera_is_refused_and_says_so() -> None:
    verdict = quality.assess(Box(x=0, y=0, width=120, height=120), IN_PROFILE, sharp_chip())

    assert verdict.accepted is False
    assert verdict.reason == "the face is turned too far away from the camera"


def test_a_face_cut_mostly_from_outside_the_frame_is_refused_and_says_so() -> None:
    """The fourth floor, and the one the other three were blind to.

    A square cut at the side of a shot is partly the edge pixel repeated. That band is sharp
    enough, big enough and as front-on as the landmarks say, so all three of the other floors let
    it through, and a streaked picture would reach the group screen with every measurement on it
    looking healthy.
    """
    verdict = quality.assess(
        Box(x=0, y=0, width=120, height=120), FRONT_ON, sharp_chip(), containment=0.4
    )

    assert verdict.accepted is False
    assert verdict.reason == "the face runs off the side of the picture"


def test_a_square_that_only_grazes_the_edge_is_still_accepted() -> None:
    """A face standing near the side of the shot is a real face, and refusing it would lose an
    appearance rather than a smear. A square at this level is clean: a streaked one sits far
    lower."""
    verdict = quality.assess(
        Box(x=0, y=0, width=120, height=120), FRONT_ON, sharp_chip(), containment=0.95
    )

    assert verdict.accepted is True


def test_how_much_was_invented_is_carried_on_the_verdict() -> None:
    """Recorded per face, which is what lets the floor be set by reading a library back rather than
    by choosing a number: a floor below the lowest square refuses nothing at all."""
    verdict = quality.assess(
        Box(x=0, y=0, width=120, height=120), FRONT_ON, sharp_chip(), containment=0.9
    )

    assert verdict.containment == pytest.approx(0.9)


def test_the_bar_can_be_lowered_for_a_deeper_look() -> None:
    box = Box(x=0, y=0, width=36, height=36)

    assert quality.assess(box, FRONT_ON, sharp_chip()).accepted is False
    assert quality.assess(box, FRONT_ON, sharp_chip(), min_pixels=32).accepted is True


# --- how good, rather than whether -------------------------------------------------------------------------


def test_failing_badly_on_one_measure_cannot_be_made_up_for_by_the_other_two() -> None:
    """A product rather than a sum: being enormous does not make a face turned away from the camera
    recognizable."""
    enormous_but_turned_away = quality.score(pixels=2000, sharp=1000.0, front=0.0)

    assert enormous_but_turned_away == 0.0


def test_a_bigger_sharper_more_front_on_face_scores_higher() -> None:
    modest = quality.score(pixels=60, sharp=100.0, front=0.6)
    better = quality.score(pixels=180, sharp=400.0, front=0.95)

    assert better > modest


def test_being_beyond_enormous_stops_helping() -> None:
    """Past the ceilings, more says something about the camera rather than about the face."""
    assert quality.score(pixels=200, sharp=500.0, front=1.0) == pytest.approx(1.0)
    assert quality.score(pixels=4000, sharp=9000.0, front=1.0) == pytest.approx(1.0)


def test_a_negative_measurement_does_not_produce_a_negative_score() -> None:
    assert quality.score(pixels=100, sharp=-5.0, front=0.5) == 0.0


def test_a_whole_square_beats_the_same_face_cut_off_at_the_side() -> None:
    """Why containment is in the score and not only at the floor.

    The floor is set low because there is nothing yet to set it from, so a square that is a fifth
    invented passes it. This is what makes it lose anyway: shown only where the appearance has
    nothing better, never preferred over a picture that is all there.
    """
    whole = quality.score(pixels=150, sharp=400.0, front=0.9)
    clipped = quality.score(pixels=150, sharp=400.0, front=0.9, containment=0.8)

    assert clipped < whole


def test_a_square_measured_without_being_warped_is_not_penalised() -> None:
    """A picture handed in already cropped was never resampled, so there is nothing to report and
    the default must not read as a complaint."""
    assert quality.score(pixels=150, sharp=400.0, front=0.9) == quality.score(
        pixels=150, sharp=400.0, front=0.9, containment=1.0
    )


def test_a_face_is_measured_down_its_long_axis_rather_than_across_its_narrow_one() -> None:
    """A face is taller than it is wide, and so is a detector's box around one.

    Read across the narrow side, a good sharp front-on face in a web-sized picture reads as 78
    pixels and is refused, while what the recognizer reads is the whole box warped onto a 112
    square, so along the axis carrying brow-to-chin detail it has every pixel it wants. Read on
    the narrow side, a video of faces a hundred pixels tall would be recorded as having no faces.
    """
    tall = Box(x=0, y=0, width=78, height=112)

    assert tall.long_side == 112
    assert tall.short_side == 78


def test_a_web_sized_portrait_face_clears_the_floor() -> None:
    """The shape the narrow side would throw away, through the real measurement."""
    measured = quality.assess(Box(x=0, y=0, width=78, height=112), FRONT_ON, sharp_chip())

    assert measured.pixels == 112
    assert measured.accepted, measured.reason


def test_a_face_that_is_small_on_both_axes_is_still_refused() -> None:
    """The floor still has to mean something. Reading the long side is not reaching for the bigger
    of two numbers to be generous: a face that is small every way it is measured has no detail in
    it whichever axis is asked about."""
    measured = quality.assess(Box(x=0, y=0, width=60, height=70), FRONT_ON, sharp_chip())

    assert not measured.accepted
    assert measured.reason is not None and "smallest usable" in measured.reason


# --- the two terms measured AFTER a face has been described ---------------------------------------


def test_how_firmly_the_recognizer_answered_multiplies_the_score() -> None:
    """Another factor in the same product, on the same argument as the other four: a face that
    fails badly on one of them cannot be rescued by the rest."""
    firm = quality.score(200, 500.0, 1.0, 1.0, 1.0)
    weak = quality.score(200, 500.0, 1.0, 1.0, 0.25)

    assert weak == pytest.approx(firm * 0.25)


def test_a_face_measured_without_a_description_scores_as_it_always_did() -> None:
    """The reference audit measures a picture BEFORE deciding whether to describe it, so the term
    has to default to no effect, or every reference would be scored on a measurement that was
    never taken."""
    assert quality.score(200, 500.0, 1.0, 1.0) == quality.score(200, 500.0, 1.0, 1.0, 1.0)


def test_assess_carries_the_raw_length_through_rather_than_only_the_term() -> None:
    """The raw number is the evidence. Only the term reaches the score, and a term cannot be
    turned back into the measurement behind it, which is the whole reason the floor for the
    second model family cannot be set today."""
    verdict = quality.assess(
        Box(x=0, y=0, width=120, height=120),
        FRONT_ON,
        sharp_chip(),
        strength=18.4,
        recognisability=0.6,
    )

    assert verdict.strength == pytest.approx(18.4)


def _a_verdict(score: float = 0.8, accepted: bool = True, reason: str | None = None) -> Quality:
    return Quality(
        pixels=200, sharpness=500.0, frontality=1.0, score=score, accepted=accepted, reason=reason
    )


def test_agreeing_with_the_rest_of_an_appearance_leaves_a_face_exactly_where_it_was() -> None:
    assert quality.with_agreement(_a_verdict(), 1.0).score == pytest.approx(0.8)


def test_agreeing_with_nothing_costs_a_face_the_whole_weight_and_no_more() -> None:
    """The bound IS the design. A term that could move a score further than this would stop being
    a tie-break, and it is not good enough to be anything else: most of the faces that agree least
    with their own appearance are perfectly good pictures."""
    worst = quality.with_agreement(_a_verdict(), 0.0)

    assert worst.score == pytest.approx(0.8 * (1.0 - quality.AGREEMENT_WEIGHT))
    assert quality.AGREEMENT_WEIGHT <= 0.2


def test_a_description_pointing_away_from_its_own_appearance_is_clamped_not_inverted() -> None:
    """Similarity runs to -1. Left unclamped it would turn the factor negative and the score with
    it, which would sort a badly wrong face ABOVE a good one under any ordering that reads the
    smallest first."""
    assert (
        quality.with_agreement(_a_verdict(), -1.0).score
        == quality.with_agreement(_a_verdict(), 0.0).score
    )


def test_disagreeing_with_an_appearance_cannot_refuse_a_face() -> None:
    """The safety property, stated as a test. This term ranks; it never judges, so a face that
    matches nothing around it is still described, still stored and still shown, merely last."""
    refused = quality.with_agreement(_a_verdict(accepted=True), -1.0)

    assert refused.accepted is True
    assert refused.reason is None


def test_a_face_already_refused_stays_refused_with_its_reason_intact() -> None:
    kept = quality.with_agreement(_a_verdict(accepted=False, reason="too blurred"), 1.0)

    assert kept.accepted is False
    assert kept.reason == "too blurred"


def test_every_refusal_is_coded_by_the_check_that_wrote_its_sentence() -> None:
    """The code a refusal is logged and counted under and the sentence beside it come from one
    check, so a face turned from the camera is not coded `too_blurred` beside "turned too far
    away"."""
    small = quality.assess(Box(x=0, y=0, width=20, height=20), FRONT_ON, sharp_chip())
    blurred = quality.assess(Box(x=0, y=0, width=120, height=120), FRONT_ON, flat_chip())
    away = quality.assess(Box(x=0, y=0, width=120, height=120), IN_PROFILE, sharp_chip())

    assert (small.failed, blurred.failed) == (Finding.TOO_SMALL, Finding.TOO_BLURRED)
    assert away.failed is Finding.TURNED_AWAY
    assert away.reason == "the face is turned too far away from the camera"
    assert (
        quality.assess(Box(x=0, y=0, width=120, height=120), FRONT_ON, sharp_chip()).failed is None
    )


@pytest.mark.parametrize(
    ("failed", "frontality", "containment", "kept"),
    [
        (Finding.TURNED_AWAY, 0.2, 1.0, True),
        # Read as fully side-on: a profile and a closer look gone wrong read the same.
        (Finding.TURNED_AWAY, 0.0, 1.0, False),
        # Turned and cut off by the edge: refused for the edge as well.
        (Finding.TURNED_AWAY, 0.2, 0.5, False),
        (Finding.TOO_BLURRED, 0.9, 1.0, False),
        (None, 0.9, 1.0, False),
    ],
)
def test_only_a_face_refused_for_its_angle_alone_is_kept_to_be_asked_about(
    failed: Finding | None, frontality: float, containment: float, kept: bool
) -> None:
    measured = Quality(
        pixels=300,
        sharpness=800.0,
        frontality=frontality,
        score=0.1,
        accepted=failed is None,
        containment=containment,
        failed=failed,
    )

    assert quality.asked_only(measured) is kept
