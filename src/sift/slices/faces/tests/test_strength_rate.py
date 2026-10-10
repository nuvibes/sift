# SPDX-License-Identifier: AGPL-3.0-or-later
"""A person's strength is the library's own rate, and Sift files no more of its own picks of her
than people confirmed."""

from __future__ import annotations

import pytest

import sift.slices.workbench.schema  # noqa: F401 (the receipts' table)
from sift.kernel.content import Ingested
from sift.kernel.db import Database
from sift.slices.faces import tuning
from sift.slices.faces.models import Attribution
from sift.slices.faces.models import Origin as FaceOrigin
from sift.slices.faces.service import FaceService, Strength
from sift.slices.faces.service_learning import PICKS_RETIRED_QUEUE, RetiredPickRecords
from sift.slices.faces.store import Store
from sift.slices.faces.store_strength import Counted
from sift.slices.faces.tests.conftest import person_vector
from sift.slices.faces.tests.test_learning_edges import _appearances
from sift.slices.faces.tests.test_rematch_questions import (  # noqa: F401 (fixtures)
    library,
    picture,
)
from sift.slices.workbench.store import Store as WorkbenchStore

pytestmark = pytest.mark.integration


def _strength(references: int, **counted: int) -> Strength:
    return Strength(
        references=references,
        target=tuning.GOOD_REFERENCES,
        floor=tuning.FEWEST_REFERENCES,
        counted=Counted(**counted),
    )


@pytest.mark.parametrize(
    ("references", "counted", "verdict"),
    [
        (0, {}, "none"),
        (2, {"matched": 9}, "few"),
        (3, {}, "unseen"),
        (3, {"matched": 4, "no": 6}, "weak"),
        (3, {"matched": 5, "no": 5}, "fair"),
        (3, {"matched": 3, "yes": 3, "asked": 1, "no": 1}, "good"),
        (3, {"matched": 9, "asked": 1}, "strong"),
        (3, {"matched": 9, "asked": 100}, "strong"),
    ],
)
def test_the_band_is_the_rate_above_the_floor(
    references: int, counted: dict[str, int], verdict: str
) -> None:
    assert _strength(references, **counted).verdict == verdict


def test_a_yes_counts_as_right_a_no_as_wrong_and_an_open_question_as_nothing() -> None:
    strength = _strength(5, matched=6, asked=2, yes=1, no=1)

    assert strength.rate == pytest.approx(7 / 8)
    assert strength.fraction == pytest.approx(7 / 8)
    assert _strength(5).fraction == 0.0


async def _reference(store: Store, person: str, origin: FaceOrigin, index: int) -> None:
    await store.add_reference(
        person,
        vector=person_vector(0, index),
        quality=1.0,
        crop=f"{origin.value} {index}".encode(),
        origin=origin,
        recognizer="test-recognizer",
    )


async def test_her_reading_counts_her_pictures_by_origin_and_her_faces_by_outcome(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,  # noqa: F811
    temp_db: Database,
) -> None:
    for index in range(2):
        await _reference(store, person, FaceOrigin.ADDED, index)
    await _reference(store, person, FaceOrigin.CONFIRMED, 5)
    tracks = await _appearances(
        store, picture.asset.id, [[person_vector(0, n)] for n in range(10, 14)]
    )
    await store.attribute(tracks[0], person, confidence=0.9, attribution=Attribution.MATCHED)
    await store.attribute(tracks[1], person, confidence=0.5, attribution=Attribution.SUGGESTED)
    await store.attribute(tracks[2], person, confidence=0.9, attribution=Attribution.CONFIRMED)
    await temp_db.execute(
        "INSERT INTO face_rejections (track_id, person_id, created_at) VALUES (?, ?, 0)",
        (tracks[3], person),
    )

    strength = await service.recognition_of(person)
    wall = (await service.reference_strengths()).people[person]

    assert strength.counted == Counted(imported=2, confirmed=1, matched=1, asked=1, yes=1, no=1)
    assert (strength.rate, strength.verdict) == (pytest.approx(2 / 3), "fair")
    assert wall.counted == strength.counted


async def test_sift_files_no_more_of_its_own_picks_than_people_confirmed(
    store: Store, person: str
) -> None:
    await _reference(store, person, FaceOrigin.CONFIRMED, 1)
    faces = [
        (f"track {n}", f"file {n}", person_vector(0, 20 + n), 0.9, f"pick {n}".encode(), 120)
        for n in range(3)
    ]

    made = await store.add_recognized_references(person, faces, recognizer="test-recognizer")

    assert list(made) == ["track 0"]


async def test_picks_past_the_cap_go_oldest_first_with_a_history_line(
    service: FaceService, store: Store, person: str, temp_db: Database
) -> None:
    service._recorder = WorkbenchStore(temp_db)
    for index in range(3):
        await _reference(store, person, FaceOrigin.CONFIRMED, index)
    faces = [
        (f"track {n}", f"file {n}", person_vector(0, 20 + n), 0.9, f"pick {n}".encode(), 120)
        for n in range(3)
    ]
    made = await store.add_recognized_references(person, faces, recognizer="test-recognizer")
    await temp_db.execute(
        "DELETE FROM face_references WHERE person_id = ? AND origin = 'confirmed' "
        "AND id IN (SELECT id FROM face_references WHERE origin = 'confirmed' LIMIT 2)",
        (person,),
    )

    assert await store.picks_over_cap() == [(person, 2)]
    assert await service.settle_picks() == 2
    assert await store.picks_over_cap() == []
    left = await temp_db.fetch_all(
        "SELECT id FROM face_references WHERE person_id = ? AND origin = 'recognized'", (person,)
    )
    assert [str(row["id"]) for row in left] == [made["track 2"]]
    said = await temp_db.fetch_all(
        "SELECT title FROM workbench_decisions WHERE queue = ?", (PICKS_RETIRED_QUEUE,)
    )
    assert len(said) == 1
    assert str(said[0]["title"]).startswith("Sift took 2 faces it recognized as ")


async def test_a_retired_pick_is_final_in_history() -> None:
    records = RetiredPickRecords()

    assert records.reversible is False
    assert await records.pictures_of(None, "{}") == ()  # type: ignore[arg-type]
    assert await records.reverse(None, "r1", "{}") is False  # type: ignore[arg-type]
