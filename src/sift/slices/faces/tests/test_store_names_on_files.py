# SPDX-License-Identifier: AGPL-3.0-or-later
"""The names the faces feature puts on a file, and takes back off it."""

from __future__ import annotations

import pytest

# For its side effect: registering the table the ledger is written to, so a slice test's
# database has it. Registration happens at import and the fixtures migrate at setup.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.content import (
    Ingested,
)
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.slices.faces.models import (
    Attribution,
)
from sift.slices.faces.store import Store
from sift.slices.faces.tests.conftest import make_person
from sift.slices.faces.tests.test_store import record

pytestmark = pytest.mark.integration


async def people_on(database: Database, asset_id: str) -> list[str]:
    rows = await database.fetch_all(
        "SELECT person_id FROM asset_people WHERE asset_id = ? ORDER BY person_id", (asset_id,)
    )
    return [str(row["person_id"]) for row in rows]


async def test_attributing_a_face_puts_that_person_on_the_file(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """The point of recognizing a face: the person turns up where the rest of Sift reads them."""
    person = await make_person(temp_db, "Ada Lovelace")
    track_id = (await record(store, clip.asset.id))[0]
    await store.attribute(track_id, person, confidence=0.9, attribution=Attribution.MATCHED)

    added, removed = await store.reconcile_people(clip.asset.id)

    assert added == [person]
    assert removed == []
    assert await people_on(temp_db, clip.asset.id) == [person]


async def test_the_batched_reconcile_answers_what_asking_one_file_at_a_time_answers(
    store: Store, clip: Ingested, other_clip: Ingested, temp_db: Database
) -> None:
    """The property the batched form has to have: the same answer, per file, and the same rows.

    It stands in for a call per file on the path that names a group of faces, so what matters is
    that it is the SAME answer: a second account of what a face attribution does to a file would put
    somebody on a file on one screen and not on another.

    Two files with DIFFERENT people, because one person across both would pass with the batch
    keeping only the last file's answer, or with the two files' answers swapped. A file asked about
    with nothing to change is in the answer holding two empty lists, which is what lets a caller
    read it without checking.
    """
    ada = await make_person(temp_db, "Ada Lovelace")
    zelda = await make_person(temp_db, "Zelda Fitzgerald")
    first = (await record(store, clip.asset.id))[0]
    second = (await record(store, other_clip.asset.id))[0]
    await store.attribute(first, ada, confidence=0.9, attribution=Attribution.MATCHED)
    await store.attribute(second, zelda, confidence=0.9, attribution=Attribution.MATCHED)

    moved = await store.reconcile_people_of([clip.asset.id, other_clip.asset.id, "no-such-file"])

    assert moved[clip.asset.id] == ([ada], [])
    assert moved[other_clip.asset.id] == ([zelda], [])
    assert moved["no-such-file"] == ([], []), (
        "a file with nothing to change is present, not missing"
    )
    assert await people_on(temp_db, clip.asset.id) == [ada]
    assert await people_on(temp_db, other_clip.asset.id) == [zelda]


async def test_the_batched_reconcile_takes_people_off_as_well_as_putting_them_on(
    store: Store, clip: Ingested, other_clip: Ingested, temp_db: Database
) -> None:
    """Both directions in one batch, which is the case a one-directional loop passes and is wrong
    about: one file gaining somebody while another loses somebody, settled by one transaction."""
    ada = await make_person(temp_db, "Ada Lovelace")
    zelda = await make_person(temp_db, "Zelda Fitzgerald")
    first = (await record(store, clip.asset.id))[0]
    second = (await record(store, other_clip.asset.id))[0]
    await store.attribute(first, ada, confidence=0.9, attribution=Attribution.MATCHED)
    await store.reconcile_people_of([clip.asset.id])

    await store.attribute(first, None, confidence=None, attribution=None)
    await store.attribute(second, zelda, confidence=0.9, attribution=Attribution.MATCHED)
    moved = await store.reconcile_people_of([clip.asset.id, other_clip.asset.id])

    assert moved[clip.asset.id] == ([], [ada]), "the one that lost somebody"
    assert moved[other_clip.asset.id] == ([zelda], []), "and the one that gained somebody"
    assert await people_on(temp_db, clip.asset.id) == []
    assert await people_on(temp_db, other_clip.asset.id) == [zelda]


async def test_withdrawing_the_last_face_takes_that_person_back_off_the_file(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")
    track_id = (await record(store, clip.asset.id))[0]
    await store.attribute(track_id, person, confidence=0.9, attribution=Attribution.MATCHED)
    await store.reconcile_people(clip.asset.id)

    await store.attribute(track_id, None, confidence=None, attribution=None)
    added, removed = await store.reconcile_people(clip.asset.id)

    assert added == []
    assert removed == [person]
    assert await people_on(temp_db, clip.asset.id) == []


async def test_a_name_somebody_put_on_by_hand_is_never_taken_off(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """The whole reason the claim is recorded at all.

    Somebody drags a person onto a file. Later a face in that file is attributed to them and then
    withdrawn: a rejected suggestion, a re-match that changed its mind. The pair in the shared
    table looks identical either way, so without a record of who wrote it the withdrawal would take
    away what a person decided.

    The insert is what decides: it found the row already there, wrote nothing, and so claimed
    nothing. Nothing this feature did not create is ever removed.
    """
    person = await make_person(temp_db, "Ada Lovelace")
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (clip.asset.id, person)
    )
    track_id = (await record(store, clip.asset.id))[0]
    await store.attribute(track_id, person, confidence=0.9, attribution=Attribution.MATCHED)

    added, _ = await store.reconcile_people(clip.asset.id)
    assert added == [], "a pair that was already there is not this feature's to claim"
    assert await store.claimed_people(clip.asset.id) == []

    await store.attribute(track_id, None, confidence=None, attribution=None)
    _, removed = await store.reconcile_people(clip.asset.id)

    assert removed == []
    assert await people_on(temp_db, clip.asset.id) == [person], (
        "withdrawing a face attribution removed a name somebody attached by hand"
    )


async def test_a_person_stays_on_the_file_while_any_face_still_names_them(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """Several faces in one file can be the same person, so the name goes when the last one does.

    Withdrawn by the pair rather than by the face for exactly this: a photograph where somebody
    appears twice, one of the two rejected, and they are still in the picture.
    """
    person = await make_person(temp_db, "Ada Lovelace")
    first, second = await record(store, clip.asset.id, count=2)
    for track_id in (first, second):
        await store.attribute(track_id, person, confidence=0.9, attribution=Attribution.MATCHED)
    await store.reconcile_people(clip.asset.id)

    await store.attribute(first, None, confidence=None, attribution=None)
    added, removed = await store.reconcile_people(clip.asset.id)

    assert (added, removed) == ([], [])
    assert await people_on(temp_db, clip.asset.id) == [person]


async def test_deleting_everything_takes_back_its_own_names_and_leaves_the_rest(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """The plainly-labelled control removes what recognizing faces produced. A decision somebody
    made themselves has never been part of that."""
    by_hand = await make_person(temp_db, "Grace Hopper")
    by_face = await make_person(temp_db, "Ada Lovelace")
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (clip.asset.id, by_hand)
    )
    track_id = (await record(store, clip.asset.id))[0]
    await store.attribute(track_id, by_face, confidence=0.9, attribution=Attribution.MATCHED)
    await store.reconcile_people(clip.asset.id)
    assert await people_on(temp_db, clip.asset.id) == sorted([by_hand, by_face])

    await store.forget_everything(actor=Actor.sift("faces"))

    assert await people_on(temp_db, clip.asset.id) == [by_hand]
    assert await store.claimed_people(clip.asset.id) == []
