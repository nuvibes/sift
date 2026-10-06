# SPDX-License-Identifier: AGPL-3.0-or-later
"""What this feature will tell a folder reader about the faces in a folder, and nothing more.

The other side of a seam. A feature that reads folder names for people needs corroboration and may
not import this one, so it asks a question shaped like counts and gets counts back.

The module is exercised from the other side of the seam already: the folder reader's own tests
drive it through a real database. That is worth nothing to this slice's gate: a module covered
only by another selection reads as covered and is not, so a branch of it can be deleted and every gate stays green. These tests are
this slice's own, and they are about what the seam promises rather than about what the caller does
with it.

Rows are seeded straight in. The tree and the face tables are what the answers are read from, and
building them through the ingest path would be testing the ingest path.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import pytest

from sift.kernel import db as db_module
from sift.kernel.config import Settings
from sift.kernel.db import Database, statement_name
from sift.slices.faces import settings as face_settings
from sift.slices.faces.evidence import FaceEvidence
from sift.slices.faces.jobs import FACE_REMATCH
from sift.slices.faces.models import Attribution
from sift.slices.faces.store import Store
from sift.slices.faces.tests.conftest import FakePreferences

pytestmark = pytest.mark.integration

ROOT = "01HX0000000000000000000501"
TOP = "01HX0000000000000000000502"
HERS = "01HX0000000000000000000503"
EMPTY = "01HX0000000000000000000504"
HER = "01HX0000000000000000000505"
SOMEBODY_ELSE = "01HX0000000000000000000506"
PILE = "01HX0000000000000000000507"
OTHER_PILE = "01HX0000000000000000000508"


async def _tree(database: Database) -> None:
    await database.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, 'Media', ?, 0)",
        (ROOT, "/library/media"),
    )
    await database.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, NULL, '', 'Media')",
        (TOP, ROOT),
    )
    for folder_id, rel_path in ((HERS, "Her"), (EMPTY, "Nothing")):
        await database.execute(
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
            (folder_id, ROOT, TOP, rel_path, rel_path),
        )
    for person_id, name in ((HER, "Her"), (SOMEBODY_ELSE, "Somebody Else")):
        await database.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (person_id, name)
        )


async def _file(database: Database, asset_id: str, rel_path: str, folder_id: str) -> None:
    await database.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
        (asset_id, f"digest-{asset_id}"),
    )
    await database.execute(
        "INSERT INTO asset_locations "
        "(id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
        (f"loc-{asset_id}", asset_id, ROOT, folder_id, rel_path, rel_path.rsplit("/", 1)[-1]),
    )


async def _looked(database: Database, asset_id: str, status: str) -> None:
    await database.execute(
        "INSERT INTO face_scans (asset_id, status, depth, coverage, frames_sampled, track_count, "
        "identified_count, detector, recognizer, settings_digest, scanned_at) "
        "VALUES (?, ?, 'fast', 1.0, 1, 0, 0, 'd', 'r', 'x', 0)",
        (asset_id, status),
    )


async def _track(
    database: Database,
    track_id: str,
    asset_id: str,
    *,
    pile_id: str | None = None,
    person_id: str | None = None,
    status: str = "open",
    attribution: str | None = None,
) -> None:
    if pile_id is not None:
        existing = await database.fetch_one("SELECT id FROM face_piles WHERE id = ?", (pile_id,))
        if existing is None:
            await database.execute(
                "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at) "
                "VALUES (?, ?, x'00', 1, 0, 0)",
                (pile_id, status),
            )
    await database.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
        "pile_id, person_id, attribution, created_at) VALUES (?, ?, 0, 0, 1, 1.0, ?, ?, ?, 0)",
        (track_id, asset_id, pile_id, person_id, attribution),
    )


class FakeTeacher:
    """The face service's two teaching halves, writing down what they were asked."""

    def __init__(self) -> None:
        self.taught: list[tuple[tuple[str, ...], str]] = []
        self.untaught: list[tuple[tuple[str, ...], str]] = []
        self.answer = 0

    async def teach(self, track_ids: Sequence[str], person_id: str) -> int:
        self.taught.append((tuple(track_ids), person_id))
        return self.answer

    async def unteach(self, track_ids: Sequence[str], person_id: str) -> int:
        self.untaught.append((tuple(track_ids), person_id))
        return self.answer


class FakeQueue:
    """Where a re-match is asked for. Only the asking is under test here."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def enqueue_when_settled(self, job_type: str, payload: object, *, delay: int) -> None:
        self.asked.append(job_type)


@pytest.fixture
def teacher() -> FakeTeacher:
    return FakeTeacher()


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
async def evidence(
    temp_db: Database,
    preferences: FakePreferences,
    settings: Settings,
    teacher: FakeTeacher,
    queue: FakeQueue,
) -> FaceEvidence:
    await temp_db.initialize_schema()
    await _tree(temp_db)
    return FaceEvidence(
        temp_db,
        preferences=preferences,
        store=Store(temp_db, data_dir=settings.data_dir),
        teacher=teacher,
        queue=queue,  # type: ignore[arg-type]
    )


async def test_recognition_being_switched_off_is_an_answer_rather_than_an_empty_one(
    evidence: FaceEvidence, preferences: FakePreferences
) -> None:
    """ "Nothing found yet" and "nothing will ever be found" lead to opposite decisions.

    The first is a folder worth waiting for. The second is a folder that has to be judged on its
    name alone, and a caller that inferred it from empty counts would wait forever.
    """
    assert await evidence.looking() is True

    preferences.set(face_settings.ENABLED_KEY, False)

    assert await evidence.looking() is False


async def test_a_folder_nothing_has_been_looked_at_in_says_so(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    await _file(temp_db, "a1", "Her/one.mp4", HERS)

    found = await evidence.faces_in(HERS)

    assert found.looked_at == 0
    assert found.with_faces == 0
    assert found.piles == {}
    assert found.named == {}


async def test_a_file_looked_at_and_holding_nobody_counts_as_looked_at_and_not_as_a_face(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    """The pair of numbers the whole ladder turns on. A file with no row has not been looked at; a
    file with the `no_faces` status has, and found nobody. Those are opposite answers."""
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _file(temp_db, "a2", "Her/two.mp4", HERS)
    await _looked(temp_db, "a1", "no_faces")
    await _looked(temp_db, "a2", "none_identified")
    await _track(temp_db, "t1", "a2", pile_id=PILE)

    found = await evidence.faces_in(HERS)

    assert found.looked_at == 2
    assert found.with_faces == 1


async def test_a_group_is_counted_by_files_rather_than_by_faces(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    """A video where one person is tracked through forty frames is one file about one person.

    Counting the frames would let a single long clip outweigh thirty photographs of somebody else,
    which is the wrong answer to "whose folder is this".
    """
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _file(temp_db, "a2", "Her/two.mp4", HERS)
    for index in range(5):
        await _track(temp_db, f"t{index}", "a1", pile_id=PILE)
    await _track(temp_db, "t9", "a2", pile_id=PILE)

    assert (await evidence.faces_in(HERS)).piles == {PILE: 2}


async def test_a_group_somebody_set_aside_is_in_no_answer_at_all(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    """Somebody has already said those faces are not worth naming, and work that comes back after
    being dismissed is worse than work that was never offered."""
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", pile_id=PILE)
    await _track(temp_db, "t2", "a1", pile_id=OTHER_PILE, status="ignored")

    assert (await evidence.faces_in(HERS)).piles == {PILE: 1}


async def test_somebody_already_recognized_is_reported_by_name_rather_than_as_a_group(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _file(temp_db, "a2", "Her/two.mp4", HERS)
    await _track(temp_db, "t1", "a1", person_id=HER, attribution=Attribution.MATCHED.value)
    await _track(temp_db, "t2", "a2", person_id=HER, attribution=Attribution.CONFIRMED.value)

    found = await evidence.faces_in(HERS)

    assert found.named == {HER: 2}
    assert found.piles == {}


async def test_a_face_only_asked_about_names_nobody_here(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    """A question about a face puts nobody on its file, and the folder reader gives a whole folder
    away on this count without asking: a question counted here would give it away on a guess."""
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _file(temp_db, "a2", "Her/two.mp4", HERS)
    await _track(temp_db, "t1", "a1", person_id=HER, attribution=Attribution.MATCHED.value)
    await _track(temp_db, "t2", "a2", person_id=HER, attribution=Attribution.SUGGESTED.value)

    assert (await evidence.faces_in(HERS)).named == {HER: 1}


async def test_the_files_that_dissent_from_the_biggest_group_are_named(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    """The files a confirmation offers to leave out: they carry a face, and none of it is the
    group being named."""
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _file(temp_db, "a2", "Her/two.mp4", HERS)
    await _file(temp_db, "a3", "Her/three.mp4", HERS)
    await _track(temp_db, "t1", "a1", pile_id=PILE)
    await _track(temp_db, "t2", "a2", pile_id=PILE)
    await _track(temp_db, "t3", "a3", pile_id=OTHER_PILE)

    assert (await evidence.faces_in(HERS)).dissenting == ("a3",)


async def test_a_folder_that_is_not_there_and_one_holding_nothing_both_answer_empty(
    evidence: FaceEvidence,
) -> None:
    assert (await evidence.faces_in("01HX0000000000000000000599")).piles == {}
    assert (await evidence.faces_in(EMPTY)).piles == {}


async def test_a_file_whose_faces_are_all_somebody_elses_contradicts(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", person_id=SOMEBODY_ELSE, attribution="matched")

    assert await evidence.contradicting(HER, ["a1"]) == {"a1"}


async def test_a_file_whose_only_faces_are_unnamed_does_not_contradict(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    """An unnamed face is not somebody else's face. In a folder filed under her it is most often
    her own, not yet recognized, and counting it would hold back exactly her own files."""
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", pile_id=PILE)
    await _track(temp_db, "t2", "a1")

    assert await evidence.contradicting(HER, ["a1"]) == set()


async def test_a_face_only_asked_about_as_somebody_else_does_not_contradict(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    """A question standing about a face names nobody: the rule a file's People already follow."""
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", person_id=SOMEBODY_ELSE, attribution="suggested")

    assert await evidence.contradicting(HER, ["a1"]) == set()


async def test_a_face_confirmed_as_somebody_else_contradicts(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", person_id=SOMEBODY_ELSE, attribution="confirmed")

    assert await evidence.contradicting(HER, ["a1"]) == {"a1"}


async def test_a_face_somebody_refused_as_her_contradicts_though_it_names_nobody(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    """Unnamed, but not unknown: somebody looked at this face and said it is not her. Filing the
    file under her without asking would overrule that answer."""
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", pile_id=PILE)
    await temp_db.execute(
        "INSERT INTO face_rejections (track_id, person_id, created_at) VALUES ('t1', ?, 0)",
        (HER,),
    )

    assert await evidence.contradicting(HER, ["a1"]) == {"a1"}
    # And the refusal is about HER: to anybody else the same face is only unnamed.
    assert await evidence.contradicting(SOMEBODY_ELSE, ["a1"]) == set()


async def test_a_file_holding_her_as_well_does_not_contradict(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    """Somebody else being in a file is not a reason to say the file is not hers: two people in
    one photograph is the ordinary case, not a contradiction."""
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", person_id=SOMEBODY_ELSE, attribution="matched")
    await _track(temp_db, "t2", "a1", person_id=HER)

    assert await evidence.contradicting(HER, ["a1"]) == set()


async def test_a_file_with_no_face_in_it_contradicts_nothing(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    """Half of what the whole feature exists for: a folder of body-only clips still takes the
    folder's name. A file nobody has looked at yet is the same answer for a different reason."""
    await _file(temp_db, "looked-at", "Her/one.mp4", HERS)
    await _file(temp_db, "never-read", "Her/two.mp4", HERS)
    await _looked(temp_db, "looked-at", "no_faces")

    assert await evidence.contradicting(HER, ["looked-at", "never-read"]) == set()


async def test_asking_about_no_files_asks_the_database_nothing(evidence: FaceEvidence) -> None:
    assert await evidence.contradicting(HER, []) == set()


async def test_naming_a_group_claims_its_unnamed_faces_and_leaves_the_named_ones_alone(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    """A face already attributed to somebody was decided elsewhere, and a folder's name is not
    evidence enough to overrule it."""
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", pile_id=PILE)
    await _track(temp_db, "t2", "a1", pile_id=PILE, person_id=SOMEBODY_ELSE)

    async with temp_db.write() as connection:
        named = await evidence.name_group(connection, PILE, HER)

    assert named == 1
    rows = await temp_db.fetch_all("SELECT id, person_id, attribution FROM face_tracks ORDER BY id")
    assert [(str(row["person_id"]), row["attribution"]) for row in rows] == [
        (HER, Attribution.CONFIRMED.value),
        (SOMEBODY_ELSE, None),
    ]


async def test_naming_a_group_that_has_nothing_left_to_claim_says_zero(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", pile_id=PILE, person_id=SOMEBODY_ELSE)

    async with temp_db.write() as connection:
        assert await evidence.name_group(connection, PILE, HER) == 0


async def test_the_stamp_moves_when_a_folders_faces_do(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    """The cheap number a pass reads to decide which folders are worth reading properly. It has to
    move when the answer would move, and it must not be the answer."""
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    before = await evidence.stamps()

    await _track(temp_db, "t1", "a1", pile_id=PILE)
    await _looked(temp_db, "a1", "none_identified")
    after = await evidence.stamps()

    assert before.get(HERS, None) is None or before[HERS].tracks == 0
    assert after[HERS].tracks == 1
    assert after[HERS].looked_at == 1


async def test_the_stamp_moves_when_a_regroup_remakes_a_folders_group(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    """A full regroup gives a group a new id without moving a face. The folder reader proposes a
    group by its id, so the folder has to be read again, or the proposal is never made for it."""
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", pile_id=PILE)
    before = await evidence.stamps()

    await temp_db.execute(
        "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at) "
        "VALUES (?, 'open', x'00', 1, 5000, 5000)",
        (OTHER_PILE,),
    )
    await temp_db.execute("UPDATE face_tracks SET pile_id = ? WHERE id = 't1'", (OTHER_PILE,))
    await temp_db.execute("DELETE FROM face_piles WHERE id = ?", (PILE,))
    after = await evidence.stamps()

    assert after[HERS].as_text() != before[HERS].as_text()


async def test_naming_a_group_says_which_faces_took_the_name(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    """Which ones, not how many, and that is what makes a bulk confirmation reversible.

    Only unclaimed faces take the name, so a group where some were already somebody's names a
    subset, and putting that back by clearing everything in the group that carries the person
    would take the others with it, which were decided elsewhere.
    """
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", pile_id=PILE)
    await _track(temp_db, "t2", "a1", pile_id=PILE, person_id=SOMEBODY_ELSE)

    async with temp_db.write() as connection:
        named = await evidence.name_group_recording(connection, PILE, HER)

    assert named == ["t1"]


async def test_unnaming_puts_back_exactly_the_faces_it_is_given(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    """By id rather than by group, so an undo puts back exactly what its own decision named."""
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", pile_id=PILE)
    await _track(temp_db, "t2", "a1", pile_id=PILE)
    async with temp_db.write() as connection:
        named = await evidence.name_group_recording(connection, PILE, HER)
    assert sorted(named) == ["t1", "t2"]

    async with temp_db.write() as connection:
        moved = await evidence.unname_faces(connection, ["t1"])

    assert moved == 1
    rows = await temp_db.fetch_all(
        "SELECT id, person_id, attribution, attributed_at FROM face_tracks ORDER BY id"
    )
    # The one put back carries nothing about a decision any more: a face with no person and a
    # moment it was attributed would describe a decision that has been taken back.
    assert rows[0]["person_id"] is None
    assert rows[0]["attribution"] is None
    assert rows[0]["attributed_at"] is None
    assert str(rows[1]["person_id"]) == HER


async def test_unnaming_nothing_asks_the_database_nothing(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    async with temp_db.write() as connection:
        assert await evidence.unname_faces(connection, []) == 0


async def test_naming_a_group_stamps_the_moment_in_milliseconds(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    """The unit every other writer of the column uses. In seconds, a group named here sorted as
    decided in 1970 on every read ordered by when."""
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", pile_id=PILE)

    async with temp_db.write() as connection:
        await evidence.name_group_recording(connection, PILE, HER)

    row = await temp_db.fetch_one("SELECT attributed_at FROM face_tracks WHERE id = 't1'")
    assert row is not None
    assert int(row["attributed_at"]) > 10**12


# --- teaching from a folder answer ----------------------------------------------------------------


async def test_teaching_hands_the_faces_to_the_service_and_asks_for_a_rematch(
    evidence: FaceEvidence, teacher: FakeTeacher, queue: FakeQueue
) -> None:
    """The other half of a folder answer: the named faces teach, and the rest of the library is
    compared with the gallery that just grew, as after every naming press."""
    teacher.answer = 2

    assert await evidence.teach(["t1", "t2"], HER) == 2

    assert teacher.taught == [(("t1", "t2"), HER)]
    assert queue.asked == [FACE_REMATCH]


async def test_teaching_nothing_asks_for_no_rematch(
    evidence: FaceEvidence, teacher: FakeTeacher, queue: FakeQueue
) -> None:
    """Nothing taught is nothing changed in any gallery, so there is nothing to compare again."""
    teacher.answer = 0

    assert await evidence.teach(["t1"], HER) == 0
    assert await evidence.teach([], HER) == 0

    assert teacher.taught == [(("t1",), HER)]
    assert queue.asked == []


async def test_unteaching_hands_the_faces_back_and_asks_for_a_rematch(
    evidence: FaceEvidence, teacher: FakeTeacher, queue: FakeQueue
) -> None:
    teacher.answer = 1

    assert await evidence.unteach(["t1"], HER) == 1

    assert teacher.untaught == [(("t1",), HER)]
    assert queue.asked == [FACE_REMATCH]


async def test_unteaching_nothing_asks_for_no_rematch(
    evidence: FaceEvidence, teacher: FakeTeacher, queue: FakeQueue
) -> None:
    """No faces is not a question for the service at all, and nothing taken back is nothing changed
    in any gallery, so neither compares the library again."""
    teacher.answer = 0

    assert await evidence.unteach([], HER) == 0
    assert await evidence.unteach(["t1"], HER) == 0

    assert teacher.untaught == [(("t1",), HER)]
    assert queue.asked == []


# --- groups proposed as somebody -------------------------------------------------------------------


async def _proposals(database: Database) -> list[tuple[str, str, str, int, int, str]]:
    rows = await database.fetch_all(
        "SELECT pile_id, person_id, folder_id, files, of_files, state FROM face_pile_proposals "
        "ORDER BY pile_id, person_id"
    )
    return [
        (
            str(row["pile_id"]),
            str(row["person_id"]),
            str(row["folder_id"]),
            int(row["files"]),
            int(row["of_files"]),
            str(row["state"]),
        )
        for row in rows
    ]


async def test_a_proposal_is_kept_and_its_counts_follow_the_folder(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", pile_id=PILE)

    assert await evidence.propose_group(PILE, HER, folder_id=HERS, files=4, of=5) is True
    assert await evidence.propose_group(PILE, HER, folder_id=HERS, files=6, of=7) is True

    assert await _proposals(temp_db) == [(PILE, HER, HERS, 6, 7, "pending")]


async def test_an_answered_proposal_is_never_made_again(
    evidence: FaceEvidence, temp_db: Database, settings: Settings
) -> None:
    """The pass runs whenever the folder's faces move. A question somebody has answered must not
    come back because a file was added."""
    store = Store(temp_db, data_dir=settings.data_dir)
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", pile_id=PILE)
    await evidence.propose_group(PILE, HER, folder_id=HERS, files=4, of=5)

    assert await store.settle_pile_proposal(PILE, HER, state="refused") is True
    assert await evidence.propose_group(PILE, HER, folder_id=HERS, files=9, of=9) is False

    assert await _proposals(temp_db) == [(PILE, HER, HERS, 4, 5, "refused")]
    assert await store.pile_proposals([PILE]) == []
    # And the answer is given once: a second answer finds nothing pending.
    assert await store.settle_pile_proposal(PILE, HER, state="accepted") is False


async def test_a_group_with_a_face_refused_as_her_is_never_proposed_as_her(
    evidence: FaceEvidence, temp_db: Database
) -> None:
    """The same answer given a face at a time, and it outlives the group: a group rebuilt under a new
    id is still those faces."""
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", pile_id=PILE)
    await temp_db.execute(
        "INSERT INTO face_rejections (track_id, person_id, created_at) VALUES ('t1', ?, 0)",
        (HER,),
    )

    assert await evidence.propose_group(PILE, HER, folder_id=HERS, files=4, of=5) is False
    assert await evidence.propose_group(PILE, SOMEBODY_ELSE, folder_id=HERS, files=4, of=5)

    assert [(one[0], one[1]) for one in await _proposals(temp_db)] == [(PILE, SOMEBODY_ELSE)]


async def test_a_group_set_aside_is_not_proposed(evidence: FaceEvidence, temp_db: Database) -> None:
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", pile_id=PILE, status="ignored")

    assert await evidence.propose_group(PILE, HER, folder_id=HERS, files=4, of=5) is False
    assert await _proposals(temp_db) == []


async def test_a_folder_naming_another_main_group_takes_back_the_first(
    evidence: FaceEvidence, temp_db: Database, settings: Settings
) -> None:
    """A folder has one main face. Once it names another group it has stopped naming the first,
    unless the first was answered, which is kept."""
    store = Store(temp_db, data_dir=settings.data_dir)
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", pile_id=PILE)
    await _track(temp_db, "t2", "a1", pile_id=OTHER_PILE)
    await evidence.propose_group(PILE, HER, folder_id=HERS, files=4, of=5)

    await evidence.propose_group(OTHER_PILE, HER, folder_id=HERS, files=5, of=6)
    assert [one[0] for one in await _proposals(temp_db)] == [OTHER_PILE]

    await store.settle_pile_proposal(OTHER_PILE, HER, state="refused")
    await evidence.propose_group(PILE, HER, folder_id=HERS, files=4, of=5)
    assert [(one[0], one[5]) for one in await _proposals(temp_db)] == [
        (PILE, "pending"),
        (OTHER_PILE, "refused"),
    ]


async def test_withdrawing_takes_back_only_what_is_pending(
    evidence: FaceEvidence, temp_db: Database, settings: Settings
) -> None:
    store = Store(temp_db, data_dir=settings.data_dir)
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", pile_id=PILE)
    await _track(temp_db, "t2", "a1", pile_id=OTHER_PILE)
    await evidence.propose_group(PILE, HER, folder_id=HERS, files=4, of=5)
    await evidence.propose_group(OTHER_PILE, SOMEBODY_ELSE, folder_id=HERS, files=4, of=5)
    await store.settle_pile_proposal(OTHER_PILE, SOMEBODY_ELSE, state="refused")

    assert await evidence.withdraw_proposals(HERS, HER) == 1
    assert await evidence.withdraw_proposals(HERS, SOMEBODY_ELSE) == 0

    assert [(one[0], one[5]) for one in await _proposals(temp_db)] == [(OTHER_PILE, "refused")]


async def test_a_proposal_goes_with_its_group(evidence: FaceEvidence, temp_db: Database) -> None:
    """A group rebuilt under a new id leaves no proposal about a group that does not exist."""
    await _file(temp_db, "a1", "Her/one.mp4", HERS)
    await _track(temp_db, "t1", "a1", pile_id=PILE)
    await evidence.propose_group(PILE, HER, folder_id=HERS, files=4, of=5)

    await temp_db.execute("DELETE FROM face_piles WHERE id = ?", (PILE,))

    assert await _proposals(temp_db) == []


async def test_a_page_of_folders_is_read_in_the_same_number_of_statements(
    evidence: FaceEvidence, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three times the folders on the Folders page and no statement more, each folder's answer
    the one it gets asked alone."""
    seen: list[str] = []
    real = db_module._judged

    @contextmanager
    def counted(stage: str, statement: Any, *rest: Any, **options: Any) -> Iterator[Any]:
        seen.append(statement_name(statement))
        with real(stage, statement, *rest, **options) as timing:
            yield timing

    folders: list[str] = []

    async def read_after(more: int) -> int:
        for _ in range(more):
            index = len(folders)
            folder_id = f"01HX00000000000000000009{index:02d}"
            folders.append(folder_id)
            await temp_db.execute(
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name)"
                " VALUES (?, ?, ?, ?, ?)",
                (folder_id, ROOT, TOP, f"F{index}", f"F{index}"),
            )
            mine, other, named = (f"f{index}-{one}" for one in ("mine", "other", "named"))
            for asset_id in (mine, other, named):
                await _file(temp_db, asset_id, f"F{index}/{asset_id}.mp4", folder_id)
                await _looked(temp_db, asset_id, "none_identified")
            await _track(temp_db, f"t{index}-a", mine, pile_id=f"pile-{index}")
            await _track(temp_db, f"t{index}-b", mine, pile_id=f"pile-{index}")
            await _track(temp_db, f"t{index}-c", other, pile_id=OTHER_PILE)
            await _track(
                temp_db, f"t{index}-d", named, person_id=HER, attribution=Attribution.CONFIRMED
            )
        asked = [*folders, TOP, EMPTY]
        seen.clear()
        with monkeypatch.context() as patched:
            patched.setattr(db_module, "_judged", counted)
            many = await evidence.faces_in_many(asked)
        ours = len(seen)
        for folder_id in asked:
            assert many[folder_id] == await evidence.faces_in(folder_id)
        assert many[folders[0]].dissenting == ("f0-named", "f0-other")
        return ours

    few = await read_after(2)
    assert await read_after(4) == few
