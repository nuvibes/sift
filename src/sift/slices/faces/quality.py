# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether a face is worth recognizing, decided before anything expensive happens to it.

A small, blurred or turned face does not produce weak numbers but WRONG ones, landing near every
other bad face, so a few of them join people who look nothing alike; refusing them first is both
cheaper and more accurate, and accuracy is the reason. Four measurements, each a different
failure: how much face there is (below the recognizer's input size there is no detail), how sharp,
how square-on (a profile is not the face the gallery holds), and how much of the square is really
the picture (a face at the frame's edge is filled with a repeated edge pixel). The fourth arrives
from the alignment, the only one not recoverable from the square afterwards. Size and containment
are floors at every setting; only blur and angle are offered as a choice, being a real trade.

Two later terms only rank and can never refuse: how firmly the recognizer answered (known only
after the description is paid for; it tells a non-face such as the back of a head from a face),
and how well a face agrees with its own appearance's other frames, a bounded tie-break because
most faces that disagree are innocent variation. Nothing here measures occlusion.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from sift.slices.faces import tuning
from sift.slices.faces.models import Box, Finding, Quality

#: The middle preset's blur and angle, as defaults so a call without a bar measures against
#: something sensible; callers that read the settings pass them.
MIN_SHARPNESS = 40.0
MIN_FRONTALITY = 0.30

#: The two floors, from `tuning`, not presets: below the first the picture is stretched past what
#: was photographed, below the second it is mostly not of the face.
MIN_PIXELS = tuning.MIN_PIXELS
MIN_CONTAINMENT = tuning.MIN_CONTAINMENT

#: Sharpness past which a face is simply sharp; more says something about the camera.
_SHARPNESS_CEILING = 500.0

#: The size past which bigger is no better: well above the floor, so the score still separates a
#: small face from a comfortable one.
_PIXELS_CEILING = 200.0

#: How far the nose reaches past the eyes' midpoint, as a share of the eye-to-mouth drop, when a
#: head is fully side-on: where `frontality` reaches zero. Set just above where real faces reach
#: (the rare face beyond clamps to zero). Too low flattens every modest turn to zero; too high lets
#: a profile score well enough to be described.
_PROFILE_OFFSET = 0.50

#: The most the agreement term may move the score: sized so it can only break a tie between frames
#: already nearly equal, and reorders nothing in an appearance under three frames. Raising it would
#: make a weak signal with a bad tail into a strong one.
AGREEMENT_WEIGHT = 0.15

#: Grey weights: the eye is most sensitive to green, so this is brightness as a person sees it.
_GREY = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def sharpness(chip: np.ndarray) -> float:
    """How much fine detail an aligned face has, as the variance of its second derivative.

    A Laplacian responds to edges, eyelashes, the line of a lip; blur collapses its variance.
    Read off the aligned square so the answer does not depend on the face's size.
    """
    grey = chip.astype(np.float32) @ _GREY
    if grey.shape[0] < 3 or grey.shape[1] < 3:
        return 0.0
    laplacian = (
        -4.0 * grey[1:-1, 1:-1]
        + grey[:-2, 1:-1]
        + grey[2:, 1:-1]
        + grey[1:-1, :-2]
        + grey[1:-1, 2:]
    )
    return float(laplacian.var())


def frontality(landmarks: tuple[tuple[float, float], ...]) -> float:
    """How square-on a face is: 1.0 looking straight ahead, 0.0 in full profile.

    Read from where the nose sits between the eyes, measured along the eye line so a tilt does not
    count as a turn: a proxy that answers whether enough face shows, without a 3D face model to
    fetch. The yardstick is the eye-to-mouth drop, which a turn does not foreshorten. Half the eye
    span is the obvious choice and is wrong: it shrinks with the very turn measured, giving
    `1 - tan(yaw)`, which hits zero at 45 degrees and makes every three-quarter view read as a
    profile. Against the drop the offset grows as plain `sin(yaw)`.
    """
    if len(landmarks) < 5:
        return 0.0
    left_eye = np.array(landmarks[0], dtype=np.float64)
    right_eye = np.array(landmarks[1], dtype=np.float64)
    nose = np.array(landmarks[2], dtype=np.float64)
    left_mouth = np.array(landmarks[3], dtype=np.float64)
    right_mouth = np.array(landmarks[4], dtype=np.float64)
    mouth = (left_mouth + right_mouth) / 2

    between = right_eye - left_eye
    span = float(np.hypot(*between))
    midpoint = (left_eye + right_eye) / 2
    drop = float(np.hypot(*(mouth - midpoint)))
    if span <= 0 or drop <= 0:
        return 0.0
    along = abs(float(np.dot(nose - midpoint, between / span)))
    return float(max(0.0, 1.0 - (along / drop) / _PROFILE_OFFSET))


def assess(
    box: Box,
    landmarks: tuple[tuple[float, float], ...],
    chip: np.ndarray,
    *,
    containment: float = 1.0,
    strength: float = 0.0,
    recognisability: float = 1.0,
    min_pixels: int = MIN_PIXELS,
    min_sharpness: float = MIN_SHARPNESS,
    min_frontality: float = MIN_FRONTALITY,
    min_containment: float = MIN_CONTAINMENT,
) -> Quality:
    """Measure a face, and say whether it clears the bar.

    The refusal's reason is carried, because a reference image somebody chose and Sift refused must
    say why. `containment` defaults to a whole square for a picture never warped (handed in already
    cropped); everything that aligns passes the real number.
    """
    pixels = box.long_side
    sharp = sharpness(chip)
    front = frontality(landmarks)

    # `strength` (the raw length, for the record) and `recognisability` (its 0-to-1 term) are the
    # same measurement; only the recognizer knows the model's scale, so both come from the caller.

    reason = None
    failed: Finding | None = None
    if pixels < min_pixels:
        reason = (
            f"the face is {pixels} pixels at its largest, and {min_pixels} is the smallest usable"
        )
        failed = Finding.TOO_SMALL
    elif sharp < min_sharpness:
        reason = "the face is too blurred to recognize"
        failed = Finding.TOO_BLURRED
    elif front < min_frontality:
        reason = "the face is turned too far away from the camera"
        failed = Finding.TURNED_AWAY
    elif containment < min_containment:
        reason = "the face runs off the side of the picture"
        failed = Finding.RUNS_OFF_EDGE

    return Quality(
        pixels=pixels,
        sharpness=sharp,
        frontality=front,
        containment=containment,
        strength=strength,
        score=score(pixels, sharp, front, containment, recognisability),
        accepted=reason is None,
        reason=reason,
        failed=failed,
    )


def asked_only(measured: Quality, *, min_containment: float = MIN_CONTAINMENT) -> bool:
    """Whether a refused face failed on its angle and on nothing else: a face kept and matched.

    Large, sharp and whole, refused for its angle alone: a person can recognize it, and so can the
    recognizer against a gallery, so it is kept, described and matched at the bar any face meets
    (`service_matching._attach_bar`). It joins no group and never becomes a reference, since such
    squares often hold a second face that a blended description would then partly be of. A face
    read as fully side-on is still refused: a real profile and landmarks the closer look got wrong
    look alike there.
    """
    return (
        measured.failed is Finding.TURNED_AWAY
        and measured.frontality > 0.0
        and measured.containment >= min_containment
    )


def with_agreement(measured: Quality, agreement: float) -> Quality:
    """The same verdict, with how well this face matched the rest of its appearance folded in.

    Applied here, not in `assess`: agreement is a fact about the SET of an appearance's frames,
    which do not exist when one face is measured. `accepted` and `reason` pass through unchanged, so
    this can never refuse a face: it only moves it among its own appearance's frames.
    """
    return replace(
        measured,
        agreement=agreement,
        score=measured.score * _agreed(agreement),
    )


def _agreed(agreement: float) -> float:
    """The agreement term: 1.0 for a face matching its appearance, `1 - AGREEMENT_WEIGHT` for one
    matching nothing in it. Negative agreement is clamped rather than inverting the score."""
    return 1.0 - AGREEMENT_WEIGHT + AGREEMENT_WEIGHT * min(1.0, max(0.0, agreement))


def score(
    pixels: int,
    sharp: float,
    front: float,
    containment: float = 1.0,
    recognisability: float = 1.0,
) -> float:
    """How good a face is, from 0 to 1, used to choose which frames of an appearance to keep.

    A product, so failing badly on one measure cannot be rescued by the others. Containment enters
    above its floor too, so a partly invented square loses to anything better without refusing a
    real appearance. `recognisability` defaults to no effect for a picture not yet described. No
    occlusion measure: a face in sunglasses is large, sharp and square-on, so a covered frame often
    scores highest, which no rearrangement of these fixes (see `recognize.Recognizer`).
    """
    size = min(1.0, pixels / _PIXELS_CEILING)
    detail = min(1.0, max(0.0, sharp) / _SHARPNESS_CEILING)
    whole = min(1.0, max(0.0, containment))
    firm = min(1.0, max(0.0, recognisability))
    return float(size * detail**0.5 * front * whole * firm)
