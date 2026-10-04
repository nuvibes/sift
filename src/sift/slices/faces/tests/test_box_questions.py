# SPDX-License-Identifier: AGPL-3.0-or-later
"""The question a stash-box's answer asks: the one face in a file a box put somebody on, offered as
theirs where Sift has no picture of them to compare, and never named on it."""

from __future__ import annotations

import json

import pytest

import sift.slices.workbench.schema  # noqa: F401 (the receipts' table, for the record)
from sift.kernel.content import Ingested
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.workbench import DOER, Named, Recorded
from sift.slices.faces import jobs as face_jobs
from sift.slices.faces import recognize
from sift.slices.faces import settings as face_settings
from sift.slices.faces.frames import Frame
from sift.slices.faces.models import AskedBy, Attribution
from sift.slices.faces.receipts import BOX_QUESTIONS_QUEUE
from sift.slices.faces.service import FaceService
from sift.slices.faces.service_box import BOX_QUESTIONS_DETAIL, BoxQuestionRecords
from sift.slices.faces.store import Store
from sift.slices.faces.tests.conftest import (
    FakeDetector,
    FakePreferences,
    FakeRecognizer,
    draw_face,
    make_person,
    noisy_frame,
    person_vector,
)
from sift.slices.faces.tests.test_disagreements import FOLDER, ROOT, _filed
from sift.slices.faces.tests.test_jobs import Context
from sift.slices.faces.tests.test_service import (  # noqa: F401 (fixtures)
    Scripted,
    clip,
    install_reader,
    library,
    restore_pipeline,
)
from sift.slices.workbench.store import Store as WorkbenchStore

pytestmark = pytest.mark.integration


@pytest.fixture
async def folder(temp_db: Database, service: FaceService) -> Database:
    """The folder every file here sits in, as the Disagreements tests lay it out."""
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, 'Media', ?, 0)",
        (ROOT, "/library/media"),
    )
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) "
        "VALUES (?, ?, NULL, '', 'Media')",
        (FOLDER, ROOT),
    )
    return temp_db


async def _state(temp_db: Database, track: str) -> tuple[object, object, object]:
    row = await temp_db.fetch_one(
        "SELECT person_id, attribution, asked_by FROM face_tracks WHERE id = ?", (track,)
    )
    assert row is not None
    return row["person_id"], row["attribution"], row["asked_by"]


async def _reference(temp_db: Database, service: FaceService, person: str, origin: str) -> None:
    recognizer = (await service.configuration()).recognizer
    await temp_db.execute(
        "INSERT INTO face_references (id, person_id, crop_path, crop_digest, embedding, quality, "
        "origin, recognizer, created_at) VALUES (?, ?, 'crop.jpg', ?, ?, 1.0, ?, ?, 0)",
        (
            new_id(),
            person,
            f"ref-{person}-{origin}",
            recognize.pack(person_vector(3)),
            origin,
            recognizer,
        ),
    )


async def test_the_one_face_in_a_file_a_box_filed_is_asked_about_them_once(
    folder: Database, service: FaceService
) -> None:
    """A question the box asks, never a name: Needs your input, not Confirmed or Recognized."""
    mia = await make_person(folder, "Mira Solvane")
    _asset, track = await _filed(folder, service, mia, person_vector(1), source="stash_box")
    assert await service.box_questions_owed()

    assert await service.ask_for_the_boxes() == 1

    assert await _state(folder, track) == (mia, Attribution.SUGGESTED.value, AskedBy.BOX.value)
    assert not await service.box_questions_owed()
    assert await service.ask_for_the_boxes() == 0
    # A question is not a name: the file carries her because the box filed her, and no face does.
    claims = await folder.fetch_all("SELECT 1 FROM face_asset_people WHERE person_id = ?", (mia,))
    assert claims == []


async def test_a_face_the_answer_is_no_claim_about_is_never_asked(
    folder: Database, service: FaceService
) -> None:
    """Two faces; two people the box named on one face; a folder's filing; somebody Sift has a
    picture of (her own, or a starter in use); a face refused as her; a face set aside."""
    recognizer = (await service.configuration()).recognizer
    two_faces = await make_person(folder, "Mira Two")
    asset, _track = await _filed(folder, service, two_faces, person_vector(1), source="stash_box")
    await folder.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
        "created_at) VALUES (?, ?, 0, 0, 1, 1.0, 0)",
        (new_id(), asset),
    )
    shared, other = await make_person(folder, "Mira Shared"), await make_person(folder, "Kell Oran")
    asset, _track = await _filed(folder, service, shared, person_vector(1), source="stash_box")
    await folder.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) "
        "VALUES (?, ?, 'stash_box', 7)",
        (asset, other),
    )
    by_folder = await make_person(folder, "Mira Folder")
    await _filed(folder, service, by_folder, person_vector(1), source="folder")
    known, started = await make_person(folder, "Mira Known"), await make_person(folder, "Mira Seed")
    await _filed(folder, service, known, person_vector(1), source="stash_box")
    await _filed(folder, service, started, person_vector(1), source="stash_box")
    await _reference(folder, service, known, "confirmed")
    await _reference(folder, service, started, "seed")
    refused = await make_person(folder, "Mira Refused")
    _asset, track = await _filed(folder, service, refused, person_vector(1), source="stash_box")
    await folder.execute(
        "INSERT INTO face_rejections (track_id, person_id, created_at) VALUES (?, ?, 0)",
        (track, refused),
    )
    aside = await make_person(folder, "Mira Aside")
    _asset, track = await _filed(folder, service, aside, person_vector(1), source="stash_box")
    pile = new_id()
    await folder.execute(
        "INSERT INTO face_piles (id, status, centroid, size, recognizer, created_at, updated_at) "
        "VALUES (?, 'ignored', ?, 1, ?, 0, 0)",
        (pile, recognize.pack(person_vector(1)), recognizer),
    )
    await folder.execute("UPDATE face_tracks SET pile_id = ? WHERE id = ?", (pile, track))

    assert not await service.box_questions_owed()
    assert await service.ask_for_the_boxes() == 0


async def test_a_retired_starter_is_no_picture_to_compare_so_the_box_still_asks(
    folder: Database, service: FaceService
) -> None:
    person = await make_person(folder, "Mira Retired")
    _asset, track = await _filed(folder, service, person, person_vector(1), source="stash_box")
    await _reference(folder, service, person, "seed")
    await folder.execute("UPDATE face_references SET retired_at = 1 WHERE person_id = ?", (person,))

    assert await service.ask_for_the_boxes() == 1
    assert (await _state(folder, track))[2] == AskedBy.BOX.value


async def test_a_yes_teaches_and_a_no_holds(folder: Database, service: FaceService) -> None:
    yes, no = await make_person(folder, "Mira Yes"), await make_person(folder, "Mira No")
    _asset, said_yes = await _filed(folder, service, yes, person_vector(1), source="stash_box")
    _asset, said_no = await _filed(folder, service, no, person_vector(2), source="stash_box")
    assert await service.ask_for_the_boxes() == 2

    await service.confirm(said_yes, yes)
    await service.reject(said_no, no)

    assert (await _state(folder, said_yes))[1] == Attribution.CONFIRMED.value
    # Remembered as hers against the face's own description: what files her pictures from it and
    # names it again after a rescan.
    remembered = await folder.fetch_all(
        "SELECT 1 FROM face_confirmations WHERE person_id = ?", (yes,)
    )
    assert remembered
    assert (await _state(folder, said_no))[0] is None
    assert await service.ask_for_the_boxes() == 0


async def test_a_rematch_keeps_the_box_question_and_never_names_it_on_a_number(
    folder: Database, service: FaceService, store: Store
) -> None:
    """With no picture of her there is nothing to judge it by, and it stands; with one that
    matches the face outright it is still asked, never recognized: only the arithmetic's own
    questions are."""
    person = await make_person(folder, "Mira Stands")
    other = await make_person(folder, "Kell Stands")
    _asset, track = await _filed(folder, service, person, person_vector(1), source="stash_box")
    await _reference(folder, service, other, "confirmed")
    assert await service.ask_for_the_boxes() == 1

    await service.rematch()
    assert await _state(folder, track) == (person, "suggested", "box")

    recognizer = (await service.configuration()).recognizer
    await folder.execute(
        "INSERT INTO face_references (id, person_id, crop_path, crop_digest, embedding, quality, "
        "origin, recognizer, created_at) VALUES (?, ?, 'crop.jpg', 'exact', ?, 1.0, 'added', ?, 0)",
        (new_id(), person, recognize.pack(person_vector(1)), recognizer),
    )
    await service.rematch()
    assert await _state(folder, track) == (person, "suggested", "box")
    assert (await store.track(track)) is not None


async def test_each_person_asked_about_gets_a_line_on_history(
    folder: Database, service: FaceService
) -> None:
    written = WorkbenchStore(folder)
    service._recorder = written
    person = await make_person(folder, "Mira Lined")
    asset, track = await _filed(folder, service, person, person_vector(1), source="stash_box")

    await service.ask_for_the_boxes()

    (receipt,), _total = await written.recent(limit=5, offset=0)
    assert receipt.queue == BOX_QUESTIONS_QUEUE
    assert json.loads(receipt.payload) == {
        "person_id": person,
        "faces": [[track, asset]],
        "boxes": [],
    }


async def test_the_line_names_the_box_the_filing_names(
    folder: Database, service: FaceService
) -> None:
    written = WorkbenchStore(folder)
    service._recorder = written
    await folder.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at)"
        " VALUES ('box-north', 'Northlight', 'https://northlight.invalid/graphql', 0)"
    )
    person = await make_person(folder, "Mira Lined")
    asset, _track = await _filed(folder, service, person, person_vector(1), source="stash_box")
    await folder.execute(
        "UPDATE asset_people SET box_id = 'box-north' WHERE asset_id = ?", (asset,)
    )

    await service.ask_for_the_boxes()

    (receipt,), _total = await written.recent(limit=5, offset=0)
    assert json.loads(receipt.payload)["boxes"] == ["Northlight"]
    assert receipt.title == "Sift asked about the face in a file Northlight filed people under"


async def test_one_line_holds_every_face_asked_about_one_person_and_counts_the_files(
    folder: Database, service: FaceService
) -> None:
    written = WorkbenchStore(folder)
    service._recorder = written
    person = await make_person(folder, "Mira Lined")
    for _ in range(2):
        await _filed(folder, service, person, person_vector(1), source="stash_box")

    await service.ask_for_the_boxes()

    (receipt,), _total = await written.recent(limit=5, offset=0)
    assert receipt.title == "Sift asked about the faces in 2 files a stash-box filed people under"


async def test_every_page_of_faces_owed_is_asked_about_not_only_the_first(
    folder: Database, service: FaceService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full page says more may wait behind it, so the pass reads on until a short one."""
    from sift.slices.faces import service_box

    monkeypatch.setattr(service_box, "BOX_QUESTIONS_PAGE", 2)
    person = await make_person(folder, "Mira Solvane")
    tracks = [
        (await _filed(folder, service, person, person_vector(1), source="stash_box"))[1]
        for _ in range(3)
    ]

    assert await service.ask_for_the_boxes() == 3

    for track in tracks:
        assert await _state(folder, track) == (person, "suggested", "box")
    assert not await service.box_questions_owed()


def _recorded(payload: object) -> Recorded:
    return Recorded(
        id="r",
        queue=BOX_QUESTIONS_QUEUE,
        payload=json.dumps(payload),
        title="stored",
        detail=BOX_QUESTIONS_DETAIL,
        decided_at=0,
    )


def test_the_line_names_the_file_the_person_and_the_box() -> None:
    words = BoxQuestionRecords().worded(
        _recorded({"person_id": "p1", "faces": [["t1", "a1"]], "boxes": ["StashDB"]})
    )
    assert words is not None
    assert words.said == (
        DOER,
        " asked whether the face in ",
        Named(kind="asset", id="a1"),
        " is ",
        Named(kind="person", id="p1"),
        ", because StashDB says they are in it",
    )
    assert words.more == (BOX_QUESTIONS_DETAIL,)

    many = BoxQuestionRecords().worded(
        _recorded({"person_id": "p1", "faces": [["t1", "a1"], ["t2", "a2"]], "boxes": []})
    )
    assert many is not None
    assert many.said[1] == " asked whether the faces in 2 files are "
    assert many.said[-1] == ", because a stash-box says they are in those files"

    for broken in (
        {"faces": [["t1", "a1"]]},
        {"person_id": "p1", "faces": []},
        {"person_id": "p1", "faces": [["t1"]]},
    ):
        assert BoxQuestionRecords().worded(_recorded(broken)) is None


async def test_the_record_is_final() -> None:
    records = BoxQuestionRecords()
    assert records.reversible is False
    assert await records.reverse(None, "r", "{}") is False  # type: ignore[arg-type]
    assert await records.pictures_of(None, "{}") == ()  # type: ignore[arg-type]


async def test_nothing_is_asked_while_recognition_is_off(
    folder: Database, service: FaceService, preferences: FakePreferences
) -> None:
    person = await make_person(folder, "Mira Off")
    await _filed(folder, service, person, person_vector(1), source="stash_box")
    preferences.values[face_settings.ENABLED_KEY] = False

    assert not await service.box_questions_owed()
    assert await service.ask_for_the_boxes() == 0
    context = Context()
    await face_jobs.box_questions(context, service=service)  # type: ignore[arg-type]
    assert context.note is None


async def test_the_pass_says_how_many_it_asked(folder: Database, service: FaceService) -> None:
    person = await make_person(folder, "Mira Counted")
    await _filed(folder, service, person, person_vector(1), source="stash_box")
    context = Context()

    await face_jobs.box_questions(context, service=service)  # type: ignore[arg-type]
    assert context.note == "Asked about 1 face in files a stash-box filed people under."

    await face_jobs.box_questions(context, service=service)  # type: ignore[arg-type]
    assert context.note == "No face in a file a stash-box filed was waiting to be asked about."


async def test_a_scan_asks_the_box_question_about_a_file_a_box_already_filed(
    service: FaceService,
    store: Store,
    clip: Ingested,  # noqa: F811
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    temp_db: Database,
) -> None:
    """The box first, the scan after: the scan asks, as the enrichment's pass would have."""
    person = await make_person(temp_db, "Mira Scanned")
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) "
        "VALUES (?, ?, 'stash_box', 1)",
        (clip.asset.id, person),
    )
    frame = noisy_frame(400, 300, seed=14)
    detector.placed = {0: [(draw_face(frame, x=60, y=40, size=180), 0.9)]}
    recognizer.rule = lambda chip: person_vector(5)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))

    await service.scan(clip.asset.id)

    (face,) = await store.tracks_of(clip.asset.id)
    assert (face.person_id, face.attribution) == (person, Attribution.SUGGESTED)
    assert await _state(temp_db, face.id) == (person, "suggested", "box")
