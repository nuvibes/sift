# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether a face is worth recognizing, decided before anything expensive happens to it.

A poor face yields wrong numbers, not weak ones, that pull strangers together. Size, sharpness,
angle and containment are measured; size and containment are fixed floors. How firmly the
recognizer answered and agreement with the appearance's other frames only rank.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from sift.slices.faces import tuning
from sift.slices.faces.models import Box, Finding, Quality

#: The middle preset's blur and angle, as defaults for a call without a bar.
MIN_SHARPNESS = 40.0
MIN_FRONTALITY = 0.30

#: The two fixed floors, from `tuning`.
MIN_PIXELS = tuning.MIN_PIXELS
MIN_CONTAINMENT = tuning.MIN_CONTAINMENT

_SHARPNESS_CEILING = 500.0

_PIXELS_CEILING = 200.0

#: The nose's offset, as a share of the eye-to-mouth drop, at which a head is fully side-on.
_PROFILE_OFFSET = 0.50

#: The most the agreement term may move the score: a tie-break, never more.
AGREEMENT_WEIGHT = 0.15

_GREY = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def sharpness(chip: np.ndarray) -> float:
    """How much fine detail an aligned face has: the variance of its Laplacian."""
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
    """How square-on a face is, from 1.0 to 0.0 in profile: the nose's offset along the eye line
    against the eye-to-mouth drop, which a turn does not foreshorten."""
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
    """Measure a face and say whether it clears the bar, with the reason for a refusal."""
    pixels = box.long_side
    sharp = sharpness(chip)
    front = frontality(landmarks)

    # Both come from the caller: only the recognizer knows the model's scale.

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
    """Whether a refused face failed on its angle alone: kept and matched, never grouped or
    filed as a reference; a fully side-on face is still refused."""
    return (
        measured.failed is Finding.TURNED_AWAY
        and measured.frontality > 0.0
        and measured.containment >= min_containment
    )


def with_agreement(measured: Quality, agreement: float) -> Quality:
    """The same verdict with agreement with its appearance folded into the score; never refuses."""
    return replace(
        measured,
        agreement=agreement,
        score=measured.score * _agreed(agreement),
    )


def _agreed(agreement: float) -> float:
    """The agreement term, from `1 - AGREEMENT_WEIGHT` to 1.0, clamped."""
    return 1.0 - AGREEMENT_WEIGHT + AGREEMENT_WEIGHT * min(1.0, max(0.0, agreement))


def score(
    pixels: int,
    sharp: float,
    front: float,
    containment: float = 1.0,
    recognisability: float = 1.0,
) -> float:
    """How good a face is, from 0 to 1: a product, so one bad measure cannot be rescued."""
    size = min(1.0, pixels / _PIXELS_CEILING)
    detail = min(1.0, max(0.0, sharp) / _SHARPNESS_CEILING)
    whole = min(1.0, max(0.0, containment))
    firm = min(1.0, max(0.0, recognisability))
    return float(size * detail**0.5 * front * whole * firm)
