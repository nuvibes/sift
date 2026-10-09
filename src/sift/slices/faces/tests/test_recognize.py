# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning one aligned face into numbers, and comparing two of them; each family's pixel
arrangement is part of its description, as the wrong one quietly matches nothing."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from sift.slices.faces import recognize
from sift.slices.faces.recognize import Recognizer, recognisability
from sift.slices.faces.runner import Loaded
from sift.slices.faces.weights import CATALOG

pytestmark = pytest.mark.unit


class Canned:
    def __init__(self, vector: np.ndarray) -> None:
        self.vector = vector
        self.blobs: list[np.ndarray] = []

    def run(self, loaded: Loaded, blob: np.ndarray) -> list[np.ndarray]:
        self.blobs.append(blob)
        return [self.vector.reshape(1, -1)]


def loaded_for(family: str) -> Loaded:
    return Loaded(
        weight=CATALOG[f"{family}.recognizer"],
        session=None,
        inputs=("input",),
        outputs=("embedding",),
        device="cpu",
    )


def a_chip(value: int = 200) -> np.ndarray:
    return np.full((112, 112, 3), value, dtype=np.uint8)


def test_a_description_comes_back_at_unit_length() -> None:
    """Unit length makes comparing one multiply-and-add, from -1 to 1."""
    runner = Canned(np.array([3.0, 4.0], dtype=np.float32))

    described = Recognizer(runner, loaded_for("accurate")).embed(a_chip())  # type: ignore[arg-type]

    assert described.vector == pytest.approx((0.6, 0.8))
    # And the length scaling threw away comes back beside it. 3-4-5.
    assert described.strength == pytest.approx(5.0)


def test_a_model_that_describes_a_face_as_nothing_is_left_alone_rather_than_divided_by_nothing() -> (
    None
):
    runner = Canned(np.zeros(4, dtype=np.float32))

    described = Recognizer(runner, loaded_for("accurate")).embed(a_chip())  # type: ignore[arg-type]

    assert described.vector == (0.0, 0.0, 0.0, 0.0)
    assert described.strength == 0.0


def test_how_firmly_a_model_answered_becomes_a_nought_to_one_term() -> None:
    """A ramp between two measured ends, flat outside them."""
    ramp = (14.0, 21.0)

    assert recognisability(10.0, ramp) == 0.0
    assert recognisability(14.0, ramp) == 0.0
    assert recognisability(17.5, ramp) == pytest.approx(0.5)
    assert recognisability(21.0, ramp) == 1.0
    assert recognisability(30.0, ramp) == 1.0


def test_a_family_nobody_has_calibrated_gets_no_term_at_all() -> None:
    """Another model's ends would be a threshold about a different quantity."""
    assert recognisability(3.0, None) == 1.0
    assert recognisability(300.0, None) == 1.0
    runner = Canned(np.array([3.0, 4.0], dtype=np.float32))
    uncalibrated = Recognizer(runner, loaded_for("permissive"))  # type: ignore[arg-type]

    assert uncalibrated.recognisability(1.0) == 1.0


def test_a_model_that_answered_nothing_at_all_is_not_ranked_last_for_it() -> None:
    """Zero is "nothing measured it", not the worst evidence."""
    assert recognisability(0.0, (14.0, 21.0)) == 1.0
    assert recognisability(-1.0, (14.0, 21.0)) == 1.0


def test_each_family_is_handed_its_picture_the_way_it_was_trained() -> None:
    accurate = Canned(np.array([1.0, 0.0], dtype=np.float32))
    Recognizer(accurate, loaded_for("accurate")).embed(a_chip(255))  # type: ignore[arg-type]

    permissive = Canned(np.array([1.0, 0.0], dtype=np.float32))
    Recognizer(permissive, loaded_for("permissive")).embed(a_chip(255))  # type: ignore[arg-type]

    assert accurate.blobs[0].max() == pytest.approx(1.0)
    assert permissive.blobs[0].max() == pytest.approx(255.0)


class Batched:
    """A model that reads a batch: one row of its own out for each row in."""

    def __init__(self, rows: np.ndarray) -> None:
        self.rows = rows
        self.blobs: list[np.ndarray] = []

    def run(self, loaded: Loaded, blob: np.ndarray) -> list[np.ndarray]:
        self.blobs.append(blob)
        return [self.rows[: len(blob)]]


def test_a_files_faces_are_described_in_one_run_of_the_model() -> None:
    """The point is the count: one ask, not one per face."""
    runner = Batched(np.array([[3.0, 4.0], [0.0, 5.0], [5.0, 0.0]], dtype=np.float32))

    described = Recognizer(runner, loaded_for("accurate")).embed_many(  # type: ignore[arg-type]
        [a_chip(10), a_chip(20), a_chip(30)]
    )

    assert len(runner.blobs) == 1
    assert runner.blobs[0].shape == (3, 3, 112, 112)
    assert [one.vector for one in described] == [
        pytest.approx((0.6, 0.8)),
        pytest.approx((0.0, 1.0)),
        pytest.approx((1.0, 0.0)),
    ]


def test_each_face_gets_its_own_answer_and_they_stay_in_order() -> None:
    """A batch that handed every face the first answer would look like a working one."""
    runner = Batched(np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))
    recognizer = Recognizer(runner, loaded_for("accurate"))  # type: ignore[arg-type]

    together = recognizer.embed_many([a_chip(10), a_chip(20)])
    apart = [
        Recognizer(Batched(np.array([[1.0, 0.0]], dtype=np.float32)), loaded_for("accurate")).embed(  # type: ignore[arg-type]
            a_chip(10)
        ),
        Recognizer(Batched(np.array([[0.0, 1.0]], dtype=np.float32)), loaded_for("accurate")).embed(  # type: ignore[arg-type]
            a_chip(20)
        ),
    ]

    assert [one.vector for one in together] == [one.vector for one in apart]


def test_a_model_that_will_not_read_a_batch_is_refused_rather_than_repeated() -> None:
    """A model exported at batch one would describe every face as the first: refused loudly."""
    runner = Canned(np.array([3.0, 4.0], dtype=np.float32))

    with pytest.raises(ValueError, match="does not read a batch"):
        Recognizer(runner, loaded_for("accurate")).embed_many(  # type: ignore[arg-type]
            [a_chip(10), a_chip(20)]
        )


def test_a_file_with_no_faces_asks_the_model_nothing() -> None:
    runner = Batched(np.zeros((0, 2), dtype=np.float32))

    assert Recognizer(runner, loaded_for("accurate")).embed_many([]) == []  # type: ignore[arg-type]
    assert runner.blobs == []


def test_a_batch_is_handed_over_the_way_its_family_was_trained() -> None:
    accurate = Batched(np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32))
    Recognizer(accurate, loaded_for("accurate")).embed_many([a_chip(255), a_chip(255)])  # type: ignore[arg-type]

    permissive = Batched(np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32))
    Recognizer(permissive, loaded_for("permissive")).embed_many([a_chip(255), a_chip(255)])  # type: ignore[arg-type]

    assert accurate.blobs[0].max() == pytest.approx(1.0)
    assert permissive.blobs[0].max() == pytest.approx(255.0)


def test_a_family_with_no_reader_is_refused_rather_than_guessed_at() -> None:
    unknown = replace(
        loaded_for("accurate"), weight=replace(CATALOG["accurate.recognizer"], family="mystery")
    )

    with pytest.raises(ValueError, match="no recognizer reader"):
        Recognizer(Canned(np.zeros(2, dtype=np.float32)), unknown)  # type: ignore[arg-type]


def test_a_recognizer_says_which_model_it_is_and_how_many_numbers_it_produces() -> None:
    recognizer = Recognizer(Canned(np.zeros(2, dtype=np.float32)), loaded_for("accurate"))  # type: ignore[arg-type]

    assert recognizer.revision == CATALOG["accurate.recognizer"].revision
    assert recognizer.dimension == CATALOG["accurate.recognizer"].dimension


# --- comparing ------------------------------------------------------------------------------------


def test_a_face_compared_with_itself_is_a_perfect_match() -> None:
    assert recognize.similarity((0.6, 0.8), (0.6, 0.8)) == pytest.approx(1.0)


def test_two_faces_pointing_in_different_directions_are_not_alike() -> None:
    assert recognize.similarity((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0)


def test_two_faces_described_by_different_models_cannot_be_compared_at_all() -> None:
    """Not a worse answer: a meaningless one. Refused rather than returned."""
    with pytest.raises(ValueError, match="different numbers of values"):
        recognize.similarity((1.0, 0.0), (1.0, 0.0, 0.0))


# --- storing --------------------------------------------------------------------------------------


def test_a_description_survives_being_stored_and_read_back() -> None:
    vector = (0.5, -0.25, 0.75, 0.0)

    assert recognize.unpack(recognize.pack(vector)) == pytest.approx(vector)


def test_a_stored_description_is_four_bytes_a_number() -> None:
    assert len(recognize.pack((0.1, 0.2, 0.3))) == 12
