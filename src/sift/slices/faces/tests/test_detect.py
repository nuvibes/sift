# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading what a detector says, with the model's answers written out by hand.

Both supported families are decoded from tensors a test builds, because that is the only way to
assert the arithmetic. A stand-in model would prove the code runs; written-out answers prove it
reads them the way the publisher's own reader does, which is the thing that goes silently wrong
when a model is swapped and boxes land in the wrong place.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from sift.slices.faces import detect, tuning
from sift.slices.faces.detect import (
    AnchorDetector,
    PyramidDetector,
    add_border,
    letterbox,
    suppress,
)
from sift.slices.faces.models import Box, Detection
from sift.slices.faces.runner import Loaded
from sift.slices.faces.weights import CATALOG

pytestmark = pytest.mark.unit

SIZE = tuning.DETECTOR_INPUT
STRIDES = (8, 16, 32)


class Canned:
    """A runner that hands back tensors a test wrote out, and remembers what it was given."""

    def __init__(self, outputs: list[np.ndarray]) -> None:
        self.outputs = outputs
        self.blobs: list[np.ndarray] = []

    def run(self, loaded: Loaded, blob: np.ndarray) -> list[np.ndarray]:
        self.blobs.append(blob)
        return self.outputs


def loaded_for(family: str) -> Loaded:
    weight = CATALOG[f"{family}.detector"]
    names = (
        tuple(f"out{index}" for index in range(9))
        if family == "accurate"
        else tuple(
            f"{kind}_{stride}" for kind in ("cls", "obj", "bbox", "kps") for stride in STRIDES
        )
    )
    return Loaded(weight=weight, session=None, inputs=("input",), outputs=names, device="cpu")


def anchor_outputs(*, position: int, stride_index: int, score: float) -> list[np.ndarray]:
    """One face reported at one position, on one of the three scales."""
    counts = [(SIZE // stride) ** 2 * 2 for stride in STRIDES]
    scores = [np.zeros((count, 1), dtype=np.float32) for count in counts]
    edges = [np.zeros((count, 4), dtype=np.float32) for count in counts]
    marks = [np.zeros((count, 10), dtype=np.float32) for count in counts]

    scores[stride_index][position, 0] = score
    # Distances from the point to each edge, in units of the stride.
    edges[stride_index][position] = [4.0, 4.0, 4.0, 4.0]
    marks[stride_index][position] = [-2, -2, 2, -2, 0, 0, -1, 2, 1, 2]
    return [*scores, *edges, *marks]


def pyramid_outputs(*, position: int, stride_index: int, score: float) -> dict[str, np.ndarray]:
    counts = [(SIZE // stride) ** 2 for stride in STRIDES]
    out: dict[str, np.ndarray] = {}
    for index, stride in enumerate(STRIDES):
        out[f"cls_{stride}"] = np.zeros((1, counts[index], 1), dtype=np.float32)
        out[f"obj_{stride}"] = np.zeros((1, counts[index], 1), dtype=np.float32)
        out[f"bbox_{stride}"] = np.zeros((1, counts[index], 4), dtype=np.float32)
        out[f"kps_{stride}"] = np.zeros((1, counts[index], 10), dtype=np.float32)
    out[f"cls_{STRIDES[stride_index]}"][0, position, 0] = score
    out[f"obj_{STRIDES[stride_index]}"][0, position, 0] = score
    out[f"bbox_{STRIDES[stride_index]}"][0, position] = [0.0, 0.0, np.log(4.0), np.log(4.0)]
    out[f"kps_{STRIDES[stride_index]}"][0, position] = [-2, -2, 2, -2, 0, 0, -1, 2, 1, 2]
    return out


# --- fitting a frame to the model --------------------------------------------------------------------


def test_a_frame_is_fitted_into_the_square_without_being_squashed() -> None:
    """Squashing would distort every face by the frame's shape, which for a phone video shot
    upright is a face half as wide as it should be."""
    frame = np.full((240, 480, 3), 100, dtype=np.uint8)

    canvas, scale = letterbox(frame, SIZE)

    assert canvas.shape == (SIZE, SIZE, 3)
    assert scale == pytest.approx(SIZE / 480)
    # The picture occupies the top-left; the rest is padding.
    assert canvas[0, 0].tolist() == [100, 100, 100]
    assert canvas[SIZE - 1, SIZE - 1].tolist() == [0, 0, 0]


def test_a_border_moves_the_picture_and_says_how_far() -> None:
    frame = np.full((100, 200, 3), 90, dtype=np.uint8)

    bordered, pad_x, pad_y = add_border(frame, 0.25)

    assert bordered.shape == (150, 300, 3)
    assert (pad_x, pad_y) == (50, 25)
    assert bordered[pad_y, pad_x].tolist() == [90, 90, 90]
    assert bordered[0, 0].tolist() == [0, 0, 0]


def test_the_same_face_found_from_several_positions_is_reported_once() -> None:
    boxes = np.array(
        [[10.0, 10.0, 50.0, 50.0], [12.0, 12.0, 52.0, 52.0], [200.0, 200.0, 240.0, 240.0]]
    )
    scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)

    assert suppress(boxes, scores, 0.4) == [0, 2]


def test_one_box_needs_no_suppressing() -> None:
    boxes = np.array([[10.0, 10.0, 50.0, 50.0]])

    assert suppress(boxes, np.array([0.9], dtype=np.float32), 0.4) == [0]


# --- reading each family's answer -----------------------------------------------------------------------


def test_the_accurate_family_is_read_as_distances_from_a_point() -> None:
    runner = Canned(anchor_outputs(position=2, stride_index=0, score=0.9))
    detector = AnchorDetector(runner, loaded_for("accurate"))  # type: ignore[arg-type]

    found = detector.detect(np.zeros((SIZE, SIZE, 3), dtype=np.uint8), retry=False)

    assert len(found) == 1
    # Position 2 of the first scale is the second grid point (two anchors per point), stride 8.
    assert found[0].box == Box(x=8 - 32, y=0 - 32, width=64, height=64)
    assert found[0].score == pytest.approx(0.9)
    assert len(found[0].landmarks) == 5


def test_the_permissive_family_is_read_as_a_centre_and_a_size() -> None:
    named = pyramid_outputs(position=2, stride_index=0, score=0.81)
    loaded = loaded_for("permissive")
    runner = Canned([named[name] for name in loaded.outputs])
    detector = PyramidDetector(runner, loaded)  # type: ignore[arg-type]

    found = detector.detect(np.zeros((SIZE, SIZE, 3), dtype=np.uint8), retry=False)

    assert len(found) == 1
    assert found[0].box.width == 32
    assert found[0].box.height == 32
    assert found[0].score == pytest.approx(0.81, abs=1e-3)


def test_a_frame_with_nothing_in_it_yields_nothing() -> None:
    runner = Canned(anchor_outputs(position=0, stride_index=0, score=0.0))
    detector = AnchorDetector(runner, loaded_for("accurate"))  # type: ignore[arg-type]

    assert detector.detect(np.zeros((SIZE, SIZE, 3), dtype=np.uint8), retry=False) == []


def test_a_face_is_looked_for_at_every_scale() -> None:
    """Three scales, so a face is found whether it fills the frame or is one of a crowd."""
    for stride_index in range(3):
        runner = Canned(anchor_outputs(position=1, stride_index=stride_index, score=0.9))
        detector = AnchorDetector(runner, loaded_for("accurate"))  # type: ignore[arg-type]
        found = detector.detect(np.zeros((SIZE, SIZE, 3), dtype=np.uint8), retry=False)
        assert len(found) == 1


# --- the second look ---------------------------------------------------------------------------------


def test_a_frame_that_found_nothing_is_looked_at_again_with_a_border() -> None:
    """For the face that fills the whole frame: invisible to these detectors, and immediately
    found once it is an ordinary size on a bigger canvas."""

    class OnlyWithABorder(Canned):
        def __init__(self) -> None:
            super().__init__([])
            self.calls = 0

        def run(self, loaded: Loaded, blob: np.ndarray) -> list[np.ndarray]:
            self.calls += 1
            empty = anchor_outputs(position=0, stride_index=0, score=0.0)
            return (
                empty if self.calls == 1 else anchor_outputs(position=4, stride_index=1, score=0.9)
            )

    runner = OnlyWithABorder()
    detector = AnchorDetector(runner, loaded_for("accurate"))  # type: ignore[arg-type]

    found = detector.detect(np.zeros((200, 200, 3), dtype=np.uint8))

    assert runner.calls == 2
    assert len(found) == 1


def test_the_second_look_is_skipped_when_the_first_one_found_something() -> None:
    runner = Canned(anchor_outputs(position=2, stride_index=0, score=0.9))
    detector = AnchorDetector(runner, loaded_for("accurate"))  # type: ignore[arg-type]

    detector.detect(np.zeros((200, 200, 3), dtype=np.uint8))

    assert len(runner.blobs) == 1


def test_looking_closely_at_a_face_reports_its_features_in_the_frames_own_pixels() -> None:
    runner = Canned(anchor_outputs(position=40, stride_index=1, score=0.9))
    detector = AnchorDetector(runner, loaded_for("accurate"))  # type: ignore[arg-type]
    detection = Detection(
        box=Box(x=100, y=100, width=80, height=80),
        score=0.77,
        landmarks=((0.0, 0.0),) * 5,
        timestamp_ms=1234,
    )

    sharper = detector.refine(np.zeros((400, 400, 3), dtype=np.uint8), detection)

    assert sharper.timestamp_ms == 1234
    # The original confidence is kept: the second look is about where the features are, not about
    # whether this is a face.
    assert sharper.score == pytest.approx(0.77)


def test_a_face_too_small_to_look_at_closely_is_left_as_it_was() -> None:
    runner = Canned(anchor_outputs(position=0, stride_index=0, score=0.0))
    detector = AnchorDetector(runner, loaded_for("accurate"))  # type: ignore[arg-type]
    detection = Detection(
        box=Box(x=0, y=0, width=4, height=4), score=0.5, landmarks=((1.0, 1.0),) * 5, timestamp_ms=0
    )

    assert detector.refine(np.zeros((400, 400, 3), dtype=np.uint8), detection) is detection


def test_a_closer_look_that_finds_nothing_leaves_the_face_as_it_was() -> None:
    runner = Canned(anchor_outputs(position=0, stride_index=0, score=0.0))
    detector = AnchorDetector(runner, loaded_for("accurate"))  # type: ignore[arg-type]
    detection = Detection(
        box=Box(x=100, y=100, width=80, height=80),
        score=0.5,
        landmarks=((1.0, 1.0),) * 5,
        timestamp_ms=0,
    )

    assert detector.refine(np.zeros((400, 400, 3), dtype=np.uint8), detection) is detection


# --- choosing a reader --------------------------------------------------------------------------------


def test_each_family_gets_its_own_reader() -> None:
    runner = Canned([])

    assert isinstance(detect.build(runner, loaded_for("accurate")), AnchorDetector)  # type: ignore[arg-type]
    assert isinstance(detect.build(runner, loaded_for("permissive")), PyramidDetector)  # type: ignore[arg-type]


def test_a_family_with_no_reader_is_refused_rather_than_guessed_at() -> None:
    unknown = Loaded(
        weight=replace(CATALOG["accurate.detector"], family="something-else"),
        session=None,
        inputs=("input",),
        outputs=(),
        device="cpu",
    )

    with pytest.raises(ValueError, match="no detector reader"):
        detect.build(Canned([]), unknown)  # type: ignore[arg-type]


def test_the_two_families_are_handed_their_pictures_the_way_each_was_trained() -> None:
    """Handing a model the other family's arrangement does not fail: it quietly produces numbers
    that match nothing."""
    frame = np.zeros((SIZE, SIZE, 3), dtype=np.uint8)
    frame[:, :, 0] = 255

    anchor_runner = Canned(anchor_outputs(position=0, stride_index=0, score=0.0))
    AnchorDetector(anchor_runner, loaded_for("accurate")).detect(frame, retry=False)  # type: ignore[arg-type]

    loaded = loaded_for("permissive")
    named = pyramid_outputs(position=0, stride_index=0, score=0.0)
    pyramid_runner = Canned([named[name] for name in loaded.outputs])
    PyramidDetector(pyramid_runner, loaded).detect(frame, retry=False)  # type: ignore[arg-type]

    # One is centred around zero; the other is raw, and in the reverse channel order.
    assert anchor_runner.blobs[0].min() < 0
    assert pyramid_runner.blobs[0].min() == 0
    assert pyramid_runner.blobs[0][0, 2, 0, 0] == 255
