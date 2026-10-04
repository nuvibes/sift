# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pass over facial fingerprints at its edges: faces it cannot compare, a switch turned off, a
press landing while the pass runs, and History records it can no longer read."""

from __future__ import annotations

import json
import time
from typing import Any

import numpy as np
import pytest

import sift.slices.workbench.schema  # noqa: F401 (its tables hold the receipts)
from sift.kernel.access import Role
from sift.kernel.content import Ingested
from sift.kernel.db import Database
from sift.kernel.workbench import Recorded
from sift.slices.faces import jobs as face_jobs
from sift.slices.faces import settings as face_settings
from sift.slices.faces.models import Origin as FaceOrigin
from sift.slices.faces.models import ScanStatus
from sift.slices.faces.receipts import FINGERPRINTS_QUEUE
from sift.slices.faces.service import FaceService
from sift.slices.faces.service_fingerprints import (
    FingerprintRecords,
    Placement,
    _closest_groups,
    held_from,
    place,
)
from sift.slices.faces.store import Store
from sift.slices.faces.tests.conftest import FakePreferences, make_person, person_vector
from sift.slices.faces.tests.test_packs import a_pack
from sift.slices.faces.tests.test_people_from_files import (
    RECOGNIZER,
    _face,
    _faces,
    _her_own,
    _people_called,
    _row,
)
from sift.slices.workbench.store import Store as WorkbenchStore
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.integration

ORLA = "Orla Tennant"


def test_faces_it_cannot_compare_place_nobody() -> None:
    """No faces, a block of faces somebody set aside, and another model's numbers: nothing to
    stack, so nothing is placed and nothing fails."""
    held = held_from([_row("a", person_vector(1))], bar=0.6)
    hers = _face("t1", person_vector(1))
    elsewhere = ("t2", "file", None, None, "g", np.zeros(3, dtype="<f4").tobytes())

    assert place(held, []) == []
    assert place(held, [hers], left_out={"t1"}) == []
    assert place(held, [elsewhere]) == []
    assert place(held, [hers]) == [Placement(entry_id="a", faces=1, person_id=None, pile_id="g")]


def test_a_group_described_by_another_model_is_asked_nothing() -> None:
    held = held_from([_row("a", person_vector(1))], bar=0.6)
    assert _closest_groups(held, [("g", np.zeros(3, dtype="<f4").tobytes())]) == []


async def test_with_faces_off_the_pass_and_its_questions_do_nothing(
    service: FaceService, store: Store, clip: Ingested, preferences: FakePreferences
) -> None:
    await service.import_pack(a_pack(people=[ORLA]))
    await _faces(store, clip.asset.id, [person_vector(0)])
    preferences.set(face_settings.PEOPLE_FROM_FILES_KEY, False)
    await service.regroup(full=True)
    preferences.set(face_settings.ENABLED_KEY, False)

    assert not await service.fingerprints_match_file(clip.asset.id)
    run = await service.recognize_from_fingerprints()
    assert (run.made, run.claimed, run.asked) == ([], [], 0)
    assert await service.fingerprint_offers() == []
    assert [str(row["name"]) for row in await store.unclaimed_entries()] == [ORLA]


async def test_a_group_is_asked_only_while_making_is_off_and_only_once_there_are_groups(
    service: FaceService, store: Store, clip: Ingested, preferences: FakePreferences
) -> None:
    """While making people is on the pass makes them, so no group asks; with it off, no group
    yet is no question."""
    await service.import_pack(a_pack(people=[ORLA]))
    await _faces(store, clip.asset.id, [person_vector(0)])
    await service.regroup(full=True)
    assert await service.fingerprint_offers() == []

    preferences.set(face_settings.PEOPLE_FROM_FILES_KEY, False)
    await store.database.execute("DELETE FROM face_piles")
    assert await service.fingerprint_offers() == []


async def test_two_groups_like_one_entry_ask_once_on_the_closer(
    service: FaceService, store: Store, temp_db: Database, preferences: FakePreferences
) -> None:
    preferences.set(face_settings.PEOPLE_FROM_FILES_KEY, False)
    await service.import_pack(a_pack(people=[ORLA]))
    now = int(time.time())
    for pile, variant in (("g-near", 0), ("g-far", 2)):
        await temp_db.execute(
            "INSERT INTO face_piles (id, status, centroid, size, recognizer, created_at,"
            " updated_at) VALUES (?, 'open', ?, 1, ?, ?, ?)",
            (
                pile,
                np.asarray(person_vector(0, variant), dtype="<f4").tobytes(),
                RECOGNIZER,
                now,
                now,
            ),
        )

    (offer,) = await service.fingerprint_offers()

    assert (offer.name, offer.pile_id) == (ORLA, "g-near")


async def test_an_entry_taken_while_the_pass_ran_is_not_given_again(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Somebody pressed Yes on the group between the pass reading the entries and reaching this
    one: she is made once, by them, and a second Yes finds nobody waiting."""
    await service.import_pack(a_pack(people=[ORLA]))
    await _faces(store, clip.asset.id, [person_vector(0)])
    admin = await create_user(temp_db, Role.ADMIN)
    placed = service._placements
    pressed: list[str] = []

    async def pressed_meanwhile(recognizer: str) -> list[Placement]:
        placements = await placed(recognizer)
        # Once: the press reads the placements too, on its way to naming the faces.
        for one in placements if not pressed else ():
            pressed.append(one.entry_id)
            assert await service.make_person_from_entry(one.entry_id, by=admin.id) is not None
        return placements

    monkeypatch.setattr(service, "_placements", pressed_meanwhile)
    run = await service.recognize_from_fingerprints()

    assert (run.made, run.claimed) == ([], [])
    (made,) = await _people_called(temp_db, ORLA)
    assert made["created_by_user_id"] == admin.id
    (entry,) = await temp_db.fetch_all("SELECT id FROM pack_entries")
    assert await service.make_person_from_entry(str(entry["id"]), by=admin.id) is None


async def test_an_entry_a_press_took_between_the_read_and_the_write_is_written_once(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Yes pressed after the pass read the entry and before its write: the press makes her and
    names her on the faces, and the pass's write finds the entry taken and writes nothing, not a
    second namesake holding nothing."""
    await service.import_pack(a_pack(people=[ORLA]))
    (track,) = await _faces(store, clip.asset.id, [person_vector(0)])
    admin = await create_user(temp_db, Role.ADMIN)
    read = service._entry_held
    pressed: list[str] = []

    async def pressed_meanwhile(entry: Any) -> Any:
        held = await read(entry)
        if not pressed:
            pressed.append(str(entry["id"]))
            assert await service.make_person_from_entry(str(entry["id"]), by=admin.id) is not None
        return held

    monkeypatch.setattr(service, "_entry_held", pressed_meanwhile)
    run = await service.recognize_from_fingerprints()

    assert (run.made, run.claimed) == ([], [])
    (made,) = await _people_called(temp_db, ORLA)
    assert made["created_by_user_id"] == admin.id
    named = await store.track(track)
    assert named is not None and named.person_id == made["id"]


async def test_a_name_another_import_gave_her_meanwhile_is_not_this_ones_to_undo(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The receipt holds only the names this entry added, so its Undo never takes away a name
    somebody else's write gave her between the check and the write."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    tamsin = await make_person(temp_db, "Tamsin")
    await _her_own(store, tamsin, person_vector(0))
    await _faces(store, clip.asset.id, [person_vector(0)])
    await service.rematch()
    await service.import_pack(a_pack(people=["Tamsin Vale"], aliases={"Tamsin Vale": ("Tam",)}))
    assert await store.add_alias(tamsin, "Tam")

    async def nobody_yet(_word: str) -> None:
        return None

    monkeypatch.setattr(store, "alias_owner", nobody_yet)
    await service.recognize_from_fingerprints()

    receipts, _total = await written.recent(limit=10, offset=0)
    (receipt,) = [one for one in receipts if one.queue == FINGERPRINTS_QUEUE]
    assert json.loads(receipt.payload)["aliases"] == ["Tamsin Vale"]
    admin = await create_user(temp_db, Role.ADMIN)
    await FingerprintRecords(service).reverse(admin, receipt.id, receipt.payload)
    assert await store.aliases_of(tamsin) == ["Tam"]


async def test_an_undo_with_faces_off_still_takes_back_the_references_and_names(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    preferences: FakePreferences,
) -> None:
    written = WorkbenchStore(temp_db)
    service._recorder = written
    tamsin = await make_person(temp_db, "Tamsin")
    await _her_own(store, tamsin, person_vector(0))
    await _faces(store, clip.asset.id, [person_vector(0)])
    await service.rematch()
    await service.import_pack(a_pack(people=["Tamsin Vale"]))
    await service.recognize_from_fingerprints()
    receipts, _total = await written.recent(limit=10, offset=0)
    (receipt,) = [one for one in receipts if one.queue == FINGERPRINTS_QUEUE]
    preferences.set(face_settings.ENABLED_KEY, False)

    admin = await create_user(temp_db, Role.ADMIN)
    undone = await FingerprintRecords(service).reverse(admin, receipt.id, receipt.payload)

    assert not isinstance(undone, bool) and undone.put_back == 1
    assert len(await store.references(tamsin)) == 3
    assert await store.aliases_of(tamsin) == []


@pytest.mark.parametrize("payload", ["{", "[]", json.dumps({"act": "made"})])
async def test_a_record_that_no_longer_says_what_it_added_puts_nothing_back(
    service: FaceService, temp_db: Database, payload: str
) -> None:
    admin = await create_user(temp_db, Role.ADMIN)
    records = FingerprintRecords(service)

    undone = await records.reverse(admin, "r1", payload)

    assert await records.pictures_of(admin, payload) == (), "a fingerprint is no file"
    if isinstance(undone, bool):
        assert undone is False
    else:
        assert (undone.put_back, undone.said) == (0, "That record no longer says what it added.")


def _worded(service: FaceService, payload: dict[str, object]) -> tuple[object, ...] | None:
    recorded = Recorded(
        id="r1",
        queue=FINGERPRINTS_QUEUE,
        payload=json.dumps(payload),
        title="",
        detail="",
        decided_at=0,
    )
    worded = FingerprintRecords(service).worded(recorded)
    return None if worded is None else worded.said


async def test_history_says_a_person_made_and_nothing_for_a_record_it_cannot_read(
    service: FaceService,
) -> None:
    said = _worded(service, {"act": "made", "person_id": "p1", "entry": ORLA})
    assert said is not None
    assert [one for one in said if isinstance(one, str)] == [
        " created ",
        " from facial fingerprints",
    ]
    assert _worded(service, {"act": "made", "entry": ORLA}) is None


class _Queue:
    def __init__(self) -> None:
        self.settled: list[str] = []

    async def enqueue_when_settled(self, job_type: str, **_: Any) -> None:
        self.settled.append(job_type)


class _Context:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {}
        self.queue = _Queue()
        self.job = type("Job", (), {"requested_by": None})()
        self.note: str | None = None

    async def set_progress(self, _done: float) -> None:
        return None

    async def set_note(self, note: str) -> None:
        self.note = note


async def test_a_scan_whose_face_matches_a_held_entry_asks_for_the_pass(
    service: FaceService, store: Store, clip: Ingested, monkeypatch: pytest.MonkeyPatch
) -> None:
    await service.import_pack(a_pack(people=[ORLA]))
    await _faces(store, clip.asset.id, [person_vector(0)])

    async def ready(**_: object) -> None:
        return None

    async def scanned(*_: object, **__: object) -> ScanStatus:
        return ScanStatus.NO_FACES

    monkeypatch.setattr(service, "weights_problem", ready)
    monkeypatch.setattr(service, "scan", scanned)
    context = _Context({"asset_id": clip.asset.id})

    await face_jobs.scan(context, service=service)  # type: ignore[arg-type]

    assert context.queue.settled == [face_jobs.FACE_PEOPLE_FROM_FILES]


async def test_the_pass_does_nothing_with_faces_off(
    service: FaceService, store: Store, clip: Ingested, preferences: FakePreferences
) -> None:
    await service.import_pack(a_pack(people=[ORLA]))
    await _faces(store, clip.asset.id, [person_vector(0)])
    preferences.set(face_settings.ENABLED_KEY, False)
    context = _Context()

    await face_jobs.people_from_files(context, service=service)  # type: ignore[arg-type]

    assert (context.note, context.queue.settled) == (None, [])
    assert [str(row["name"]) for row in await store.unclaimed_entries()] == [ORLA]


def test_activity_says_who_was_given_fingerprints_and_how_many_wait_for_an_answer() -> None:
    assert face_jobs._placed(made=0, claimed=2, asked=1200) == (
        "Added fingerprints to 2 people already here, 1,200 waiting for your answer"
    )


async def test_share_everyone_means_everybody_with_a_face_somebody_chose(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """A starter is a stash-box's picture that only lets Sift ask, so it shares nobody."""
    chosen = await make_person(temp_db, "Tamsin Vale")
    started = await make_person(temp_db, ORLA)
    await _her_own(store, chosen, person_vector(0), pictures=1)
    await store.add_reference(
        started,
        vector=person_vector(1),
        quality=1.0,
        crop=b"starter",
        origin=FaceOrigin.SEED,
        recognizer=RECOGNIZER,
    )

    assert await service.shareable_people() == [chosen]
