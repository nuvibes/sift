# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a file's own name for where it came from, and a folder's for whose username it is.

Two readers and two postures, and the difference between them is the whole design.

**A filename shape applies itself.** It is the narrowest reader in the slice (one shape, both
halves present, in one order), and its known false reads are rare. Each of them is refused here,
by name, because a reader whose known failures are fixed at the reader is worth more than a queue
of forty cards nobody can check by eye.

**A folder shape only ever asks.** `<username> (Coomer)` can reach many more files and rests on
one person's filing habit rather than on a tool's signature, so it makes a claim and the board
answers it.

What neither of them writes is a PERSON. A username is what a site calls somebody, and one name
can be a username on several different sites together, so a username is evidence of where a file
came from and never of who is in it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Any

import pytest

# Imported for its side effect: the workbench slice registers the ledger's table, which a username
# arriving is now written to (`catalog._seed_username_on`). `temp_db.initialize_schema()` creates
# only what is registered, so without this the table would depend on import order in the worker.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import (
    MADE_BY_A_PERSON,
    Repository,
    Viewer,
    link_username_to_asset,
    seed_site_username,
)
from sift.kernel.access.history import Event, history_of_asset
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, LibraryStore
from sift.kernel.db import Database
from sift.kernel.ingress import Origin, verify_ingress
from sift.kernel.workbench import Band
from sift.slices.suggestions.naming import (
    mirror_in_folder,
    one_post,
    posted_in_filename,
    username_in_filename,
)
from sift.slices.suggestions.queue import FiledFromFilenamesQueue
from sift.slices.suggestions.service import (
    FROM_FILENAME,
    SuggestionService,
)
from sift.slices.suggestions.settings import FILE_FROM_FILENAMES_KEY
from sift.slices.suggestions.store import NameFiling, Store
from sift.slices.suggestions.tests.conftest import (
    CORPUS,
    FakeFaces,
    FakePreferences,
    Library,
)
from sift.slices.workbench.store import Store as DecisionStore
from sift.testing.fixtures import hide

#: The receipts this pass wrote, and not the ledger's other rows. A pass that makes a username now
#: writes its arrival as well (`catalog._seed_username_on`), under the ledger's own queue word.
_RECEIPTS = "SELECT id, title, payload FROM workbench_decisions WHERE queue <> 'ledger'"


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


async def filings_of(db: Database, asset_id: str) -> list[tuple[str, str | None]]:
    rows = await db.fetch_all(
        "SELECT username_id, source FROM asset_usernames WHERE asset_id = ?", (asset_id,)
    )
    return [(str(row["username_id"]), row["source"]) for row in rows]


async def history_of(
    db: Database, access: Repository, asset_id: str, viewer: Viewer
) -> list[Event]:
    """One file's own pane, read the way the route reads it.

    `final_queues` is empty, which is the kernel's documented default and is the safe direction: it
    offers an Undo the workbench would then refuse. The filename pile is reversible, so what this
    proves is the same either way, and naming the queue here would be this test holding a copy of
    the registry's answer.
    """
    return await history_of_asset(db, access, viewer, asset_id)


async def account_row(db: Database, handle: str) -> dict[str, Any]:
    row = await db.fetch_one(
        "SELECT a.id AS id, a.number AS number, p.name AS site"
        " FROM usernames a LEFT JOIN sites p ON p.id = a.site_id WHERE a.name = ?",
        (handle,),
    )
    assert row is not None
    return {"id": str(row["id"]), "number": row["number"], "site": row["site"]}


async def posts_of(db: Database, asset_id: str) -> list[str | None]:
    rows = await db.fetch_all("SELECT post_id FROM asset_usernames WHERE asset_id = ?", (asset_id,))
    return [None if row["post_id"] is None else str(row["post_id"]) for row in rows]


class TestTheShapeNamesTheSite:
    """The reader: what a signature says, and what it refuses to say."""

    def test_it_reads_the_site_the_handle_and_the_number(self) -> None:
        """The whole shape, and the site comes from the SHAPE rather than from the username."""
        found = posted_in_filename("orla_fennimore_3141592653_2718281828459045235_56432312663.jpg")
        assert found is not None
        assert found.site == "Instagram"
        assert found.number == "56432312663"

    def test_the_handle_keeps_its_own_spelling(self) -> None:
        """RAW, because a folded username is not an address.

        `username_in_filename` answers `orla fennimore`, which is right for matching a username
        against a person and wrong for creating a username, so the username reader keeps the
        spelling.
        """
        found = posted_in_filename("orla_fennimore_3141592653_2718281828459045235_56432312663.jpg")
        assert found is not None
        assert found.username == "orla_fennimore"

    def test_a_handle_that_ends_in_separators_keeps_them(self) -> None:
        """One separator goes to the timestamp and no more. A trailing underscore is real."""
        found = posted_in_filename("talia_brandt___3141592653_2718281828459045235_43000012.jpg")
        assert found is not None
        assert found.username == "talia_brandt__"

    @pytest.mark.parametrize(
        "filename",
        [
            # Three false reads, by shape.
            #
            # A hash on the end, so the shape does not close, and a username `ivo` out of it would
            # be a `6` stripped off `ivo6`, which is a guess about where a word ends rather than a
            # reading of what a tool wrote.
            "161803398_2718281828459045270_4815162342_7c1d2e3f4a5b6_ivo6_2_7c1d2e3f4a5b7.mov",
            # A downloader's own brand where the username goes, and only two numbers after it.
            "SaveInsta.App - 2718281828459045209_314159265.mp4",
            "SaveInsta.App_-_2718281828459045555_2718281828_7c1d2e3f4a5b8.mp4",
            # The right count of numbers in the wrong order: the username number before the post id.
            "reya_solberg_3141592653_56432312663_2718281828459045235.jpg",
            # A post id that is not nineteen digits, which is not this tool's shape.
            "reya_solberg_3141592653_304648327269533_56432312663.jpg",
            # Nothing machine-made about it at all.
            "holiday snap.jpg",
            # Numbers and no username: the site's own CDN name.
            "_3141592653_2718281828459045235_56432312663.jpg",
        ],
    )
    def test_it_refuses_everything_that_is_not_the_shape(self, filename: str) -> None:
        assert posted_in_filename(filename) is None

    def test_a_phrase_where_the_handle_goes_is_refused(self) -> None:
        """The numbers can be right and the name still be a title. Same bar as every reader here."""
        assert (
            posted_in_filename("my rise in the ranks_3141592653_2718281828459045235_43000012.mp4")
            is None
        )

    def test_the_saveinsta_brand_is_known_now(self) -> None:
        """The refusal is at the reader and it reaches the loose reader too."""
        assert username_in_filename("SaveInsta.App - 2718281828459045209_314159265.mp4") == ""

    def test_a_short_name_left_by_a_cut_inside_a_token_is_refused(self) -> None:
        """The third false read, at the loose reader where it would be made.

        `ivo` would come out of `ivo6` by stripping the `6`: a cut inside a token, which is a guess
        about where a word ends rather than a reading of what a tool wrote. The strict shape refuses
        that filename outright, but `username_in_filename` is what fed it to the username hunt.
        """
        assert (
            username_in_filename(
                "161803398_2718281828459045270_4815162342_7c1d2e3f4a5b6_ivo6_2_7c1d2e3f4a5b7.mov"
            )
            == ""
        )

    def test_a_long_enough_name_left_by_the_same_cut_is_kept(self) -> None:
        """Both directions, or the floor could be a reader that has stopped answering at all.

        The same cut, one character longer: `orla2` loses its trailing digit exactly as `ivo6` did,
        and four characters is where a real username starts. So the floor bites the debris and
        leaves the usernames standing beside it.
        """
        assert username_in_filename("orla2_3141592653_2718281828459045235.jpg") == "orla"


class TestTheDatedShapeNamesOnlyANumber:
    """The second signature: a desktop downloader that writes a clock, a media id and a number.

    It carries no username at all, which is the whole reason it is a separate case rather than a
    wider version of the first. Thousands of files in one library can be named this way under a
    handful of username numbers.
    """

    def test_it_reads_the_site_and_the_number_and_names_nobody(self) -> None:
        found = posted_in_filename("2023-07-10 12.37.17 2718281828459045085_31415926.jpg")
        assert found is not None
        assert found.site == "Instagram"
        assert found.number == "31415926"
        assert found.media == "2718281828459045085"
        assert found.username == ""

    def test_the_clock_becomes_a_second_so_one_column_holds_one_kind_of_value(self) -> None:
        """The post id column holds a second, and its own note says two spellings cannot be read
        back. So the wall clock is turned into one here rather than written as it was found."""
        found = posted_in_filename("2023-07-10 12.37.17 2718281828459045085_31415926.jpg")
        assert found is not None
        assert found.saved.isdigit()
        assert int(found.saved) == 1688992637

    def test_two_files_of_one_moment_are_one_post(self) -> None:
        first = posted_in_filename("2023-10-29 14.27.20 5491505199839216117_6034654495.jpg")
        second = posted_in_filename("2023-10-29 14.27.20 7312083403052046722_6034654495.jpg")
        assert first is not None and second is not None
        assert one_post(first) == one_post(second)

    def test_two_moments_are_two_posts(self) -> None:
        first = posted_in_filename("2023-10-29 14.27.20 5491505199839216117_6034654495.jpg")
        later = posted_in_filename("2023-10-29 14.28.01 7312083403052046722_6034654495.jpg")
        assert first is not None and later is not None
        assert one_post(first) != one_post(later)

    @pytest.mark.parametrize(
        "filename",
        [
            # The spelling a tool writes when it has no time at all: unix zero, rendered in the
            # machine's own timezone.
            "1969-12-31 19.00.00 3000000000000000001_10000000001.jpg",
            # The same instant on a machine set to UTC, and on one an hour the other side of it.
            "1970-01-01 00.00.00 3000000000000000001_10000000001.jpg",
            "1970-01-01 01.00.00 3000000000000000001_10000000001.jpg",
        ],
    )
    def test_an_epoch_stamp_is_no_moment_and_so_no_post(self, filename: str) -> None:
        """Every stampless file of one username would otherwise be one post holding all of them."""
        found = posted_in_filename(filename)
        assert found is not None
        assert found.number == "10000000001"
        assert found.saved == ""

    def test_the_same_rule_reaches_the_shape_that_writes_a_unix_second(self) -> None:
        """One rule for both spellings, which is what keeps it from being two rules that drift."""
        found = posted_in_filename("orla_fennimore_0000000000_2718281828459045235_56432312663.jpg")
        assert found is not None
        assert found.username == "orla_fennimore"
        assert found.saved == ""

    def test_a_date_no_calendar_has_reads_the_account_and_no_moment(self) -> None:
        """The pattern cannot say February has twenty-eight days; the calendar can."""
        found = posted_in_filename("2023-02-30 10.00.00 2718281828459045085_31415926.jpg")
        assert found is not None
        assert found.number == "31415926"
        assert found.saved == ""

    @pytest.mark.parametrize(
        "filename",
        [
            # A colon where the tool writes a full stop: a different tool, unmeasured.
            "2023-07-10 12:37:17 2718281828459045085_31415926.jpg",
            # An underscore where the tool writes a space.
            "2023-07-10 12.37.17_2718281828459045085_31415926.jpg",
            # A media id that is not nineteen digits.
            "2023-07-10 12.37.17 271828182845904508_31415926.jpg",
            # A username number too short to be one.
            "2023-07-10 12.37.17 2718281828459045085_31415.jpg",
            # A date somebody typed at the front of a name they wrote themselves.
            "2023-07-10 holiday snap.jpg",
            # Anchored at the front: a shape in the middle of a longer name is not this tool's.
            "pack 2023-07-10 12.37.17 2718281828459045085_31415926.jpg",
        ],
    )
    def test_it_refuses_everything_that_is_not_the_shape(self, filename: str) -> None:
        assert posted_in_filename(filename) is None


class TestAFolderThatNamesAnAccount:
    def test_it_reads_the_handle_and_the_site_in_the_brackets(self) -> None:
        assert mirror_in_folder("reyasolberg (Coomer)") == ("reyasolberg", "Coomer")

    def test_a_site_that_is_not_a_mirror_reads_the_same_way(self) -> None:
        """RedGifs is posted to rather than mirrored, and the brackets say the same thing either way."""
        assert mirror_in_folder("orlafennimore (RedGifs)") == ("orlafennimore", "RedGifs")

    def test_a_site_this_library_holds_counts_as_one(self) -> None:
        assert mirror_in_folder("orlafennimore (Northlight)", sites=["Northlight"]) is not None

    @pytest.mark.parametrize(
        "name",
        [
            "Orla Fennimore (2)",  # a copy, not a site
            "Videos (Instagram)",  # junk where the username goes
            "Orla Fennimore",  # no brackets at all
            "(Coomer)",  # no username at all
            "Orla Fennimore (Dorian Halstead)",  # two people, no site
        ],
    )
    def test_it_refuses_a_folder_that_names_no_account(self, name: str) -> None:
        assert mirror_in_folder(name) is None


class TestFilingFromAFilename:
    async def test_it_files_the_file_under_the_account_the_shape_names(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """The whole write, in one: the Site, the username, its number, and the filing's word."""
        landed = await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )
        assert await service.file_from_filenames() == 1

        account = await account_row(temp_db, "orla_fennimore")
        assert account["site"] == "Instagram"
        assert account["number"] == "56432312663"
        assert await filings_of(temp_db, landed.asset.id) == [(account["id"], "filename")]

    async def test_it_writes_no_person_and_no_date(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """Both of these are refusals rather than omissions (see `Posted` and `file_from_filenames`)."""
        landed = await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )
        await service.file_from_filenames()

        people = await temp_db.fetch_all(
            "SELECT person_id FROM asset_people WHERE asset_id = ?", (landed.asset.id,)
        )
        assert people == []
        row = await temp_db.fetch_one(
            "SELECT release_date FROM assets WHERE id = ?", (landed.asset.id,)
        )
        assert row is not None
        assert row["release_date"] is None

    async def test_one_account_for_many_files_of_the_same_handle(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        for post in range(3):
            await add_file(
                library,
                f"Unsorted/orla_fennimore_141421356{post}_304648327269533132{post}_56432312663.mp4",
            )
        assert await service.file_from_filenames() == 3
        rows = await temp_db.fetch_all(
            "SELECT id FROM usernames WHERE name = ?", ("orla_fennimore",)
        )
        assert len(rows) == 1

    async def test_it_leaves_a_filing_somebody_already_made(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """The first answer stands. A pass agreeing does not turn a decision into an automatic one."""
        landed = await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )
        # Filed by hand first, under a different site entirely.
        _, by_hand = await seed_site_username(
            temp_db, site="Fansly", name="orla_fennimore", made=MADE_BY_A_PERSON
        )
        await link_username_to_asset(temp_db, asset_id=landed.asset.id, username_id=by_hand)

        assert await service.file_from_filenames() == 0
        assert await filings_of(temp_db, landed.asset.id) == [(by_hand, None)]

    async def test_one_asset_in_two_places_is_filed_once_and_recorded_once(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """A file in two folders is ONE file, and the read is driven from LOCATIONS.

        A library can hold more locations carrying the signature than there are assets, so the second
        sighting of one of those is an insert that does nothing: the pair is already there and the
        first answer stands. Counting it anyway would report more files filed than exist and, worse,
        would write the asset into the record twice, so an undo would try to take back a filing this
        decision never made.
        """
        landed = await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )
        # The same asset seen a second time, under the same name in another folder. A second
        # LOCATION and not a second asset: Sift identifies a file by its content.
        await temp_db.execute(
            "INSERT INTO asset_locations"
            " (id, asset_id, root_id, folder_id, rel_path, filename, size_bytes, mtime, status,"
            "  first_seen_at, last_seen_at)"
            " SELECT 'loc-second', asset_id, root_id, folder_id,"
            "        'Elsewhere/' || filename, filename, size_bytes, mtime, status,"
            "        first_seen_at, last_seen_at"
            " FROM asset_locations WHERE asset_id = ?",
            (landed.asset.id,),
        )

        assert await recording.file_from_filenames() == 1
        rows = await temp_db.fetch_all(_RECEIPTS)
        assert json.loads(str(rows[0]["payload"]))["assets"] == [landed.asset.id]

    async def test_the_pass_is_named_as_the_task_that_filed_and_made_the_username(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """Not "Sift" and nothing else: the receipt names the pass, and so does the username's
        arrival the same pass wrote."""
        await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )

        assert await recording.file_from_filenames() == 1
        said = await temp_db.fetch_all(
            "SELECT verb, actor_kind, actor_id FROM workbench_decisions ORDER BY verb"
        )
        assert [tuple(row) for row in said] == [
            ("added", "sift", "filename"),
            ("filed", "sift", "filename"),
        ]

    async def test_running_twice_files_nothing_the_second_time(
        self,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """The set only shrinks, which is what makes an uncapped read cheap on the second pass."""
        await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )
        assert await service.file_from_filenames() == 1
        assert await service.file_from_filenames() == 0

    async def test_running_it_again_writes_no_second_decision(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """A pass run twice leaves ONE receipt, not two: the question asked as a HISTORY question.

        The test above it counts what was filed and this counts what was WRITTEN DOWN, which is the
        half somebody notices: a receipt is a row in the file's own History and in the board's
        record, so a pass that wrote one every time it ran would grow a thread of identical entries
        on every file in the library and an Undo on each of them that takes back nothing.

        It cannot happen, and the reason is in the READ rather than in a guard here:
        `filenames_of_unfiled_files` (`kernel/access/catalog.py`) returns only files under no site
        at all, so the second pass is handed an empty list before it reaches a write. The same is
        true of the two passes beside it (generating writes no decision, and an identification
        writes an attribution only where it changes something), so the answer to "does running
        these again fill up my history" is no, for three different reasons. This holds the one of
        the three that is a pass over the whole library.
        """
        await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )
        assert await recording.file_from_filenames() == 1
        after_one = await temp_db.fetch_all("SELECT id FROM workbench_decisions")

        assert await recording.file_from_filenames() == 0

        assert await temp_db.fetch_all("SELECT id FROM workbench_decisions") == after_one
        # And the username is not made a second time either, which is the other row a re-run could
        # duplicate: the upsert matches on the site's own number for the username.
        assert len(await temp_db.fetch_all("SELECT id FROM usernames")) == 1

    async def test_a_handle_whose_files_disagree_about_its_number_files_nothing(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """The same refusal the number hunt makes, on the same evidence.

        Two numbers under one name on one site means the word is two usernames, or a tool wrote one
        of them wrong, and a filename cannot say which. Filing either way is worse than filing
        neither: the upsert matches on the NUMBER first and falls back to the name, so the second
        group's files would join the first group's username and nothing would look wrong anywhere.
        """
        for post, number in enumerate(("56432312663", "77777777777")):
            await add_file(
                library,
                f"Unsorted/orla_fennimore_141421356{post}_304648327269533132{post}_{number}.mp4",
            )

        assert await service.file_from_filenames() == 0
        assert await temp_db.fetch_all("SELECT id FROM usernames") == []

    async def test_a_library_with_nothing_unfiled_reads_nothing(
        self, service: SuggestionService
    ) -> None:
        assert await service.file_from_filenames() == 0


class TestFilingFromANumberAlone:
    """The dated shape, whose reading names a username and does not say what it is called.

    The rule the whole class holds: a number this library already knows FILES, and a number it does
    not know proposes nothing at all. There is no third answer, and in particular there is no
    username invented from a number: the usernames wall would then hold a row naming nobody that
    nothing could correct, because the one thing that would correct it is the name.
    """

    async def test_a_number_nothing_knows_files_nothing_and_invents_nothing(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        await add_file(library, "Unsorted/2023-07-10 12.37.17 2718281828459045085_31415926.jpg")

        assert await service.file_from_filenames() == 0
        assert await temp_db.fetch_all("SELECT id FROM usernames") == []
        assert await temp_db.fetch_all("SELECT id FROM sites") == []

    async def test_a_number_this_library_knows_files_under_that_account(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        _, known = await seed_site_username(
            temp_db,
            site="Instagram",
            name="orla_fennimore",
            number="31415926",
            made=MADE_BY_A_PERSON,
        )
        landed = await add_file(
            library, "Unsorted/2023-07-10 12.37.17 2718281828459045085_31415926.jpg"
        )

        assert await service.file_from_filenames() == 1
        assert await filings_of(temp_db, landed.asset.id) == [(known, "filename")]
        # And no second username beside it: the number found the row that was already there.
        rows = await temp_db.fetch_all("SELECT id FROM usernames")
        assert len(rows) == 1

    async def test_the_same_number_on_another_site_is_another_account(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """A number belongs to a site. The shape says which site, so a match on the number alone
        would file under whichever username happened to carry it."""
        await seed_site_username(
            temp_db,
            site="Fansly",
            name="orla_fennimore",
            number="31415926",
            made=MADE_BY_A_PERSON,
        )
        await add_file(library, "Unsorted/2023-07-10 12.37.17 2718281828459045085_31415926.jpg")

        assert await service.file_from_filenames() == 0

    async def test_the_receipt_says_the_handle_the_library_holds(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """The name is not in the filename, so it comes off the username row, which is also
        the spelling already seen on screen."""
        await seed_site_username(
            temp_db,
            site="Instagram",
            name="orla_fennimore",
            number="31415926",
            made=MADE_BY_A_PERSON,
        )
        await add_file(library, "Unsorted/2023-07-10 12.37.17 2718281828459045085_31415926.jpg")

        assert await recording.file_from_filenames() == 1
        rows = await temp_db.fetch_all(_RECEIPTS)
        assert [str(row["title"]) for row in rows] == [
            "Filed under orla_fennimore on Instagram from the file's name"
        ]

    async def test_two_numbers_on_one_site_are_not_one_disputed_handle(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """The dispute rule is about one WORD carrying two numbers, and these readings have no
        word. Folded in, every nameless number on a site would look like one username in dispute
        and every file of all of them would be dropped."""
        for handle, number in (("orla_fennimore", "31415926"), ("talia_brandt", "2155088945")):
            await seed_site_username(
                temp_db,
                site="Instagram",
                name=handle,
                number=number,
                made=MADE_BY_A_PERSON,
            )
            await add_file(
                library, f"Unsorted/2023-07-10 12.37.17 271828182845904508{number[0]}_{number}.jpg"
            )

        assert await service.file_from_filenames() == 2

    async def test_a_file_whose_stamp_is_no_moment_is_filed_and_grouped_with_nothing(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """The filing is the part that must not be lost; the post is the part nothing knows."""
        _, known = await seed_site_username(
            temp_db,
            site="Instagram",
            name="orla_fennimore",
            number="31415926",
            made=MADE_BY_A_PERSON,
        )
        first = await add_file(
            library, "Unsorted/1969-12-31 19.00.00 2718281828459045085_31415926.jpg"
        )
        second = await add_file(
            library, "Unsorted/1969-12-31 19.00.00 2718281828459045086_31415926.jpg"
        )

        assert await service.file_from_filenames() == 2
        assert await filings_of(temp_db, first.asset.id) == [(known, "filename")]
        assert await posts_of(temp_db, first.asset.id) == [None]
        assert await posts_of(temp_db, second.asset.id) == [None]


class TestTheRecordAndTakingItBack:
    async def test_one_receipt_per_file_names_that_file_and_nothing_else(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """One per FILE, not one per username per pass, because the unit of undo is the file.

        One per username would make a misread file's own History carry a line saying what happened
        to it and an Undo that would unfile thousands of innocent ones with it. See
        `_record_filings` for the whole argument.
        """
        for post in range(4):
            await add_file(
                library,
                f"Unsorted/orla_fennimore_141421356{post}_304648327269533132{post}_56432312663.mp4",
            )
        await recording.file_from_filenames()

        everything = await temp_db.fetch_all(
            "SELECT id, queue, user_id, title, payload, verb FROM workbench_decisions"
        )
        # The username's own arrival is recorded beside the filings: one
        # `added` row, which is not a receipt and is not counted here.
        assert [str(row["verb"]) for row in everything].count("added") == 1
        rows = [row for row in everything if str(row["verb"]) != "added"]
        assert len(rows) == 4
        assert {str(row["queue"]) for row in rows} == {"filenames"}
        # Nobody decided it, and the column says so rather than naming whoever was signed in.
        assert [row["user_id"] for row in rows] == [None] * 4
        assert {str(row["title"]) for row in rows} == {
            "Filed under orla_fennimore on Instagram from the file's name"
        }
        # Each names ONE file, and between them they name all four: a receipt that named its own
        # file plus somebody else's would undo two files from one press.
        named = [json.loads(str(row["payload"]))["assets"] for row in rows]
        assert [len(one) for one in named] == [1, 1, 1, 1]
        assert len({one[0] for one in named}) == 4

    async def test_each_file_carries_its_own_undo_in_its_own_history(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        access: Repository,
        admin: Viewer,
    ) -> None:
        """The whole point of a receipt per file, read the way a person meets it.

        Through the history read rather than through the link table, because a decision linked to a
        file that the pane does not draw is a decision with no Undo anywhere a person can reach.
        """
        landed = [
            await add_file(
                library,
                f"Unsorted/orla_fennimore_141421356{post}_304648327269533132{post}_56432312663.mp4",
            )
            for post in range(2)
        ]
        await recording.file_from_filenames()

        events = await history_of(temp_db, access, landed[0].asset.id, admin)
        decided = [one for one in events if one.kind == "decided"]
        assert [one.what for one in decided] == [
            "Sift filed this file under orla_fennimore on Instagram from the file's name"
        ]
        assert decided[0].undo is not None
        assert decided[0].undo.kind == "decision"

        # And it is THIS file's decision, not the other file's. One Undo, one file.
        other = await history_of(temp_db, access, landed[1].asset.id, admin)
        mine = {one.undo.id for one in decided if one.undo}
        theirs = {one.undo.id for one in other if one.kind == "decided" and one.undo}
        assert mine.isdisjoint(theirs)

    async def test_the_undo_puts_back_exactly_what_it_filed(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        landed = await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )
        await recording.file_from_filenames()
        rows = await temp_db.fetch_all(_RECEIPTS)
        payload = str(rows[0]["payload"])

        queue = FiledFromFilenamesQueue(recording)
        assert await queue.reverse(admin, str(rows[0]["id"]), payload) is True
        assert await filings_of(temp_db, landed.asset.id) == []
        # The username stays. Removing it would take a row a download may have attached itself to.
        assert (await account_row(temp_db, "orla_fennimore"))["number"] == "56432312663"

    async def test_a_set_record_with_nothing_to_unmake_sets_puts_nothing_back(
        self, recording: SuggestionService, admin: Viewer
    ) -> None:
        """A service built without the photo-set seam cannot unmake a set, and says so rather than
        claiming an undo it did not do."""
        queue = FiledFromFilenamesQueue(recording)
        payload = json.dumps({"kind": "post_set", "photo_set_id": "set-1", "assets": ["a-1"]})

        assert await queue.reverse(admin, "decision-1", payload) is False

    async def test_a_filing_taken_back_is_not_filed_again_while_new_files_still_are(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        """The undo is remembered against the file, and only that file: a later pass leaves it
        under no site and files the one that arrived since."""
        undone = await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )
        await recording.file_from_filenames()
        rows = await temp_db.fetch_all(_RECEIPTS)
        receipt = next(row for row in rows if '"filed"' in str(row["payload"]))
        queue = FiledFromFilenamesQueue(recording)
        assert await queue.reverse(admin, str(receipt["id"]), str(receipt["payload"])) is True
        arrived = await add_file(
            library, "Unsorted/orla_fennimore_3141680302_2718281828459055326_56432312663.mp4"
        )

        assert await recording.file_from_filenames() == 1

        assert await filings_of(temp_db, undone.asset.id) == []
        assert await filings_of(temp_db, arrived.asset.id) != []

    async def test_one_file_saved_under_two_posts_names_is_filed_once(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """The same picture saved twice, a day apart: two names, two posts, one file. The second
        post's filing finds it already filed and writes nothing, so the count and the receipt name
        the file once."""
        landed = await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )
        await temp_db.execute(
            "INSERT INTO asset_locations"
            " (id, asset_id, root_id, folder_id, rel_path, filename, size_bytes, mtime, status,"
            "  first_seen_at, last_seen_at)"
            " SELECT 'loc-second', asset_id, root_id, folder_id, 'Elsewhere/' || ?, ?,"
            "        size_bytes, mtime, status, first_seen_at, last_seen_at"
            " FROM asset_locations WHERE asset_id = ?",
            (
                "orla_fennimore_3141680302_2718281828459045235_56432312663.mp4",
                "orla_fennimore_3141680302_2718281828459045235_56432312663.mp4",
                landed.asset.id,
            ),
        )

        assert await recording.file_from_filenames() == 1

        assert len(await filings_of(temp_db, landed.asset.id)) == 1
        receipts = [
            json.loads(str(row["payload"]))["assets"]
            for row in await temp_db.fetch_all(_RECEIPTS)
            if '"filed"' in str(row["payload"])
        ]
        assert receipts == [[landed.asset.id]]

    @pytest.mark.parametrize(
        "payload",
        [
            "not json at all",
            '"a string"',
            "{}",
            '{"username_id": "acc-1"}',
            '{"assets": ["a-1"]}',
            '{"username_id": "acc-1", "assets": "not a list"}',
        ],
    )
    async def test_a_record_it_cannot_read_puts_nothing_back(
        self, recording: SuggestionService, admin: Viewer, payload: str
    ) -> None:
        """A record can outlive the version that wrote it. Saying so beats failing the request."""
        queue = FiledFromFilenamesQueue(recording)
        assert await queue.reverse(admin, "decision-1", payload) is False

    @pytest.mark.parametrize(
        "payload",
        ["not json at all", '"a string"', '{"assets": "not a list"}'],
    )
    async def test_a_record_it_cannot_read_draws_nothing_either(
        self, recording: SuggestionService, admin: Viewer, payload: str
    ) -> None:
        """The same reading, through the other caller. One parse answers both (see `_filed_record`),
        and a record the undo refuses must not still produce a row of pictures."""
        queue = FiledFromFilenamesQueue(recording)
        assert await queue.pictures_of(admin, payload) == ()

    async def test_taking_back_nothing_is_not_a_write(self, recording: SuggestionService) -> None:
        assert await recording.take_back_filings(username_id="acc-1", asset_ids=[]) is False

    async def test_undoing_one_file_leaves_the_others_filed_and_the_next_pass_alone(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        """The two halves of what a per-file undo has to mean.

        One file goes and the other stays, and the one that went does not come back, which is the
        half that has to be remembered somewhere: the pass reads every file under no site at all, so
        without the refusal the undo would last until tomorrow morning.
        """
        landed = [
            await add_file(
                library,
                f"Unsorted/orla_fennimore_141421356{post}_304648327269533132{post}_56432312663.mp4",
            )
            for post in range(2)
        ]
        assert await recording.file_from_filenames() == 2

        rows = await temp_db.fetch_all(_RECEIPTS)
        mine = next(
            row for row in rows if json.loads(str(row["payload"]))["assets"] == [landed[0].asset.id]
        )
        queue = FiledFromFilenamesQueue(recording)
        assert await queue.reverse(admin, str(mine["id"]), str(mine["payload"])) is True

        assert await filings_of(temp_db, landed[0].asset.id) == []
        assert len(await filings_of(temp_db, landed[1].asset.id)) == 1

        # And the pass runs again over a library where that file is once more under no site.
        assert await recording.file_from_filenames() == 0
        assert await filings_of(temp_db, landed[0].asset.id) == []

    async def test_a_refusal_is_remembered_only_when_something_was_put_back(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """A press that put nothing back has said nothing about the file.

        The undo is the only thing that writes a refusal, and it writes one for what the decision
        NAMED, so a decision naming a file that was never filed under that username must not leave
        a standing no behind. Otherwise a replayed or stale receipt would quietly take a file out of
        the reader's reach for ever, and nothing on any screen would say so.
        """
        landed = await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )

        assert (
            await recording.take_back_filings(username_id="acc-never", asset_ids=[landed.asset.id])
            is False
        )
        assert await temp_db.fetch_all("SELECT asset_id FROM filename_refusals") == []
        # So the pass still has it to read.
        assert await recording.file_from_filenames() == 1


class TestTheCard:
    async def test_it_reports_what_was_filed_and_offers_the_way_in(
        self,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )
        await service.file_from_filenames()

        queue = FiledFromFilenamesQueue(service)
        assert await queue.available() is True
        # The band is DECLARED on the queue and stamped on the way out by the workbench: a survey
        # never sets it, so it is asserted where it is actually said.
        assert queue.band is Band.LOG
        summary = await queue.survey(admin)
        assert summary.count == 1
        assert summary.title == "Enriched from filenames"
        assert summary.verb == "files enriched from names"
        assert summary.verb_one == "file enriched from its name"
        # The way in is the queue's OWN page (`/organize/filenames`, grouped by username),
        # so nothing is declared here: a queue with a panel already has a way in, and saying it
        # twice would be two answers to one question. See the class docstring.
        assert summary.opens is None
        # And the page says how to read it, in the queue's own words. See `Summary.advice`.
        assert summary.advice is not None
        assert "largest first" in summary.advice

    async def test_the_vault_shut_takes_its_files_out_of_the_card_and_the_page(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        """The card's number, the page's total and each group's number count only what may be shown.

        Counting every filing would, with the vault shut, say how many of the vault's files the
        pass had filed, which is the one number the vault is shut to keep back. Open, it counts
        them again.
        """
        landed = await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )
        await service.file_from_filenames()
        assert (await FiledFromFilenamesQueue(service).survey(admin)).count == 1

        await hide(temp_db, "asset", landed.asset.id, admin.id)
        assert (await FiledFromFilenamesQueue(service).survey(admin)).count == 0
        assert await service.filings_by_username(admin) == ([], 0)

        unlocked = replace(admin, show_hidden=True)
        assert (await FiledFromFilenamesQueue(service).survey(unlocked)).count == 1
        groups, total = await service.filings_by_username(unlocked)
        assert (total, [group.files for group in groups]) == (1, [1])

    async def test_an_empty_pile_counts_nothing(
        self, service: SuggestionService, admin: Viewer
    ) -> None:
        """A zero here is the honest kind: no file in this library carries a shape Sift knows."""
        summary = await FiledFromFilenamesQueue(service).survey(admin)
        assert summary.count == 0

    async def test_it_counts_only_the_filings_its_own_way_in_would_open(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        """The number and the page it opens are the same question or the card is lying.

        A file filed by hand carries no source word, so neither the page nor `enriched:filename`
        finds it and the card must not count it. Otherwise pressing Open all reaches fewer files
        than the figure beside the button.
        """
        landed = await add_file(library, "Unsorted/holiday snap.mp4")
        _, by_hand = await seed_site_username(
            temp_db, site="Fansly", name="orla_fennimore", made=MADE_BY_A_PERSON
        )
        await link_username_to_asset(temp_db, asset_id=landed.asset.id, username_id=by_hand)

        summary = await FiledFromFilenamesQueue(service).survey(admin)
        assert summary.count == 0

    async def test_it_draws_the_files_one_decision_filed(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        landed = await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )
        await recording.file_from_filenames()
        rows = await temp_db.fetch_all(_RECEIPTS)

        queue = FiledFromFilenamesQueue(recording)
        pictures = await queue.pictures_of(admin, str(rows[0]["payload"]))
        assert [one.id for one in pictures] == [landed.asset.id]

    async def test_a_record_with_no_files_in_it_draws_nothing(
        self, service: SuggestionService, admin: Viewer
    ) -> None:
        assert await FiledFromFilenamesQueue(service).pictures_of(admin, "{}") == ()


class TestThePageOfFilings:
    """The queue's own page: what the pass filed, grouped by the username it filed under."""

    async def test_it_groups_by_account_and_hands_each_file_its_own_undo(
        self,
        recording: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        """The group is what the pass DECIDED; the undo is per file, which is why both are here.

        Two files off one username and one off another, so the grouping is doing work rather than
        answering a single row. The decision id on each file is the thing the page hangs an Undo on:
        without it the row is a sentence nobody can act on, which is exactly what the per-file
        receipt is for.
        """
        await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )
        await add_file(
            library, "Unsorted/orla_fennimore_3141592654_2718281828459045236_56432312663.mp4"
        )
        await add_file(
            library, "Unsorted/tam_brightwater_3141592655_2718281828459045237_97765617070.mp4"
        )
        await recording.file_from_filenames()

        groups, total = await recording.filings_by_username(admin)
        assert total == 2
        # Biggest first, for the reason the faces piles are: a misreading is worth the most where
        # the pass filed the most.
        assert [one.files for one in groups] == [2, 1]
        assert groups[0].name == "orla_fennimore"
        assert groups[0].site is not None
        # Nobody is said for the username yet, so a press on it opens its files, not a person.
        assert groups[0].person_id is None
        assert len(groups[0].shown) == 2
        # Every file carries the decision that takes its own filing back, and they are DIFFERENT
        # decisions: one receipt naming both would be one Undo that unfiles both.
        offered = [one.decision_id for one in groups[0].shown]
        assert all(one is not None for one in offered)
        assert len(set(offered)) == 2

    async def test_a_filing_taken_back_offers_no_undo_a_second_time(
        self,
        recording: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        """An Undo the server would refuse is worse than none: the only way to find out is to press.

        The decision is marked reversed rather than deleted, so the row it describes stays readable
        and the control beside it has to go on its own.
        """
        await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )
        await recording.file_from_filenames()
        groups, _ = await recording.filings_by_username(admin)
        filed = groups[0].shown[0]
        assert filed.decision_id is not None

        assert await recording.take_back_filings(
            username_id=groups[0].username_id, asset_ids=[filed.asset_id]
        )
        # The filing is gone, so the username carries nothing this pass wrote and the page is empty.
        groups, total = await recording.filings_by_username(admin)
        assert total == 0
        assert groups == []

    async def test_an_empty_page_is_empty_rather_than_absent(
        self, service: SuggestionService, admin: Viewer
    ) -> None:
        """A library no shape matched. Zero is a true thing to say, not a feature failing to start."""
        assert await service.filings_by_username(admin) == ([], 0)

    async def test_a_file_lying_in_two_places_is_one_row_on_the_card(
        self,
        recording: SuggestionService,
        library: Library,
        content_store: ContentStore,
        library_store: LibraryStore,
        settings: Settings,
        admin: Viewer,
    ) -> None:
        """A copy is a second PLACE, never a second file, and the card has to say so.

        Sift identifies a file by its contents, so the same bytes under two names are one asset with
        two locations, which is ordinary in any library somebody has tidied. A read that joined the
        places would hand that file back twice: the screen draws these keyed by the file's own id,
        a repeated key is one the client refuses outright, and the whole panel would then render
        NOTHING under its heading. Nothing on screen would say why, and a page that never finishes
        drawing is indistinguishable from a page that is slow.

        It would cost the cap as well, which is the quieter half: twenty-four rows that were twelve
        files, on a card whose whole job is to show enough of a pass's work to check it by eye.
        """
        body = (CORPUS / "accepted.mp4").read_bytes() + b"two-places"
        for rel_path in (
            "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4",
            "Copies/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4",
        ):
            folder = await library_store.upsert_folder(
                library.root.id, str(PurePosixPath(rel_path).parent)
            )
            target = library.path / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            await content_store.ingest(
                verify_ingress(target, origin=Origin.SCAN, settings=settings),
                root_id=library.root.id,
                rel_path=rel_path,
                folder_id=folder.id,
            )
        await recording.file_from_filenames()

        groups, _ = await recording.filings_by_username(admin)
        shown = groups[0].shown
        assert len(shown) == 1
        assert len({one.asset_id for one in shown}) == len(shown)
        # One of the two names, not a made-up one and not both stuck together. Which of them is not
        # a question this card can answer: the pass read A name and either is that name.
        assert shown[0].filename.startswith("orla_fennimore_")

    async def test_each_file_carries_the_token_its_still_is_addressed_by(
        self,
        recording: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        """Without it every still on the page is fetched again on every visit.

        A bare picture address is served `private, no-cache`, so a dozen cards would ask the server
        for a hundred and fifty pictures it had already sent, each with a permission check, every
        time the page was opened. The token is what makes the address name the picture, and the browser
        may then keep it.

        None here rather than a string, and that is the assertion worth having: this library has
        been indexed and nothing has built a thumbnail yet, which is exactly when the address must
        be left bare. A token invented before there are pictures to name would pin an empty frame in
        the browser's store for a week.
        """
        await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )
        await recording.file_from_filenames()

        groups, _ = await recording.filings_by_username(admin)
        assert [one.art for one in groups[0].shown] == [None]


class TestTheMirrorFolderAsks:
    async def test_it_claims_the_account_rather_than_a_person(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
    ) -> None:
        """The brackets say username-on-a-site, so the folder's name must not also mean a person."""
        await add_file(library, "Unsorted/orlafennimore (Coomer)/one.mp4")
        await name_folders()
        await service.rebuild()

        rows = await temp_db.fetch_all("SELECT kind, proposed, site, evidence FROM folder_claims")
        assert [str(row["kind"]) for row in rows] == ["username"]
        assert str(rows[0]["proposed"]) == "orlafennimore"
        assert str(rows[0]["site"]) == "Coomer"
        assert str(rows[0]["evidence"]) == "username_folder"

    async def test_confirming_files_the_folder_under_that_account(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        landed = await add_file(library, "Unsorted/orlafennimore (Coomer)/one.mp4")
        await name_folders()
        await recording.rebuild()
        claim = (await temp_db.fetch_all("SELECT id FROM folder_claims"))[0]

        applied = await recording.confirm(admin, str(claim["id"]))
        assert applied.files == 1
        account = await account_row(temp_db, "orlafennimore")
        # The mirror's OWN site, because nothing in the folder says which upstream it re-hosts.
        assert account["site"] == "Coomer"
        assert await filings_of(temp_db, landed.asset.id) == [(account["id"], "folder")]
        # And nobody was named: a username says where a file came from, not who is in it.
        assert await temp_db.fetch_all("SELECT id FROM people") == []

    async def test_the_folder_is_asked_about_as_the_account_rather_than_the_person(
        self,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """A third kind of claim is a third question, or the Folders page's words disagree with its
        button: "Is this the person in this folder?" over a folder that says `(Coomer)` on it asks
        about somebody, and pressing yes files a site. The claim carries what the page asks from:
        the kind, the username and the Site.
        """
        await add_file(library, "Unsorted/orlafennimore (Coomer)/one.mp4")
        await name_folders()
        await service.rebuild()

        [claim] = (await service.pending(admin)).items
        assert (claim.kind, claim.proposed, claim.site) == ("username", "orlafennimore", "Coomer")

    async def test_a_folder_set_aside_is_not_claimed_again(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """A no is about the PAIR (this username on this site) and holds wherever it turns up.

        The folder is read again on the next pass, because a pass reads every folder whose contents
        have moved; the claim must not come back, or a no is a delay rather than an answer.
        """
        await add_file(library, "Unsorted/orlafennimore (Coomer)/one.mp4")
        await name_folders()
        await service.rebuild()
        claim = (await temp_db.fetch_all("SELECT id FROM folder_claims"))[0]
        await service.reject(admin, str(claim["id"]))

        await add_file(library, "Unsorted/orlafennimore (Coomer)/two.mp4")
        await name_folders()
        await service.rebuild()

        rows = await temp_db.fetch_all("SELECT id, state FROM folder_claims")
        assert [str(row["state"]) for row in rows] == ["rejected"]

    async def test_a_file_already_filed_there_is_not_this_decision_s_to_take_back(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        """The record holds the rows this decision WROTE, never the rows it found.

        One file in the folder was already filed under that username by somebody, carrying no source
        word: the insert keeps that first answer. Recording it would let the undo remove a filing
        this decision never made, which is a decision taken back that nobody took.
        """
        mine = await add_file(library, "Unsorted/orlafennimore (Coomer)/one.mp4")
        theirs = await add_file(library, "Unsorted/orlafennimore (Coomer)/two.mp4")
        _, by_hand = await seed_site_username(
            temp_db, site="Coomer", name="orlafennimore", made=MADE_BY_A_PERSON
        )
        await link_username_to_asset(temp_db, asset_id=theirs.asset.id, username_id=by_hand)
        await name_folders()
        await recording.rebuild()
        claim = (await temp_db.fetch_all("SELECT id FROM folder_claims"))[0]

        applied = await recording.confirm(admin, str(claim["id"]))
        assert applied.files == 1
        assert applied.written.filed == ((mine.asset.id, by_hand),)

        await recording.take_back(applied.written, claim_id=applied.claim_id)
        assert await filings_of(temp_db, mine.asset.id) == []
        # Theirs is untouched, and still carries no word: it was never this decision's.
        assert await filings_of(temp_db, theirs.asset.id) == [(by_hand, None)]

    async def test_taking_that_confirmation_back_unfiles_the_files(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        landed = await add_file(library, "Unsorted/orlafennimore (Coomer)/one.mp4")
        await name_folders()
        await recording.rebuild()
        claim = (await temp_db.fetch_all("SELECT id FROM folder_claims"))[0]
        applied = await recording.confirm(admin, str(claim["id"]))

        assert await recording.take_back(applied.written, claim_id=applied.claim_id) is True
        assert await filings_of(temp_db, landed.asset.id) == []


class TestTheSwitchOverIt:
    """The one switch over this half of the pass, honoured where the pass is run.

    The switch is on out of the box, so the interesting test is the off one, and the other half of
    it matters just as much: turning the FILENAME reading off must not turn the FOLDER reading off,
    because a control that switches off a thing nobody asked it about is worse than no control.
    """

    async def test_off_the_pass_files_nothing(
        self,
        store: Store,
        access: Repository,
        faces: FakeFaces,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        refusing = SuggestionService(
            store=store,
            access=access,
            faces=faces,
            preferences=FakePreferences(**{FILE_FROM_FILENAMES_KEY: False}),
        )
        landed = await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )

        await refusing.rebuild()

        assert await filings_of(temp_db, landed.asset.id) == []

    async def test_off_the_folder_reading_still_happens(
        self,
        store: Store,
        access: Repository,
        faces: FakeFaces,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        name_folders: Callable[[], Any],
    ) -> None:
        refusing = SuggestionService(
            store=store,
            access=access,
            faces=faces,
            preferences=FakePreferences(**{FILE_FROM_FILENAMES_KEY: False}),
        )
        await add_file(library, "Unsorted/orlafennimore (Coomer)/one.mp4")
        await name_folders()

        await refusing.rebuild()

        assert await temp_db.fetch_all("SELECT id FROM folder_claims") != []

    async def test_on_it_files(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """The same library through the same call, with the switch left where it ships."""
        landed = await add_file(
            library, "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4"
        )

        await service.rebuild()

        assert await filings_of(temp_db, landed.asset.id) != []


class TestANoOnAWholeUsername:
    """The row's No on the filenames page: every file a name filed under one username comes off,
    as ONE decision by the person who pressed, and that decision's Undo puts each back as it was."""

    _NAMES = (
        "Unsorted/orla_fennimore_3141592653_2718281828459045235_56432312663.mp4",
        "Unsorted/orla_fennimore_3141592654_2718281828459045236_56432312663.mp4",
    )

    async def _filed(
        self, recording: SuggestionService, library: Library, add_file: Callable[..., Any]
    ) -> list[str]:
        landed = [await add_file(library, name) for name in self._NAMES]
        await recording.file_from_filenames()
        return [one.asset.id for one in landed]

    async def test_it_takes_every_file_off_as_one_decision_and_the_next_pass_leaves_them(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        files = await self._filed(recording, library, add_file)
        username_id = (await account_row(temp_db, "orla_fennimore"))["id"]
        before = len(await temp_db.fetch_all(_RECEIPTS))

        taken = await recording.take_back_username(admin, username_id=username_id)

        assert taken.files == 2 and taken.decision_id
        assert [await filings_of(temp_db, one) for one in files] == [[], []]
        rows = await temp_db.fetch_all(
            "SELECT user_id, verb, title FROM workbench_decisions WHERE id = ?",
            (taken.decision_id,),
        )
        assert [(row["user_id"], row["verb"]) for row in rows] == [(admin.id, "removed")]
        assert str(rows[0]["title"]) == "Removed 2 files from orla_fennimore on Instagram"
        assert len(await temp_db.fetch_all(_RECEIPTS)) == before + 1, "one decision, not one each"
        # Refused, as one file's Undo refuses it: the next pass files neither again.
        assert await recording.file_from_filenames() == 0

    async def test_its_undo_puts_every_filing_back_as_it_stood(
        self,
        recording: SuggestionService,
        store: Store,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        files = await self._filed(recording, library, add_file)
        username_id = (await account_row(temp_db, "orla_fennimore"))["id"]
        stood = [await filings_of(temp_db, one) for one in files]
        taken = await recording.take_back_username(admin, username_id=username_id)
        assert taken.decision_id
        (receipt,) = await temp_db.fetch_all(
            "SELECT payload FROM workbench_decisions WHERE id = ?", (taken.decision_id,)
        )

        queue = FiledFromFilenamesQueue(recording)
        assert await queue.reverse(admin, taken.decision_id, str(receipt["payload"])) is True

        assert [await filings_of(temp_db, one) for one in files] == stood
        assert await store.refused_filenames() == set(), "the refusals it added are forgotten"
        shown = await queue.pictures_of(admin, str(receipt["payload"]))
        assert {one.id for one in shown} == set(files)

    async def _taken_back(
        self, recording: SuggestionService, temp_db: Database, admin: Viewer
    ) -> tuple[str, str]:
        """Take the whole username back; the decision's id and its payload, for its Undo."""
        username_id = (await account_row(temp_db, "orla_fennimore"))["id"]
        taken = await recording.take_back_username(admin, username_id=username_id)
        assert taken.decision_id
        (receipt,) = await temp_db.fetch_all(
            "SELECT payload FROM workbench_decisions WHERE id = ?", (taken.decision_id,)
        )
        return taken.decision_id, str(receipt["payload"])

    async def test_its_undo_skips_a_file_deleted_since_and_puts_the_rest_back(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        gone, kept = await self._filed(recording, library, add_file)
        stood = await filings_of(temp_db, kept)
        decision_id, payload = await self._taken_back(recording, temp_db, admin)
        await temp_db.execute("DELETE FROM assets WHERE id = ?", (gone,))

        queue = FiledFromFilenamesQueue(recording)
        assert await queue.reverse(admin, decision_id, payload) is True

        assert await filings_of(temp_db, kept) == stood
        assert await filings_of(temp_db, gone) == []

    async def test_its_undo_leaves_a_file_the_vault_holds_back_since(
        self,
        recording: SuggestionService,
        store: Store,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        """The Undo is scoped as the take-back was: a file this viewer may not be shown now is not
        theirs to put back, and its refusal stands."""
        held, shown = await self._filed(recording, library, add_file)
        stood = await filings_of(temp_db, shown)
        decision_id, payload = await self._taken_back(recording, temp_db, admin)
        await hide(temp_db, "asset", held, admin.id)

        queue = FiledFromFilenamesQueue(recording)
        assert await queue.reverse(admin, decision_id, payload) is True

        assert await filings_of(temp_db, shown) == stood
        assert await filings_of(temp_db, held) == []
        assert await store.refused_filenames() == {held}

    async def test_a_filing_whose_file_went_during_the_undo_is_skipped(
        self,
        recording: SuggestionService,
        store: Store,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """The write after the service's read: a file deleted between the two fails its own row's
        foreign key, and that row alone is skipped."""
        (kept, _other) = await self._filed(recording, library, add_file)
        username_id = str((await account_row(temp_db, "orla_fennimore"))["id"])
        await temp_db.execute("DELETE FROM asset_usernames WHERE asset_id = ?", (kept,))
        missing = NameFiling(
            asset_id="01HX0000000000000000000098", source=FROM_FILENAME, decided_at=1, post_id=None
        )
        back = NameFiling(asset_id=kept, source=FROM_FILENAME, decided_at=1, post_id=None)

        async with store.write() as connection:
            written = await store.refile_from_a_name_on(connection, username_id, [missing, back])

        assert written == 1
        assert [one for one, _source in await filings_of(temp_db, kept)] == [username_id]

    async def test_a_file_the_vault_holds_back_stays_filed(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        """Only the files the row counted come off: one the shut vault keeps from this viewer is not
        theirs to see, so it is not theirs to take back from here, and a record naming it would be
        missing from their own History."""
        held, shown = await self._filed(recording, library, add_file)
        username_id = (await account_row(temp_db, "orla_fennimore"))["id"]
        await hide(temp_db, "asset", held, admin.id)

        taken = await recording.take_back_username(admin, username_id=username_id)

        assert taken.files == 1
        assert await filings_of(temp_db, held) != []
        assert await filings_of(temp_db, shown) == []

    async def test_without_a_recorder_the_files_still_come_off_and_nothing_is_recorded(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        files = await self._filed(service, library, add_file)
        username_id = (await account_row(temp_db, "orla_fennimore"))["id"]
        before = len(await temp_db.fetch_all(_RECEIPTS))

        taken = await service.take_back_username(admin, username_id=username_id)

        assert (taken.files, taken.decision_id) == (2, None)
        assert [await filings_of(temp_db, one) for one in files] == [[], []]
        assert len(await temp_db.fetch_all(_RECEIPTS)) == before

    async def test_an_undo_with_nothing_it_may_put_back_restores_nothing(
        self,
        recording: SuggestionService,
        store: Store,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        """No filings in the record, a username gone or out of sight, every file out of sight, and
        a second Undo over filings already back: each answers False and writes nothing."""
        files = await self._filed(recording, library, add_file)
        username_id = str((await account_row(temp_db, "orla_fennimore"))["id"])
        decision_id, payload = await self._taken_back(recording, temp_db, admin)
        filings = [
            NameFiling(asset_id=one, source=FROM_FILENAME, decided_at=1, post_id=None)
            for one in files
        ]
        undo = recording.put_username_back

        assert await undo(admin, username_id=username_id, filings=[], refused=files) is False
        nobody = "01HX0000000000000000000099"
        assert await undo(admin, username_id=nobody, filings=filings, refused=files) is False
        queue = FiledFromFilenamesQueue(recording)
        assert await queue.reverse(admin, decision_id, payload) is True
        assert await store.refused_filenames() == set()
        # Everything is back, so a second Undo writes no filing and forgets no refusal.
        async with store.write() as connection:
            await store.refuse_filename_on(connection, files)
        assert await queue.reverse(admin, decision_id, payload) is False
        assert await store.refused_filenames() == set(files)
        for one in files:
            await hide(temp_db, "asset", one, admin.id)
        assert await undo(admin, username_id=username_id, filings=filings, refused=files) is False

    async def test_a_second_press_finds_nothing_to_take_back_and_records_nothing(
        self,
        recording: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        admin: Viewer,
    ) -> None:
        await self._filed(recording, library, add_file)
        username_id = (await account_row(temp_db, "orla_fennimore"))["id"]
        await recording.take_back_username(admin, username_id=username_id)
        before = len(await temp_db.fetch_all(_RECEIPTS))

        again = await recording.take_back_username(admin, username_id=username_id)
        nobody = await recording.take_back_username(admin, username_id="01HX0000000000000000000099")

        assert (again.files, again.decision_id) == (0, None)
        assert (nobody.files, nobody.decision_id) == (0, None)
        assert len(await temp_db.fetch_all(_RECEIPTS)) == before


async def test_a_folder_nobody_filed_has_no_filenames_to_suggest_from(store: Store) -> None:
    assert await store.filenames_in("01HNOSUCHFOLDER00000000000") == []
