# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pass, the one action, and the promises about never asking twice.

Every library here is real: real files on a real disk, indexed the way a scan indexes them, so the
folder rows are the ones the scanner actually produces rather than ones a test invented.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

# Imported for its side effect: the workbench slice registers the ledger's table, which a username
# arriving is written to (`catalog._seed_username_on`). `temp_db.initialize_schema()` creates
# only what is registered, so without this the table would depend on import order in the worker.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import (
    MADE_BY_A_PERSON,
    Repository,
    Viewer,
    clear_refusal_on,
    create_person_on,
    refuse_person_on,
    refused_for,
    seed_site_username,
)
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.serving import face_version
from sift.slices.suggestions.service import SuggestionService
from sift.slices.suggestions.store import Store
from sift.slices.suggestions.tests.conftest import (
    ANOTHER_JANE,
    ONE_JANE,
    SOMEBODY_KNOWN,
    FakeFaces,
    FakePreferences,
    Library,
)
from sift.testing.fixtures import hide

pytestmark = pytest.mark.integration


async def people_of(db: Database, asset_id: str) -> set[str]:
    rows = await db.fetch_all("SELECT person_id FROM asset_people WHERE asset_id = ?", (asset_id,))
    return {str(row["person_id"]) for row in rows}


async def create_person(db: Database, name: str) -> str:
    """Somebody already in the library, which is the whole premise of recognising them."""
    person_id = new_id()
    await db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (person_id, name)
    )
    return person_id


async def person_named(db: Database, name: str) -> str | None:
    row = await db.fetch_one("SELECT id FROM people WHERE name = ? COLLATE NOCASE", (name,))
    return None if row is None else str(row["id"])


async def aliases_of(db: Database, person_id: str) -> set[str]:
    rows = await db.fetch_all("SELECT alias FROM people_aliases WHERE person_id = ?", (person_id,))
    return {str(row["alias"]) for row in rows}


class TestThePass:
    async def test_a_person_folder_with_one_group_becomes_a_question(
        self,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        for index in range(5):
            await add_file(library, f"Nadia Vance/clip{index}.mp4")
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 5}}

        assert await service.rebuild() == 1
        page = await service.pending(admin)
        assert len(page.items) == 1
        assert page.items[0].proposed == "Nadia Vance"
        assert page.items[0].group_id == "pile-1"
        assert page.items[0].evidence == "face_group"
        assert page.items[0].files == 5

    async def test_the_plan_is_the_folders_the_pass_reads_and_writes_nothing(
        self,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """A dry run asks `plan`; the pass reads exactly the folders it names, and planning twice
        asks nothing. Once read, a folder is not in the plan again."""
        for index in range(5):
            await add_file(library, f"Nadia Vance/clip{index}.mp4")
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 5}}

        planned = await service.plan()
        assert [one.name for one in planned.moved] == ["Nadia Vance"]
        assert [one.id for one in (await service.plan()).moved] == [planned.moved[0].id]
        assert (await service.pending(admin)).items == []

        assert await service.rebuild() == 1
        assert (await service.plan()).moved == ()

    async def test_the_face_beside_a_question_carries_its_crop_token(
        self,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        # The crop's address carries this, and an address without it is answered the careful way:
        # every visit to the Folders tab re-asked about every face on the page.
        for index in range(5):
            await add_file(library, f"Nadia Vance/clip{index}.mp4")
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 5}}

        assert await service.rebuild() == 1
        item = (await service.pending(admin)).items[0]
        assert item.face_id == "face-of-pile-1"
        assert item.face_art == face_version(admin.cache_stamp)
        assert item.face_art

    async def test_a_junk_folder_proposes_nothing(
        self,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        for index in range(5):
            await add_file(library, f"Videos/clip{index}.mp4")
        await name_folders()
        faces.looked = {"Videos": (5, 5)}
        faces.piles_here = {"Videos": {"pile-1": 5}}

        assert await service.rebuild() == 0
        assert (await service.pending(admin)).items == []

    async def test_a_named_group_attributes_the_folder_without_asking(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        added = [await add_file(library, f"Nadia Vance/clip{i}.mp4") for i in range(5)]
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)",
            (SOMEBODY_KNOWN, "Someone"),
        )
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.named_here = {"Nadia Vance": {SOMEBODY_KNOWN: 5}}

        await service.rebuild()
        assert (await service.pending(admin)).items == []
        for one in added:
            assert await people_of(temp_db, one.asset.id) == {SOMEBODY_KNOWN}

    async def test_a_silent_attribution_leaves_out_somebody_elses_files(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """A subfolder of other people's videos does not take the folder's name.

        The case: a library root named after somebody, holding a subfolder of
        unrelated clips. Her face runs through the root, so the root is hers and nobody is asked,
        and the claim covers the whole subtree, which is what makes a person filed across `Videos`
        and `Pics` one question instead of two. The subtree is also where somebody else's files sit.

        A silent write has to be narrower than one somebody approved, so the faces get a veto: a
        file holding other people's faces and none of hers is left alone. A file with no face in it
        is NOT left alone: it contradicts nothing, and body-only clips are half of what the
        feature exists for.
        """
        hers = [await add_file(library, f"Nadia Vance/clip{i}.mp4") for i in range(3)]
        faceless = await add_file(library, "Nadia Vance/PMV/no-faces.mp4")
        theirs = await add_file(library, "Nadia Vance/PMV/somebody-else.mp4")
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)",
            (SOMEBODY_KNOWN, "Someone"),
        )
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.named_here = {"Nadia Vance": {SOMEBODY_KNOWN: 3}}
        faces.contradicts = {SOMEBODY_KNOWN: {theirs.asset.id}}

        await service.rebuild()

        for one in hers:
            assert await people_of(temp_db, one.asset.id) == {SOMEBODY_KNOWN}
        assert await people_of(temp_db, faceless.asset.id) == {SOMEBODY_KNOWN}
        assert await people_of(temp_db, theirs.asset.id) == set()

    async def test_a_file_named_after_somebody_else_is_left_out_of_a_silent_write(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
    ) -> None:
        """The third veto, and the only one that reads the file's own name.

        A folder of one person's work can hold a handful of files posted by other people, and each
        of those says whose it is in its own name. Attributing the folder silently would put them
        under her: a wrong attribution wearing the shape of a right one.

        It withholds only where the name is somebody the library HOLDS. A file naming a word nobody
        here answers to is left in, because it may well be her under a spelling nobody has recorded.
        """
        hers = [await add_file(library, f"Nadia Vance/clip{i}.mp4") for i in range(3)]
        theirs = await add_file(library, "Nadia Vance/Talia Brandt - a night out.mp4")
        stranger = await add_file(library, "Nadia Vance/Dorian Halstead - guest spot.mp4")
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)",
            (SOMEBODY_KNOWN, "Someone"),
        )
        await create_person(temp_db, "Talia Brandt")
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.named_here = {"Nadia Vance": {SOMEBODY_KNOWN: 4}}

        await service.rebuild()

        for one in hers:
            assert await people_of(temp_db, one.asset.id) == {SOMEBODY_KNOWN}
        assert await people_of(temp_db, theirs.asset.id) == set()
        assert await people_of(temp_db, stranger.asset.id) == {SOMEBODY_KNOWN}, (
            "a name nobody in this library answers to held a file back"
        )

    async def test_the_name_check_only_looks_at_the_files_it_was_handed(
        self,
        service: SuggestionService,
        store: Store,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """Its own test, called directly, because the caller currently hands it every file.

        The check reads the folder and is asked about a SET, and those are two different lists the
        moment anything narrows the set before calling it, which is one line's distance away, and
        the veto it produces is permanent as far as anybody looking at the screen can tell.
        """
        held_back = await add_file(library, "Nadia Vance/Talia Brandt - a night out.mp4")
        also = await add_file(library, "Nadia Vance/Talia Brandt - backstage.mp4")
        person_id = await create_person(temp_db, "Talia Brandt")
        hers = await create_person(temp_db, "Nadia Vance")
        row = await temp_db.fetch_one("SELECT id FROM folders WHERE name = ?", ("Nadia Vance",))
        assert row is not None
        folder_id = str(row["id"])

        found = await service._files_naming_somebody_else(folder_id, hers, {held_back.asset.id})

        assert person_id
        assert found == {held_back.asset.id}, "a file outside the set was judged anyway"
        assert also.asset.id not in found

    async def test_a_folder_where_the_faces_veto_everything_writes_nothing(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """Every file in the folder holds somebody else and none of hers, so nothing is attributed.

        Its own case rather than a corner of the one above: the difference between "write what is
        left" and "write nothing" is a transaction that opens for no rows, and the folder-to-person
        record that goes with it. Filing the folder under her here would say a pass had decided
        something about a folder it in fact refused every file of.
        """
        theirs = [await add_file(library, f"Nadia Vance/other{i}.mp4") for i in range(3)]
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)",
            (SOMEBODY_KNOWN, "Someone"),
        )
        await name_folders()
        faces.looked = {"Nadia Vance": (3, 3)}
        faces.named_here = {"Nadia Vance": {SOMEBODY_KNOWN: 3}}
        faces.contradicts = {SOMEBODY_KNOWN: {one.asset.id for one in theirs}}

        await service.rebuild()

        for one in theirs:
            assert await people_of(temp_db, one.asset.id) == set()
        assert await service.filed(admin) == []

    async def test_taking_a_person_off_a_file_sticks(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
    ) -> None:
        """A name taken off by hand is not put back by the next pass.

        The pass re-reads a folder whenever its faces change, and the folder is still hers, so
        without a memory of the removal the file takes her name again, silently, and the only sign
        is somebody noticing it a second time. Deleting the attribution says what is true now and
        nothing about what should happen next.

        Recorded whoever made the attribution. Limiting it to the ones a pass wrote would leave the
        case wide open: a name put on by hand and taken off again is put back by the same rung.
        """
        files = [await add_file(library, f"Nadia Vance/clip{i}.mp4") for i in range(3)]
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)",
            (SOMEBODY_KNOWN, "Someone"),
        )
        await name_folders()
        faces.looked = {"Nadia Vance": (3, 3)}
        faces.named_here = {"Nadia Vance": {SOMEBODY_KNOWN: 3}}
        await service.rebuild()
        assert await people_of(temp_db, files[0].asset.id) == {SOMEBODY_KNOWN}

        # Taken off, the way the screen does it.
        async with temp_db.write() as connection:
            await connection.execute(
                "DELETE FROM asset_people WHERE asset_id = ? AND person_id = ?",
                (files[0].asset.id, SOMEBODY_KNOWN),
            )
            await refuse_person_on(
                connection, asset_id=files[0].asset.id, person_id=SOMEBODY_KNOWN, now=1
            )

        # A new file lands, which is what makes the pass read this folder again, and it is the
        # whole case: on a folder nothing is ever added to, deleting the row would have been enough.
        landed = await add_file(library, "Nadia Vance/clip-new.mp4")
        assert await service.rebuild() >= 0

        # The new one takes the name. The one somebody took her off does not get it back.
        assert await people_of(temp_db, landed.asset.id) == {SOMEBODY_KNOWN}
        assert await people_of(temp_db, files[0].asset.id) == set()
        assert await people_of(temp_db, files[1].asset.id) == {SOMEBODY_KNOWN}

    async def test_attributing_again_forgets_the_refusal(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
    ) -> None:
        """A change of mind needs no ceremony.

        A refusal left behind after somebody has attributed the same person again would go on
        blocking a pass from agreeing with a decision that has already been made.
        """
        one = await add_file(library, "Nadia Vance/clip.mp4")
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)",
            (SOMEBODY_KNOWN, "Someone"),
        )
        async with temp_db.write() as connection:
            await refuse_person_on(
                connection, asset_id=one.asset.id, person_id=SOMEBODY_KNOWN, now=1
            )
            await clear_refusal_on(connection, asset_id=one.asset.id, person_id=SOMEBODY_KNOWN)

        assert await refused_for(temp_db, SOMEBODY_KNOWN, [one.asset.id]) == set()

    async def test_a_silent_attribution_says_it_was_not_a_person(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
    ) -> None:
        """Every row a pass writes says a pass wrote it.

        Without this an inference nobody was asked about is the same row as a decision somebody
        made, and a sweep that got something wrong can only be found by remembering that it ran.
        """
        files = [await add_file(library, f"Nadia Vance/clip{i}.mp4") for i in range(3)]
        one = files[0]
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)",
            (SOMEBODY_KNOWN, "Someone"),
        )
        await name_folders()
        faces.looked = {"Nadia Vance": (3, 3)}
        faces.named_here = {"Nadia Vance": {SOMEBODY_KNOWN: 3}}

        await service.rebuild()

        rows = await temp_db.fetch_all(
            "SELECT source FROM asset_people WHERE asset_id = ?", (one.asset.id,)
        )
        assert [row["source"] for row in rows] == ["folder"]

    async def test_the_pass_is_incremental(
        self,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
    ) -> None:
        for index in range(5):
            await add_file(library, f"Nadia Vance/clip{index}.mp4")
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 5}}

        assert await service.rebuild() == 1
        # Nothing has moved, so the second pass reads no folder at all and files nothing.
        assert await service.rebuild() == 0

    async def test_a_folder_nothing_has_looked_at_yet_is_not_offered(
        self,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        for index in range(5):
            await add_file(library, f"Nadia Vance/clip{index}.mp4")
        await name_folders()
        faces.looked = {"Nadia Vance": (2, 0)}

        await service.rebuild()
        assert (await service.pending(admin)).items == []

    async def test_a_folder_looked_at_with_no_faces_is_offered_and_marked(
        self,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        for index in range(5):
            await add_file(library, f"Nadia Vance/clip{index}.mp4")
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 0)}

        await service.rebuild()
        page = await service.pending(admin)
        assert len(page.items) == 1
        assert page.items[0].evidence == "name_only"

    async def test_a_site_folder_proposes_the_site_and_the_names_under_it(
        self,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        await add_file(library, "QMTV/QMTV - Jane Doe - Show 34.mp4")
        await add_file(library, "QMTV/QMTV - Mary Roe - Show 35.mp4")
        await add_file(library, "QMTV/QMTV - Jane Doe - Show 36.mp4")
        await name_folders()
        faces.looked = {"QMTV": (3, 0)}

        await service.rebuild()
        page = await service.pending(admin)
        assert len(page.items) == 1
        assert page.items[0].kind == "site"
        assert page.items[0].proposed == "QMTV"
        assert page.items[0].per_file == ("Jane Doe", "Mary Roe")


class TestTheOneAction:
    async def test_confirming_does_everything_in_one_go(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        added = [await add_file(library, f"Instagram/harlowquin/clip{i}.mp4") for i in range(5)]
        await name_folders()
        faces.looked = {"harlowquin": (5, 5)}
        faces.piles_here = {"harlowquin": {"pile-1": 5}}
        await service.rebuild()

        claim = (await service.pending(admin)).items[0]
        applied = await service.confirm(admin, claim.id)

        # The person was made, spelled exactly as the folder spells it.
        assert applied.created is True
        assert await person_named(temp_db, "harlowquin") == applied.person_id
        # Every file in the folder carries them.
        assert applied.files == 5
        for one in added:
            assert await people_of(temp_db, one.asset.id) == {applied.person_id}
        # The face group was named.
        assert faces.named_groups == [("pile-1", applied.person_id)]
        # The folder's spelling became an also-known-as name.
        assert "harlowquin" in await aliases_of(temp_db, applied.person_id)
        # And the username folder's username now points at them: the join nothing ever wrote.
        assert applied.username_linked is True
        row = await temp_db.fetch_one(
            "SELECT person_id FROM usernames WHERE name = ?", ("harlowquin",)
        )
        assert row is not None
        assert str(row["person_id"]) == applied.person_id
        # And it is never asked again.
        assert (await service.pending(admin)).items == []

    async def test_the_account_a_confirmation_invents_says_the_folder_made_it(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """One act, one answer to who made it, on the person AND on the username beside them.

        Somebody pressed yes, and it is still the folder that made these rows: the username is the
        folder's own spelling and nothing on that screen typed it. `_person_for` says `folder`
        on the person, and the username seeded in the same transaction has to say it too: left
        to the default it would say Sift with no pass named, the one answer the column exists to
        rule out.
        """
        for index in range(5):
            await add_file(library, f"Instagram/harlowquin/clip{index}.mp4")
        await name_folders()
        faces.looked = {"harlowquin": (5, 5)}
        faces.piles_here = {"harlowquin": {"pile-1": 5}}
        await service.rebuild()

        claim = (await service.pending(admin)).items[0]
        applied = await service.confirm(admin, claim.id)

        person = await temp_db.fetch_one(
            "SELECT created_by_kind, created_by_via FROM people WHERE id = ?",
            (applied.person_id,),
        )
        assert person is not None
        assert (person["created_by_kind"], person["created_by_via"]) == ("sift", "folder")

        account = await temp_db.fetch_one(
            "SELECT p.created_by_kind AS kind, p.created_by_via AS via FROM usernames a"
            " JOIN sites p ON p.id = a.site_id WHERE a.name = ?",
            ("harlowquin",),
        )
        assert account is not None
        assert (account["kind"], account["via"]) == ("sift", "folder")

    async def test_a_person_this_answer_invents_is_given_the_first_file_as_their_picture(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """A person made by this answer is drawn as a monogram until somebody opens their page.

        On a library filed a folder at a time that is nearly everybody, so the decision that made
        them gives them the first file it attributed. The FIRST ATTRIBUTED file rather than the
        folder's own cover still: they are usually the same picture and they are not the same fact,
        and only one of the two is a file this decision actually wrote.
        """
        added = [await add_file(library, f"Marla Quist/clip{i}.mp4") for i in range(5)]
        await name_folders()
        faces.looked = {"Marla Quist": (5, 5)}
        faces.piles_here = {"Marla Quist": {"pile-1": 5}}
        await service.rebuild()

        claim = (await service.pending(admin)).items[0]
        applied = await service.confirm(admin, claim.id)

        assert applied.created is True
        row = await temp_db.fetch_one(
            "SELECT cover_asset_id FROM people WHERE id = ?", (applied.person_id,)
        )
        assert row is not None
        assert str(row["cover_asset_id"]) == added[0].asset.id

    async def test_a_person_who_already_had_a_picture_keeps_it(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """Only where this answer INVENTED them, and only where they have no picture already.

        Somebody already in the library has had a page and a chance to choose one, and a folder
        agreeing with their name is not grounds to replace what they chose. Both halves are asserted
        here because either alone would let a confirmation quietly rewrite a picture.

        The person is made AFTER the claim is raised, which is the sequence this actually happens in:
        a folder waits on the board, somebody adds that person in another window and gives them a
        picture, and the folder is answered afterwards. Made before it, the pass files the folder
        silently and there is no claim to answer at all, which is the feature working, and not this
        rule being exercised.
        """
        added = [await add_file(library, f"Marla Quist/clip{i}.mp4") for i in range(5)]
        await name_folders()
        faces.looked = {"Marla Quist": (5, 5)}
        faces.piles_here = {"Marla Quist": {"pile-1": 5}}
        await service.rebuild()
        claim = (await service.pending(admin)).items[0]

        # Somebody makes them in the meantime, and chooses a picture that is not the first file.
        async with temp_db.write() as connection:
            person_id = await create_person_on(connection, "Marla Quist", made=MADE_BY_A_PERSON)
            assert person_id is not None
            await connection.execute(
                "UPDATE people SET cover_asset_id = ? WHERE id = ?",
                (added[-1].asset.id, person_id),
            )

        applied = await service.confirm(admin, claim.id)

        assert applied.created is False, "they were already here"
        assert applied.person_id == person_id
        row = await temp_db.fetch_one(
            "SELECT cover_asset_id FROM people WHERE id = ?", (person_id,)
        )
        assert row is not None
        assert str(row["cover_asset_id"]) == added[-1].asset.id
        assert str(row["cover_asset_id"]) != added[0].asset.id

    async def test_the_odd_files_out_can_be_left_behind(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        added = [await add_file(library, f"Nadia Vance/clip{i}.mp4") for i in range(5)]
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 4}}
        faces.dissenting = {"Nadia Vance": (added[-1].asset.id,)}
        await service.rebuild()

        claim = (await service.pending(admin)).items[0]
        assert claim.dissenting == (added[-1].asset.id,)
        applied = await service.confirm(admin, claim.id, skip=[added[-1].asset.id])

        assert applied.files == 4
        assert await people_of(temp_db, added[-1].asset.id) == set()

    async def test_a_second_spelling_answers_itself(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        for index in range(5):
            await add_file(library, f"Nadia Vance/clip{index}.mp4")
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5), "nadia_vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 5}}
        await service.rebuild()
        claim = (await service.pending(admin)).items[0]
        applied = await service.confirm(admin, claim.id)

        # The same person, filed again under a different spelling.
        second = [await add_file(library, f"nadia_vance/other{i}.mp4") for i in range(5)]
        await name_folders()
        await service.rebuild()

        # Nothing to answer: the alias took it, silently.
        assert (await service.pending(admin)).items == []
        for one in second:
            assert await people_of(temp_db, one.asset.id) == {applied.person_id}

    async def test_a_rejection_survives_a_rescan(
        self,
        service: SuggestionService,
        store: Store,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        for index in range(5):
            await add_file(library, f"Sandbar Runways/clip{index}.mp4")
        await name_folders()
        faces.looked = {"Sandbar Runways": (5, 5)}
        faces.piles_here = {"Sandbar Runways": {"pile-1": 5}}
        await service.rebuild()

        claim = (await service.pending(admin)).items[0]
        assert (await service.reject(admin, claim.id)).settled is True

        # Every trace of the last pass forgotten, so the rescan is a genuinely fresh one.
        await store.database.execute("DELETE FROM folder_passes", ())
        await store.database.execute("DELETE FROM folder_claims", ())
        await service.rebuild()
        assert (await service.pending(admin)).items == []

    async def test_a_file_arriving_later_is_attributed_on_its_own(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        for index in range(5):
            await add_file(library, f"Nadia Vance/clip{index}.mp4")
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 5}}
        await service.rebuild()
        claim = (await service.pending(admin)).items[0]
        applied = await service.confirm(admin, claim.id)

        # A file lands in the folder that was answered, and the next pass (the one the queue
        # runs when a batch of imports settles) gives it the name without asking anybody.
        arriving = await add_file(library, "Nadia Vance/later.mp4")
        assert await service.rebuild() == 1
        assert await people_of(temp_db, arriving.asset.id) == {applied.person_id}

    async def test_a_file_moved_out_keeps_what_was_decided_about_it(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        added = [await add_file(library, f"Nadia Vance/clip{i}.mp4") for i in range(5)]
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 5}}
        await service.rebuild()
        claim = (await service.pending(admin)).items[0]
        applied = await service.confirm(admin, claim.id)

        # The copy is taken out of the folder, exactly as a move would.
        await temp_db.execute(
            "DELETE FROM asset_locations WHERE asset_id = ?", (added[0].asset.id,)
        )
        assert await people_of(temp_db, added[0].asset.id) == {applied.person_id}

    async def test_renaming_the_folder_does_not_re_ask(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        for index in range(5):
            await add_file(library, f"Nadia Vance/clip{index}.mp4")
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5), "Nadia.Vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 5}}
        await service.rebuild()
        claim = (await service.pending(admin)).items[0]
        await service.confirm(admin, claim.id)

        # Renamed on disk: a different row, a different spelling, the same person.
        await temp_db.execute(
            "UPDATE folders SET name = ?, rel_path = ? WHERE name = ?",
            ("Nadia.Vance", "Nadia.Vance", "Nadia Vance"),
        )
        await temp_db.execute("DELETE FROM folder_passes", ())
        await name_folders()
        await service.rebuild()
        assert (await service.pending(admin)).items == []

    async def test_confirming_twice_is_refused_rather_than_applied_twice(
        self,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        for index in range(5):
            await add_file(library, f"Nadia Vance/clip{index}.mp4")
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 5}}
        await service.rebuild()
        claim = (await service.pending(admin)).items[0]
        await service.confirm(admin, claim.id)

        from sift.slices.suggestions.service import NotFound

        with pytest.raises(NotFound):
            await service.confirm(admin, claim.id)


class TestConcealment:
    """The one place a quiet mistake here is a disclosure rather than an annoyance."""

    async def test_a_folder_the_reader_has_hidden_is_not_suggested(
        self,
        service: SuggestionService,
        access: Repository,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        for index in range(5):
            await add_file(library, f"Nadia Vance/clip{index}.mp4")
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 5}}
        await service.rebuild()
        assert len((await service.pending(admin)).items) == 1

        row = await temp_db.fetch_one("SELECT id FROM folders WHERE name = ?", ("Nadia Vance",))
        assert row is not None
        await access.set_folder_vault(admin, str(row["id"]), vault=True)

        page = await service.pending(admin)
        assert page.items == []
        # And the total agrees with the list. A count larger than what is on screen would publish
        # the number of folders this user is not being told about.
        assert page.total == 0

    async def test_the_count_is_of_what_the_reader_may_open(
        self,
        service: SuggestionService,
        access: Repository,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        added = [await add_file(library, f"Nadia Vance/clip{i}.mp4") for i in range(5)]
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 5}}
        await service.rebuild()
        assert (await service.pending(admin)).items[0].files == 5

        await access.set_asset_vault(admin, added[0].asset.id, vault=True)
        await access.set_asset_vault(admin, added[1].asset.id, vault=True)
        assert (await service.pending(admin)).items[0].files == 3

    async def test_a_concealed_file_is_not_offered_for_unticking(
        self,
        service: SuggestionService,
        access: Repository,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        added = [await add_file(library, f"Nadia Vance/clip{i}.mp4") for i in range(5)]
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 4}}
        faces.dissenting = {"Nadia Vance": (added[-1].asset.id,)}
        await service.rebuild()
        assert (await service.pending(admin)).items[0].dissenting == (added[-1].asset.id,)

        await access.set_asset_vault(admin, added[-1].asset.id, vault=True)
        assert (await service.pending(admin)).items[0].dissenting == ()

    async def test_confirming_does_not_make_a_concealed_file_visible(
        self,
        service: SuggestionService,
        access: Repository,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
        guest: Viewer,
    ) -> None:
        added = [await add_file(library, f"Nadia Vance/clip{i}.mp4") for i in range(5)]
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 5}}
        await service.rebuild()
        await access.set_asset_vault(admin, added[0].asset.id, vault=True)

        claim = (await service.pending(admin)).items[0]
        await service.confirm(admin, claim.id)

        # Still concealed from the user who hid it, after being attributed.
        visible = await access.visible_of(admin, [one.asset.id for one in added])
        assert added[0].asset.id not in visible
        # And nothing about it reached anybody else either.
        assert await access.visible_of(guest, [added[0].asset.id]) == set()

    async def test_a_guest_is_never_served_this_list(
        self,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        guest: Viewer,
    ) -> None:
        for index in range(5):
            await add_file(library, f"Nadia Vance/clip{index}.mp4")
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 5}}
        await service.rebuild()

        # The route refuses a guest outright; this proves the service would not have leaked one
        # either, because a guest can see none of the library and the list is scoped to what they
        # can see. Both are needed: the route is the control, this is what makes it safe if the
        # route ever moved.
        assert (await service.pending(guest)).items == []


class TestWhatWasFiledWithoutAsking:
    """The other half of the screen: work already done, rather than work outstanding.

    A pass that files a folder under somebody silently has to be able to say so afterwards, or the
    only evidence it ran is the attributions themselves. It is scoped exactly as the questions are
    and for the same reason: a folder is a thing that exists and a person is a name, so a row this
    user may not be shown is not a row it is told about.
    """

    async def test_a_folder_filed_silently_is_reported_with_its_person_and_its_count(
        self,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        temp_db: Database,
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        for index in range(5):
            await add_file(library, f"Nadia Vance/clip{index}.mp4")
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)",
            (SOMEBODY_KNOWN, "Someone"),
        )
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.named_here = {"Nadia Vance": {SOMEBODY_KNOWN: 5}}
        await service.rebuild()

        filed = await service.filed(admin)

        assert len(filed) == 1
        assert filed[0].person_id == SOMEBODY_KNOWN
        assert filed[0].person == "Someone"
        assert filed[0].folder == "Nadia Vance"
        assert filed[0].files == 5

    async def test_the_count_is_of_the_files_carrying_the_person_not_the_folder_size(
        self,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        temp_db: Database,
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """The faces hold some back, so the two numbers are different and the row must say which.

        Reporting the folder's size would say a pass touched files it deliberately left alone,
        which is the opposite of what this list is for.
        """
        for index in range(4):
            await add_file(library, f"Nadia Vance/clip{index}.mp4")
        theirs = await add_file(library, "Nadia Vance/PMV/somebody-else.mp4")
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)",
            (SOMEBODY_KNOWN, "Someone"),
        )
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.named_here = {"Nadia Vance": {SOMEBODY_KNOWN: 4}}
        faces.contradicts = {SOMEBODY_KNOWN: {theirs.asset.id}}
        await service.rebuild()

        assert (await service.filed(admin))[0].files == 4

    async def test_a_folder_this_account_may_not_see_is_not_reported(
        self,
        service: SuggestionService,
        access: Repository,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        for index in range(5):
            await add_file(library, f"Nadia Vance/clip{index}.mp4")
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)",
            (SOMEBODY_KNOWN, "Someone"),
        )
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.named_here = {"Nadia Vance": {SOMEBODY_KNOWN: 5}}
        await service.rebuild()
        assert len(await service.filed(admin)) == 1

        row = await temp_db.fetch_one("SELECT id FROM folders WHERE name = ?", ("Nadia Vance",))
        assert row is not None
        await access.set_folder_vault(admin, str(row["id"]), vault=True)

        assert await service.filed(admin) == []

    async def test_a_person_this_account_may_not_see_is_not_named(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """The two checks are separate, and this is the one the folder check cannot stand in for.

        The folder is perfectly visible. What is concealed is the name, which is the most
        identifying column there is, so the row goes rather than being reported without it.
        """
        for index in range(5):
            await add_file(library, f"Nadia Vance/clip{index}.mp4")
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)",
            (SOMEBODY_KNOWN, "Someone"),
        )
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.named_here = {"Nadia Vance": {SOMEBODY_KNOWN: 5}}
        await service.rebuild()
        assert len(await service.filed(admin)) == 1

        await hide(temp_db, "person", SOMEBODY_KNOWN, admin.id)

        assert await service.filed(admin) == []

    async def test_a_library_nothing_was_filed_in_reports_nothing(
        self, service: SuggestionService, admin: Viewer
    ) -> None:
        assert await service.filed(admin) == []


class TestTheTwoGuardsAreTwo:
    """The folder check and the file check are not the same check, and each is proved alone.

    A file can be visible while the folder holding it is not: an item-level share outranks a
    restrict on the folder, which is how one file is opened up inside an otherwise closed tree. So
    the file count can be non-zero for somebody who may not be told the folder exists, and a
    suggestion names the folder.
    """

    async def test_the_folder_check_fires_on_its_own(
        self,
        service: SuggestionService,
        access: Repository,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
        guest: Viewer,
    ) -> None:
        from sift.kernel.access import Effect, ObjectType

        added = [await add_file(library, f"Nadia Vance/clip{i}.mp4") for i in range(5)]
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 5}}
        await service.rebuild()

        row = await temp_db.fetch_one("SELECT id FROM folders WHERE name = ?", ("Nadia Vance",))
        assert row is not None
        folder_id = str(row["id"])
        await access.grant(ObjectType.ITEM, added[0].asset.id, guest.id, Effect.SHARE)

        # The file is theirs to see; the folder is not shared with them (a restriction on it would
        # hide the file too). So they may see the file and must not be told what it is filed under.
        assert await access.visible_of(guest, [added[0].asset.id]) == {added[0].asset.id}
        assert await access.get_folder(guest, folder_id) is None
        assert (await service.pending(guest)).items == []

    async def test_the_file_check_fires_on_its_own(
        self,
        service: SuggestionService,
        access: Repository,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        added = [await add_file(library, f"Nadia Vance/clip{i}.mp4") for i in range(5)]
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 5}}
        await service.rebuild()

        # The folder stays perfectly visible and every file in it is put away, which is a folder
        # this user can see the name of and nothing inside. Saying "a folder of nothing" about
        # it is still saying something about what is in it.
        for one in added:
            await access.set_asset_vault(admin, one.asset.id, vault=True)
        assert (await service.pending(admin)).items == []


class TestOnePersonIsOneQuestion:
    """A person filed across several folders is asked about once, about her own folder.

    The claim is filed against the folder whose name made it, not the folder the FILES sat in. On a
    person's folder holding `Videos` and `Pics` the other way would mean two rows on the screen for
    one person, each labelled with a word that names nobody, and each answer attributing a third of
    her library.
    """

    async def test_type_folders_under_a_person_are_one_claim_about_her(
        self,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        for index in range(3):
            await add_file(library, f"Nadia Vance/Videos/clip{index}.mp4")
        for index in range(3):
            await add_file(library, f"Nadia Vance/Pics/shot{index}.mp4")
        await name_folders()
        faces.looked = {"Nadia Vance": (6, 6)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 6}}

        await service.rebuild()
        page = await service.pending(admin)

        assert len(page.items) == 1
        assert page.items[0].proposed == "Nadia Vance"
        # Her folder, not the one the files happen to sit in.
        assert page.items[0].folder == "Nadia Vance"
        # And all of them, not a third.
        assert page.items[0].files == 6

    async def test_answering_it_attributes_the_whole_subtree(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        added = [await add_file(library, f"Nadia Vance/Videos/clip{i}.mp4") for i in range(3)]
        added += [await add_file(library, f"Nadia Vance/Pics/shot{i}.mp4") for i in range(3)]
        await name_folders()
        faces.looked = {"Nadia Vance": (6, 6)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 6}}
        await service.rebuild()

        claim = (await service.pending(admin)).items[0]
        applied = await service.confirm(admin, claim.id)

        assert applied.files == 6
        for one in added:
            assert await people_of(temp_db, one.asset.id) == {applied.person_id}

    async def test_a_file_arriving_in_either_of_them_afterwards_is_attributed(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        for index in range(3):
            await add_file(library, f"Nadia Vance/Videos/clip{index}.mp4")
        for index in range(3):
            await add_file(library, f"Nadia Vance/Pics/shot{index}.mp4")
        await name_folders()
        faces.looked = {"Nadia Vance": (6, 6)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 6}}
        await service.rebuild()
        claim = (await service.pending(admin)).items[0]
        applied = await service.confirm(admin, claim.id)

        # The standing decision is about HER folder, so it reaches a file landing anywhere under it.
        arriving = await add_file(library, "Nadia Vance/Pics/later.mp4")
        await service.rebuild()
        assert await people_of(temp_db, arriving.asset.id) == {applied.person_id}


class TestTheSiteFolder:
    """A folder whose every filename opens with the same word is a SITE, not a person.

    It is the one row on the screen that answers differently, and treating it as an ordinary folder
    is a wrong attribution of exactly the shape this feature exists to avoid: a person invented out
    of a Site's name, with every show in the folder filed under them.
    """

    async def test_confirming_it_makes_a_site_and_a_person_per_file(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        jane = await add_file(library, "QMTV/QMTV - Jane Doe - Show 34.mp4")
        mary = await add_file(library, "QMTV/QMTV - Mary Roe - Show 35.mp4")
        await name_folders()
        faces.looked = {"QMTV": (2, 0)}
        await service.rebuild()

        claim = (await service.pending(admin)).items[0]
        applied = await service.confirm(admin, claim.id)

        # The word became a site, and nobody was invented out of it.
        assert applied.site is True
        assert await person_named(temp_db, "QMTV") is None
        row = await temp_db.fetch_one("SELECT id FROM sites WHERE name = ?", ("QMTV",))
        assert row is not None

        # And each file carries the person its own filename named.
        assert applied.people == 2
        assert applied.person_id == ""
        jane_id = await person_named(temp_db, "jane doe")
        mary_id = await person_named(temp_db, "mary roe")
        assert jane_id is not None and mary_id is not None
        assert await people_of(temp_db, jane.asset.id) == {jane_id}
        assert await people_of(temp_db, mary.asset.id) == {mary_id}

    async def test_every_file_is_filed_under_the_site(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        added = [
            await add_file(library, "QMTV/QMTV - Jane Doe - Show 34.mp4"),
            await add_file(library, "QMTV/QMTV - Mary Roe - Show 35.mp4"),
        ]
        await name_folders()
        faces.looked = {"QMTV": (2, 0)}
        await service.rebuild()
        claim = (await service.pending(admin)).items[0]
        await service.confirm(admin, claim.id)

        # Through a username, because every question about a site reaches a file that way.
        for one in added:
            row = await temp_db.fetch_one(
                "SELECT COUNT(*) AS found FROM asset_usernames WHERE asset_id = ?", (one.asset.id,)
            )
            assert row is not None
            assert int(row["found"]) == 1

    async def test_a_name_unticked_is_left_out(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        jane = await add_file(library, "QMTV/QMTV - Jane Doe - Show 34.mp4")
        mary = await add_file(library, "QMTV/QMTV - Mary Roe - Show 35.mp4")
        await name_folders()
        faces.looked = {"QMTV": (2, 0)}
        await service.rebuild()

        claim = (await service.pending(admin)).items[0]
        applied = await service.confirm(admin, claim.id, skip=["mary roe"])

        assert applied.people == 1
        assert await person_named(temp_db, "mary roe") is None
        assert await people_of(temp_db, mary.asset.id) == set()
        assert await people_of(temp_db, jane.asset.id) != set()

    async def test_somebody_recognised_in_a_title_can_be_unticked_too(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """A show whose title has no name in the shape a tool writes, and holds one anyway.

        Two ways a file gets a person, and the screen shows one list of names to untick, so a
        name recognised inside a title has to answer to the same tick as one the reader pulled out
        of a filename, or unticking it does nothing and says nothing.
        """
        person_id = await create_person(temp_db, "Talia Brandt")
        jane = await add_file(library, "QMTV/QMTV - Jane Doe - Show 34.mp4")
        hers = await add_file(library, "QMTV/QMTV - Talia Brandt in Slow Motion 2023.mp4")
        await name_folders()
        faces.looked = {"QMTV": (2, 0)}
        await service.rebuild()

        claim = (await service.pending(admin)).items[0]
        applied = await service.confirm(admin, claim.id, skip=["Talia Brandt"])

        assert applied.people == 1
        assert await people_of(temp_db, hers.asset.id) == set()
        assert await people_of(temp_db, jane.asset.id) != set()
        assert await person_named(temp_db, "Talia Brandt") == person_id

    async def test_a_name_two_people_answer_to_is_left_alone(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        jane = await add_file(library, "QMTV/QMTV - Jane Doe - Show 34.mp4")
        for person_id in (ONE_JANE, ANOTHER_JANE):
            await temp_db.execute(
                "INSERT INTO people (id, name, created_at) VALUES (?, 'jane doe', 0)", (person_id,)
            )
        await add_file(library, "QMTV/QMTV - Mary Roe - Show 35.mp4")
        await name_folders()
        faces.looked = {"QMTV": (2, 0)}
        await service.rebuild()

        claim = (await service.pending(admin)).items[0]
        applied = await service.confirm(admin, claim.id)

        # Which Jane was meant is not a question a filename can settle.
        assert await people_of(temp_db, jane.asset.id) == set()
        assert applied.people == 1

    async def test_a_show_landing_afterwards_is_named_too(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        await add_file(library, "QMTV/QMTV - Jane Doe - Show 34.mp4")
        await add_file(library, "QMTV/QMTV - Mary Roe - Show 35.mp4")
        await name_folders()
        faces.looked = {"QMTV": (2, 0)}
        await service.rebuild()
        claim = (await service.pending(admin)).items[0]
        await service.confirm(admin, claim.id)

        # A Site folder has no single person to carry forward, so the filenames are read again.
        later = await add_file(library, "QMTV/QMTV - Ada Lovelace - Show 36.mp4")
        await service.rebuild()

        ada = await person_named(temp_db, "ada lovelace")
        assert ada is not None
        assert await people_of(temp_db, later.asset.id) == {ada}


class TestLearningTheAccountNumber:
    """The number a site knows a username by, read out of filenames it is already in.

    Without this pass the column could only be set by a download, which on a library built by a
    browser extension never happens.
    """

    async def _number_of(self, store: Store, handle: str) -> object:
        row = await store.database.fetch_one(
            "SELECT number FROM usernames WHERE name = ?", (handle,)
        )
        assert row is not None
        return row["number"]

    async def test_a_pass_fills_it_in_from_the_filenames(
        self,
        service: SuggestionService,
        store: Store,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
    ) -> None:
        await add_file(
            library, "harlowquin/harlowquin_3141592653_2718281828459045235_16180339887.jpg"
        )
        await name_folders()
        await seed_site_username(
            store.database, site="Instagram", name="harlowquin", made=MADE_BY_A_PERSON
        )

        await service.rebuild()

        assert await self._number_of(store, "harlowquin") == "16180339887"

    async def test_a_number_already_stored_is_never_overwritten(
        self,
        service: SuggestionService,
        store: Store,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
    ) -> None:
        """A number does not change, so a filename that disagrees is a question rather than an
        instruction, and two usernames sharing a spelling is exactly how that happens."""
        await add_file(
            library, "harlowquin/harlowquin_3141592653_2718281828459045235_16180339887.jpg"
        )
        await name_folders()
        await seed_site_username(
            store.database,
            site="Instagram",
            name="harlowquin",
            number="kept",
            made=MADE_BY_A_PERSON,
        )

        await service.rebuild()

        assert await self._number_of(store, "harlowquin") == "kept"

    async def test_a_filename_with_no_number_in_it_leaves_the_account_alone(
        self,
        service: SuggestionService,
        store: Store,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
    ) -> None:
        await add_file(library, "pellquorley/pellquorley-1414213562373095048.mp4")
        await name_folders()
        await seed_site_username(
            store.database, site="TikTok", name="pellquorley", made=MADE_BY_A_PERSON
        )

        await service.rebuild()

        assert await self._number_of(store, "pellquorley") is None

    async def test_a_file_that_only_starts_with_the_handle_is_not_about_that_account(
        self,
        service: SuggestionService,
        store: Store,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
    ) -> None:
        """The candidate query matches on a PREFIX, and a prefix is not an identity.

        A username called `orla` collects every file belonging to `orla fennimore` on the way in,
        and the number in one of those files is the other username's. Written, it would be permanent
        (the column is only ever filled when blank), and it would defeat the one thing the number
        exists for, because a rename on the real username would then fail to find its own row.
        """
        await add_file(library, "orla/orla_fennimore_1732050807_5772156649015328606_2236067977.jpg")
        await name_folders()
        await seed_site_username(
            store.database, site="Instagram", name="orla", made=MADE_BY_A_PERSON
        )

        await service.rebuild()

        assert await self._number_of(store, "orla") is None

    async def test_two_numbers_across_one_accounts_files_writes_neither(
        self,
        service: SuggestionService,
        store: Store,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
    ) -> None:
        """The background pass reads every file a username has, not one folder's worth.

        Two different numbers under one name is a library where the same word is two usernames, or
        a tool that wrote one of them wrong. Which is meant is not a question a filename can answer,
        and a guess here is permanent and silent, so neither is written.
        """
        for index, number in enumerate(("16180339887", "77777777777")):
            await add_file(
                library,
                f"harlowquin/harlowquin_314159265{index}_271828182845904523{index}_{number}.jpg",
            )
        await name_folders()
        await seed_site_username(
            store.database, site="Instagram", name="harlowquin", made=MADE_BY_A_PERSON
        )

        await service.rebuild()

        assert await self._number_of(store, "harlowquin") is None

    async def test_confirming_a_handle_gives_the_account_the_number_in_its_files(
        self,
        service: SuggestionService,
        store: Store,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
        faces: FakeFaces,
        admin: Viewer,
    ) -> None:
        """The point of the column, and nothing reached it.

        Confirming a username is the ONE place in Sift that has a number to give: a download knows
        only the username in the address. Without this the column would be filled by the background
        pass and read by nobody, so a username would still make a second row the day the site
        renamed it: exactly the failure the number exists to prevent.
        """
        for index in range(3):
            await add_file(
                library,
                f"Instagram/harlowquin/harlowquin_314159265{index}"
                f"_271828182845904523{index}_16180339887.jpg",
            )
        await name_folders()
        faces.looked = {"harlowquin": (3, 3)}
        faces.piles_here = {"harlowquin": {"pile-1": 3}}
        await service.rebuild()

        claim = next(one for one in (await service.pending(admin)).items if one.proposed)
        await service.confirm(admin, claim.id)

        assert await self._number_of(store, "harlowquin") == "16180339887"

    async def test_a_folder_holding_two_accounts_numbers_neither(
        self,
        service: SuggestionService,
        store: Store,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
        faces: FakeFaces,
        admin: Viewer,
    ) -> None:
        """Which of the two was meant is not a question a filename can answer, and the column is
        written once and never corrected, so a guess here is permanent and silent."""
        for index, number in enumerate(("16180339887", "77777777777", "77777777777")):
            await add_file(
                library,
                f"Instagram/harlowquin/harlowquin_314159265{index}"
                f"_271828182845904523{index}_{number}.jpg",
            )
        await name_folders()
        faces.looked = {"harlowquin": (3, 3)}
        faces.piles_here = {"harlowquin": {"pile-1": 3}}
        await service.rebuild()

        claim = next(one for one in (await service.pending(admin)).items if one.proposed)
        await service.confirm(admin, claim.id)

        assert await self._number_of(store, "harlowquin") is None


class TestAKnownPersonInOneFilesName:
    """The per-file half of the library recognising itself.

    The folder-level pass asks whether one known person accounts for a WHOLE folder. This asks the
    same question of one file, which is the only thing that can answer for a folder holding several
    people who are each named in their own file, and it is the only reading that reaches a title,
    because the filename reader can only pull out a name a tool assembled.
    """

    async def test_a_file_naming_somebody_the_library_holds_is_filed_under_them(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        # A title. Nothing in it says where a name begins or ends, and no rule would find her.
        talia = await add_file(
            library, "QMTV/QMTV - 1080 Talia Brandt in Slow Motion   Swim Week 2023.mp4"
        )
        await add_file(library, "QMTV/QMTV - Mary Roe - Show 35.mp4")
        await name_folders()
        faces.looked = {"QMTV": (2, 0)}
        person_id = await create_person(temp_db, "Talia Brandt")
        await service.rebuild()

        claim = (await service.pending(admin)).items[0]
        await service.confirm(admin, claim.id)

        assert await people_of(temp_db, talia.asset.id) == {person_id}

    async def test_a_file_naming_two_of_them_is_left_alone(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """A file about both is not a file about whichever matched longest. Picking one would be a
        wrong attribution wearing the shape of a right one."""
        both = await add_file(library, "QMTV/QMTV - Talia Brandt vs Linnea Ross 2023.mp4")
        await add_file(library, "QMTV/QMTV - Mary Roe - Show 35.mp4")
        await name_folders()
        faces.looked = {"QMTV": (2, 0)}
        await create_person(temp_db, "Talia Brandt")
        await create_person(temp_db, "Linnea Ross")
        await service.rebuild()

        claim = (await service.pending(admin)).items[0]
        await service.confirm(admin, claim.id)

        assert await people_of(temp_db, both.asset.id) == set()


class TestTheLibraryRecognisingItselfInFilenames:
    """A folder whose own name says nothing, holding files that name somebody already here.

    `Videos` names nobody by any rule and never will. The person is in the filenames, and she is
    already in the library because somebody put her there, so the question is not "what does this
    name look like" but "is this person in it", which the library can answer about itself.
    """

    async def test_a_folder_named_after_nobody_takes_the_person_in_its_files(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """The rung that gets better on its own: it only works once somebody exists to recognise.

        It OFFERS rather than files. A name in a title is not proof the file is of them (it may be
        who they were filmed with), so this is a question rather than an attribution.
        """
        person_id = await create_person(temp_db, "Talia Brandt")
        for name in (
            "1080 Talia Brandt in Slow Motion   QHB Fashion Week 2023.mp4",
            "Talia Brandt backstage clip two.mp4",
            "An evening with Talia Brandt.mp4",
            # Naming nobody, which is most of a folder and is not evidence either way.
            "beach sunset.mp4",
        ):
            await add_file(library, f"Videos/{name}")
        await name_folders()

        await service.rebuild()

        items = (await service.pending(admin)).items
        assert [(one.proposed, one.evidence) for one in items] == [
            ("Talia Brandt", "known_in_filenames")
        ]
        rows = await temp_db.fetch_all("SELECT id FROM assets", ())
        for row in rows:
            assert await people_of(temp_db, str(row["id"])) == set(), (
                "a name read out of a title attributed the file rather than asking about it"
            )
        assert await person_named(temp_db, "Talia Brandt") == person_id, (
            "recognising somebody already here made a second person"
        )

    async def test_two_known_people_across_one_folder_claim_nothing(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """A mixed folder, and a folder-wide claim about one of them would be wrong about the rest.

        The bar is the same one the faces use and has to be: the answer is one person or nobody,
        never an argument between two.
        """
        await create_person(temp_db, "Talia Brandt")
        await create_person(temp_db, "Nadia Vance")
        for name in (
            "1080 Talia Brandt in Slow Motion   QHB Fashion Week 2023.mp4",
            "An evening with Talia Brandt.mp4",
            "Nadia Vance on the beach.mp4",
        ):
            await add_file(library, f"Videos/{name}")
        await name_folders()

        await service.rebuild()

        assert (await service.pending(admin)).items == []

    async def test_a_name_already_said_no_to_is_not_offered_again(
        self,
        service: SuggestionService,
        store: Store,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """A rejection is somebody having answered this question, and asking it again by a different
        route is the same nuisance arriving from a direction they have no way to stop."""
        await create_person(temp_db, "Talia Brandt")
        for name in (
            "1080 Talia Brandt in Slow Motion   QHB Fashion Week 2023.mp4",
            "Talia Brandt backstage clip two.mp4",
            "An evening with Talia Brandt.mp4",
        ):
            await add_file(library, f"Videos/{name}")
        await name_folders()
        async with store.write() as connection:
            await store.reject_name_on(connection, "talia brandt")

        await service.rebuild()

        assert (await service.pending(admin)).items == []


class TestWhatAnAnswerAsksFor:
    async def test_an_answer_that_filed_files_tells_the_passes_that_read_them(
        self,
        store: Store,
        access: Repository,
        faces: FakeFaces,
        preferences: FakePreferences,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """The shoots pass reads who has loose pictures, and a folder answer is what makes them:
        the answer that filed files says so, once, after its own write; one that filed nothing
        says nothing."""
        told: list[int] = []

        async def after_filing() -> None:
            told.append(1)

        service = SuggestionService(
            store=store,
            access=access,
            faces=faces,
            preferences=preferences,
            after_filing=after_filing,
        )
        for i in range(5):
            await add_file(library, f"Instagram/harlowquin/clip{i}.mp4")
        await name_folders()
        faces.looked = {"harlowquin": (5, 5)}
        faces.piles_here = {"harlowquin": {"pile-1": 5}}
        await service.rebuild()
        claim = (await service.pending(admin)).items[0]

        applied = await service.confirm(admin, claim.id)

        assert applied.files == 5
        assert told == [1]

    async def test_a_pending_claim_takes_the_spelling_the_reader_uses_now(
        self,
        service: SuggestionService,
        store: Store,
        faces: FakeFaces,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """A claim still on the screen is re-spelled when the folder is read again with a reader
        that spells names differently; it counts as nothing new, and an answered claim is left."""
        for i in range(5):
            await add_file(library, f"Instagram/harlowquin/clip{i}.mp4")
        await name_folders()
        faces.looked = {"harlowquin": (5, 5)}
        faces.piles_here = {"harlowquin": {"pile-1": 5}}
        await service.rebuild()
        pending = (await service.pending(admin)).items[0]
        claim = await store.claim(pending.id)
        assert claim is not None

        written = await store.add_claim(
            folder_id=claim.folder_id,
            kind=claim.kind,
            name_key=claim.name_key,
            proposed=claim.proposed.upper(),
            person_id=None,
            group_id=None,
            site=None,
            is_username=False,
            evidence=claim.evidence,
        )

        assert written == 0
        assert (await service.pending(admin)).items[0].proposed == claim.proposed.upper()
