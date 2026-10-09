# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning a face in a frame into the aligned square and storing it; alignment fails quietly, so
its properties are asserted: landmarks land on the template, rotation comes out, no mirroring."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sift.kernel.config import Settings
from sift.slices.faces import crop as cropping
from sift.slices.faces import tuning
from sift.slices.faces.crop import CHIP_SIZE, LANDMARK_TEMPLATE, AlignmentError
from sift.slices.faces.models import Box
from sift.slices.faces.tests.conftest import draw_face, noisy_frame

pytestmark = pytest.mark.unit

TEMPLATE = np.array(LANDMARK_TEMPLATE, dtype=np.float64)


# --- the transform ---------------------------------------------------------------------------------


def test_the_transform_carries_the_landmarks_onto_the_template() -> None:
    """The property alignment exists for, stated directly."""
    source = TEMPLATE * 2.0 + np.array([40.0, 25.0])

    matrix = cropping.similarity_transform(source, TEMPLATE)
    moved = (np.hstack([source, np.ones((5, 1))]) @ matrix.T)[:, :2]

    assert np.allclose(moved, TEMPLATE, atol=1e-6)


def test_a_rotated_face_comes_out_upright() -> None:
    angle = np.radians(30.0)
    turn = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    source = TEMPLATE @ turn.T

    matrix = cropping.similarity_transform(source, TEMPLATE)
    moved = (np.hstack([source, np.ones((5, 1))]) @ matrix.T)[:, :2]

    assert np.allclose(moved, TEMPLATE, atol=1e-6)


def test_a_mirrored_arrangement_is_not_fitted_by_flipping_the_face() -> None:
    """A reflection fits the points as well as a rotation and produces a mirrored face, which a
    recognizer reads as a different person."""
    mirrored = TEMPLATE.copy()
    mirrored[:, 0] = 112.0 - mirrored[:, 0]

    matrix = cropping.similarity_transform(mirrored, TEMPLATE)

    assert np.linalg.det(matrix[:2, :2]) > 0


def test_landmarks_all_in_one_place_cannot_describe_a_face() -> None:
    with pytest.raises(AlignmentError, match="same place"):
        cropping.similarity_transform(np.zeros((5, 2)), TEMPLATE)


def test_alignment_needs_exactly_five_landmarks() -> None:
    with pytest.raises(AlignmentError, match="five"):
        cropping.similarity_transform(np.zeros((3, 2)), TEMPLATE)


# --- resampling --------------------------------------------------------------------------------------


def test_an_aligned_face_is_the_square_the_model_expects() -> None:
    frame = noisy_frame(400, 300, seed=1)
    box = draw_face(frame, x=60, y=40, size=200)
    landmarks = (
        (box.x + 60.0, box.y + 76.0),
        (box.x + 140.0, box.y + 76.0),
        (box.x + 100.0, box.y + 110.0),
        (box.x + 70.0, box.y + 150.0),
        (box.x + 130.0, box.y + 150.0),
    )

    aligned = cropping.align(frame, landmarks)

    assert aligned.chip.shape == (CHIP_SIZE, CHIP_SIZE, 3)
    assert aligned.chip.dtype == np.uint8


def test_a_face_at_the_very_edge_gets_the_edge_repeated_rather_than_a_black_band() -> None:
    """A hard black edge sits exactly where a model looks for a jawline."""
    frame = np.full((80, 80, 3), 190, dtype=np.uint8)
    landmarks = ((2.0, 4.0), (30.0, 4.0), (16.0, 16.0), (6.0, 28.0), (26.0, 28.0))

    aligned = cropping.align(frame, landmarks)

    assert aligned.chip.min() > 0


def test_resampling_can_be_asked_for_a_different_size() -> None:
    frame = noisy_frame(200, 200, seed=2)
    landmarks = ((40.0, 60.0), (120.0, 60.0), (80.0, 95.0), (50.0, 130.0), (110.0, 130.0))

    assert cropping.align(frame, landmarks, size=64).chip.shape == (64, 64, 3)


# --- how much of the square is really the picture ------------------------------------------------


def test_a_face_in_the_middle_of_the_frame_is_wholly_real() -> None:
    frame = noisy_frame(400, 300, seed=3)
    landmarks = (
        (160.0, 120.0),
        (240.0, 120.0),
        (200.0, 155.0),
        (170.0, 190.0),
        (230.0, 190.0),
    )

    assert cropping.align(frame, landmarks).containment == 1.0


def test_a_face_running_off_the_side_reports_how_much_of_it_was_invented() -> None:
    """The number a smeared crop turns on: eyes at the left edge, a third of the square outside."""
    frame = np.full((200, 200, 3), 190, dtype=np.uint8)
    landmarks = ((-30.0, 40.0), (30.0, 40.0), (0.0, 66.0), (-20.0, 92.0), (20.0, 92.0))

    aligned = cropping.align(frame, landmarks)

    assert 0.0 < aligned.containment < 0.8


def test_a_face_almost_entirely_outside_the_frame_reports_almost_nothing_real() -> None:
    frame = np.full((200, 200, 3), 190, dtype=np.uint8)
    landmarks = ((-160.0, 40.0), (-100.0, 40.0), (-130.0, 66.0), (-150.0, 92.0), (-110.0, 92.0))

    assert cropping.align(frame, landmarks).containment < 0.1


def test_an_index_past_the_edge_folds_back_without_repeating_the_edge_itself() -> None:
    """`[a b c d e]` read past either end gives the picture mirrored, and no doubled edge."""
    assert list(cropping._mirror(np.array([-1, -2, -3]), 5)) == [1, 2, 3]
    assert list(cropping._mirror(np.array([5, 6, 7]), 5)) == [3, 2, 1]
    assert list(cropping._mirror(np.array([0, 2, 4]), 5)) == [0, 2, 4]
    # A picture one pixel wide has nothing to reflect into and must not divide by zero.
    assert list(cropping._mirror(np.array([-3, 0, 7]), 1)) == [0, 0, 0]


def test_the_part_of_a_square_that_fell_outside_is_texture_rather_than_a_smear() -> None:
    """Outside the frame is mirrored, not streaked: a repeated column is constant along its rows."""
    frame = noisy_frame(200, 200, seed=11)
    # Placed so the square reaches well past the left-hand edge.
    landmarks = ((-40.0, 90.0), (20.0, 90.0), (-10.0, 116.0), (-30.0, 142.0), (10.0, 142.0))

    chip = cropping.align(frame, landmarks).chip
    band = chip[:, :6].astype(np.int64)

    spread = band.std(axis=1).mean()
    assert spread > 1.0, "the invented band is one column repeated, which is the streak"


def whole_square_containment(
    frame: np.ndarray, landmarks: tuple[tuple[float, float], ...]
) -> float:
    """The measure this replaced, over the whole square, kept so a test shows the difference."""
    matrix = cropping.similarity_transform(np.array(landmarks, dtype=np.float64), TEMPLATE)
    rows, columns = np.mgrid[:CHIP_SIZE, :CHIP_SIZE]
    grid = np.stack([columns.ravel(), rows.ravel(), np.ones(CHIP_SIZE**2)], axis=-1)
    mapped = grid @ np.linalg.inv(matrix).T
    height, width = frame.shape[:2]
    inside = (
        (mapped[:, 0] >= 0)
        & (mapped[:, 0] <= width - 1)
        & (mapped[:, 1] >= 0)
        & (mapped[:, 1] <= height - 1)
    )
    return float(inside.mean())


def test_a_head_at_the_top_of_the_picture_is_not_scored_on_its_missing_hair() -> None:
    """Measured over the face: a selfie's missing hairline is not a missing face, though the
    square as a whole falls below the floor."""
    frame = noisy_frame(400, 400, seed=7)
    landmarks = ((170.0, 44.0), (230.0, 44.0), (200.0, 70.0), (180.0, 96.0), (220.0, 96.0))

    aligned = cropping.align(frame, landmarks)

    assert aligned.containment >= tuning.MIN_CONTAINMENT, (
        "a face whose only missing part is the hair above it was refused"
    )
    assert whole_square_containment(frame, landmarks) < tuning.MIN_CONTAINMENT, (
        "this face has to be one the old measure refused, or the test proves nothing"
    )


def test_a_face_cut_through_its_features_is_still_caught() -> None:
    """A face at the side of a shot loses its features and still scores badly."""
    frame = noisy_frame(400, 400, seed=8)
    landmarks = ((-10.0, 120.0), (50.0, 120.0), (20.0, 146.0), (0.0, 172.0), (40.0, 172.0))

    aligned = cropping.align(frame, landmarks)

    assert aligned.containment < tuning.MIN_CONTAINMENT


# --- storing -------------------------------------------------------------------------------------------


def test_encoding_arguments_ask_for_one_stored_picture_per_square() -> None:
    argv = cropping.encode_args(3, CHIP_SIZE, 95, Settings())

    assert "-frames:v" in argv
    assert argv[argv.index("-frames:v") + 1] == "3"
    assert argv[argv.index("-s") + 1] == f"{CHIP_SIZE}x{CHIP_SIZE}"
    assert argv[-1] == "pipe:1"


def test_a_run_of_stored_pictures_is_cut_apart_at_the_marker_each_one_begins_with() -> None:
    first = b"\xff\xd8\xff" + b"one" * 5
    second = b"\xff\xd8\xff" + b"two" * 5

    assert cropping.split_encoded(b"rubbish" + first + second) == [first, second]


def test_a_stream_with_no_pictures_in_it_yields_none() -> None:
    assert cropping.split_encoded(b"nothing here") == []


async def test_storing_nothing_asks_for_nothing(settings: Settings) -> None:
    assert await cropping.encode([], settings) == []


async def test_squares_of_different_sizes_cannot_be_stored_together(settings: Settings) -> None:
    with pytest.raises(ValueError, match="same size"):
        await cropping.encode(
            [np.zeros((112, 112, 3), np.uint8), np.zeros((64, 64, 3), np.uint8)], settings
        )


async def test_a_partial_answer_is_refused_rather_than_paired_up_wrongly(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """If the count comes back wrong, which picture belongs to which face is unknown, and storing
    them anyway would attach the wrong face to a person."""

    async def only_one(*_: object, **__: object) -> bytes:
        return b"\xff\xd8\xff" + b"single"

    monkeypatch.setattr(cropping, "run_tool", only_one)

    with pytest.raises(ValueError, match="not storing any"):
        await cropping.encode([np.zeros((112, 112, 3), np.uint8)] * 2, settings)


async def test_a_stored_picture_reads_back_as_the_square_it_was_made_from(
    settings: Settings,
) -> None:
    """A square round-trips the encoder and decoder close enough to describe the same face, in
    the same colour order."""
    # Smooth pictures, each channel running a different way, so swapped colours would show.
    across = np.linspace(20, 235, CHIP_SIZE).astype(np.uint8)
    squares = []
    for down_first in (False, True):
        square = np.empty((CHIP_SIZE, CHIP_SIZE, 3), dtype=np.uint8)
        square[..., 0] = across[np.newaxis, :] if down_first else across[:, np.newaxis]
        square[..., 1] = across[:, np.newaxis] if down_first else across[np.newaxis, :]
        square[..., 2] = 120
        squares.append(square)
    stored = await cropping.encode(squares, settings)

    back = await cropping.decode(stored, settings)

    assert [one.shape for one in back] == [(CHIP_SIZE, CHIP_SIZE, 3)] * 2
    for square, one in zip(squares, back, strict=True):
        assert np.abs(square.astype(int) - one.astype(int)).mean() < 6
    assert await cropping.decode([], settings) == []


async def test_a_partial_read_back_is_refused_rather_than_paired_up_wrongly(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    async def one_square_short(*_: object, **__: object) -> bytes:
        return bytes(CHIP_SIZE * CHIP_SIZE * 3)

    monkeypatch.setattr(cropping, "run_tool", one_square_short)

    with pytest.raises(ValueError, match="not describing any"):
        await cropping.decode([b"\xff\xd8\xff one", b"\xff\xd8\xff two"], settings)


# --- identity ---------------------------------------------------------------------------------------


def test_the_same_picture_has_the_same_identity_and_a_different_one_does_not() -> None:
    """What makes importing the same pack twice write nothing, and the same face arriving from a
    folder and from a pack one reference rather than two."""
    assert cropping.digest(b"a picture") == cropping.digest(b"a picture")
    assert cropping.digest(b"a picture") != cropping.digest(b"another picture")
    assert len(cropping.digest(b"a picture")) == 64


# --- the picture a person looks at, which is not the one the recognizer reads ---------------------


def test_a_cover_is_cut_larger_than_the_square_the_recognizer_reads() -> None:
    """The whole point. A face 200 pixels across should produce a picture bigger than 112."""
    frame = noisy_frame(1280, 720, seed=11)
    box = Box(x=500, y=200, width=200, height=200)

    cut = cropping.portrait(frame, box)

    assert cut.shape[0] > CHIP_SIZE
    assert cut.shape[0] == cut.shape[1], "every place a cover is drawn is square"


def test_a_cover_keeps_room_around_the_face_rather_than_cropping_to_it() -> None:
    """The detector's box is the face and nothing else: no hair, no chin. Cut to it exactly, a
    cover reads as having had somebody's head cut off."""
    frame = noisy_frame(1280, 720, seed=12)
    box = Box(x=500, y=200, width=200, height=200)

    cut = cropping.portrait(frame, box)

    assert cut.shape[0] > box.width


def test_a_cover_at_the_edge_of_a_frame_is_moved_rather_than_shrunk() -> None:
    """Otherwise a cover's size would depend on where somebody happened to be standing, and a face
    near an edge would come back smaller than the same face in the middle."""
    frame = noisy_frame(1280, 720, seed=13)
    middle = cropping.portrait(frame, Box(x=540, y=260, width=200, height=200))
    corner = cropping.portrait(frame, Box(x=0, y=0, width=200, height=200))

    assert corner.shape == middle.shape


def test_a_cover_cannot_reach_outside_a_frame_smaller_than_it_wants() -> None:
    """The one case where it does shrink, because there is nothing else to do."""
    frame = noisy_frame(200, 150, seed=14)

    cut = cropping.portrait(frame, Box(x=10, y=10, width=180, height=130))

    assert cut.shape[0] <= 150
    assert cut.shape[0] == cut.shape[1]


def test_a_cover_is_reduced_by_the_encoder_and_never_enlarged_by_it() -> None:
    """A face that was small in the frame is small. Stretching it only makes the blur bigger."""
    argv = cropping.portrait_args(900, 900, 512, Settings())

    assert "scale='min(512,iw)':-2" in argv


@pytest.mark.integration
async def test_a_cover_is_stored_as_a_picture(tmp_path: Path) -> None:
    """The encoder is a real process: the only proof these arguments are accepted."""
    frame = noisy_frame(400, 400, seed=15)
    draw_face(frame, x=100, y=100, size=200)

    written = await cropping.encode_portrait(
        cropping.portrait(frame, Box(x=100, y=100, width=200, height=200)), Settings()
    )

    assert written is not None
    assert written.startswith(b"\xff\xd8"), "a stored picture, and one this format can read"
