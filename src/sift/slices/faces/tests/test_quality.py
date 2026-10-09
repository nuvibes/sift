# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measuring whether a face is worth recognizing, with the reason a refusal carries."""

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
    """`FRONT_ON` with the head yawed as a camera sees it: the nose swings out by the sine, the
    eyes close up by the cosine, and the eye-to-mouth drop does not move."""
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
    """The commonest pose does not score zero: against the eye span the turn counts twice."""
    assert quality.frontality(turned(40.0)) > quality.MIN_FRONTALITY
    assert quality.frontality(turned(50.0)) > 0.0
    assert quality.frontality(turned(65.0)) > 0.0


def test_the_angle_measure_keeps_ranking_past_the_point_the_old_one_flattened() -> None:
    """A measure that saturates cannot rank the frames it exists to rank."""
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
    # The mouth on the eyes leaves no drop to divide by: refused, not waved through.
    assert quality.frontality(((0.0, 0.0),) * 5) == 0.0
    flat = ((30.0, 40.0), (70.0, 40.0), (50.0, 40.0), (35.0, 40.0), (65.0, 40.0))
    assert quality.frontality(flat) == 0.0


def test_a_face_the_detector_gave_only_three_points_for_is_refused_rather_than_guessed_at() -> None:
    """A caller that cannot supply the mouth gets the safe answer, never a guessed 1.0."""
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
    """The fourth floor: a streaked square passes the other three."""
    verdict = quality.assess(
        Box(x=0, y=0, width=120, height=120), FRONT_ON, sharp_chip(), containment=0.4
    )

    assert verdict.accepted is False
    assert verdict.reason == "the face runs off the side of the picture"


def test_a_square_that_only_grazes_the_edge_is_still_accepted() -> None:
    """A face near the side of the shot is real; a streaked square sits far lower."""
    verdict = quality.assess(
        Box(x=0, y=0, width=120, height=120), FRONT_ON, sharp_chip(), containment=0.95
    )

    assert verdict.accepted is True


def test_how_much_was_invented_is_carried_on_the_verdict() -> None:
    """Recorded per face, so the floor can be set from a library read back."""
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
    """A partly invented square passes the low floor but loses to a whole one in the score."""
    whole = quality.score(pixels=150, sharp=400.0, front=0.9)
    clipped = quality.score(pixels=150, sharp=400.0, front=0.9, containment=0.8)

    assert clipped < whole


def test_a_square_measured_without_being_warped_is_not_penalised() -> None:
    """A picture handed in already cropped was never resampled, so it is not penalised."""
    assert quality.score(pixels=150, sharp=400.0, front=0.9) == quality.score(
        pixels=150, sharp=400.0, front=0.9, containment=1.0
    )


def test_a_face_is_measured_down_its_long_axis_rather_than_across_its_narrow_one() -> None:
    """A face's box is taller than wide, and the recognizer reads the whole box."""
    tall = Box(x=0, y=0, width=78, height=112)

    assert tall.long_side == 112
    assert tall.short_side == 78


def test_a_web_sized_portrait_face_clears_the_floor() -> None:
    """The shape the narrow side would throw away, through the real measurement."""
    measured = quality.assess(Box(x=0, y=0, width=78, height=112), FRONT_ON, sharp_chip())

    assert measured.pixels == 112
    assert measured.accepted, measured.reason


def test_a_face_that_is_small_on_both_axes_is_still_refused() -> None:
    """A face small every way it is measured has no detail on any axis."""
    measured = quality.assess(Box(x=0, y=0, width=60, height=70), FRONT_ON, sharp_chip())

    assert not measured.accepted
    assert measured.reason is not None and "smallest usable" in measured.reason


# --- the two terms measured AFTER a face has been described ---------------------------------------


def test_how_firmly_the_recognizer_answered_multiplies_the_score() -> None:
    firm = quality.score(200, 500.0, 1.0, 1.0, 1.0)
    weak = quality.score(200, 500.0, 1.0, 1.0, 0.25)

    assert weak == pytest.approx(firm * 0.25)


def test_a_face_measured_without_a_description_scores_as_it_always_did() -> None:
    """The audit measures before describing, so the term defaults to no effect."""
    assert quality.score(200, 500.0, 1.0, 1.0) == quality.score(200, 500.0, 1.0, 1.0, 1.0)


def test_assess_carries_the_raw_length_through_rather_than_only_the_term() -> None:
    """The raw number is the evidence; a term cannot be turned back into it."""
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
    """The bound is the design: a tie-break, as most faces that disagree are good pictures."""
    worst = quality.with_agreement(_a_verdict(), 0.0)

    assert worst.score == pytest.approx(0.8 * (1.0 - quality.AGREEMENT_WEIGHT))
    assert quality.AGREEMENT_WEIGHT <= 0.2


def test_a_description_pointing_away_from_its_own_appearance_is_clamped_not_inverted() -> None:
    """Unclamped, a similarity of -1 would make the score negative and sort it first."""
    assert (
        quality.with_agreement(_a_verdict(), -1.0).score
        == quality.with_agreement(_a_verdict(), 0.0).score
    )


def test_disagreeing_with_an_appearance_cannot_refuse_a_face() -> None:
    """This term ranks and never judges."""
    refused = quality.with_agreement(_a_verdict(accepted=True), -1.0)

    assert refused.accepted is True
    assert refused.reason is None


def test_a_face_already_refused_stays_refused_with_its_reason_intact() -> None:
    kept = quality.with_agreement(_a_verdict(accepted=False, reason="too blurred"), 1.0)

    assert kept.accepted is False
    assert kept.reason == "too blurred"


def test_every_refusal_is_coded_by_the_check_that_wrote_its_sentence() -> None:
    """Code and sentence come from one check."""
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
