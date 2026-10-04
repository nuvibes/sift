# SPDX-License-Identifier: AGPL-3.0-or-later
"""Starter pictures from a stash-box: they may make Sift ASK about somebody, and nothing more.

A person linked to a stash-box and never named in this library has no reference, so Sift cannot
recognize her at all. A starter is one of the box's photos of her, checked like a picture in an
imported folder and filed with the `seed` origin. Every rule below is one half of what keeps that
safe (a link is made on a NAME, so a same-name stranger's photos arrive exactly as hers would),
and each test is the one a mutation of that rule turns red.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

import sift.slices.workbench.schema  # noqa: F401 (its tables hold the receipts)
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.ingress import Origin, verify_ingress
from sift.slices.faces import matching, packs, tuning
from sift.slices.faces import settings as face_settings
from sift.slices.faces.models import (
    Appearance,
    Attribution,
    Box,
    Described,
    Detection,
    Match,
    Quality,
    ScanStatus,
    Vector,
)
from sift.slices.faces.models import Origin as FaceOrigin
from sift.slices.faces.service import FACE_STARTERS, STARTERS_QUEUE, FaceService, Recognition
from sift.slices.faces.store import PassRecord, Store
from sift.slices.faces.tests.conftest import (
    FakeDetector,
    FakePreferences,
    draw_face,
    make_person,
    noisy_frame,
    person_vector,
)
from sift.slices.workbench.store import Store as WorkbenchStore

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"
RECOGNIZER = "test-recognizer"


@pytest.fixture
async def library(library_store: LibraryStore, tmp_path: Path) -> Root:
    directory = tmp_path / "library"
    directory.mkdir()
    return await library_store.create_root(name="Clips", abs_path=directory)


@pytest.fixture
async def picture(content_store: ContentStore, library: Root, settings: Settings) -> Ingested:
    target = Path(str(library.abs_path)) / "still.jpg"
    target.write_bytes((CORPUS / "accepted.jpg").read_bytes())
    checked = verify_ingress(target, origin=Origin.SCAN, settings=settings)
    return await content_store.ingest(checked, root_id=library.id, rel_path="still.jpg")


async def _face(store: Store, asset_id: str, vector: Vector) -> str:
    """One appearance on the file, described by `vector`, with nobody on it."""
    (track_id,) = await store.replace_pass(
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
        ],
        [[b"\xff\xd8\xff picture"]],
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
    return track_id


async def _reference(
    store: Store, person_id: str, vector: Vector, origin: FaceOrigin, name: str
) -> str:
    written = await store.add_reference(
        person_id,
        vector=vector,
        quality=1.0,
        crop=f"picture-{person_id}-{name}".encode(),
        origin=origin,
        recognizer=RECOGNIZER,
        source="FansDB" if origin is FaceOrigin.SEED else None,
    )
    assert written is not None
    return written


async def _retired(database: Database, reference_id: str) -> bool:
    row = await database.fetch_one(
        "SELECT retired_at FROM face_references WHERE id = ?", (reference_id,)
    )
    assert row is not None
    return row["retired_at"] is not None


# --- the trust rule -------------------------------------------------------------------------------


async def test_a_person_known_only_by_starters_is_asked_about_and_never_named(
    service: FaceService, store: Store, person: str, picture: Ingested
) -> None:
    """The same picture found in the library scores at the top of the scale, and is still ASKED.

    That is the case the rule exists for: a stash-box's promotional photo is very often a file in
    the library too, so a starter's likeliest first match is a perfect one.
    """
    await _reference(store, person, person_vector(0), FaceOrigin.SEED, "a")
    track_id = await _face(store, picture.asset.id, person_vector(0))

    assert await service.rematch() == 1

    asked = await store.track(track_id)
    assert asked is not None
    assert (asked.person_id, asked.attribution) == (person, Attribution.SUGGESTED)


async def test_her_own_picture_lets_the_same_match_name_her(
    service: FaceService, store: Store, person: str, picture: Ingested
) -> None:
    """The control for the test above: the one thing that differs is where the picture came from."""
    await _reference(store, person, person_vector(0), FaceOrigin.ADDED, "a")
    track_id = await _face(store, picture.asset.id, person_vector(0))

    assert await service.rematch() == 1

    named = await store.track(track_id)
    assert named is not None and named.attribution is Attribution.MATCHED


def test_the_top_of_the_scale_never_attaches_even_past_one() -> None:
    """Single-precision arithmetic can put a perfect match a hair over one."""
    over = Match(person_id="p", confidence=1.0000001)

    assert matching.verdict(over, attach_above=tuning.ALWAYS_ASK) is Attribution.SUGGESTED


def test_the_gallery_names_who_is_described_by_starters_alone() -> None:
    from sift.slices.faces.models import Reference

    def one(person_id: str, origin: FaceOrigin) -> Reference:
        return Reference(
            id=f"{person_id}-{origin.value}",
            person_id=person_id,
            vector=person_vector(0),
            quality=1.0,
            crop_digest=origin.value,
            origin=origin,
        )

    gallery = matching.build_gallery(
        {"starters": [one("starters", FaceOrigin.SEED)], "own": [one("own", FaceOrigin.ADDED)]}
    )

    assert gallery.starters_only == frozenset({"starters"})


# --- retiring -------------------------------------------------------------------------------------


async def test_her_own_reference_retires_her_starters(
    store: Store, person: str, temp_db: Database
) -> None:
    starter = await _reference(store, person, person_vector(0), FaceOrigin.SEED, "a")
    stamp = await store.reference_stamp()

    await _reference(store, person, person_vector(0, 1), FaceOrigin.CONFIRMED, "b")

    assert await _retired(temp_db, starter) is True
    gallery = await store.reference_gallery(RECOGNIZER)
    assert [one.origin for one in gallery[person]] == [FaceOrigin.CONFIRMED]
    assert await store.reference_stamp() != stamp


async def test_a_no_about_her_retires_her_starters(
    service: FaceService, store: Store, person: str, picture: Ingested, temp_db: Database
) -> None:
    starter = await _reference(store, person, person_vector(0), FaceOrigin.SEED, "a")
    track_id = await _face(store, picture.asset.id, person_vector(0))
    await service.rematch()
    stamp = await store.reference_stamp()

    await service.reject(track_id, person)

    assert await _retired(temp_db, starter) is True
    # And the gallery built against the stamp learns it: retiring is an UPDATE, which moves
    # neither the count nor the newest id.
    assert await store.reference_stamp() != stamp
    assert person not in await store.reference_gallery(RECOGNIZER)


async def test_the_gallery_leaves_starters_out_beside_her_own(
    store: Store, person: str, temp_db: Database
) -> None:
    """A merge moves references without passing either writer that retires, so the read holds."""
    other = await make_person(temp_db, "Nadia Vance")
    await _reference(store, other, person_vector(0), FaceOrigin.SEED, "a")
    await _reference(store, person, person_vector(1), FaceOrigin.ADDED, "b")
    await temp_db.execute(
        "UPDATE face_references SET person_id = ? WHERE person_id = ?", (person, other)
    )

    gallery = await store.reference_gallery(RECOGNIZER)

    assert [one.origin for one in gallery[person]] == [FaceOrigin.ADDED]


# --- counted apart --------------------------------------------------------------------------------


async def test_starters_are_never_counted_as_what_sift_knows_her_by(
    service: FaceService, store: Store, person: str
) -> None:
    await _reference(store, person, person_vector(0), FaceOrigin.SEED, "a")

    assert await store.reference_count(person) == 0
    assert await store.reference_counts() == {}
    assert await store.reference_counts(recognizer=RECOGNIZER) == {}
    strength = await service.recognition_of(person)
    assert (strength.references, strength.starters, strength.starters_from) == (0, 1, ("FansDB",))
    ((_, _, faces, starters),) = await service.roster()
    assert (faces, starters) == (0, 1)


async def test_a_file_whose_face_matches_nobody_is_no_disagreement_whatever_her_pictures(
    store: Store, person: str, picture: Ingested, temp_db: Database
) -> None:
    """Filed under her by a folder, one face nobody is on: no evidence against the filing, from
    starters or her own pictures alike. Named as somebody else, it is one."""
    track = await _face(store, picture.asset.id, person_vector(5))
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, 0)",
        (picture.asset.id, person, Store.FILED_BY_A_PASS[0]),
    )
    await _reference(store, person, person_vector(0), FaceOrigin.SEED, "a")
    assert await store.filed_but_unrecognised(RECOGNIZER, most=10) == []
    await _reference(store, person, person_vector(0, 1), FaceOrigin.ADDED, "b")
    assert await store.filed_but_unrecognised(RECOGNIZER, most=10) == []

    elsewhere = await make_person(temp_db, "Wren Hale")
    await store.attribute(track, elsewhere, confidence=0.8, attribution=Attribution.MATCHED)
    assert len(await store.filed_but_unrecognised(RECOGNIZER, most=10)) == 1


async def test_a_pack_never_carries_a_starter_out(
    service: FaceService, store: Store, person: str
) -> None:
    """Read back, a pack's faces are references somebody chose, trusted to name her."""
    await _reference(store, person, person_vector(0), FaceOrigin.SEED, "a")

    raw = await service.export_pack(
        name="Mine", version="1", person_ids=[person], include_pictures=False
    )

    assert packs.read(raw, expect_recognizer=RECOGNIZER).people == ()


# --- filing them ------------------------------------------------------------------------------------


def _decoding(monkeypatch: pytest.MonkeyPatch, frame: np.ndarray) -> None:
    async def decode(blob: bytes, settings: Settings, **_: object) -> np.ndarray | None:
        return frame

    monkeypatch.setattr("sift.slices.faces.frames.decode_picture_bytes", decode)


async def test_a_box_picture_with_one_face_is_filed_as_a_starter_from_that_box(
    monkeypatch: pytest.MonkeyPatch,
    service: FaceService,
    store: Store,
    person: str,
    detector: FakeDetector,
) -> None:
    frame = noisy_frame(400, 400, seed=2)
    box = draw_face(frame, x=80, y=80, size=240)
    _decoding(monkeypatch, frame)
    detector.placed = {0: [(box, 0.9)]}

    filed = await service.file_starters(person, [("FansDB", b"one")])

    assert len(filed) == 1
    assert (await store.starters_of(person)) == (1, 0, ("FansDB",))
    assert await service.wants_starters([person]) == []


async def test_a_group_photo_from_a_box_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    service: FaceService,
    store: Store,
    person: str,
    detector: FakeDetector,
) -> None:
    frame = noisy_frame(400, 400, seed=3)
    first = draw_face(frame, x=20, y=100, size=140)
    second = draw_face(frame, x=220, y=100, size=140)
    _decoding(monkeypatch, frame)
    detector.placed = {0: [(first, 0.9), (second, 0.9)]}

    assert await service.file_starters(person, [("FansDB", b"two")]) == []
    assert await service.wants_starters([person]) == [person]


async def test_somebody_whose_every_box_picture_was_refused_leaves_the_count_until_one_passes(
    monkeypatch: pytest.MonkeyPatch,
    service: FaceService,
    store: Store,
    person: str,
    detector: FakeDetector,
) -> None:
    """Without a note, "Starters for 22 people" would stay 22 for ever, every Run fetching the same
    pictures and refusing them again. The refusal is kept where the count reads it; a later picture
    that passes files a starter and takes the note away."""
    frame = noisy_frame(400, 400, seed=3)
    first = draw_face(frame, x=20, y=100, size=140)
    second = draw_face(frame, x=220, y=100, size=140)
    _decoding(monkeypatch, frame)
    detector.placed = {0: [(first, 0.9), (second, 0.9)]}
    assert await service.starters_wanted([person]) == [person]

    assert await service.file_starters(person, [("FansDB", b"two")]) == []

    assert await service.starters_wanted([person]) == []
    # Still nobody Sift knows: a press made for her by name looks again.
    assert await service.wants_starters([person]) == [person]

    single = noisy_frame(400, 400, seed=2)
    _decoding(monkeypatch, single)
    detector.placed = {0: [(draw_face(single, x=80, y=80, size=240), 0.9)]}
    assert len(await service.file_starters(person, [("FansDB", b"one")])) == 1
    assert await store.starters_refused([person], recognizer="any") == set()
    row = await store.database.fetch_one("SELECT COUNT(*) AS n FROM face_starter_refusals", ())
    assert row is not None and row["n"] == 0


async def test_no_picture_on_any_box_leaves_the_count_like_a_refusal(
    service: FaceService, store: Store, person: str
) -> None:
    """An empty list is every box answering with no picture of her (a box that could not be asked
    hands the job None, and the job never calls this). Unnoted, two such People would keep
    "starters for 2 people" after a Run that refused everybody else's pictures."""
    assert await service.starters_wanted([person]) == [person]

    assert await service.file_starters(person, []) == []

    assert await service.starters_wanted([person]) == []
    row = await store.database.fetch_one(
        "SELECT pictures FROM face_starter_refusals WHERE person_id = ?", (person,)
    )
    assert row is not None and row["pictures"] == 0


async def test_somebody_with_a_reference_is_given_no_starters(
    monkeypatch: pytest.MonkeyPatch,
    service: FaceService,
    store: Store,
    person: str,
    detector: FakeDetector,
) -> None:
    """Nor somebody whose starters were retired: that row is the memory of a "no"."""
    await _reference(store, person, person_vector(0), FaceOrigin.ADDED, "a")
    frame = noisy_frame(400, 400, seed=2)
    detector.placed = {0: [(draw_face(frame, x=80, y=80, size=240), 0.9)]}
    _decoding(monkeypatch, frame)

    assert await service.file_starters(person, [("FansDB", b"one")]) == []


class _Queue:
    def __init__(self) -> None:
        self.queued: list[tuple[str, dict[str, object]]] = []

    async def enqueue_when_settled(
        self, job_type: str, payload: dict[str, object], **_: object
    ) -> str:
        self.queued.append((job_type, dict(payload)))
        return "job"


async def test_a_link_queues_starters_only_for_somebody_with_no_reference(
    service: FaceService, store: Store, person: str, temp_db: Database
) -> None:
    queue = _Queue()
    heard = Recognition(service, queue=queue)  # type: ignore[arg-type]
    known = await make_person(temp_db, "Nadia Vance")
    await _reference(store, known, person_vector(0), FaceOrigin.ADDED, "a")

    await heard.linked(person)
    await heard.linked(known)

    # One run asked for, collapsed with any other link of the same burst, over everybody linked
    # since starters existed, never a task per person.
    assert queue.queued == [(FACE_STARTERS, {"linked": True})]


async def test_a_link_asks_for_nothing_where_there_is_no_queue_or_the_feature_is_off(
    service: FaceService, person: str, preferences: FakePreferences
) -> None:
    """Quiet rather than refused: a person linked on an install that never turned recognition on
    must simply be linked."""
    queue = _Queue()
    await Recognition(service).linked(person)
    preferences.set(face_settings.ENABLED_KEY, False)
    await Recognition(service, queue=queue).linked(person)  # type: ignore[arg-type]

    assert queue.queued == []


async def test_a_run_of_starters_is_one_record_naming_who_got_them_and_from_which_boxes(
    service: FaceService, store: Store, person: str, temp_db: Database
) -> None:
    """One History record for the run, about the People given starters (not somebody who got
    none), with the boxes named in the title and kept in the payload."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    nobody = await make_person(temp_db, "Nadia Vance")
    first = await _reference(store, person, person_vector(0), FaceOrigin.SEED, "a")
    second = await _reference(store, person, person_vector(0, 3), FaceOrigin.SEED, "b")

    await service.record_starters({person: [first, second], nobody: []}, ["StashDB", "FansDB"])

    (receipt,), total = await written.recent(limit=5, offset=0)
    assert total == 1
    assert receipt.queue == STARTERS_QUEUE
    assert receipt.title == "Added 2 starter pictures from FansDB and StashDB for 1 person"
    assert json.loads(receipt.payload) == {
        "references": {person: [first, second]},
        "boxes": ["FansDB", "StashDB"],
    }


async def test_a_run_that_filed_nothing_or_has_nowhere_to_write_records_nothing(
    service: FaceService, person: str, temp_db: Database
) -> None:
    await service.record_starters({person: ["01REF"]}, ["FansDB"])
    written = WorkbenchStore(temp_db)
    service._recorder = written

    await service.record_starters({person: []}, ["FansDB"])

    _receipts, total = await written.recent(limit=5, offset=0)
    assert total == 0


async def test_undoing_a_run_retires_its_starters_once_and_never_her_own_picture(
    service: FaceService, store: Store, person: str, temp_db: Database
) -> None:
    """What the record's Undo does: the starters it filed are retired, a second press moves nothing,
    and an id naming her own reference is not a starter to retire."""
    starter = await _reference(store, person, person_vector(0), FaceOrigin.SEED, "a")
    own = await make_person(temp_db, "Nadia Vance")
    hers = await _reference(store, own, person_vector(1), FaceOrigin.ADDED, "b")

    assert await service.retire_starters([starter, hers]) == 1
    assert await service.retire_starters([starter]) == 0

    assert await _retired(temp_db, starter)
    assert not await _retired(temp_db, hers)
