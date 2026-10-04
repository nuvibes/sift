# SPDX-License-Identifier: AGPL-3.0-or-later
"""Facial fingerprints come in as fingerprints, and place themselves by face.

An import holds every person a facial fingerprints file or a folder of people names, and never
gives anybody anything by name (`test_packs.py`). The pass after it places each held entry by the
faces in the library that match it: given to the person whose own pictures already match those
faces, made into a new person by Sift where the faces are nobody's, asked about on the group while
making people is off, and left held where nothing matches. Each person made or given an entry is
one History line, and its Undo takes back what it added and keeps the next pass off the entry.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

import sift.slices.workbench.schema  # noqa: F401 (its tables hold the receipts)
from sift.kernel.access import Role
from sift.kernel.config import Settings
from sift.kernel.content import Ingested
from sift.kernel.db import Database
from sift.kernel.workbench import Recorded
from sift.slices.faces import jobs as face_jobs
from sift.slices.faces import settings as face_settings
from sift.slices.faces.models import (
    Appearance,
    Attribution,
    Box,
    Described,
    Detection,
    Quality,
    ScanStatus,
    Vector,
)
from sift.slices.faces.models import Origin as FaceOrigin
from sift.slices.faces.receipts import FINGERPRINTS_QUEUE
from sift.slices.faces.service import FaceService
from sift.slices.faces.service_fingerprints import (
    FingerprintRecords,
    Placement,
    held_from,
    place,
)
from sift.slices.faces.store import PassRecord, Store
from sift.slices.faces.tests.conftest import (
    FakeDetector,
    FakePreferences,
    FakeRecognizer,
    RecordingReindexer,
    draw_face,
    make_person,
    noisy_frame,
    person_vector,
    unit,
)
from sift.slices.faces.tests.test_edges import import_folder
from sift.slices.faces.tests.test_packs import a_pack, overtaken
from sift.slices.workbench.store import Decision
from sift.slices.workbench.store import Store as WorkbenchStore
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.integration

RECOGNIZER = "test-recognizer"


async def _faces(store: Store, asset_id: str, vectors: list[Vector]) -> list[str]:
    """One appearance per vector on the file, with nobody on any of them yet."""
    return await store.replace_pass(
        asset_id,
        [
            Appearance(
                started_ms=0,
                ended_ms=0,
                seen_in=1,
                quality=0.8,
                faces=(
                    Described(
                        detection=Detection(
                            box=Box(x=10, y=10, width=100, height=100),
                            score=0.9,
                            landmarks=((1.0, 1.0),) * 5,
                            timestamp_ms=0,
                        ),
                        quality=Quality(
                            pixels=120, sharpness=500.0, frontality=0.9, score=0.8, accepted=True
                        ),
                        vector=vector,
                        chip=np.zeros((1, 1, 3), dtype=np.uint8),
                    ),
                ),
            )
            for vector in vectors
        ],
        [[b"\xff\xd8\xff picture"] for _ in vectors],
        PassRecord(
            status=ScanStatus.NONE_IDENTIFIED,
            depth="fast",
            coverage=1.0,
            frames_sampled=1,
            detector="test-detector",
            recognizer=RECOGNIZER,
            settings_digest="abcd1234",
        ),
    )


async def _people_called(database: Database, name: str) -> list[dict[str, object]]:
    rows = await database.fetch_all(
        "SELECT id, created_by_kind, created_by_via, created_by_user_id FROM people"
        " WHERE name = ? ORDER BY created_at, id",
        (name,),
    )
    return [dict(row) for row in rows]


async def _her_own(store: Store, person_id: str, vector: Vector, pictures: int = 3) -> None:
    for index in range(pictures):
        await store.add_reference(
            person_id,
            vector=vector,
            quality=1.0,
            crop=f"her-own-{person_id}-{index}".encode(),
            origin=FaceOrigin.CONFIRMED,
            recognizer=RECOGNIZER,
        )


# --- the arithmetic -------------------------------------------------------------------------------


def _row(entry: str, vector: Vector) -> tuple[str, bytes]:
    return entry, np.asarray(vector, dtype="<f4").tobytes()


def _face(
    track: str, vector: Vector, person: str | None = None, how: str | None = None, pile: str = "g"
) -> tuple[str, str, str | None, str | None, str | None, bytes]:
    return (track, "file", person, how, pile, np.asarray(vector, dtype="<f4").tobytes())


def test_a_face_counts_for_the_one_entry_it_is_likest_and_the_named_faces_decide() -> None:
    """A face matching two entries counts for the closer one alone. Whose faces they are is
    counted per person, MATCHED or CONFIRMED only: a question is nobody's match."""
    held = held_from(
        [_row("a", person_vector(1)), _row("a", person_vector(1, 1)), _row("b", person_vector(2))],
        bar=0.6,
    )
    faces = [
        _face("t1", person_vector(1), "her", Attribution.MATCHED.value),
        _face("t2", person_vector(1, 1), "her", Attribution.CONFIRMED.value),
        _face("t3", person_vector(1), "other", Attribution.SUGGESTED.value),
        _face("t6", person_vector(1, 1), "other", Attribution.SUGGESTED.value),
        _face("t7", person_vector(1), "other", Attribution.SUGGESTED.value),
        _face("t4", person_vector(2), pile="p2"),
        _face("t5", person_vector(5)),
    ]

    placed = {one.entry_id: one for one in place(held, faces)}

    assert (placed["a"].faces, placed["a"].person_id) == (5, "her")
    assert (placed["b"].faces, placed["b"].person_id, placed["b"].pile_id) == (1, None, "p2")
    assert place(held, faces, left_out={"t4"})[-1].entry_id == "a", "a set-aside face counts"


# --- the pass -------------------------------------------------------------------------------------


async def test_an_import_attaches_nothing_and_the_pass_makes_a_second_namesake_by_face(
    service: FaceService, store: Store, temp_db: Database, clip: Ingested
) -> None:
    """A box-made Liora Fenwick has no face to match, so the file's Liora Fenwick, whose faces
    are in the library and nobody's, becomes a second person made by Sift from facial
    fingerprints, never the first one's references."""
    box_made = await make_person(temp_db, "Liora Fenwick")
    await service.import_pack(a_pack(people=["Liora Fenwick"]))
    tracks = await _faces(store, clip.asset.id, [person_vector(0), person_vector(0, 1)])
    assert await store.references(box_made) == []

    run = await service.recognize_from_fingerprints()

    assert run.made == ["Liora Fenwick"]
    namesakes = await _people_called(temp_db, "Liora Fenwick")
    assert len(namesakes) == 2
    made = namesakes[1]
    assert (made["created_by_kind"], made["created_by_via"]) == ("sift", "facial_fingerprints")
    assert len(await store.references(str(made["id"]))) == 2
    assert await store.references(box_made) == []
    assert not await store.unclaimed_entries()
    # The re-match after it names the faces as her.
    await service.rematch()
    named = await store.track(tracks[0])
    assert named is not None and named.person_id == made["id"]


async def test_an_entry_joins_the_person_whose_own_faces_match_whatever_she_is_called(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    preferences: FakePreferences,
) -> None:
    """Her confirmed faces match the faces the entry matches, so the entry is hers: its faces her
    references and its name one she also answers to. A claim is not a new person, so it happens
    with making people switched off too."""
    preferences.set(face_settings.PEOPLE_FROM_FILES_KEY, False)
    tamsin = await make_person(temp_db, "Tamsin")
    await _her_own(store, tamsin, person_vector(0))
    await _faces(store, clip.asset.id, [person_vector(0), person_vector(0, 1)])
    await service.rematch()
    await service.import_pack(a_pack(people=["Tamsin Vale"]))

    run = await service.recognize_from_fingerprints()

    assert (run.claimed, run.made) == (["Tamsin Vale"], [])
    assert len(await _people_called(temp_db, "Tamsin Vale")) == 0
    origins = [one.origin for one in await store.references(tamsin)]
    assert origins.count(FaceOrigin.PACK) == 2
    assert await store.aliases_of(tamsin) == ["Tamsin Vale"]


async def test_an_entry_nothing_matches_stays_held(
    service: FaceService, store: Store, temp_db: Database, clip: Ingested
) -> None:
    """Never a person without a face."""
    await service.import_pack(a_pack(people=["Orla Tennant"]))
    await _faces(store, clip.asset.id, [person_vector(7)])

    run = await service.recognize_from_fingerprints()

    assert (run.made, run.claimed, run.asked) == ([], [], 0)
    assert [str(row["name"]) for row in await store.unclaimed_entries()] == ["Orla Tennant"]


async def test_with_making_off_the_group_is_asked_and_a_yes_makes_the_person(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    preferences: FakePreferences,
) -> None:
    preferences.set(face_settings.PEOPLE_FROM_FILES_KEY, False)
    await service.import_pack(a_pack(people=["Wren Halloway"], confirmed=3))
    await _faces(store, clip.asset.id, [person_vector(0), person_vector(0, 1)])
    await service.regroup(full=True)

    run = await service.recognize_from_fingerprints()

    assert (run.made, run.asked) == ([], 1)
    assert not await _people_called(temp_db, "Wren Halloway")
    (offer,) = await service.fingerprint_offers()
    assert offer.name == "Wren Halloway"
    # What the question says the entry holds: its faces, the count its file gave, and the file.
    assert (offer.faces, offer.confirmed, offer.source) == (2, 3, "Sample set")
    piles = await temp_db.fetch_all("SELECT DISTINCT pile_id FROM face_tracks")
    assert {str(row["pile_id"]) for row in piles} == {offer.pile_id}

    admin = await create_user(temp_db, Role.ADMIN)
    person_id = await service.make_person_from_entry(offer.entry_id, by=admin.id)

    (made,) = await _people_called(temp_db, "Wren Halloway")
    assert made["id"] == person_id
    assert (made["created_by_kind"], made["created_by_user_id"]) == ("user", admin.id)
    assert await service.fingerprint_offers() == []
    # Turned on later, the pass finds nothing left to make.
    preferences.set(face_settings.PEOPLE_FROM_FILES_KEY, True)
    assert (await service.recognize_from_fingerprints()).made == []


async def test_turning_making_on_later_makes_the_held_entries_that_already_match(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    preferences: FakePreferences,
) -> None:
    preferences.set(face_settings.PEOPLE_FROM_FILES_KEY, False)
    await service.import_pack(a_pack(people=["Wren Halloway"]))
    await _faces(store, clip.asset.id, [person_vector(0)])
    assert (await service.recognize_from_fingerprints()).made == []

    preferences.set(face_settings.PEOPLE_FROM_FILES_KEY, True)

    assert (await service.recognize_from_fingerprints()).made == ["Wren Halloway"]


async def test_a_swaps_entries_are_never_placed_by_face(
    service: FaceService, store: Store, temp_db: Database, clip: Ingested
) -> None:
    """A swap's held faces are a suggestion about somebody already here: never a new namesake."""
    await make_person(temp_db, "Wren Halloway")
    await service.take_from_swap(
        a_pack(name=f"{store.SWAPPED_PACKS}session person", people=["Wren Halloway"]),
        suggest_only=True,
    )
    await _faces(store, clip.asset.id, [person_vector(0)])

    run = await service.recognize_from_fingerprints()

    assert (run.made, run.claimed) == ([], [])
    assert len(await _people_called(temp_db, "Wren Halloway")) == 1


async def test_a_re_import_names_the_folder_of_an_entry_made_before_folders_were_named(
    store: Store, temp_db: Database
) -> None:
    """An entry made before folders were named takes its folder from the next import of it; an
    entry that has a folder keeps the first."""
    folder = await store.folder_import_pack(RECOGNIZER)
    entry = await store.keep_pack_entry(pack_id=folder, name="Orla Tennant", aliases=(), links=())
    again = await store.keep_pack_entry(
        pack_id=folder, name="Orla Tennant", aliases=(), links=(), source="Gallery"
    )
    later = await store.keep_pack_entry(
        pack_id=folder, name="orla tennant", aliases=(), links=(), source="Other"
    )

    assert entry == again == later
    row = await temp_db.fetch_one("SELECT source FROM pack_entries WHERE id = ?", (entry,))
    assert row is not None and row["source"] == "Gallery"


async def test_a_folder_of_people_is_placed_by_face_and_its_faces_are_added_not_a_packs(
    service: FaceService, store: Store, temp_db: Database, clip: Ingested
) -> None:
    folder = await store.folder_import_pack(RECOGNIZER)
    entry = await store.keep_pack_entry(pack_id=folder, name="Orla Tennant", aliases=(), links=())
    await store.keep_entry_face(
        entry,
        vector=person_vector(5),
        quality=0.9,
        crop=b"\xff\xd8\xff folder",
        digest="folder-face",
        recognizer=RECOGNIZER,
    )
    await _faces(store, clip.asset.id, [person_vector(5)])

    assert (await service.recognize_from_fingerprints()).made == ["Orla Tennant"]

    (made,) = await _people_called(temp_db, "Orla Tennant")
    (reference,) = await store.references(str(made["id"]))
    assert reference.origin is FaceOrigin.ADDED


# --- the faces that made her are hers ------------------------------------------------------------


async def _filed_under(database: Database, asset_id: str) -> list[str]:
    rows = await database.fetch_all(
        "SELECT person_id FROM asset_people WHERE asset_id = ? ORDER BY person_id", (asset_id,)
    )
    return [str(row["person_id"]) for row in rows]


@pytest.mark.parametrize("grouped", [False, True], ids=["in no group", "in a group"])
async def test_a_person_made_from_faces_is_named_on_them_and_holds_their_files(
    service: FaceService, store: Store, temp_db: Database, clip: Ingested, grouped: bool
) -> None:
    """The faces that matched the entry are named as the person it made, MATCHED at the likeness
    they matched with, and the file they are in is filed under her, before any re-match: a person
    made from facial fingerprints never holds nothing. The re-match after it leaves each face
    named as it is."""
    await service.import_pack(a_pack(people=["Liora Fenwick"]))
    tracks = await _faces(store, clip.asset.id, [person_vector(0), person_vector(0, 1)])
    if grouped:
        await service.regroup(full=True)
    piles = {one.pile_id for one in (await store.tracks(tracks)).values()}
    assert (piles != {None}) is grouped

    assert (await service.recognize_from_fingerprints()).made == ["Liora Fenwick"]

    (made,) = await _people_called(temp_db, "Liora Fenwick")
    for track_id in tracks:
        named = await store.track(track_id)
        assert named is not None
        assert (named.person_id, named.attribution) == (made["id"], Attribution.MATCHED)
        assert named.confidence is not None and named.confidence >= 0.6
    assert await _filed_under(temp_db, clip.asset.id) == [made["id"]]
    assert await store.claimed_people(clip.asset.id) == [made["id"]]
    await service.rematch()
    again = await store.track(tracks[0])
    assert again is not None and again.attribution is Attribution.MATCHED


async def test_a_question_the_entry_matches_is_named_and_a_face_only_near_it_makes_nobody(
    service: FaceService, store: Store, temp_db: Database, clip: Ingested, other_clip: Ingested
) -> None:
    """A face asked about somebody else is a question, not a match, so the entry it matches
    names it; a face under the entry's bar is no match at all, and an entry with only such a face
    makes nobody."""
    tamsin = await make_person(temp_db, "Tamsin")
    await service.import_pack(a_pack(people=["Liora Fenwick", "Orla Tennant"]))
    (asked,) = await _faces(store, clip.asset.id, [person_vector(0)])
    await store.attribute(asked, tamsin, confidence=0.45, attribution=Attribution.SUGGESTED)
    # Half way to Orla Tennant's faces: well under her bar.
    (near,) = await _faces(
        store, other_clip.asset.id, [unit([0.0, 0.5, 0.0, 0.0, 0.86, 0.0, 0.0, 0.0])]
    )

    run = await service.recognize_from_fingerprints()

    assert run.made == ["Liora Fenwick"]
    (made,) = await _people_called(temp_db, "Liora Fenwick")
    named = await store.track(asked)
    assert named is not None and (named.person_id, named.attribution) == (
        made["id"],
        Attribution.MATCHED,
    )
    assert not await _people_called(temp_db, "Orla Tennant")
    left = await store.track(near)
    assert left is not None and left.person_id is None
    assert await _filed_under(temp_db, other_clip.asset.id) == []


async def test_a_claim_names_her_on_the_faces_nobody_had_and_a_second_run_names_nothing(
    service: FaceService, store: Store, temp_db: Database, clip: Ingested, other_clip: Ingested
) -> None:
    """Given to the person whose own faces match, the entry names her on its other faces too and
    files their file under her, all but a face somebody said is not her. Run again, the pass finds
    the entry spent and writes nothing."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    tamsin = await make_person(temp_db, "Tamsin")
    await _her_own(store, tamsin, person_vector(0))
    await _faces(store, clip.asset.id, [person_vector(0)])
    await service.rematch()
    fresh, refused = await _faces(
        store, other_clip.asset.id, [person_vector(0, 1), person_vector(0, 2)]
    )
    assert await store.reject(refused, tamsin)
    await service.import_pack(a_pack(people=["Tamsin Vale"]))

    assert (await service.recognize_from_fingerprints()).claimed == ["Tamsin Vale"]
    named = await store.track(fresh)
    assert named is not None and (named.person_id, named.attribution) == (
        tamsin,
        Attribution.MATCHED,
    )
    assert await _filed_under(temp_db, other_clip.asset.id) == [tamsin]
    kept_off = await store.track(refused)
    assert kept_off is not None and kept_off.person_id is None, "a face refused for her was named"

    run = await service.recognize_from_fingerprints()
    assert (run.made, run.claimed) == ([], [])
    receipts, _total = await written.recent(limit=10, offset=0)
    assert len([one for one in receipts if one.queue == FINGERPRINTS_QUEUE]) == 1


async def test_an_entry_another_write_placed_first_gives_her_nothing(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pass and a press on one entry: the write that lands second writes nothing at all."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    tamsin = await make_person(temp_db, "Tamsin")
    await _her_own(store, tamsin, person_vector(0))
    await _faces(store, clip.asset.id, [person_vector(0), person_vector(0, 1)])
    await service.rematch()
    await service.import_pack(a_pack(people=["Tamsin Vale"]))
    overtaken(service, store, monkeypatch, by=await make_person(temp_db, "Tamsin Vale"))

    assert (await service.recognize_from_fingerprints()).claimed == []

    assert {one.origin for one in await store.references(tamsin)} == {FaceOrigin.CONFIRMED}
    assert await store.aliases_of(tamsin) == []
    receipts, _total = await written.recent(limit=10, offset=0)
    assert [one for one in receipts if one.queue == FINGERPRINTS_QUEUE] == []


async def test_a_face_answered_while_the_pass_was_placing_it_keeps_the_answer(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read as nobody's, then confirmed as somebody before the pass wrote: it stays hers."""
    tamsin = await make_person(temp_db, "Tamsin")
    await service.import_pack(a_pack(people=["Liora Fenwick"]))
    first, answered = await _faces(store, clip.asset.id, [person_vector(0), person_vector(0, 1)])
    placements = service._placements

    async def then_answered(recognizer: str) -> list[Placement]:
        placed = await placements(recognizer)
        await store.attribute(answered, tamsin, confidence=1.0, attribution=Attribution.CONFIRMED)
        return placed

    monkeypatch.setattr(service, "_placements", then_answered)

    assert (await service.recognize_from_fingerprints()).made == ["Liora Fenwick"]

    (made,) = await _people_called(temp_db, "Liora Fenwick")
    named = await store.track(first)
    assert named is not None and named.person_id == made["id"]
    kept = await store.track(answered)
    assert kept is not None and (kept.person_id, kept.attribution) == (
        tamsin,
        Attribution.CONFIRMED,
    )


async def test_a_file_already_filed_under_her_is_not_sent_to_the_search_index_again(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    reindexer: RecordingReindexer,
) -> None:
    """Somebody filed it under her by hand: naming her face there moves none of its People."""
    tamsin = await make_person(temp_db, "Tamsin")
    await _her_own(store, tamsin, person_vector(0))
    await _faces(store, clip.asset.id, [person_vector(0)])
    await service.rematch()
    (fresh,) = await _faces(store, other_clip.asset.id, [person_vector(0, 1)])
    async with temp_db.write() as connection:
        await connection.execute(
            "INSERT INTO asset_people (asset_id, person_id, decided_at) VALUES (?, ?, 0)",
            (other_clip.asset.id, tamsin),
        )
    await service.import_pack(a_pack(people=["Tamsin Vale"]))
    reindexer.touched_ids.clear()

    assert (await service.recognize_from_fingerprints()).claimed == ["Tamsin Vale"]

    named = await store.track(fresh)
    assert named is not None and named.person_id == tamsin
    assert await _filed_under(temp_db, other_clip.asset.id) == [tamsin]
    assert other_clip.asset.id not in reindexer.touched_ids


async def test_a_yes_makes_only_the_entry_asked_about_and_names_only_its_faces(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    preferences: FakePreferences,
) -> None:
    preferences.set(face_settings.PEOPLE_FROM_FILES_KEY, False)
    await service.import_pack(a_pack(people=["Wren Halloway", "Neve Arbor"]))
    (wren,) = await _faces(store, clip.asset.id, [person_vector(0)])
    (neve,) = await _faces(store, other_clip.asset.id, [person_vector(1)])
    held = {str(row["name"]): str(row["id"]) for row in await store.unclaimed_entries()}
    admin = await create_user(temp_db, Role.ADMIN)

    made = await service.make_person_from_entry(held["Wren Halloway"], by=admin.id)

    named = await store.track(wren)
    assert made is not None and named is not None and named.person_id == made
    left = await store.track(neve)
    assert left is not None and left.person_id is None
    assert [str(row["name"]) for row in await store.unclaimed_entries()] == ["Neve Arbor"]


# --- the record and its Undo ----------------------------------------------------------------------


async def test_undo_of_a_person_made_takes_her_and_her_names_back_and_keeps_the_entry_held(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    preferences: FakePreferences,
) -> None:
    written = WorkbenchStore(temp_db)
    service._recorder = written
    await service.import_pack(
        a_pack(people=["Liora Fenwick"], aliases={"Liora Fenwick": ("Riss",)})
    )
    tracks = await _faces(store, clip.asset.id, [person_vector(0), person_vector(0, 1)])
    await service.recognize_from_fingerprints()
    (made,) = await _people_called(temp_db, "Liora Fenwick")
    await service.rematch()
    receipts, _total = await written.recent(limit=10, offset=0)
    (receipt,) = [one for one in receipts if one.queue == FINGERPRINTS_QUEUE]
    payload = json.loads(receipt.payload)
    assert (payload["act"], payload["person_id"], payload["aliases"]) == (
        "made",
        made["id"],
        ["Riss"],
    )
    assert sorted(one["track"] for one in payload["named"]) == sorted(tracks)
    assert await _filed_under(temp_db, clip.asset.id) == [made["id"]]

    admin = await create_user(temp_db, Role.ADMIN)
    undone = await FingerprintRecords(service).reverse(admin, receipt.id, receipt.payload)

    assert not isinstance(undone, bool) and undone.put_back == 1
    assert not await _people_called(temp_db, "Liora Fenwick"), "the person stayed"
    assert await store.alias_owner("Riss") is None
    back = await store.track(tracks[0])
    assert back is not None and back.person_id is None
    assert await _filed_under(temp_db, clip.asset.id) == [], "her file stayed filed"
    assert await store.claimed_people(clip.asset.id) == []
    assert await store.references(str(made["id"])) == []
    # Held again, offered to somebody adding her by hand, and left alone by the next pass.
    assert [str(row["name"]) for row in await store.unclaimed_entries("Liora Fenwick")] == [
        "Liora Fenwick"
    ]
    assert (await service.recognize_from_fingerprints()).made == []
    # Nor asked about on the group while making people is off.
    preferences.set(face_settings.PEOPLE_FROM_FILES_KEY, False)
    await service.regroup(full=True)
    assert await service.fingerprint_offers() == []


async def test_undo_of_a_claim_takes_the_references_and_the_name_back_and_keeps_her(
    service: FaceService, store: Store, temp_db: Database, clip: Ingested
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
    worded = FingerprintRecords(service).worded(_recorded(receipt))
    assert worded is not None and " added the facial fingerprints of Tamsin Vale to " in worded.said

    admin = await create_user(temp_db, Role.ADMIN)
    await FingerprintRecords(service).reverse(admin, receipt.id, receipt.payload)

    assert len(await _people_called(temp_db, "Tamsin")) == 1
    assert {one.origin for one in await store.references(tamsin)} == {FaceOrigin.CONFIRMED}
    assert await store.aliases_of(tamsin) == []
    assert (await service.recognize_from_fingerprints()).claimed == []


async def test_undo_that_puts_every_face_back_with_somebody_groups_nothing(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a face back with nobody wants a group; one back as a question about her has a home."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    tamsin = await make_person(temp_db, "Tamsin")
    await service.import_pack(a_pack(people=["Liora Fenwick"]))
    (asked,) = await _faces(store, clip.asset.id, [person_vector(0)])
    await store.attribute(asked, tamsin, confidence=0.45, attribution=Attribution.SUGGESTED)
    assert (await service.recognize_from_fingerprints()).made == ["Liora Fenwick"]
    receipts, _total = await written.recent(limit=10, offset=0)
    (receipt,) = [one for one in receipts if one.queue == FINGERPRINTS_QUEUE]
    regrouped: list[bool] = []

    async def regroup(*, full: bool = False) -> None:
        regrouped.append(full)

    monkeypatch.setattr(service, "regroup", regroup)
    admin = await create_user(temp_db, Role.ADMIN)

    await FingerprintRecords(service).reverse(admin, receipt.id, receipt.payload)

    back = await store.track(asked)
    assert back is not None and (back.person_id, back.attribution) == (
        tamsin,
        Attribution.SUGGESTED,
    )
    assert regrouped == []


async def test_undo_skips_what_it_cannot_read_in_the_record_and_puts_back_the_rest(
    service: FaceService, store: Store, temp_db: Database, clip: Ingested
) -> None:
    """A History line outlives the version that wrote it: an unreadable face costs only itself."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    await service.import_pack(a_pack(people=["Liora Fenwick"]))
    tracks = await _faces(store, clip.asset.id, [person_vector(0), person_vector(0, 1)])
    await service.recognize_from_fingerprints()
    receipts, _total = await written.recent(limit=10, offset=0)
    (receipt,) = [one for one in receipts if one.queue == FINGERPRINTS_QUEUE]
    payload = json.loads(receipt.payload)
    payload["named"] = ["unreadable", {"track": 7}, *payload["named"]]
    admin = await create_user(temp_db, Role.ADMIN)

    undone = await FingerprintRecords(service).reverse(admin, receipt.id, json.dumps(payload))

    assert not isinstance(undone, bool) and undone.put_back == 1
    for track_id in tracks:
        back = await store.track(track_id)
        assert back is not None and back.person_id is None


def _recorded(receipt: Decision) -> Recorded:
    return Recorded(
        id=receipt.id,
        queue=receipt.queue,
        payload=receipt.payload,
        title=receipt.title,
        detail=receipt.detail,
        decided_at=0,
    )


# --- what asks for the pass -----------------------------------------------------------------------


async def test_a_scan_asks_for_the_pass_only_when_a_face_of_the_file_matches_an_entry(
    service: FaceService, store: Store, clip: Ingested, other_clip: Ingested
) -> None:
    assert not await service.fingerprints_match_file(clip.asset.id), "nothing is held yet"
    await service.import_pack(a_pack(people=["Orla Tennant"]))
    await _faces(store, clip.asset.id, [person_vector(0)])
    await _faces(store, other_clip.asset.id, [person_vector(6)])

    assert await service.fingerprints_match_file(clip.asset.id)
    assert not await service.fingerprints_match_file(other_clip.asset.id)


async def test_the_run_matches_again_once_somebody_was_placed(
    service: FaceService, store: Store, clip: Ingested, monkeypatch: pytest.MonkeyPatch
) -> None:
    await service.import_pack(a_pack(people=["Orla Tennant"]))
    await _faces(store, clip.asset.id, [person_vector(0)])
    asked: list[int] = []
    notes: list[str] = []

    async def rematching(_queue: object, *, delay: int = 0, **_: object) -> None:
        asked.append(delay)

    monkeypatch.setattr(face_jobs, "ask_for_rematching", rematching)

    class _Context:
        queue = object()

        async def set_progress(self, _done: float) -> None:
            return None

        async def set_note(self, note: str) -> None:
            notes.append(note)

    await face_jobs.people_from_files(_Context(), service=service)  # type: ignore[arg-type]
    await face_jobs.people_from_files(_Context(), service=service)  # type: ignore[arg-type]

    assert asked == [0], "one re-match for the run that placed somebody, none for the empty one"
    assert notes == ["Added 1 person", "No faces matched the facial fingerprints"]


def test_making_people_from_fingerprints_is_on_by_default() -> None:
    from sift.kernel.settings_registry import get_registered

    declared = get_registered(face_settings.PEOPLE_FROM_FILES_KEY)
    assert declared is not None and declared.default is True
    assert declared.label == "Create people from these fingerprints as their faces are recognized"


# --- end to end -----------------------------------------------------------------------------------


async def test_a_folder_of_three_people_is_held_then_claimed_made_and_left_held_by_face(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The whole road on the test rig: a folder of three people is held as three entries and
    asks nothing; the pass gives one to the person here whose own faces match it (under another
    name), makes one a new person where the faces are nobody's, and leaves one held that no face
    matches. Each person made or given one is one History line."""
    shades = {"Tamsin Vale": 40, "Liora Fenwick": 130, "Orla Tennant": 220}
    root = tmp_path / "references"
    for name in shades:
        (root / name).mkdir(parents=True)
        (root / name / "one.jpg").write_bytes(name.encode())

    async def decode(path: Path, settings: Settings, **_: object) -> np.ndarray:
        frame = noisy_frame(400, 400, seed=3)
        box = draw_face(frame, x=80, y=80, size=200, shade=shades[path.parent.name])
        detector.placed = {0: [(box, 0.9)]}
        return frame

    monkeypatch.setattr("sift.slices.faces.frames.decode_image", decode)
    # Each folder's face is told apart by how bright it was drawn: one person per brightness.
    recognizer.rule = lambda chip: person_vector(min(int(float(chip.mean()) // 86), 2) + 4)

    written = WorkbenchStore(temp_db)
    service._recorder = written
    tamsin = await make_person(temp_db, "Tamsin")
    await _her_own(store, tamsin, person_vector(4))
    await _faces(store, clip.asset.id, [person_vector(4), person_vector(5), person_vector(5, 1)])
    await service.rematch()

    reports = await import_folder(service, root)

    assert sorted(report.added for report in reports) == [1, 1, 1]
    assert len(await store.unclaimed_entries()) == 3
    # Each says the folder of people it came from, never the one pack every folder shares.
    assert {str(one["source"]) for one in await service.waiting()} == {"references"}
    assert await store.references(tamsin) and len(await store.references(tamsin)) == 3

    run = await service.recognize_from_fingerprints()

    assert (run.claimed, run.made, run.asked) == (["Tamsin Vale"], ["Liora Fenwick"], 0)
    assert len(await store.references(tamsin)) == 4
    assert [str(row["name"]) for row in await store.unclaimed_entries()] == ["Orla Tennant"]
    receipts, _total = await written.recent(limit=20, offset=0)
    assert len([one for one in receipts if one.queue == FINGERPRINTS_QUEUE]) == 2
    # The person it made says the folder too, under her name on the known list.
    (liora,) = await _people_called(temp_db, "Liora Fenwick")
    assert await service.created_from([str(liora["id"])]) == {str(liora["id"]): "references"}
