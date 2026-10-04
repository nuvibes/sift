# SPDX-License-Identifier: AGPL-3.0-or-later
"""The refusals and the empty answers, which are most of what this feature does.

A folder reader spends nearly all its time deciding NOT to say something, so the cases here are the
feature working rather than its edges: a folder nothing can be read out of, a name two people
answer to, a claim about a folder that has gone. Each of them is a place where doing the wrong
thing means a wrong attribution, and a wrong attribution in a library nobody audits gives no sign
which entries to distrust.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from sift.kernel.access import Repository, Viewer
from sift.kernel.attribution import FolderFaces
from sift.kernel.content import LibraryStore
from sift.kernel.db import Database
from sift.slices.suggestions.ladder import dominant
from sift.slices.suggestions.naming import (
    NEAR_MISS_EDITS,
    is_date_like,
    near_misses,
    person_in_filename,
    read_chain,
    reads_like_a_name,
    repeated_prefix,
    strip_noise,
)
from sift.slices.suggestions.service import (
    NotFound,
    SuggestionError,
    SuggestionService,
    _a_face_to_show,
)
from sift.slices.suggestions.store import Store
from sift.slices.suggestions.tests.conftest import (
    ANOTHER_JANE,
    NADIA,
    ONE_JANE,
    SOMEBODY_KNOWN,
    FakeFaces,
    Library,
)

pytestmark = pytest.mark.integration


async def people_of_asset(db: Database, asset_id: str) -> set[str]:
    rows = await db.fetch_all("SELECT person_id FROM asset_people WHERE asset_id = ?", (asset_id,))
    return {str(row["person_id"]) for row in rows}


class TestTheReaderSaysNothing:
    def test_a_tie_that_clears_the_share_still_names_nobody(self) -> None:
        # Both groups in every file that has a face. The share cannot separate them, so the
        # written-out tie guard is what does, and it has to, because picking one here is a wrong
        # attribution that looks exactly like a right one.
        assert dominant({"pile-a": 5, "pile-b": 5}, with_faces=5) is None

    def test_a_name_that_is_nothing_but_punctuation(self) -> None:
        assert strip_noise("...") == ""
        assert not reads_like_a_name("...")
        assert not is_date_like("...")

    def test_a_name_of_five_words_is_a_phrase(self) -> None:
        assert not reads_like_a_name("one two three four five")

    def test_a_folder_whose_deepest_word_is_not_a_name_claims_nobody(self) -> None:
        # `Instagram` is a site, so the deepest WORD is the sentence below it, which is not a
        # name, and the reading comes back with the site and nothing else.
        reading = read_chain(["Instagram", "send these to dave before friday"])
        assert reading.name == ""
        assert reading.site == "Instagram"

    def test_a_chain_of_nothing_but_sites(self) -> None:
        assert read_chain(["Instagram"]).name == ""
        assert read_chain(["Instagram"]).site == "Instagram"

    def test_a_prefix_that_is_not_name_shaped_is_no_prefix(self) -> None:
        assert repeated_prefix(["2023 - a - 1.mp4", "2023 - b - 2.mp4"]) == ""

    def test_a_filename_field_that_is_not_name_shaped_claims_nobody(self) -> None:
        assert person_in_filename("QMTV - 1080p - Show 34.mp4", after_prefix=True) == ""


class TestNearMisses:
    def test_a_letter_apart_is_worth_mentioning(self) -> None:
        assert near_misses("Nadia", [("p-1", "Nady")]) == ["p-1"]
        assert NEAR_MISS_EDITS == 2

    def test_the_same_name_is_not_a_near_miss(self) -> None:
        assert near_misses("Nadia", [("p-1", "nadia")]) == []

    def test_a_different_name_is_not_one_either(self) -> None:
        assert near_misses("Nadia", [("p-1", "Alexandra")]) == []

    def test_names_too_short_to_judge_are_left_alone(self) -> None:
        # Two edits turn any three-letter name into any other, so below the floor everybody would
        # be everybody else's near miss.
        assert near_misses("Ana", [("p-1", "Alex")]) == []
        assert near_misses("Alex", [("p-1", "Ana")]) == []

    def test_an_empty_name_matches_nobody(self) -> None:
        assert near_misses("", [("p-1", "Nadia")]) == []
        assert near_misses("Nadia", [("p-1", "")]) == []

    async def test_it_reaches_the_screen(
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
            await add_file(library, f"Nady/clip{index}.mp4")
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (NADIA, "Nadia")
        )
        await name_folders()
        faces.looked = {"Nady": (5, 5)}
        faces.piles_here = {"Nady": {"pile-1": 5}}
        await service.rebuild()

        claim = (await service.pending(admin)).items[0]
        assert claim.near_miss == NADIA
        # And nothing was merged. Suggesting one is the whole of what this does.
        assert claim.proposed == "Nady"

    async def test_a_near_miss_this_account_may_not_see_is_not_mentioned(
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
            await add_file(library, f"Nady/clip{index}.mp4")
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (NADIA, "Nadia")
        )
        await name_folders()
        faces.looked = {"Nady": (5, 5)}
        faces.piles_here = {"Nady": {"pile-1": 5}}
        await service.rebuild()

        await temp_db.execute(
            "INSERT INTO person_user_state (person_id, user_id, hidden, hidden_at, updated_at) "
            "VALUES (?, ?, 1, 0, 0)",
            (NADIA, admin.id),
        )
        claim = (await service.pending(admin)).items[0]
        assert claim.near_miss is None


class TestTheOnesThatCannotBeAnswered:
    async def test_a_name_two_people_answer_to_is_left_for_a_human(
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
            await add_file(library, f"Jane/clip{index}.mp4")
        await name_folders()
        faces.looked = {"Jane": (5, 5)}
        faces.piles_here = {"Jane": {"pile-1": 5}}
        await service.rebuild()
        claim = (await service.pending(admin)).items[0]

        # Two people called Jane arrive between the pass and the answer, which the stash-box allows
        # on purpose. Which of them was meant is not a question a folder name can answer.
        for person_id in (ONE_JANE, ANOTHER_JANE):
            await temp_db.execute(
                "INSERT INTO people (id, name, created_at) VALUES (?, 'Jane', 0)", (person_id,)
            )
        with pytest.raises(SuggestionError):
            await service.confirm(admin, claim.id)

    async def test_a_claim_about_a_folder_this_account_has_hidden(
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
        """Held between reading the list and answering it, so the answer is refused too.

        The list would already have dropped this row. The refusal here is the second half of the
        same rule, and it is the half that matters: a claim id somebody kept from an earlier page
        must not be a way to act on a folder they have since put away.
        """
        for index in range(5):
            await add_file(library, f"Nadia Vance/clip{index}.mp4")
        await name_folders()
        faces.looked = {"Nadia Vance": (5, 5)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 5}}
        await service.rebuild()
        claim = (await service.pending(admin)).items[0]

        row = await temp_db.fetch_one("SELECT id FROM folders WHERE name = ?", ("Nadia Vance",))
        assert row is not None
        await access.set_folder_vault(admin, str(row["id"]), vault=True)

        with pytest.raises(NotFound):
            await service.confirm(admin, claim.id)
        with pytest.raises(NotFound):
            await service.reject(admin, claim.id)

    async def test_rejecting_something_already_settled(
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
        await service.reject(admin, claim.id)
        with pytest.raises(NotFound):
            await service.reject(admin, claim.id)


class TestThePassDoesNothing:
    async def test_an_empty_folder_is_read_and_dropped(
        self, service: SuggestionService, store: Store, library: Library
    ) -> None:
        # A folder row with no files under it: the tree query never offers one, and this is the
        # belt to that brace: a folder emptied between the two reads of one pass.
        assert await service.rebuild() == 0

    async def test_a_folder_already_answered_for_that_person_is_not_written_again(
        self,
        service: SuggestionService,
        store: Store,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
    ) -> None:
        await add_file(library, "Nadia Vance/one.mp4")
        await add_file(library, "Nadia Vance/two.mp4")
        await add_file(library, "Nadia Vance/three.mp4")
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)",
            (SOMEBODY_KNOWN, "Someone"),
        )
        await name_folders()
        faces.looked = {"Nadia Vance": (3, 3)}
        faces.named_here = {"Nadia Vance": {SOMEBODY_KNOWN: 3}}

        assert await service.rebuild() == 3
        await temp_db.execute("DELETE FROM folder_passes", ())
        # Answered already, so the second pass writes nothing rather than three more rows.
        assert await service.rebuild() == 0

    async def test_a_site_folder_whose_filenames_name_nobody(
        self,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        await add_file(library, "QMTV/QMTV - 1080p.mp4")
        await add_file(library, "QMTV/QMTV - 720p.mp4")
        await name_folders()
        faces.looked = {"QMTV": (2, 0)}

        await service.rebuild()
        assert (await service.pending(admin)).items == []

    async def test_a_site_name_that_has_been_rejected(
        self,
        service: SuggestionService,
        store: Store,
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
        await service.reject(admin, claim.id)

        await store.database.execute("DELETE FROM folder_passes", ())
        await store.database.execute("DELETE FROM folder_claims", ())
        await service.rebuild()
        assert (await service.pending(admin)).items == []


class TestArrivals:
    async def test_a_file_landing_in_a_folder_nobody_answered_is_left_alone(
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
        await service.confirm(admin, claim.id)

        # A different folder entirely, which nobody has answered. Nothing standing applies to it,
        # and nothing has looked at it, so the pass leaves it alone.
        elsewhere = await add_file(library, "Somebody Else/one.mp4")
        await name_folders()
        await service.rebuild()
        assert await people_of_asset(temp_db, elsewhere.asset.id) == set()


class TestTheLastFewBranches:
    """Cases that exist because the code guards against them, proved rather than assumed."""

    def test_a_name_of_nothing_but_numbers(self) -> None:
        assert not reads_like_a_name("12 34")

    def test_files_with_no_names_share_no_prefix(self) -> None:
        assert repeated_prefix(["", ""]) == ""

    def test_a_filename_with_no_fields_at_all(self) -> None:
        assert person_in_filename("", after_prefix=False) == ""

    async def test_a_claim_this_folder_has_already_made_is_not_made_twice(
        self,
        service: SuggestionService,
        store: Store,
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

        # The pass runs again over the same folder, which is what happens when a file arrives.
        await store.database.execute("DELETE FROM folder_passes", ())
        assert await service.rebuild() == 0

    async def test_attributing_what_is_already_attributed_writes_nothing(
        self,
        service: SuggestionService,
        store: Store,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
    ) -> None:
        added = [await add_file(library, f"Nadia Vance/clip{i}.mp4") for i in range(3)]
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)",
            (SOMEBODY_KNOWN, "Someone"),
        )
        for one in added:
            await temp_db.execute(
                "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)",
                (one.asset.id, SOMEBODY_KNOWN),
            )
        await name_folders()
        faces.looked = {"Nadia Vance": (3, 3)}
        faces.named_here = {"Nadia Vance": {SOMEBODY_KNOWN: 3}}

        # Every file already carries them, so the rung fires and lands nothing.
        assert await service.rebuild() == 0

    async def test_a_folder_that_is_visible_but_holds_nothing_visible(
        self,
        service: SuggestionService,
        access: Repository,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        added = [await add_file(library, f"Nadia Vance/clip{i}.mp4") for i in range(3)]
        await name_folders()
        faces.looked = {"Nadia Vance": (3, 3)}
        faces.piles_here = {"Nadia Vance": {"pile-1": 3}}
        await service.rebuild()

        for one in added:
            await access.set_asset_vault(admin, one.asset.id, vault=True)
        # The folder itself is not concealed, but there is nothing in it this user may open,
        # so there is nothing to say about it, and saying "a folder of nothing" says something.
        assert (await service.pending(admin)).items == []

    async def test_confirming_a_name_somebody_already_has_finds_them_rather_than_making_one(
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

        # Created by hand between the pass and the answer.
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)",
            (NADIA, "nadia vance"),
        )
        applied = await service.confirm(admin, claim.id)
        assert applied.person_id == NADIA
        assert applied.created is False


async def test_the_schema_is_only_built_once(temp_db: Database) -> None:
    """A database already at this version is left alone rather than rebuilt over."""
    from sift.slices.suggestions import schema

    await temp_db.initialize_schema()
    async with temp_db.write() as connection:
        await schema.initialize(connection, schema.VERSION)


async def test_a_library_from_before_the_standing_no_to_a_silent_write_gains_it(
    temp_db: Database,
) -> None:
    """Step 11 on a library at 10: the table the Undo of a silent write keeps its no in, and
    nothing else touched."""
    from sift.slices.suggestions import schema

    await temp_db.initialize_schema()
    async with temp_db.write() as connection:
        await connection.execute("DROP TABLE folder_refusals")
        await schema.initialize(connection, 10)
        found = list(
            await connection.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'folder_refusals'"
            )
        )
    assert [row["name"] for row in found] == ["folder_refusals"]
    assert schema.VERSION == 11


async def test_a_standing_no_is_said_only_for_a_folder_still_here(
    store: Store, library: Library, library_store: LibraryStore
) -> None:
    """The Undo of a filing whose folder was deleted since has nothing to refuse: no row, and the
    answer says so. The same press on a folder still here says its no."""
    folder = (await library_store.upsert_folder(library.root.id, "refused/here")).id
    async with store.database.write() as connection:
        await connection.execute(
            "INSERT INTO people (id, name, created_at) VALUES ('p-refused', 'someone', 0)"
        )
        gone = await store.refuse_folder_person_on(
            connection, folder_id="f-gone", person_id="p-refused"
        )
        here = await store.refuse_folder_person_on(
            connection, folder_id=folder, person_id="p-refused"
        )
    assert (gone, here) == (False, True)
    assert await store.folder_refusals() == {(folder, "p-refused")}


# --- the picture on a folder claim ----------------------------------------------------------------


def _folder_with(piles: dict[str, int]) -> FolderFaces:
    return FolderFaces(piles=piles, portraits={pile: f"face-of-{pile}" for pile in piles})


def test_a_claim_shows_its_own_groups_face() -> None:
    assert _a_face_to_show("pile-a", _folder_with({"pile-a": 3, "pile-b": 9})) == "face-of-pile-a"


def test_a_claim_whose_group_was_rebuilt_away_shows_the_folders_biggest_group() -> None:
    """Regrouping replaces every pile wholesale and runs whenever a batch of scanning settles, so
    after any sweep the id on a claim names nothing. Without this the card whose whole reason for
    existing is "the same face runs through this folder" would draw a blank person glyph after a
    full rescan."""
    gone = "01HX000000000000000000GNE0"

    shown = _a_face_to_show(gone, _folder_with({"pile-small": 2, "pile-big": 17}))

    assert shown == "face-of-pile-big"


def test_the_biggest_group_is_picked_the_same_way_the_dissenting_count_picks_it() -> None:
    """Most files, then by id, so a folder with two equal groups does not answer differently from
    one read to the next."""
    shown = _a_face_to_show(None, _folder_with({"pile-b": 5, "pile-a": 5}))

    assert shown == "face-of-pile-b"


def test_a_folder_with_no_grouped_faces_left_shows_nothing_rather_than_guessing() -> None:
    assert _a_face_to_show("pile-gone", FolderFaces()) is None


async def test_a_username_whose_site_has_gone_tells_no_site(
    store: Store, temp_db: Database
) -> None:
    """A Site deleted leaves its Usernames with no Site: a press on one tells the Sites it is
    on, and there are none, while a Site named outright is told."""
    await temp_db.execute(
        "INSERT INTO sites (id, name, name_sort, kind, created_at) VALUES ('s1', 'Instagram',"
        " 'instagram', NULL, 0)"
    )
    for username_id, site_id in (("u-on", "s1"), ("u-loose", None)):
        await temp_db.execute(
            "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, 0)",
            (username_id, site_id, username_id),
        )
    async with store.write() as connection:
        assert await store.sites_of_on(connection, usernames=["u-loose"]) == set()
        assert await store.sites_of_on(connection, usernames=["u-on", "u-loose"]) == {"s1"}
        assert await store.sites_of_on(connection, named=["Instagram"]) == {"s1"}
