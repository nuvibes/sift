# SPDX-License-Identifier: AGPL-3.0-or-later
"""One decision, one receipt, and putting it back without touching anything else.

The two things this file is really about are the two the whole workbench rests on.

**A group is one decision.** A card covering forty-seven files issues one decision, never
forty-seven. The regression is silent (the screen looks the same, the files end up attributed
either way), and it only shows itself in the record, where somebody trying to undo a mistake finds
forty-seven rows to undo one at a time.

**An undo puts back what the decision wrote and nothing else.** Which means the assertions that
matter are about the rows it did NOT write: the file that already carried the person, the person who
already existed, the alias somebody had already added, the username already pointing somewhere.
Those are the ones a reversal built from the folder rather than from the record would take with it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from sift.kernel.access import Repository, Viewer, attribute_assets_on
from sift.kernel.db import Database
from sift.kernel.workbench import Workbench
from sift.slices.suggestions.queue import FolderQueue
from sift.slices.suggestions.service import SuggestionService
from sift.slices.suggestions.store import Store
from sift.slices.suggestions.tests.conftest import FakeFaces, FakePreferences, Library
from sift.slices.workbench.service import WorkbenchService
from sift.slices.workbench.store import Store as DecisionStore

pytestmark = pytest.mark.integration


async def people_of(db: Database, asset_id: str) -> set[str]:
    rows = await db.fetch_all("SELECT person_id FROM asset_people WHERE asset_id = ?", (asset_id,))
    return {str(row["person_id"]) for row in rows}


async def person_named(db: Database, name: str) -> str | None:
    row = await db.fetch_one("SELECT id FROM people WHERE name = ?", (name,))
    return None if row is None else str(row["id"])


async def aliases_of(db: Database, person_id: str) -> set[str]:
    rows = await db.fetch_all("SELECT alias FROM people_aliases WHERE person_id = ?", (person_id,))
    return {str(row["alias"]) for row in rows}


async def subjects_of(db: Database, decision_id: str) -> set[tuple[str, str]]:
    rows = await db.fetch_all(
        "SELECT kind, subject_id FROM workbench_decision_subjects WHERE decision_id = ?",
        (decision_id,),
    )
    return {(str(row["kind"]), str(row["subject_id"])) for row in rows}


@pytest.fixture
async def decisions(temp_db: Database) -> DecisionStore:
    return DecisionStore(temp_db)


@pytest.fixture
async def recording(
    store: Store,
    access: Repository,
    faces: FakeFaces,
    preferences: FakePreferences,
    decisions: DecisionStore,
) -> SuggestionService:
    """The service as the application builds it, writing a receipt for every decision."""
    return SuggestionService(
        store=store, access=access, faces=faces, preferences=preferences, recorder=decisions
    )


@pytest.fixture
def undoing(
    recording: SuggestionService, decisions: DecisionStore, workbench: Workbench
) -> WorkbenchService:
    workbench.register(FolderQueue(recording))
    return WorkbenchService(store=decisions, workbench=workbench)


@pytest.fixture
def workbench() -> Workbench:
    return Workbench()


class TestOneDecisionForTheWholeGroup:
    async def test_a_card_of_many_files_issues_one_decision(
        self,
        recording: SuggestionService,
        decisions: DecisionStore,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """The single choice that decides whether this screen is usable at this library's size.

        Forty-seven files, one press, one row in the record. Degraded into a decision per file it
        would still attribute everything correctly and still look right, and the record would be
        forty-seven rows nobody can undo in one go.
        """
        for index in range(47):
            await add_file(library, f"Reya Solberg/clip{index}.mp4")
        await name_folders()
        faces.looked = {"Reya Solberg": (47, 47)}
        faces.piles_here = {"Reya Solberg": {"pile-1": 47}}
        await recording.rebuild()

        claim = (await recording.pending(admin)).items[0]
        applied = await recording.confirm(admin, claim.id)

        assert applied.files == 47
        found, total = await decisions.recent(limit=100, offset=0)
        assert total == 1
        assert len(found) == 1

    async def test_and_the_one_receipt_names_the_whole_group(
        self,
        recording: SuggestionService,
        decisions: DecisionStore,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """The count is on the receipt because that is what makes it one decision rather than a
        row that happens to be alone."""
        for index in range(47):
            await add_file(library, f"Reya Solberg/clip{index}.mp4")
        await name_folders()
        faces.on = False
        await recording.rebuild()

        claim = (await recording.pending(admin)).items[0]
        await recording.confirm(admin, claim.id)

        found, _ = await decisions.recent(limit=10, offset=0)
        assert "47 files" in found[0].title
        written = json.loads(found[0].payload)["written"]
        assert len(written["attributed"]) == 47


class TestUndoPutsBackOnlyWhatItWrote:
    async def test_it_detaches_the_files_the_decision_attributed(
        self,
        recording: SuggestionService,
        undoing: WorkbenchService,
        decisions: DecisionStore,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        added = [await add_file(library, f"Reya Solberg/clip{index}.mp4") for index in range(4)]
        await name_folders()
        faces.on = False
        await recording.rebuild()
        claim = (await recording.pending(admin)).items[0]
        applied = await recording.confirm(admin, claim.id)

        found, _ = await decisions.recent(limit=10, offset=0)
        assert (await undoing.undo(admin, found[0].id)).put_back == 1

        for one in added:
            assert await people_of(temp_db, one.asset.id) == set()
        assert applied.person_id is not None

    async def test_a_site_folder_s_filings_come_off_and_the_site_stays(
        self,
        recording: SuggestionService,
        undoing: WorkbenchService,
        decisions: DecisionStore,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """Yes to a Site folder files every file under the Site, so its undo takes the files off the
        Site as well as the people: left filed, the Site would go on counting the whole folder
        after the decision that put it there was taken back. The Site itself stays, as it is
        written."""
        added = [
            await add_file(library, "QMTV/QMTV - Jane Doe - Show 34.mp4"),
            await add_file(library, "QMTV/QMTV - Mary Roe - Show 35.mp4"),
        ]
        await name_folders()
        faces.looked = {"QMTV": (2, 0)}
        await recording.rebuild()
        claim = (await recording.pending(admin)).items[0]
        await recording.confirm(admin, claim.id)

        found, _ = await decisions.recent(limit=10, offset=0)
        assert (await undoing.undo(admin, found[0].id)).put_back == 1

        for one in added:
            row = await temp_db.fetch_one(
                "SELECT COUNT(*) AS found FROM asset_usernames WHERE asset_id = ?", (one.asset.id,)
            )
            assert row is not None and int(row["found"]) == 0
        site = await temp_db.fetch_one("SELECT id FROM sites WHERE name = ?", ("QMTV",))
        assert site is not None

    async def test_but_leaves_a_file_that_already_carried_that_person(
        self,
        recording: SuggestionService,
        undoing: WorkbenchService,
        decisions: DecisionStore,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """The fault this whole record exists to prevent: testing only what the decision wrote and
        never what it left alone.

        One file was attributed by hand, before the folder was ever confirmed. The confirmation
        found it already done and wrote nothing for it. Undoing the confirmation must not take that
        earlier decision with it.
        """
        added = [await add_file(library, f"Reya Solberg/clip{index}.mp4") for index in range(4)]
        await name_folders()
        faces.on = False
        await recording.rebuild()
        claim = (await recording.pending(admin)).items[0]

        # Somebody said so by hand, first. This is not the confirmation's row, and the
        # confirmation will find it already done and write nothing for that file.
        person_id = await _make_person(temp_db, claim.proposed)
        async with temp_db.write() as connection:
            await attribute_assets_on(
                connection, asset_ids=[added[0].asset.id], person_id=person_id
            )

        await recording.confirm(admin, claim.id)
        found, _ = await decisions.recent(limit=10, offset=0)
        await undoing.undo(admin, found[0].id)

        assert await people_of(temp_db, added[0].asset.id) == {person_id}
        assert await people_of(temp_db, added[1].asset.id) == set()

    async def test_and_keeps_a_person_who_already_existed(
        self,
        recording: SuggestionService,
        undoing: WorkbenchService,
        decisions: DecisionStore,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """A person the decision found rather than made. Removing them on an undo would take their
        whole history with them: every file, tag and note attached anywhere else."""
        await add_file(library, "Reya Solberg/clip.mp4")
        await name_folders()
        faces.on = False
        await recording.rebuild()
        claim = (await recording.pending(admin)).items[0]

        # Made after the question was raised, so the confirmation FINDS them rather than inventing
        # them. A folder whose name already names somebody is attributed silently and never asked
        # about at all, which is correct and is not the case this is for.
        person_id = await _make_person(temp_db, claim.proposed)

        applied = await recording.confirm(admin, claim.id)
        assert applied.created is False

        found, _ = await decisions.recent(limit=10, offset=0)
        await undoing.undo(admin, found[0].id)

        assert await person_named(temp_db, claim.proposed) == person_id

    async def test_and_keeps_an_alias_that_was_already_written(
        self,
        recording: SuggestionService,
        undoing: WorkbenchService,
        decisions: DecisionStore,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """Somebody wrote the spelling down first. The confirmation found it there and added
        nothing, so there is nothing for the undo to take back, and taking it back anyway would
        lose a name they typed, which no later pass would ever put back."""
        await add_file(library, "Reya Solberg/clip.mp4")
        await name_folders()
        faces.on = False
        await recording.rebuild()
        claim = (await recording.pending(admin)).items[0]

        # Written down after the question was raised, for the reason above.
        person_id = await _make_person(temp_db, claim.proposed)
        async with temp_db.write() as connection:
            await connection.execute(
                "INSERT INTO people_aliases (id, person_id, alias) VALUES ('a1', ?, ?)",
                (person_id, claim.proposed),
            )

        await recording.confirm(admin, claim.id)

        found, _ = await decisions.recent(limit=10, offset=0)
        await undoing.undo(admin, found[0].id)

        assert claim.proposed in await aliases_of(temp_db, person_id)

    async def test_and_puts_the_question_back_so_it_can_be_answered_again(
        self,
        recording: SuggestionService,
        undoing: WorkbenchService,
        decisions: DecisionStore,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """Undoing a wrong answer has to leave the question outstanding. Left settled, the folder
        would be silently unattributed for ever with nothing anywhere offering to fix it."""
        await add_file(library, "Reya Solberg/clip.mp4")
        await name_folders()
        faces.on = False
        await recording.rebuild()
        claim = (await recording.pending(admin)).items[0]
        await recording.confirm(admin, claim.id)
        assert (await recording.pending(admin)).items == []

        found, _ = await decisions.recent(limit=10, offset=0)
        await undoing.undo(admin, found[0].id)

        assert [one.id for one in (await recording.pending(admin)).items] == [claim.id]

    async def test_and_forgets_the_standing_answer_the_confirmation_recorded(
        self,
        recording: SuggestionService,
        undoing: WorkbenchService,
        decisions: DecisionStore,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """Load-bearing rather than tidy. The standing answer is what re-applies the person to files
        that land in the folder later, so leaving it behind has the very next pass put back
        exactly what the undo removed, with nothing on any screen saying why."""
        await add_file(library, "Reya Solberg/clip.mp4")
        await name_folders()
        faces.on = False
        await recording.rebuild()
        claim = (await recording.pending(admin)).items[0]

        # The person has to SURVIVE the undo for this to test anything. One the decision invented
        # is removed, and removing a person takes their standing answers with them, so the
        # explicit forget would look unnecessary in exactly the case where it is not needed, and
        # the case where it IS needed would go unwatched.
        person_id = await _make_person(temp_db, claim.proposed)

        await recording.confirm(admin, claim.id)
        assert await _standing_answers(temp_db) == 1

        found, _ = await decisions.recent(limit=10, offset=0)
        await undoing.undo(admin, found[0].id)

        assert await person_named(temp_db, claim.proposed) == person_id
        assert await _standing_answers(temp_db) == 0

    async def test_and_removes_a_person_it_invented_and_nothing_is_left_on(
        self,
        recording: SuggestionService,
        undoing: WorkbenchService,
        decisions: DecisionStore,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """Otherwise an undo leaves a person nobody made a decision about, on the People wall,
        with nothing on them and no sign of where they came from."""
        await add_file(library, "Reya Solberg/clip.mp4")
        await name_folders()
        faces.on = False
        await recording.rebuild()
        claim = (await recording.pending(admin)).items[0]
        applied = await recording.confirm(admin, claim.id)
        assert applied.created is True

        found, _ = await decisions.recent(limit=10, offset=0)
        await undoing.undo(admin, found[0].id)

        assert await person_named(temp_db, claim.proposed) is None

    async def test_but_keeps_one_it_invented_that_has_since_been_used(
        self,
        recording: SuggestionService,
        undoing: WorkbenchService,
        decisions: DecisionStore,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """Created by the decision is not enough on its own.

        Between a bulk confirmation and somebody noticing it was wrong, files can have been put on
        that person by hand, and removing the person then takes all of that with it, which is a
        far larger thing than the decision being undone. So it is emptiness that decides, not
        authorship.
        """
        await add_file(library, "Reya Solberg/clip.mp4")
        # A file outside the folder, so putting the person on it later is plainly not something
        # the confirmation did. Its own folder is named for no one, so it raises no question.
        elsewhere = await add_file(library, "beach.mp4")
        await name_folders()
        faces.on = False
        await recording.rebuild()
        claim = next(
            one for one in (await recording.pending(admin)).items if one.folder == "Reya Solberg"
        )
        applied = await recording.confirm(admin, claim.id)

        # Somebody put another file on them afterwards, by hand.
        async with temp_db.write() as connection:
            await attribute_assets_on(
                connection, asset_ids=[elsewhere.asset.id], person_id=applied.person_id
            )

        found, _ = await decisions.recent(limit=10, offset=0)
        await undoing.undo(admin, found[0].id)

        assert await person_named(temp_db, claim.proposed) == applied.person_id
        assert await people_of(temp_db, elsewhere.asset.id) == {applied.person_id}


class TestSettingAFolderAside:
    async def test_it_writes_a_receipt_like_everything_else(
        self,
        recording: SuggestionService,
        decisions: DecisionStore,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        await add_file(library, "Sandbar Runways/clip.mp4")
        await name_folders()
        faces.on = False
        await recording.rebuild()
        claim = (await recording.pending(admin)).items[0]

        await recording.reject(admin, claim.id)

        found, total = await decisions.recent(limit=10, offset=0)
        assert total == 1
        assert "set aside" in found[0].title

    async def test_and_can_be_taken_back_after_a_slip(
        self,
        recording: SuggestionService,
        undoing: WorkbenchService,
        decisions: DecisionStore,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """Permanent means no pass ever raises it again. It does not mean a mis-click cannot be
        corrected, and those are different promises."""
        await add_file(library, "Sandbar Runways/clip.mp4")
        await name_folders()
        faces.on = False
        await recording.rebuild()
        claim = (await recording.pending(admin)).items[0]
        await recording.reject(admin, claim.id)
        assert (await recording.pending(admin)).items == []

        found, _ = await decisions.recent(limit=10, offset=0)
        assert (await undoing.undo(admin, found[0].id)).put_back == 1

        assert [one.id for one in (await recording.pending(admin)).items] == [claim.id]

    async def test_and_the_permanent_no_is_forgotten_with_it(
        self,
        recording: SuggestionService,
        undoing: WorkbenchService,
        decisions: DecisionStore,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """Both halves, or the feature lies. The claim back without the no forgotten is a question
        the next pass settles again on its own, seconds later."""
        await add_file(library, "Sandbar Runways/clip.mp4")
        await name_folders()
        faces.on = False
        await recording.rebuild()
        claim = (await recording.pending(admin)).items[0]
        await recording.reject(admin, claim.id)

        found, _ = await decisions.recent(limit=10, offset=0)
        await undoing.undo(admin, found[0].id)

        rows = await temp_db.fetch_all("SELECT name_key FROM claim_rejections", ())
        assert rows == []


async def _standing_answers(db: Database) -> int:
    """How many folders carry a standing answer: the memory that re-applies a person to files
    landing in the folder later."""
    rows = await db.fetch_all("SELECT folder_id FROM folder_people", ())
    return len(rows)


async def _make_person(db: Database, name: str) -> str:
    from sift.kernel.ids import new_id

    person_id = new_id()
    async with db.write() as connection:
        await connection.execute(
            "INSERT INTO people (id, name, notes, created_at) VALUES (?, ?, NULL, 0)",
            (person_id, name),
        )
    return person_id


class TestAHandleFolder:
    async def test_confirming_points_the_handle_at_the_person_and_undo_lets_it_go(
        self,
        recording: SuggestionService,
        undoing: WorkbenchService,
        decisions: DecisionStore,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """A folder named after a username on a site is the one moment somebody has actually said
        the username and the person are the same. Taking the decision back has to let that go again,
        and scoped to the person it named, so a link made afterwards to somebody else survives.
        """
        await add_file(library, "Instagram/harlowquin/clip.mp4")
        await name_folders()
        faces.on = False
        await recording.rebuild()
        claim = next(
            one for one in (await recording.pending(admin)).items if one.folder == "harlowquin"
        )
        applied = await recording.confirm(admin, claim.id)
        assert applied.username_linked is True

        row = await temp_db.fetch_one(
            "SELECT person_id FROM usernames WHERE name = ?", ("harlowquin",)
        )
        assert row is not None and str(row["person_id"]) == applied.person_id

        found, _ = await decisions.recent(limit=10, offset=0)
        await undoing.undo(admin, found[0].id)

        # The username itself stays: it is a row about a site, not about this decision. What goes
        # is the claim that it is this person.
        after = await temp_db.fetch_one(
            "SELECT person_id FROM usernames WHERE name = ?", ("harlowquin",)
        )
        assert after is not None
        assert after["person_id"] is None


class TestWhatTheDecisionSaysItWasAbout:
    """The link that puts a folder confirmation into each of its files' histories.

    Read out of `Written`, which is already the list of rows the decision created and is the same
    list undo works from, so a confirmation of two hundred files records what it touched without
    a single extra query.
    """

    async def test_a_confirmation_names_the_folder_its_files_and_the_person(
        self,
        recording: SuggestionService,
        decisions: DecisionStore,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        added = [await add_file(library, f"Reya Solberg/clip{index}.mp4") for index in range(3)]
        await name_folders()
        faces.on = False
        await recording.rebuild()
        claim = (await recording.pending(admin)).items[0]

        applied = await recording.confirm(admin, claim.id)

        found, _ = await decisions.recent(limit=10, offset=0)
        assert await subjects_of(temp_db, found[0].id) == {
            ("folder", claim.folder_id),
            ("person", applied.person_id),
            *{("asset", one.asset.id) for one in added},
        }

    async def test_a_no_names_the_folder_and_nothing_else(
        self,
        recording: SuggestionService,
        decisions: DecisionStore,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """A no writes nothing onto any file, so there is nothing else it touched, and the
        folder's files are deliberately NOT read for it, which would put a page read on a request
        that costs two small writes."""
        await add_file(library, "Sandbar Runways/clip.mp4")
        await name_folders()
        faces.on = False
        await recording.rebuild()
        claim = (await recording.pending(admin)).items[0]

        await recording.reject(admin, claim.id)

        found, _ = await decisions.recent(limit=10, offset=0)
        assert await subjects_of(temp_db, found[0].id) == {("folder", claim.folder_id)}
