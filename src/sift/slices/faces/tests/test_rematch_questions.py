# SPDX-License-Identifier: AGPL-3.0-or-later
"""A question standing is judged again by the arithmetic a face arriving today meets.

A re-match that read only the faces nobody is on would never look again at a face Sift once asked
about: a question at 57% would go on waiting after the line its person had earned dropped to 55, a
question offered from a named group would never be scored, and moving either line would re-judge
nothing. Each test below is the one a mutation of its rule turns red.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import sift.slices.workbench.schema  # noqa: F401 (its tables hold the receipts)
from sift.kernel.access import Role
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ingress import Origin, verify_ingress
from sift.kernel.workbench import Workbench
from sift.slices.faces import tuning
from sift.slices.faces.models import (
    Appearance,
    AskedBy,
    Attribution,
    Box,
    Described,
    Detection,
    Quality,
    ScanStatus,
    Vector,
)
from sift.slices.faces.models import Origin as FaceOrigin
from sift.slices.faces.queue import IdentifiedRecords
from sift.slices.faces.service import STOPPED_ASKING, FaceService
from sift.slices.faces.store import PassRecord, Ruling, Store
from sift.slices.faces.tests.conftest import FakePreferences, make_person, person_vector, unit
from sift.slices.workbench.service import WorkbenchService
from sift.slices.workbench.store import Store as WorkbenchStore
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"

#: Against a person described by `person_vector(0)` this scores 0.5705: over the line for asking
#: (45), under the ordinary line for attaching (60), over it once that line is 55.
AT_57 = person_vector(0, variant=12)


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


async def _one_face(store: Store, asset_id: str, vector: Vector) -> str:
    """One appearance on the file, described by `vector`, with nobody on it yet."""
    (track_id,) = await _faces(store, asset_id, [vector])
    return track_id


async def _faces(store: Store, asset_id: str, vectors: list[Vector]) -> list[str]:
    """One appearance per vector on the file, in order, with nobody on any of them yet."""
    return await store.replace_pass(
        asset_id,
        [
            Appearance(
                started_ms=0, ended_ms=0, seen_in=1, quality=0.8, faces=(_described_face(v),)
            )
            for v in vectors
        ],
        [[b"\xff\xd8\xff picture"] for _ in vectors],
        PassRecord(
            status=ScanStatus.NONE_IDENTIFIED,
            depth="fast",
            coverage=1.0,
            frames_sampled=1,
            detector="test-detector",
            recognizer="test-recognizer",
            settings_digest="abcd1234",
        ),
    )


def _described_face(vector: Vector) -> Described:
    return Described(
        detection=Detection(
            box=Box(x=10, y=10, width=100, height=100),
            score=0.9,
            landmarks=((1.0, 1.0),) * 5,
            timestamp_ms=0,
        ),
        quality=Quality(pixels=120, sharpness=500.0, frontality=0.9, score=0.8, accepted=True),
        vector=vector,
        chip=np.zeros((1, 1, 3), dtype=np.uint8),
    )


async def _described(store: Store, person_id: str, vector: Vector, pictures: int = 2) -> None:
    for index in range(pictures):
        await store.add_reference(
            person_id,
            vector=vector,
            quality=1.0,
            crop=f"reference-{person_id}-{index}".encode(),
            origin=FaceOrigin.ADDED,
            recognizer="test-recognizer",
        )


async def _a_question(
    service: FaceService, store: Store, person: str, picture: Ingested, vector: Vector = AT_57
) -> str:
    """A face the arithmetic asked about: over the line for asking, under the one for attaching."""
    await _described(store, person, person_vector(0))
    track_id = await _one_face(store, picture.asset.id, vector)
    assert await service.rematch() == 1
    asked = await store.track(track_id)
    assert asked is not None and asked.attribution is Attribution.SUGGESTED
    return track_id


async def test_the_read_of_questions_carries_the_person_and_the_confidence(
    service: FaceService, store: Store, person: str, picture: Ingested, temp_db: Database
) -> None:
    track_id = await _a_question(service, store, person, picture)

    (asked,) = await store.asked("test-recognizer")

    assert (asked.track_id, asked.person_id) == (track_id, person)
    assert asked.confidence == pytest.approx(0.5705, abs=1e-3)
    assert asked.asked_by is AskedBy.MATCH
    # Written down by the re-match that asked, never left to be read off the number.
    assert await _asked_by(temp_db, track_id) == "match"
    assert await store.asked("another-recognizer") == []


async def test_a_question_that_now_clears_the_line_is_recognized_and_undo_asks_it_again(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    preferences: FakePreferences,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same bar a face arriving today meets. Undo puts it back as a question, not unnamed."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    track_id = await _a_question(service, store, person, picture)

    monkeypatch.setattr(tuning, "AUTO_APPLY_CONFIDENCE", 0.55)
    assert await service.rematch() == 1

    now = await store.track(track_id)
    assert now is not None and (now.person_id, now.attribution) == (person, Attribution.MATCHED)
    (receipt,), _total = await written.recent(limit=5, offset=0)
    payload = json.loads(receipt.payload)
    assert payload["attribution"] == {track_id: "suggested"}
    assert payload["confidence"][track_id] == pytest.approx(0.5705, abs=1e-3)
    assert "Needs your input" in receipt.detail

    admin = await create_user(temp_db, Role.ADMIN)
    assert await IdentifiedRecords(service).reverse(admin, receipt.id, receipt.payload) is True

    back = await store.track(track_id)
    assert back is not None
    assert (back.person_id, back.attribution) == (person, Attribution.SUGGESTED)
    assert back.confidence == pytest.approx(0.5705, abs=1e-3)
    # And it STAYS asked. The next re-match meets the same face at the same score against the same
    # line, and without the Undo written down on the face it would recognize it again at once.
    assert await _asked_by(temp_db, track_id) == "undone"
    assert await service.rematch() == 0
    kept = await store.track(track_id)
    assert kept is not None and kept.attribution is Attribution.SUGGESTED


async def test_undoing_a_name_sift_added_to_a_face_nobody_was_on_asks_instead_and_stays_asked(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    preferences: FakePreferences,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A face an Undo left unnamed would be named again at the same score by the next re-match. Undo
    asks about the face instead, and a re-match never recognizes a face somebody took a name like
    that back from."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    await _described(store, person, person_vector(0))
    track_id = await _one_face(store, picture.asset.id, AT_57)
    monkeypatch.setattr(tuning, "AUTO_APPLY_CONFIDENCE", 0.55)
    assert await service.rematch() == 1
    named = await store.track(track_id)
    assert named is not None and named.attribution is Attribution.MATCHED
    (receipt,), _total = await written.recent(limit=5, offset=0)
    assert "asks you about it under Needs your input" in receipt.detail

    admin = await create_user(temp_db, Role.ADMIN)
    assert await IdentifiedRecords(service).reverse(admin, receipt.id, receipt.payload) is True

    asked = await store.track(track_id)
    assert asked is not None
    assert (asked.person_id, asked.attribution) == (person, Attribution.SUGGESTED)
    assert asked.confidence == pytest.approx(0.5705, abs=1e-3)
    assert await _asked_by(temp_db, track_id) == "undone"
    assert await service.rematch() == 0
    kept = await store.track(track_id)
    assert kept is not None and kept.attribution is Attribution.SUGGESTED


async def test_a_question_under_the_line_for_asking_goes_and_undo_puts_it_back(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    preferences: FakePreferences,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written = WorkbenchStore(temp_db)
    service._recorder = written
    track_id = await _a_question(service, store, person, picture)

    monkeypatch.setattr(tuning, "SUGGEST_CONFIDENCE", 0.6)
    assert await service.rematch() == 1

    gone = await store.track(track_id)
    assert gone is not None and gone.person_id is None
    (receipt,), _total = await written.recent(limit=5, offset=0)
    payload = json.loads(receipt.payload)
    assert payload["act"] == STOPPED_ASKING
    assert payload["attribution"] == {track_id: "suggested"}

    admin = await create_user(temp_db, Role.ADMIN)
    assert await IdentifiedRecords(service).reverse(admin, receipt.id, receipt.payload) is True

    back = await store.track(track_id)
    assert back is not None
    assert (back.person_id, back.attribution) == (person, Attribution.SUGGESTED)
    assert back.confidence == pytest.approx(0.5705, abs=1e-3)


async def _asked_by(database: Database, track_id: str) -> str | None:
    """The stored word itself, which the reads never hand back for a face that is not a question."""
    row = await database.fetch_one("SELECT asked_by FROM face_tracks WHERE id = ?", (track_id,))
    assert row is not None
    return None if row["asked_by"] is None else str(row["asked_by"])


async def test_a_question_a_group_asked_is_scored_and_stays_a_question(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    preferences: FakePreferences,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For somebody known from fewer confirmed faces than `GROUP_NAMING_REFERENCES`, a group's
    question is never named on a number: however well it scores, the re-match writes its score and
    leaves it a question. The SECOND pass is the one that matters: once scored it has a confidence
    like any other question, and without who asked stored on it that pass would recognize it."""
    await _described(store, person, person_vector(0))
    track_id = await _one_face(store, picture.asset.id, AT_57)
    await store.attribute(
        track_id,
        person,
        confidence=None,
        attribution=Attribution.SUGGESTED,
        asked_by=AskedBy.GROUP,
    )
    monkeypatch.setattr(tuning, "AUTO_APPLY_CONFIDENCE", 0.55)

    for _pass in range(2):
        assert await service.rematch() == 0
        kept = await store.track(track_id)
        assert kept is not None
        assert (kept.person_id, kept.attribution) == (person, Attribution.SUGGESTED)
        assert kept.confidence == pytest.approx(0.5705, abs=1e-3)
    assert await _asked_by(temp_db, track_id) == "group"


async def test_naming_a_face_asks_about_the_rest_of_its_group_as_the_group(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    preferences: FakePreferences,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real press: name one face, and the other face of its group is offered as the group's
    question, scored by the re-match that follows, and still a question after the next one."""
    await _described(store, person, person_vector(0))
    named, offered = await _faces(store, picture.asset.id, [person_vector(0), AT_57])
    await store.add_piles([(person_vector(0), [named, offered])])
    monkeypatch.setattr(tuning, "AUTO_APPLY_CONFIDENCE", 0.55)

    assert await service.name_with_their_group([named], person) == (1, 1)
    assert await _asked_by(temp_db, offered) == "group"
    await service.rematch()
    await service.rematch()

    kept = await store.track(offered)
    assert kept is not None
    assert (kept.person_id, kept.attribution) == (person, Attribution.SUGGESTED)
    assert kept.confidence is not None


async def test_offering_a_group_is_one_write_however_large_the_group(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole offer is one guarded write, so no face of the group goes through the one-face
    write: a turn at the single writer per face would take over a minute for a group of hundreds
    while other work is writing."""
    await _described(store, person, person_vector(0))
    named, *offered = await _faces(store, picture.asset.id, [person_vector(0), AT_57, AT_57, AT_57])
    await store.add_piles([(person_vector(0), [named, *offered])])
    one_by_one: list[str] = []
    original = store.attribute

    async def counted(track_id: str, *args: object, **kwargs: object) -> None:
        one_by_one.append(track_id)
        await original(track_id, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "attribute", counted)

    assert await service.name_with_their_group([named], person) == (1, 3)
    assert not set(one_by_one) & set(offered), "an offered face was written on its own"


async def test_a_question_a_group_asked_goes_under_the_line_and_undo_asks_it_as_the_group(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    preferences: FakePreferences,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under the line for asking it goes, like any question. Undo puts it back with its score,
    and still as the group's, so the next pass at a lower line does not recognize it."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    await _described(store, person, person_vector(0))
    track_id = await _one_face(store, picture.asset.id, AT_57)
    await store.attribute(
        track_id,
        person,
        confidence=0.5705,
        attribution=Attribution.SUGGESTED,
        asked_by=AskedBy.GROUP,
    )
    monkeypatch.setattr(tuning, "SUGGEST_CONFIDENCE", 0.6)
    assert await service.rematch() == 1
    gone = await store.track(track_id)
    assert gone is not None and gone.person_id is None

    (receipt,), _total = await written.recent(limit=5, offset=0)
    admin = await create_user(temp_db, Role.ADMIN)
    assert await IdentifiedRecords(service).reverse(admin, receipt.id, receipt.payload) is True
    monkeypatch.setattr(tuning, "SUGGEST_CONFIDENCE", 0.45)
    monkeypatch.setattr(tuning, "AUTO_APPLY_CONFIDENCE", 0.55)
    await service.rematch()

    back = await store.track(track_id)
    assert back is not None
    assert (back.person_id, back.attribution) == (person, Attribution.SUGGESTED)
    assert await _asked_by(temp_db, track_id) == "group"


async def test_a_face_put_back_keeps_who_asked_and_a_face_asked_again_says_who(
    store: Store, person: str, picture: Ingested, temp_db: Database
) -> None:
    """The word outlives the question: taking the person off leaves it, putting the question back
    with no word keeps it, and a writer that asks writes its own over it."""
    track_id = await _one_face(store, picture.asset.id, AT_57)
    await store.attribute(
        track_id, person, confidence=None, attribution=Attribution.SUGGESTED, asked_by=AskedBy.GROUP
    )
    await store.attribute(track_id, None, confidence=None, attribution=None)
    assert await _asked_by(temp_db, track_id) == "group"

    await store.attribute(track_id, person, confidence=0.57, attribution=Attribution.SUGGESTED)
    (asked,) = await store.asked("test-recognizer")
    assert asked.asked_by is AskedBy.GROUP

    await store.attribute(
        track_id, person, confidence=0.57, attribution=Attribution.SUGGESTED, asked_by=AskedBy.MATCH
    )
    assert await _asked_by(temp_db, track_id) == "match"


async def test_a_group_question_from_before_the_word_keeps_its_kind_once_scored(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    preferences: FakePreferences,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An offer with no word (put back by an Undo from before the step) is read as the group's by
    its missing number, and the pass that gives it a number writes the word, or the pass after
    would read the number and recognize it."""
    await _described(store, person, person_vector(0))
    track_id = await _one_face(store, picture.asset.id, AT_57)
    await store.attribute(track_id, person, confidence=None, attribution=Attribution.SUGGESTED)
    monkeypatch.setattr(tuning, "AUTO_APPLY_CONFIDENCE", 0.55)

    await service.rematch()
    await service.rematch()

    kept = await store.track(track_id)
    assert kept is not None and kept.attribution is Attribution.SUGGESTED
    assert await _asked_by(temp_db, track_id) == "group"


async def test_a_question_from_before_the_word_is_read_by_the_rule_the_step_used(
    store: Store, person: str, picture: Ingested, temp_db: Database
) -> None:
    """No word on a question (an Undo of an answer given before the step): no number means the
    group asked, a number means the arithmetic did: the rule every standing question was marked
    by."""
    track_id = await _one_face(store, picture.asset.id, AT_57)
    await store.attribute(track_id, person, confidence=None, attribution=Attribution.SUGGESTED)
    (asked,) = await store.asked("test-recognizer")
    assert asked.asked_by is AskedBy.GROUP

    await store.attribute(track_id, person, confidence=0.57, attribution=Attribution.SUGGESTED)
    (asked,) = await store.asked("test-recognizer")
    assert asked.asked_by is AskedBy.MATCH


async def test_a_question_is_never_moved_to_somebody_else(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    temp_db: Database,
) -> None:
    """It was put as "is this her?"; answering "no, him" on somebody's behalf is not a re-score."""
    other = await make_person(temp_db, "Marit Halvorsen")
    await _described(store, person, person_vector(0))
    await _described(store, other, person_vector(4), pictures=10)
    values = [0.0] * len(person_vector(0))
    values[0], values[4] = 0.62, 0.78
    track_id = await _one_face(store, picture.asset.id, unit(values))
    await store.attribute(track_id, person, confidence=0.5, attribution=Attribution.SUGGESTED)

    assert await service.rematch() == 0

    kept = await store.track(track_id)
    assert kept is not None
    assert (kept.person_id, kept.attribution) == (person, Attribution.SUGGESTED)
    assert kept.confidence == pytest.approx(0.62, abs=1e-2)


async def test_raising_the_line_takes_no_name_off(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    preferences: FakePreferences,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A face Sift recognized stays recognized: only questions are judged again."""
    await _described(store, person, person_vector(0))
    track_id = await _one_face(store, picture.asset.id, AT_57)
    monkeypatch.setattr(tuning, "AUTO_APPLY_CONFIDENCE", 0.55)
    assert await service.rematch() == 1

    monkeypatch.setattr(tuning, "AUTO_APPLY_CONFIDENCE", 0.6)
    assert await service.rematch() == 0

    still = await store.track(track_id)
    assert still is not None and still.attribution is Attribution.MATCHED


async def test_a_face_answered_while_the_pass_ran_keeps_the_answer(
    store: Store, person: str, picture: Ingested
) -> None:
    """A pass reads, works, then writes; the write lands only where the face still stands as read."""
    track_id = await _one_face(store, picture.asset.id, AT_57)
    await store.attribute(track_id, person, confidence=0.57, attribution=Attribution.SUGGESTED)
    await store.attribute(track_id, person, confidence=1.0, attribution=Attribution.CONFIRMED)

    landed = await store.restate(
        [
            Ruling(
                track_id=track_id,
                was_person=person,
                was=Attribution.SUGGESTED,
                person_id=person,
                attribution=Attribution.MATCHED,
                confidence=0.57,
            )
        ]
    )

    assert landed == set()
    answered = await store.track(track_id)
    assert answered is not None and answered.attribution is Attribution.CONFIRMED


# --- an Undo of a naming takes back what rested on it ------------------------------------------


async def _named_then_recognized(
    service: FaceService, store: Store, person: str, picture: Ingested, temp_db: Database
) -> tuple[WorkbenchStore, Any, str, str, list[str]]:
    """One face named by hand, then a re-match that recognizes two more from its picture alone.

    The record, the one who pressed, the naming's receipt, the re-match's, and the two faces."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    named, *others = await _faces(store, picture.asset.id, [person_vector(0)] * 3)
    admin = await create_user(temp_db, Role.ADMIN)
    await service.confirm(named, person, viewer=admin)
    assert await service.rematch() == 2
    receipts, _total = await written.recent(limit=5, offset=0)
    naming = next(one for one in receipts if one.user_id is not None)
    run = next(one for one in receipts if one.user_id is None)
    return written, admin, naming.id, run.id, others


async def test_undoing_a_naming_takes_back_the_recognitions_that_rested_on_it(
    service: FaceService, store: Store, person: str, picture: Ingested, temp_db: Database
) -> None:
    """Naming one face let recognition attach two more; the naming's Undo takes both back in the
    same press, and the recognition's own line reads as taken back."""
    written, admin, naming, run, others = await _named_then_recognized(
        service, store, person, picture, temp_db
    )
    bench = Workbench()
    bench.register_reverser(IdentifiedRecords(service))

    undone = await WorkbenchService(store=written, workbench=bench).undo(admin, naming)

    assert undone.along == (run,)
    for track_id in others:
        now = await store.track(track_id)
        assert now is not None and now.person_id is None, "back to nobody, where it stood"
    ran = await written.decision(run)
    assert ran is not None and ran.reversed_at is not None


async def test_a_recognition_that_also_rested_on_a_picture_that_stays_is_left(
    service: FaceService, store: Store, person: str, picture: Ingested, temp_db: Database
) -> None:
    """A run compared with a picture the Undo did not take away still has something to rest on."""
    await _described(store, person, person_vector(0), pictures=1)
    written, admin, naming, _run, others = await _named_then_recognized(
        service, store, person, picture, temp_db
    )

    reversed_as = await IdentifiedRecords(service).reverse(
        admin,
        naming,
        (await written.decision(naming)).payload,  # type: ignore[union-attr]
    )

    assert reversed_as is True, "the naming alone went back"
    for track_id in others:
        now = await store.track(track_id)
        assert now is not None and now.person_id == person


async def _rewrite(temp_db: Database, receipt_id: str, **fields: object) -> None:
    """The run's receipt as another build wrote it: `None` drops a field."""
    row = await temp_db.fetch_one(
        "SELECT payload FROM workbench_decisions WHERE id = ?", (receipt_id,)
    )
    assert row is not None
    payload = json.loads(row["payload"])
    for key, value in fields.items():
        if value is None:
            payload.pop(key, None)
        else:
            payload[key] = value
    await temp_db.execute(
        "UPDATE workbench_decisions SET payload = ? WHERE id = ?", (json.dumps(payload), receipt_id)
    )


async def _undo(written: WorkbenchStore, service: FaceService, admin: Any, receipt: str) -> None:
    bench = Workbench()
    bench.register_reverser(IdentifiedRecords(service))
    await WorkbenchService(store=written, workbench=bench).undo(admin, receipt)


@pytest.mark.parametrize(
    "rewritten",
    [{"track_ids": "not a list"}, {"act": "agreed-with-matches"}],
    ids=["no list of faces", "a press, not a run"],
)
async def test_a_receipt_that_is_not_a_run_of_sifts_is_never_taken_back_with_a_naming(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    temp_db: Database,
    rewritten: dict[str, object],
) -> None:
    """Only a pass's own run, read whole, is a recognition that can rest on a picture."""
    written, admin, naming, run, others = await _named_then_recognized(
        service, store, person, picture, temp_db
    )
    await _rewrite(temp_db, run, **rewritten)

    await _undo(written, service, admin, naming)

    for track_id in others:
        now = await store.track(track_id)
        assert now is not None and now.person_id == person
    ran = await written.decision(run)
    assert ran is not None and ran.reversed_at is None


@pytest.mark.parametrize("a_picture_stays", [False, True])
async def test_a_run_that_does_not_say_what_it_rested_on_goes_only_with_her_last_picture(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    temp_db: Database,
    a_picture_stays: bool,
) -> None:
    """A receipt kept from before runs named their pictures says only how many: whatever it
    rested on has gone once she has no picture of her own, and may still stand while one stays."""
    if a_picture_stays:
        await _described(store, person, person_vector(0), pictures=1)
    written, admin, naming, run, others = await _named_then_recognized(
        service, store, person, picture, temp_db
    )
    await _rewrite(temp_db, run, rested_on=None)

    await _undo(written, service, admin, naming)

    for track_id in others:
        now = await store.track(track_id)
        assert now is not None
        assert now.person_id == (person if a_picture_stays else None)


async def test_two_runs_that_do_not_say_what_they_rested_on_both_go_with_her_last_picture(
    service: FaceService, store: Store, person: str, picture: Ingested, temp_db: Database
) -> None:
    """Each kept receipt from before runs named their pictures is judged by one count of what she
    has left, asked once for the whole Undo."""
    written, admin, naming, run, others = await _named_then_recognized(
        service, store, person, picture, temp_db
    )
    row = await temp_db.fetch_one("SELECT payload FROM workbench_decisions WHERE id = ?", (run,))
    assert row is not None
    payload = json.loads(row["payload"])
    payload.pop("rested_on", None)
    second = new_id()
    await temp_db.execute(
        "CREATE TEMP TABLE kept AS SELECT * FROM workbench_decisions WHERE id = ?", (run,)
    )
    await temp_db.execute(
        "UPDATE kept SET id = ?, payload = ?",
        (second, json.dumps({**payload, "track_ids": [others[1]]})),
    )
    await temp_db.execute("INSERT INTO workbench_decisions SELECT * FROM kept")
    await temp_db.execute(
        "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
        " SELECT ?, kind, subject_id, name FROM workbench_decision_subjects WHERE decision_id = ?",
        (second, run),
    )
    await _rewrite(temp_db, run, rested_on=None, track_ids=[others[0]])

    await _undo(written, service, admin, naming)

    for track_id in others:
        now = await store.track(track_id)
        assert now is not None and now.person_id is None
    for receipt in (run, second):
        ran = await written.decision(receipt)
        assert ran is not None and ran.reversed_at is not None, receipt


async def test_a_face_answered_since_the_run_keeps_its_answer_when_the_run_is_taken_back(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One face named as somebody else since, one agreed to as her: neither is the run's to take
    back, so nothing comes off and nothing is placed into a group again."""
    written, _admin, naming, run, others = await _named_then_recognized(
        service, store, person, picture, temp_db
    )
    ran = await written.decision(run)
    assert ran is not None
    rested = json.loads(ran.payload)["rested_on"]
    someone_else = await make_person(temp_db, "Bryn Calloway")
    await service.confirm(others[0], someone_else)
    await service.confirm(others[1], person)
    regrouped: list[bool] = []

    async def regroup(*, full: bool = True) -> object:
        regrouped.append(full)
        return None

    monkeypatch.setattr(service, "regroup", regroup)

    taken, faces = await service.take_back_recognitions(person, rested, since=naming)

    assert (taken, faces) == ([run], 0)
    first, second = [await store.track(one) for one in others]
    assert first is not None and first.person_id == someone_else
    assert second is not None and (second.person_id, second.attribution) == (
        person,
        Attribution.CONFIRMED,
    )
    assert regrouped == []


# --- an Undo that leaves her no picture returns Sift's questions about her to nobody ---------------


async def _named_recognized_and_asked(
    service: FaceService, store: Store, person: str, picture: Ingested, temp_db: Database
) -> tuple[WorkbenchStore, Any, str, str, str]:
    """One face named by hand, then a re-match that recognizes one more and asks about a third.

    The record, the one who pressed, the naming's receipt, the face Sift asked about, and a face a
    GROUP asked about, put on her after the re-match so its arithmetic leaves it as it is."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    named, _recognized, asked, offered = await _faces(
        store, picture.asset.id, [person_vector(0), person_vector(0), AT_57, person_vector(3)]
    )
    admin = await create_user(temp_db, Role.ADMIN)
    await service.confirm(named, person, viewer=admin)
    assert await service.rematch() == 2
    question = await store.track(asked)
    assert question is not None and question.attribution is Attribution.SUGGESTED
    assert await _asked_by(temp_db, asked) == "match"
    await store.attribute(
        offered, person, confidence=None, attribution=Attribution.SUGGESTED, asked_by=AskedBy.GROUP
    )
    receipts, _total = await written.recent(limit=5, offset=0)
    naming = next(one for one in receipts if one.user_id is not None)
    return written, admin, naming.id, asked, offered


async def test_an_undo_that_leaves_her_no_picture_returns_her_questions_to_nobody(
    service: FaceService, store: Store, person: str, picture: Ingested, temp_db: Database
) -> None:
    """The naming was her only picture: the question Sift asked from it rests on nothing, goes back
    to nobody in the same press, and says so in History with an Undo that asks it again. The
    question a group asked is somebody's naming of that group, and stays."""
    written, admin, naming, asked, offered = await _named_recognized_and_asked(
        service, store, person, picture, temp_db
    )
    bench = Workbench()
    bench.register_reverser(IdentifiedRecords(service))

    await WorkbenchService(store=written, workbench=bench).undo(admin, naming)

    went = await store.track(asked)
    assert went is not None and (went.person_id, went.attribution) == (None, None)
    kept = await store.track(offered)
    assert kept is not None and (kept.person_id, kept.attribution) == (
        person,
        Attribution.SUGGESTED,
    )
    receipts, _total = await written.recent(limit=10, offset=0)
    (line,) = [one for one in receipts if "went back to nobody" in one.title]
    assert line.title.endswith("1 question about them went back to nobody")
    assert line.reversed_at is None
    payload = json.loads(line.payload)
    assert (payload["act"], payload["track_ids"]) == (STOPPED_ASKING, [asked])

    # Its own Undo asks the question again, at the confidence it had been put at.
    await WorkbenchService(store=written, workbench=bench).undo(admin, line.id)
    back = await store.track(asked)
    assert back is not None and (back.person_id, back.attribution) == (
        person,
        Attribution.SUGGESTED,
    )
    assert back.confidence == pytest.approx(0.5705, abs=1e-3)


async def test_an_undo_that_leaves_her_a_picture_leaves_her_questions_asked(
    service: FaceService, store: Store, person: str, picture: Ingested, temp_db: Database
) -> None:
    """A picture of her that the Undo did not take is something a question still rests on."""
    await _described(store, person, person_vector(0), pictures=1)
    written, admin, naming, asked, _offered = await _named_recognized_and_asked(
        service, store, person, picture, temp_db
    )
    bench = Workbench()
    bench.register_reverser(IdentifiedRecords(service))

    await WorkbenchService(store=written, workbench=bench).undo(admin, naming)

    stays = await store.track(asked)
    assert stays is not None and (stays.person_id, stays.attribution) == (
        person,
        Attribution.SUGGESTED,
    )
    receipts, _total = await written.recent(limit=10, offset=0)
    assert not [one for one in receipts if "went back to nobody" in one.title]


async def test_with_nothing_recording_the_questions_still_go_back(
    service: FaceService, store: Store, person: str, picture: Ingested, temp_db: Database
) -> None:
    """A tool or a test with no record still returns them: the line is the record's, the move is
    the faces'."""
    track_id = await _one_face(store, picture.asset.id, AT_57)
    await store.attribute(
        track_id, person, confidence=0.57, attribution=Attribution.SUGGESTED, asked_by=AskedBy.MATCH
    )
    service._recorder = None

    assert await service.return_questions(person) == 1
    went = await store.track(track_id)
    assert went is not None and went.person_id is None


# --- a group's questions named for somebody well known, and Sift learning from its own names -------


async def _confirmed(store: Store, person_id: str, vector: Vector, pictures: int = 2) -> None:
    """Pictures of her people confirmed (`Origin.CONFIRMED`), the only ones the two rules count."""
    for index in range(pictures):
        await store.add_reference(
            person_id,
            vector=vector,
            quality=1.0,
            crop=f"confirmed-{person_id}-{index}".encode(),
            origin=FaceOrigin.CONFIRMED,
            recognizer="test-recognizer",
        )


async def _group_questions(store: Store, person: str, track_ids: list[str]) -> None:
    for track_id in track_ids:
        await store.attribute(
            track_id,
            person,
            confidence=None,
            attribution=Attribution.SUGGESTED,
            asked_by=AskedBy.GROUP,
        )


async def test_a_groups_questions_about_somebody_well_known_are_named_in_one_line_with_one_undo(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    preferences: FakePreferences,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over her bar, for somebody with enough confirmed faces: named, written down once for the
    run in the group's own words, and one Undo asks about every one of them again, for good."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    monkeypatch.setattr(tuning, "GROUP_NAMING_REFERENCES", 2)
    monkeypatch.setattr(tuning, "LEARNING_REFERENCES", 1000)
    monkeypatch.setattr(tuning, "AUTO_APPLY_CONFIDENCE", 0.55)
    await _confirmed(store, person, person_vector(0))
    faces = await _faces(store, picture.asset.id, [AT_57, AT_57])
    await _group_questions(store, person, faces)

    assert await service.rematch() == 2

    for track_id in faces:
        named = await store.track(track_id)
        assert named is not None and (named.person_id, named.attribution) == (
            person,
            Attribution.MATCHED,
        )
    (receipt,), _total = await written.recent(limit=5, offset=0)
    name = await store.person_name(person)
    assert receipt.title == f"Sift named 2 faces as {name} from the groups that looked like them"
    assert json.loads(receipt.payload)["attribution"] == dict.fromkeys(faces, "suggested")

    admin = await create_user(temp_db, Role.ADMIN)
    assert await IdentifiedRecords(service).reverse(admin, receipt.id, receipt.payload) is True
    await service.rematch()
    for track_id in faces:
        back = await store.track(track_id)
        assert back is not None and (back.person_id, back.attribution) == (
            person,
            Attribution.SUGGESTED,
        )
        assert await _asked_by(temp_db, track_id) == "undone"


async def test_a_groups_question_stays_one_while_too_few_faces_of_her_were_confirmed(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    preferences: FakePreferences,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only CONFIRMED pictures count: a folder's pictures, however many, do not name a group."""
    monkeypatch.setattr(tuning, "GROUP_NAMING_REFERENCES", 2)
    monkeypatch.setattr(tuning, "AUTO_APPLY_CONFIDENCE", 0.55)
    await _described(store, person, person_vector(0), pictures=3)
    await _confirmed(store, person, person_vector(0), pictures=1)
    track_id = await _one_face(store, picture.asset.id, AT_57)
    await _group_questions(store, person, [track_id])

    assert await service.rematch() == 0

    kept = await store.track(track_id)
    assert kept is not None and kept.attribution is Attribution.SUGGESTED
    assert await _asked_by(temp_db, track_id) == "group"


async def _recognized(database: Database, person_id: str) -> list[str]:
    """The faces her references Sift learned from were cut from."""
    rows = await database.fetch_all(
        "SELECT track_id FROM face_references WHERE person_id = ? AND origin = 'recognized' "
        "ORDER BY track_id",
        (person_id,),
    )
    return [str(row["track_id"]) for row in rows]


async def test_sift_learns_from_its_surest_name_and_the_undo_of_it_unlearns_for_good(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    preferences: FakePreferences,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A face named at 60% or more, of somebody with enough confirmed faces, becomes one of her
    references with a line of its own; the Undo takes the name and the reference off together, and
    the next re-match neither names nor learns from it again."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    monkeypatch.setattr(tuning, "LEARNING_REFERENCES", 2)
    await _confirmed(store, person, person_vector(0))
    track_id = await _one_face(store, picture.asset.id, person_vector(0, variant=1))

    assert await service.rematch() == 1

    assert await _recognized(temp_db, person) == [track_id]
    receipts, _total = await written.recent(limit=5, offset=0)
    name = await store.person_name(person)
    (learned,) = [
        one for one in receipts if one.title.startswith("Sift added 1 face it recognized")
    ]
    assert learned.title == f"Sift added 1 face it recognized as {name} to their reference pictures"

    admin = await create_user(temp_db, Role.ADMIN)
    assert await IdentifiedRecords(service).reverse(admin, learned.id, learned.payload) is True
    assert await _recognized(temp_db, person) == []
    await service.rematch()
    back = await store.track(track_id)
    assert back is not None and back.attribution is Attribution.SUGGESTED
    assert await _recognized(temp_db, person) == []


async def test_a_question_a_name_under_sixty_and_a_thinly_known_person_teach_nothing(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    preferences: FakePreferences,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never a question, never a name under 60%, never for somebody with too few confirmed faces."""
    monkeypatch.setattr(tuning, "LEARNING_REFERENCES", 2)
    await _confirmed(store, person, person_vector(0))
    asked = await _one_face(store, picture.asset.id, AT_57)
    await service.rematch()
    question = await store.track(asked)
    assert question is not None and question.attribution is Attribution.SUGGESTED
    assert await _recognized(temp_db, person) == []

    monkeypatch.setattr(tuning, "AUTO_APPLY_CONFIDENCE", 0.55)
    await service.rematch()
    named = await store.track(asked)
    assert named is not None and named.attribution is Attribution.MATCHED
    assert await _recognized(temp_db, person) == [], "a name at 57% was learned from"

    monkeypatch.setattr(tuning, "LEARNING_REFERENCES", 3)
    close = await _one_face(store, picture.asset.id, person_vector(0, variant=1))
    await service.rematch()
    sure = await store.track(close)
    assert sure is not None and sure.attribution is Attribution.MATCHED
    assert await _recognized(temp_db, person) == [], "somebody with too few confirmed faces taught"
