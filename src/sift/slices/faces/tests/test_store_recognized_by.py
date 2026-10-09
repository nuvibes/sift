# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a person is recognized by: references, models and measuring again."""

from __future__ import annotations

import pytest

# For its side effect: registering the table the ledger is written to, so a slice test's
# database has it. Registration happens at import and the fixtures migrate at setup.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.content import (
    Ingested,
)
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.faces import recognize
from sift.slices.faces.models import (
    Attribution,
)
from sift.slices.faces.models import Origin as FaceOrigin
from sift.slices.faces.store import Remeasured, Store
from sift.slices.faces.tests.conftest import make_person, person_vector
from sift.slices.faces.tests.test_store import record

pytestmark = pytest.mark.integration


async def test_matching_and_grouping_see_only_the_faces_the_model_in_use_described(
    store: Store, temp_db: Database, clip: Ingested, other_clip: Ingested
) -> None:
    """Two models' numbers are the same length and mean nothing to each other. A file the
    previous model described is out of matching and grouping until it is measured again, and it
    is what the pass that measures again is handed."""
    await record(store, clip.asset.id)
    await record(store, other_clip.asset.id)
    await temp_db.execute(
        "UPDATE face_scans SET recognizer = 'older' WHERE asset_id = ?", (other_clip.asset.id,)
    )

    listed = await store.unattributed("test-recognizer")

    assert [asset_id for _, asset_id, _ in listed] == [clip.asset.id]
    assert await store.measured_by_others("test-recognizer", limit=10) == [other_clip.asset.id]
    assert await store.count_measured_by_others("test-recognizer") == 1


async def test_the_gallery_is_one_models_references_and_the_rest_wait_to_be_measured_again(
    store: Store, temp_db: Database
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")
    kept = await store.add_reference(
        person,
        vector=person_vector(0),
        quality=0.9,
        crop=b"one picture",
        origin=FaceOrigin.CONFIRMED,
        recognizer="test-recognizer",
    )
    older = await store.add_reference(
        person,
        vector=person_vector(1),
        quality=0.9,
        crop=b"another picture",
        origin=FaceOrigin.CONFIRMED,
        recognizer="older",
    )
    numbers_only = await store.add_reference(
        person,
        vector=person_vector(2),
        quality=0.9,
        crop=None,
        digest="abc123",
        origin=FaceOrigin.PACK,
        recognizer="older",
    )
    assert kept and older and numbers_only

    gallery = await store.reference_gallery("test-recognizer")

    assert [reference.id for reference in gallery[person]] == [kept]
    assert [
        reference_id
        for reference_id, _ in await store.references_measured_by_others(
            "test-recognizer", limit=10
        )
    ] == [older], "the one with a picture can be measured again; numbers alone cannot"
    assert await store.count_unmeasurable_references("test-recognizer") == 1

    await store.remeasure_reference(
        older, embedding=recognize.pack(person_vector(3)), recognizer="test-recognizer"
    )
    assert {
        reference.id for reference in (await store.reference_gallery("test-recognizer"))[person]
    } == {
        kept,
        older,
    }
    assert await store.references_measured_by_others("test-recognizer", limit=10) == []


async def test_measuring_a_file_again_replaces_its_numbers_carries_every_decision_and_undoes_the_arithmetic(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """The one transaction that a model change rests on. Each face's numbers are replaced; the
    three memories that hold a copy of a face's numbers (a name given, a pile set aside, a pile
    built by hand) are found by the copy and given the new ones, which is only possible at
    this moment; what Sift decided by arithmetic is undone and what a person decided stays; and
    the scan is restamped, which puts the file back into matching."""
    person = await make_person(temp_db, "Ada Lovelace")
    named, matched = await record(store, clip.asset.id, 2, distinct=True)
    named_face = (await store.faces_of(named))[0]
    matched_face = (await store.faces_of(matched))[0]
    await store.attribute(named, person, confidence=1.0, attribution=Attribution.CONFIRMED)
    await store.remember_confirmation(named, person)
    await store.attribute(matched, person, confidence=0.9, attribution=Attribution.MATCHED)
    old_matched = recognize.pack(matched_face.vector)
    await temp_db.execute(
        "INSERT INTO face_ignored (id, asset_id, pile_id, embedding, centroid, created_at) "
        "VALUES (?, ?, 'pile-1', ?, ?, 1)",
        (new_id(), clip.asset.id, old_matched, old_matched),
    )
    await temp_db.execute(
        "INSERT INTO face_grouping (id, asset_id, pile_id, embedding, centroid, created_at) "
        "VALUES (?, ?, 'pile-1', ?, ?, 1)",
        (new_id(), clip.asset.id, old_matched, old_matched),
    )

    removed = await store.remeasure_file(
        clip.asset.id,
        [
            Remeasured(
                id=named_face.id,
                previous=recognize.pack(named_face.vector),
                embedding=recognize.pack(person_vector(5)),
                strength=17.0,
            ),
            Remeasured(
                id=matched_face.id,
                previous=old_matched,
                embedding=recognize.pack(person_vector(6)),
                strength=18.0,
            ),
        ],
        gone=[],
        recognizer="newest",
    )

    assert removed == 0
    assert recognize.similarity((await store.faces_of(named))[0].vector, person_vector(5)) > 0.99
    assert recognize.similarity((await store.faces_of(matched))[0].vector, person_vector(6)) > 0.99
    remembered = await store.confirmations_for(clip.asset.id)
    assert [who for who, _ in remembered] == [person]
    assert recognize.similarity(remembered[0][1], person_vector(5)) > 0.99, "the name's copy moved"
    assert (
        recognize.similarity((await store.ignored_for(clip.asset.id))[0][1], person_vector(6))
        > 0.99
    )
    assert (
        recognize.similarity((await store.grouped_for(clip.asset.id))[0][1], person_vector(6))
        > 0.99
    )
    kept = await store.track(named)
    assert (
        kept is not None and kept.attribution is Attribution.CONFIRMED and kept.person_id == person
    )
    undone = await store.track(matched)
    assert undone is not None and undone.person_id is None and undone.attribution is None
    scan = await store.scan_of(clip.asset.id)
    assert scan is not None and scan.recognizer == "newest"
    assert await store.count_measured_by_others("newest") == 0


async def test_a_face_whose_picture_has_gone_is_dropped_and_its_appearance_with_it_when_it_was_the_last(
    store: Store, clip: Ingested
) -> None:
    kept, lost = await record(store, clip.asset.id, 2, distinct=True)
    kept_face = (await store.faces_of(kept))[0]
    lost_face = (await store.faces_of(lost))[0]

    removed = await store.remeasure_file(
        clip.asset.id,
        [
            Remeasured(
                id=kept_face.id,
                previous=recognize.pack(kept_face.vector),
                embedding=recognize.pack(person_vector(5)),
                strength=17.0,
            )
        ],
        gone=[lost_face.id],
        recognizer="newest",
    )

    assert removed == 1
    assert [track.id for track in await store.tracks_of(clip.asset.id)] == [kept]
    assert await store.faces_of(lost) == []


async def test_stored_pictures_are_read_together_and_a_missing_one_reads_as_none(
    store: Store, clip: Ingested
) -> None:
    await record(store, clip.asset.id, 2, distinct=True)
    listed = await store.detections_of_file(clip.asset.id)
    assert len(listed) == 2
    stored = [path for _, path, _ in listed]
    store.resolve(stored[1]).unlink()

    pictures = await store.read_pictures(stored)

    assert pictures[0] == b"\xff\xd8\xff picture 0"
    assert pictures[1] is None


async def test_taking_a_name_off_takes_back_the_pictures_that_face_gave_them(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """What taking a name off has to do as well as taking the name off. Otherwise a face agreed to
    by mistake goes on being one of the things that person is recognized by, permanently."""
    person = await make_person(temp_db, "Ada Lovelace")
    tracks = await record(store, clip.asset.id, count=1)
    detections = await store.faces_of(tracks[0])
    picture = store.resolve(detections[0].crop_path).read_bytes()
    await store.add_reference(
        person,
        vector=person_vector(0),
        quality=0.9,
        crop=picture,
        origin=FaceOrigin.CONFIRMED,
        recognizer="test-recognizer",
    )
    assert await store.reference_count(person) == 1

    gone = await store.remove_references_from_track(person, tracks[0])

    assert gone == 1
    assert await store.reference_count(person) == 0


async def test_taking_a_name_off_a_face_with_no_picture_left_takes_nothing_back(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """A reference is keyed by the identity of its picture, so a face whose crops have gone from
    disk cannot say which rows it put there. Nothing is removed rather than something guessed."""
    person = await make_person(temp_db, "Ada Lovelace")
    tracks = await record(store, clip.asset.id, count=1)
    for detection in await store.faces_of(tracks[0]):
        store.resolve(detection.crop_path).unlink()
    await store.add_reference(
        person,
        vector=person_vector(0),
        quality=0.9,
        crop=b"some other picture",
        origin=FaceOrigin.CONFIRMED,
        recognizer="test-recognizer",
    )

    assert await store.remove_references_from_track(person, tracks[0]) == 0
    assert await store.reference_count(person) == 1


async def test_how_many_appearances_somebody_agreed_to(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """Not the same number as their reference count, and the difference is why it is reported:
    agreeing to one appearance files every stored picture of it, so eight decisions can add
    thirty-odd references and a bar that jumps by thirty reads as broken when it is truthful."""
    person = await make_person(temp_db, "Ada Lovelace")
    tracks = await record(store, clip.asset.id, count=3)

    assert await store.confirmed_appearances(person) == 0

    await store.attribute(tracks[0], person, confidence=1.0, attribution=Attribution.CONFIRMED)
    # Two of the other kind rather than one, so that counting the wrong side of the comparison
    # gives a different number rather than the same one by coincidence.
    await store.attribute(tracks[1], person, confidence=0.7, attribution=Attribution.MATCHED)
    await store.attribute(tracks[2], person, confidence=0.6, attribution=Attribution.MATCHED)

    assert await store.confirmed_appearances(person) == 1


async def test_everybodys_reference_count_in_one_answer(store: Store, temp_db: Database) -> None:
    """For a picker drawing several people together. Asking per row turns choosing a name into one
    request per keystroke per candidate."""
    ada = await make_person(temp_db, "Ada Lovelace")
    grace = await make_person(temp_db, "Grace Hopper")
    await make_person(temp_db, "Nobody At All")
    for index in range(2):
        await store.add_reference(
            ada,
            vector=person_vector(index * 2),
            quality=0.9,
            crop=f"ada {index}".encode(),
            origin=FaceOrigin.CONFIRMED,
            recognizer="test-recognizer",
        )
    await store.add_reference(
        grace,
        vector=person_vector(1),
        quality=0.9,
        crop=b"grace",
        origin=FaceOrigin.CONFIRMED,
        recognizer="test-recognizer",
    )

    counts = await store.reference_counts()

    # People with no references are absent rather than zero: that is what the table can say.
    assert counts == {ada: 2, grace: 1}


async def test_the_count_that_decides_a_bar_is_of_this_models_pictures_only(
    store: Store, temp_db: Database
) -> None:
    """Two questions, two answers, and only one of them is about the comparison being made.

    A screen reporting how well covered somebody is wants every picture of them. The bar Sift
    attaches to them at wants the ones a comparison actually reads, and after a model change
    those differ, so counting all of them there would lower the bar on evidence nothing is using.
    """
    ada = await make_person(temp_db, "Ada Lovelace")
    for index, model in enumerate(("test-recognizer", "test-recognizer", "an-older-model")):
        await store.add_reference(
            ada,
            vector=person_vector(index),
            quality=0.9,
            crop=f"ada {index}".encode(),
            origin=FaceOrigin.CONFIRMED,
            recognizer=model,
        )

    assert await store.reference_counts() == {ada: 3}
    assert await store.reference_counts(recognizer="test-recognizer") == {ada: 2}
