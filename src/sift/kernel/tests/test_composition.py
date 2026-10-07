# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning a stash-box's names into rows, which is the one job no slice can do.

`composition` is the only file in Sift that names two slices together. It exists because people,
tags and Sites are owned by two different areas and a slice may not import another slice, so
the shapes are declared in the kernel and the implementation is built at the wiring.

Two rules carry the whole of it, and both are tested here rather than reasoned about:

  - **`creating` is permission, not intent.** Without it, a name that matches nothing answers with
    nothing and no row is made. That is what lets a bulk pass count what it WOULD invent before
    anybody presses a button.
  - **A name that matches several people matches none of them.** Two people really can share a
    spelling, and picking the first is picking at random and calling it a match.
"""

from __future__ import annotations

from typing import Any

import pytest

# Filing records an event through the ledger door, and the door writes into the workbench's
# own table, so a database built without that component has nowhere to put it and every
# filing test fails on `no such table`. Imported for the registration, nothing else.
import sift.slices.workbench.schema  # noqa: F401
from sift.composition import LibraryDropFiling, LibraryFiling, LibraryNaming
from sift.kernel.access import Repository, Role
from sift.kernel.content.user_state import UserStateStore
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.kernel.vocabulary import VIA_STASH_LIBRARY
from sift.slices.collections.service import CollectionService
from sift.slices.people.service import PeopleService
from sift.slices.photo_sets.service import PhotoSetService
from sift.slices.songs.service import SongService
from sift.slices.stash_boxes.enrich import IMPORTED, AssetWriter
from sift.slices.tags_ratings.service import DuplicateTag, Tag, TagService
from sift.testing.fixtures import World, create_user

pytestmark = pytest.mark.regression


@pytest.fixture
def naming(temp_db: Database, access: Repository) -> LibraryNaming:
    return LibraryNaming(temp_db, TagService(temp_db, access))


@pytest.fixture
def filing(temp_db: Database, access: Repository) -> LibraryFiling:
    return LibraryFiling(temp_db, TagService(temp_db, access))


@pytest.fixture
async def user(temp_db: Database, access: Repository) -> str:
    """A real user for the one doing the dropping.

    Not a made-up id: every drop records a ledger event, and the event keeps the user key beside the
    snapshot of the name, with a foreign key onto the `users` table, so an id belonging to nobody
    is refused by the database.
    """
    return (await create_user(temp_db, Role.ADMIN)).id


@pytest.fixture
def drop_filing(temp_db: Database, access: Repository) -> LibraryDropFiling:
    """The composition root's dispatch for a link dropped ON something.

    Built from the real services rather than from doubles, because what this class IS is the
    six-way choice between them: a double would leave the dispatch proved and every branch it
    dispatches to unexercised, which is the half that can be wrong.
    """
    return LibraryDropFiling(
        temp_db,
        people=PeopleService(temp_db, access),
        tags=TagService(temp_db, access),
        collections=CollectionService(temp_db, access),
        photo_sets=PhotoSetService(temp_db, access),
        songs=SongService(temp_db),
        user_state=UserStateStore(temp_db),
    )


class TestNamingSomebody:
    async def test_a_name_that_matches_nobody_makes_nobody_without_permission(
        self, naming: LibraryNaming, world: World, temp_db: Database
    ) -> None:
        assert await naming.person_named("Nobody At All", creating=False) is None
        rows = await temp_db.fetch_all("SELECT id FROM people WHERE name = ?", ("Nobody At All",))
        assert not rows

    async def test_with_permission_it_makes_them(self, naming: LibraryNaming) -> None:
        made = await naming.person_named("Somebody New", creating=True)
        assert made is not None
        assert await naming.person_named("Somebody New", creating=False) == made

    async def test_an_alias_finds_the_person_it_belongs_to(
        self, naming: LibraryNaming, world: World, temp_db: Database
    ) -> None:
        """The half that stops a library filling up with duplicates. A stash-box's spelling that
        somebody already wrote down as an also-known-as must find them, not make a second them."""
        made = await naming.person_named("Original Name", creating=True)
        assert made is not None
        await temp_db.execute(
            "INSERT INTO people_aliases (id, person_id, alias) VALUES ('a1', ?, 'Other Spelling')",
            (made,),
        )
        assert await naming.person_named("Other Spelling", creating=True) == made

    async def test_a_name_two_people_answer_to_matches_neither(
        self, naming: LibraryNaming, world: World, temp_db: Database
    ) -> None:
        """Ambiguous is not a match, and it is not a reason to make a third.

        Guessing would file somebody else's video under a person who has never been near it,
        silently, in a library nobody is auditing.
        """
        first = await naming.person_named("Shared Spelling", creating=True)
        assert first is not None
        second = await naming.person_named("Somebody Else", creating=True)
        assert second is not None
        await temp_db.execute(
            "INSERT INTO people_aliases (id, person_id, alias) VALUES ('a2', ?, 'Shared Spelling')",
            (second,),
        )
        assert await naming.person_named("Shared Spelling", creating=True) is None

    async def test_a_blank_name_names_nothing(self, naming: LibraryNaming) -> None:
        assert await naming.person_named("   ", creating=True) is None

    async def test_the_creator_of_an_edit_is_marked_as_one(
        self, naming: LibraryNaming, temp_db: Database
    ) -> None:
        """The fourth verb, and the only one that writes a fact about somebody rather than finding
        them. It goes through the catalog's one statement, so a creator reached from a file's answer
        and one reached from a link by hand are the same row written the same way."""
        made = await naming.person_named("Bramble Cutwork", creating=True)
        assert made is not None

        await naming.mark_pmv_creator(made)

        row = await temp_db.fetch_one("SELECT pmv_creator FROM people WHERE id = ?", (made,))
        assert row is not None and bool(row["pmv_creator"])

    async def test_saying_it_twice_is_saying_it_once(
        self, naming: LibraryNaming, temp_db: Database
    ) -> None:
        """Every file of theirs that a box recognises says it again, so it has to be free to repeat.

        The seam answers with nothing on purpose: the statement underneath reports whether the mark
        MOVED, which is a fact about the first file rather than about this one."""
        made = await naming.person_named("Bramble Cutwork", creating=True)
        assert made is not None

        await naming.mark_pmv_creator(made)
        await naming.mark_pmv_creator(made)

        rows = await temp_db.fetch_all("SELECT id FROM people WHERE pmv_creator = 1")
        assert [str(row["id"]) for row in rows] == [made]

    async def test_the_box_that_invented_a_row_is_written_once_and_never_rewritten(
        self, naming: LibraryNaming, temp_db: Database
    ) -> None:
        """The fifth verb. A row is created by exactly ONE act, so this only ever fills a blank.

        A second box describing the same person later must not be able to take the credit, and the
        writer has no way to know whether it is the first to say so: the seam answers with
        nothing, so the guard has to be in the statement rather than in the caller.

        A kind nothing can be created under, and a row that is not there, are both the same answer
        for the same reason: there is nothing to record, and neither is a failure to report.
        """
        made = await naming.person_named("Bramble Cutwork", creating=True)
        assert made is not None

        await naming.mark_created_by_box("person", made, "box-one")
        await naming.mark_created_by_box("person", made, "box-two")
        await naming.mark_created_by_box("sandwich", made, "box-two")
        await naming.mark_created_by_box("person", "nobody-at-all", "box-two")

        row = await temp_db.fetch_one("SELECT created_by_box_id FROM people WHERE id = ?", (made,))
        assert row is not None
        assert str(row["created_by_box_id"]) == "box-one"


class TestNamingASite:
    async def test_it_finds_one_that_exists_whatever_the_capitalisation(
        self, naming: LibraryNaming
    ) -> None:
        """The name is unique under a case-insensitive collation, so `TikTok` and `tiktok` are one
        site, and an import that made a second would split a library down the middle."""
        made = await naming.site_named("SomeSite", creating=True)
        assert made is not None
        assert await naming.site_named("somesite", creating=False) == made

    async def test_without_permission_it_makes_none(self, naming: LibraryNaming) -> None:
        assert await naming.site_named("Never Seen", creating=False) is None

    async def test_a_blank_name_names_nothing(self, naming: LibraryNaming) -> None:
        assert await naming.site_named("", creating=True) is None


class TestNamingATag:
    async def test_it_finds_or_makes_one(self, naming: LibraryNaming) -> None:
        assert await naming.tag_named("brand new tag", creating=False) is None
        made = await naming.tag_named("brand new tag", creating=True)
        assert made is not None
        assert await naming.tag_named("BRAND NEW TAG", creating=False) == made

    async def test_a_blank_name_names_nothing(self, naming: LibraryNaming) -> None:
        assert await naming.tag_named(" ", creating=True) is None

    async def test_two_imports_racing_for_one_new_tag_both_get_the_same_row(
        self, naming: LibraryNaming, world: World, temp_db: Database
    ) -> None:
        """The race between looking and making, handled by catching the refusal rather than by
        locking. Two imports asking for the same new tag at the same time is an ordinary thing, and
        the second one wants the row the first just made.

        Played out by making the tag between the look and the make, which is exactly what the
        other import would have done.
        """
        await temp_db.execute(
            "INSERT INTO tags (id, name, created_at) VALUES ('t-race', 'Raced', 0)"
        )
        assert await naming.tag_named("raced", creating=True) == "t-race"

    async def test_a_tag_made_between_the_look_and_the_make_is_found_anyway(
        self,
        naming: LibraryNaming,
        temp_db: Database,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The real race, played out rather than argued about.

        The look finds nothing, another import makes the tag, and the make is then refused. Reading
        again is what turns that refusal into the row the other import just wrote: without it, an
        import that lost a race would silently drop a tag.
        """

        async def refused(_service: TagService, name: str, *, made: object) -> Tag:
            await temp_db.execute(
                "INSERT INTO tags (id, name, created_at) VALUES ('t-late', 'Late', 0)"
            )
            raise DuplicateTag(name)

        monkeypatch.setattr(TagService, "create", refused)
        assert await naming.tag_named("Late", creating=True) == "t-late"

    async def test_a_refusal_with_no_row_behind_it_answers_with_nothing(
        self,
        naming: LibraryNaming,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The other half of the same branch. A refusal Sift cannot explain is not a tag id, and
        answering with one would put an import's writes on a row that is not there."""

        async def refused(_service: TagService, name: str, *, made: object) -> Tag:
            raise DuplicateTag(name)

        monkeypatch.setattr(TagService, "create", refused)
        assert await naming.tag_named("Never Written", creating=True) is None


class TestFiling:
    async def test_it_reads_what_is_on_a_file_by_name(
        self, filing: LibraryFiling, world: World, temp_db: Database
    ) -> None:
        assert await filing.people_on(world.loose) == ()
        await filing.attribute(world.loose, world.person, source=IMPORTED)
        assert len(await filing.people_on(world.loose)) == 1

    async def test_it_reads_the_tags_on_a_file_by_name(
        self, filing: LibraryFiling, world: World
    ) -> None:
        """The other half of what an enrichment plan compares against.

        Read by name and not by row, and ordered by the sort name, because the plan it feeds is a
        comparison of words: a box that answers "Solo" about a file already tagged "Solo" must
        find it there whatever id the tag was given.
        """
        assert await filing.tags_on(world.solo) == ("tag",)
        assert await filing.tags_on(world.loose) == ()
        await filing.attach_tag(world.loose, world.tag, source=IMPORTED)
        assert await filing.tags_on(world.loose) == ("tag",)

    async def test_an_attribution_carries_how_it_got_there(
        self, filing: LibraryFiling, world: World, temp_db: Database
    ) -> None:
        """The mark is what makes an import findable again: counted, filtered, and taken back off
        without touching the ones somebody chose."""
        await filing.attribute(world.loose, world.person, source=IMPORTED)
        row = await temp_db.fetch_one(
            "SELECT source FROM asset_people WHERE asset_id = ? AND person_id = ?",
            (world.loose, world.person),
        )
        assert row is not None
        assert row["source"] == IMPORTED

    async def test_a_hand_made_attribution_is_not_overwritten_by_an_import(
        self, filing: LibraryFiling, world: World, temp_db: Database
    ) -> None:
        """The first answer wins. A person somebody put there by hand does not become an automatic
        one because a stash-box agreed with them a week later."""
        await temp_db.execute(
            "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)",
            (world.loose, world.person),
        )
        await filing.attribute(world.loose, world.person, source=IMPORTED)
        row = await temp_db.fetch_one(
            "SELECT source FROM asset_people WHERE asset_id = ? AND person_id = ?",
            (world.loose, world.person),
        )
        assert row is not None
        assert row["source"] is None

    async def test_a_person_with_no_picture_is_drawn_by_the_file_a_stash_box_put_them_on(
        self, filing: LibraryFiling, world: World, temp_db: Database
    ) -> None:
        """A stash-box's answer is a decision Sift made, like a folder filed, so a person it puts on
        a file and who has no picture is given a picture by the cover rule: their first file
        (`solo`, filed before `loose`). A later answer replaces nothing."""
        await temp_db.execute(
            "UPDATE people SET cover_asset_id = NULL, cover_upload_id = NULL WHERE id = ?",
            (world.person,),
        )
        await filing.attribute(world.loose, world.person, source=IMPORTED)
        row = await temp_db.fetch_one(
            "SELECT cover_asset_id FROM people WHERE id = ?", (world.person,)
        )
        assert row is not None
        assert row["cover_asset_id"] == world.solo, "a stash-box's person was left as a letter"

        await filing.attribute(world.solo, world.person, source=IMPORTED)
        row = await temp_db.fetch_one(
            "SELECT cover_asset_id FROM people WHERE id = ?", (world.person,)
        )
        assert row is not None
        assert row["cover_asset_id"] == world.solo, "a later answer replaced the picture"

    async def test_a_tag_carries_how_it_got_there_too(
        self, filing: LibraryFiling, world: World, temp_db: Database
    ) -> None:
        await filing.attach_tag(world.loose, world.tag, source=IMPORTED)
        assert await filing.tags_on(world.loose) != ()
        row = await temp_db.fetch_one(
            "SELECT source FROM asset_tags WHERE asset_id = ? AND tag_id = ?",
            (world.loose, world.tag),
        )
        assert row is not None
        assert row["source"] == IMPORTED

    async def test_a_stash_import_s_filings_are_said_as_its_own_not_a_stash_box_s(
        self, filing: LibraryFiling, world: World, temp_db: Database
    ) -> None:
        await filing.attach_tag(world.loose, world.tag, source=VIA_STASH_LIBRARY)
        await filing.file_under_site(world.loose, "Elsewhere", source=VIA_STASH_LIBRARY)

        said = await temp_db.fetch_all(
            "SELECT DISTINCT actor_id FROM workbench_decisions WHERE actor_kind = 'sift'"
        )
        made = await temp_db.fetch_one("SELECT created_by_via FROM sites WHERE name = 'Elsewhere'")
        assert [row["actor_id"] for row in said] == [VIA_STASH_LIBRARY]
        assert made is not None and made["created_by_via"] == VIA_STASH_LIBRARY

    async def test_filing_a_file_under_a_site_carries_how_it_got_there_too(
        self, filing: LibraryFiling, world: World, temp_db: Database
    ) -> None:
        """The third of the three marks, through the one body that knows what "filed under a site"
        writes: a username row with an empty name, and the join to it.

        It goes through that body rather than being written here because the join lives in the
        permission layer's catalog and the slice asking for it may not name it, so what is
        asserted is that the site is reachable afterwards by the ordinary read, with the mark on
        the row that carries it.
        """
        await temp_db.execute("INSERT INTO sites (id, name) VALUES ('pf9', 'Somewhere')")

        await filing.file_under_site(world.loose, "Somewhere", source=IMPORTED)

        assert await filing.site_of(world.loose) == "Somewhere"
        row = await temp_db.fetch_one(
            "SELECT source FROM asset_usernames WHERE asset_id = ?", (world.loose,)
        )
        assert row is not None
        assert row["source"] == IMPORTED

    async def test_a_file_with_no_username_on_it_is_on_no_site(
        self, filing: LibraryFiling, world: World, temp_db: Database
    ) -> None:
        """A file belongs to a site through the username that posted it, and a scanned library has
        none. Answering with nothing is the truth rather than a gap."""
        assert await filing.site_of(world.loose) is None

    async def test_the_site_comes_through_the_username_that_posted_it(
        self, filing: LibraryFiling, world: World, temp_db: Database
    ) -> None:
        await temp_db.execute("INSERT INTO sites (id, name) VALUES ('pf1', 'Somewhere')")
        await temp_db.execute(
            "INSERT INTO usernames (id, site_id, name, created_at)"
            " VALUES ('ac1', 'pf1', 'someone', 0)"
        )
        await temp_db.execute(
            "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, 'ac1')", (world.loose,)
        )
        assert await filing.site_of(world.loose) == "Somewhere"

    async def test_the_usernames_a_file_is_filed_under_are_read_with_their_site_and_page(
        self, filing: LibraryFiling, world: World, temp_db: Database
    ) -> None:
        """What a stash-box answer is compared against: every named username on the file, each
        with its Site and its page, in the order the Sites and handles sort. A Site's nameless row
        (a file filed under the Site alone) names no account and is not one."""
        await temp_db.execute("INSERT INTO sites (id, name) VALUES ('pf2', 'Zinefront')")
        await temp_db.execute("INSERT INTO sites (id, name) VALUES ('pf3', 'Atrium')")
        assert await filing.file_under_username(
            world.loose,
            site="Zinefront",
            handle="wrenh",
            url="https://zine.example/wrenh",
            source=IMPORTED,
        )
        assert await filing.file_under_username(
            world.loose, site="Atrium", handle="Bryn", url=None, source=IMPORTED
        )
        await filing.file_under_site(world.loose, "Atrium", source=IMPORTED)

        assert await filing.accounts_on(world.loose) == (
            {"site": "Atrium", "handle": "Bryn", "url": ""},
            {"site": "Zinefront", "handle": "wrenh", "url": "https://zine.example/wrenh"},
        )
        assert await filing.accounts_on(world.twin) == ()


async def test_it_answers_against_the_database_it_was_built_with(
    temp_db: Database, access: Repository
) -> None:
    """A guard against the shape rather than the behaviour: both of these are built at the wiring
    from the application's own handle, and one holding a different database is a set of writes
    landing where nothing reads them."""
    naming = LibraryNaming(temp_db, TagService(temp_db, access))
    assert isinstance(temp_db, Database)
    assert await naming.tag_named("nothing here", creating=False) is None


class TestDropFiling:
    """Where a dropped link's file ends up, one branch at a time.

    Six kinds and a seventh answer, and every one of them calls the same code the matching verb in
    a menu calls, which is the property the class exists for and the reason none of these assert
    on rows this file wrote itself. What is asserted is what the shared write left behind.

    It runs minutes after the drop, in a job with no request behind it, so a target that has since
    been deleted means "nothing to file" rather than an error. That is the case each branch is
    asked twice about: once with the target there, once without.
    """

    async def test_a_kind_nobody_recognises_files_nothing_and_does_not_raise(
        self, drop_filing: LibraryDropFiling, world: World
    ) -> None:
        """The aim is written on a ledger row that can be months old by the time a retry reads it.
        A download that failed because an old row names a kind this version dropped would be a
        download lost to a rename."""
        assert (
            await drop_filing.file_under(
                kind="whatever-that-was",
                target_id=world.person,
                asset_ids=[world.loose],
                for_user="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            )
            == 0
        )

    async def test_nothing_to_file_is_nothing_to_do(
        self, drop_filing: LibraryDropFiling, world: World, user: str
    ) -> None:
        """A download that produced no file at all still reaches this, and asking the services to
        file an empty list would be a write for nobody."""
        assert (
            await drop_filing.file_under(
                kind="person", target_id=world.person, asset_ids=[], for_user=user
            )
            == 0
        )

    async def test_a_person_is_put_on_the_file(
        self, drop_filing: LibraryDropFiling, world: World, temp_db: Database
    ) -> None:
        filed = await drop_filing.file_under(
            kind="person",
            target_id=world.person,
            asset_ids=[world.loose],
            for_user="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        )
        assert filed == 1
        row = await temp_db.fetch_one(
            "SELECT source FROM asset_people WHERE asset_id = ? AND person_id = ?",
            (world.loose, world.person),
        )
        assert row is not None
        assert row["source"] == "by_hand"

    async def test_a_file_already_on_that_person_is_not_counted_twice(
        self, drop_filing: LibraryDropFiling, world: World, user: str
    ) -> None:
        """The count is taken from what the write ANSWERED with rather than from the number handed
        in. A file already on that person is not a second filing of it, and reporting it as one
        would put a number on a screen that nothing in the library agrees with."""
        first = await drop_filing.file_under(
            kind="person", target_id=world.person, asset_ids=[world.loose], for_user=user
        )
        again = await drop_filing.file_under(
            kind="person", target_id=world.person, asset_ids=[world.loose], for_user=user
        )
        assert (first, again) == (1, 0)

    async def test_a_site_is_reached_by_its_NAME_and_files_the_file_under_it(
        self, drop_filing: LibraryDropFiling, world: World, temp_db: Database, user: str
    ) -> None:
        """Filing under a site takes a name, not an id: the row carrying the attribution is an
        username on that site, found or made from the name, which is what every question about a
        site joins through. The same call the `Add to > Site` verb makes."""
        filed = await drop_filing.file_under(
            kind="site", target_id=world.site, asset_ids=[world.loose], for_user=user
        )
        assert filed == 1
        row = await temp_db.fetch_one(
            "SELECT ac.site_id FROM asset_usernames aa"
            " JOIN usernames ac ON ac.id = aa.username_id"
            " WHERE aa.asset_id = ? AND ac.site_id = ?",
            (world.loose, world.site),
        )
        assert row is not None

    async def test_a_site_deleted_while_the_download_ran_files_nothing(
        self, drop_filing: LibraryDropFiling, world: World, user: str
    ) -> None:
        """The one branch with a lookup of its own, and the reason it has one: the name has to come
        from somewhere, and a site that has gone has no name to give. Zero rather than a raised
        error: the file arrived and is in the library; only the aim is gone."""
        assert (
            await drop_filing.file_under(
                kind="site",
                target_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
                asset_ids=[world.loose],
                for_user=user,
            )
            == 0
        )

    async def test_a_collection_takes_the_file_at_the_end_of_its_sequence(
        self, drop_filing: LibraryDropFiling, world: World, temp_db: Database, user: str
    ) -> None:
        filed = await drop_filing.file_under(
            kind="collection",
            target_id=world.collection,
            asset_ids=[world.loose],
            for_user=user,
        )
        assert filed == 1
        row = await temp_db.fetch_one(
            "SELECT 1 AS there FROM collection_items WHERE collection_id = ? AND asset_id = ?",
            (world.collection, world.loose),
        )
        assert row is not None

    async def test_a_photo_set_takes_it_too(
        self, drop_filing: LibraryDropFiling, world: World, temp_db: Database, user: str
    ) -> None:
        filed = await drop_filing.file_under(
            kind="photo_set",
            target_id=world.photo_set,
            asset_ids=[world.loose],
            for_user=user,
        )
        assert filed == 1
        row = await temp_db.fetch_one(
            "SELECT 1 AS there FROM photo_set_items WHERE photo_set_id = ? AND asset_id = ?",
            (world.photo_set, world.loose),
        )
        assert row is not None

    async def test_a_song_takes_it_by_hand_and_moves_it_off_another(
        self, drop_filing: LibraryDropFiling, world: World, temp_db: Database, user: str
    ) -> None:
        """A link dropped on a song's page: what arrives is put on that song, as the page's own
        Add files puts it, a person's hand, so a song a Site's page names later never replaces it."""
        song = await SongService(temp_db).create("Harbour Lights", by_user=user)
        filed = await drop_filing.file_under(
            kind="song", target_id=song.id, asset_ids=[world.loose], for_user=user
        )
        assert filed == 1
        row = await temp_db.fetch_one(
            "SELECT song_id, source FROM song_files WHERE asset_id = ?", (world.loose,)
        )
        assert row is not None and (row["song_id"], row["source"]) == (song.id, None)

    async def test_a_tag_is_attached_and_marked_as_somebodys_own_doing(
        self, drop_filing: LibraryDropFiling, world: World, temp_db: Database, user: str
    ) -> None:
        filed = await drop_filing.file_under(
            kind="tag", target_id=world.tag, asset_ids=[world.loose], for_user=user
        )
        assert filed == 1
        row = await temp_db.fetch_one(
            "SELECT source FROM asset_tags WHERE asset_id = ? AND tag_id = ?",
            (world.loose, world.tag),
        )
        assert row is not None
        assert row["source"] == "by_hand"

    async def test_the_heart_is_the_one_that_belongs_to_an_ACCOUNT(
        self, drop_filing: LibraryDropFiling, world: World, temp_db: Database
    ) -> None:
        """Five of the six kinds are facts about the library and this one is an opinion, which is
        the whole reason the user is carried this far. `target_id` names nothing (the target IS
        the heart), and the route refuses a drop on Favorites that carries one."""
        someone = (await create_user(temp_db, Role.ADMIN)).id
        filed = await drop_filing.file_under(
            kind="favorite", target_id="", asset_ids=[world.loose], for_user=someone
        )
        assert filed == 1
        row = await temp_db.fetch_one(
            "SELECT favorite FROM asset_user_state WHERE asset_id = ? AND user_id = ?",
            (world.loose, someone),
        )
        assert row is not None
        assert row["favorite"] == 1


class _NoReindex:
    """The one seam this join does not exercise, and it says so rather than pretending.

    `write` tells the index once, after the writes, because the index reads the title, the people
    and the tags: telling it three times would rebuild one row three times for one decision. That
    ordering has its own test against doubles. What this class is here for is to let the REST of the
    call run against a real database.
    """

    def __init__(self) -> None:
        self.told: list[str] = []

    async def touched(self, asset_id: str) -> None:
        self.told.append(asset_id)

    async def renamed(self) -> None:  # pragma: no cover - this writer never renames
        raise AssertionError("the asset writer does not rename")


class TestTickingSomeNamesAndNotOthers:
    """`creating` IS ASKED PER NAME, and this is the only test that asks it of a real database.

    `may_create` is two lines and has its own tests; `LibraryNaming.person_named` is tested above,
    one name at a time, against this same database; and `AssetWriter.write` is tested against
    doubles, where the "naming" it talks to invents an id for any name it is allowed to. This walks
    the whole thing: a page of matches offering two people, one ticked, and the writer deciding per
    name through the real lookup that makes the row.

    The case matters because "all of them or none" is not an answer anybody has. A stash-box page
    can name dozens of things a library has never heard of, half of them people worth having and
    half words somebody else files as tags, so the ones ticked are made and the rest are dropped,
    with the same drop a run with no permission at all would give.
    """

    async def test_only_the_ticked_name_is_invented_and_the_other_is_dropped(
        self, naming: LibraryNaming, filing: LibraryFiling, world: World
    ) -> None:
        writer = AssetWriter(
            content=None,  # type: ignore[arg-type]
            filing=filing,
            naming=naming,
            reindex=_NoReindex(),  # type: ignore[arg-type]
        )

        await writer.write(
            world.solo,
            {"people": ["Ticked Person", "Not Ticked Person"]},
            creating=frozenset({("person", "Ticked Person")}),
            actor=Actor.sift("stash"),
        )

        made = await naming.person_named("Ticked Person", creating=False)
        assert made is not None, "the ticked name was not made"
        assert await naming.person_named("Not Ticked Person", creating=False) is None, (
            "a name nobody ticked was invented anyway"
        )
        assert "Ticked Person" in await filing.people_on(world.solo)
        assert "Not Ticked Person" not in await filing.people_on(world.solo)

    async def test_a_name_the_library_already_knows_is_attached_whether_it_was_ticked_or_not(
        self, naming: LibraryNaming, filing: LibraryFiling, world: World
    ) -> None:
        """The other half of the promise, and the half that makes the drop safe to live with.

        Dropping the names nobody ticked must not cost the file the names it already knows, so an
        existing person is found and attached with no permission of any kind, because finding is not
        creating.
        """
        existing = await naming.person_named("Already Here", creating=True)
        assert existing is not None
        writer = AssetWriter(
            content=None,  # type: ignore[arg-type]
            filing=filing,
            naming=naming,
            reindex=_NoReindex(),  # type: ignore[arg-type]
        )

        await writer.write(
            world.solo,
            {"people": ["Already Here", "Still A Stranger"]},
            creating=frozenset(),
            actor=Actor.sift("stash"),
        )

        on_it = await filing.people_on(world.solo)
        assert "Already Here" in on_it
        assert "Still A Stranger" not in on_it
        assert await naming.person_named("Still A Stranger", creating=False) is None


# --- the swap's two lines into the faces feature ----------------------------------------------


class _Configured:
    family = "accurate"


class _FakeFaces:
    """The face service as the two swap adapters see it: switched on, a catalog family, and a
    pack import that counts what it was handed."""

    def __init__(self, *, on: bool = True, described: object = None, adds: int = 2) -> None:
        self.on = on
        self.described = described
        self.adds = adds
        self.imported: list[bytes] = []
        #: What each import was asked besides the bytes.
        self.asked: list[dict[str, object]] = []
        #: The person ids a swap's offer asked the pictures of.
        self.pictured: list[list[str]] = []

    async def enabled(self) -> bool:
        return self.on

    async def configuration(self) -> _Configured:
        return _Configured()

    async def descriptions_for_swap(self, person_ids: object) -> object:
        return self.described

    async def pictures_for_swap(self, person_ids: list[str], *, best: int) -> dict[str, bytes]:
        self.pictured.append(list(person_ids))
        return {"d1": b"\xff\xd8\xffd1"}

    async def take_from_swap(self, raw: bytes, **asked: object) -> object:
        self.imported.append(raw)
        self.asked.append(asked)
        from sift.slices.faces.service import PackOutcome

        return PackOutcome(added=self.adds, held=[], alias_clashes=[])


class _FakeQueue:
    def __init__(self) -> None:
        self.asked = 0

    async def enqueue_when_settled(self, *args: object, **kwargs: object) -> None:
        self.asked += 1


async def test_a_swaps_offer_reads_the_faces_feature_through_one_line_and_none_when_it_is_off() -> (
    None
):
    from sift.composition import SwapFaceDescriptions

    described = (
        "rev-1",
        4,
        {"p1": [("d1", 0.9, [0.1, 0.2, 0.3, 0.4]), ("d2", 0.5, [1.0, 0, 0, 0])]},
    )
    faces_on: Any = _FakeFaces(described=described)
    found = await SwapFaceDescriptions(faces_on).descriptions(["p1"])
    assert found is not None and set(found) == {"p1"}
    assert (found["p1"].recognizer, found["p1"].dimension) == ("rev-1", 4)
    assert [one.digest for one in found["p1"].faces] == ["d1", "d2"]

    faces_off: Any = _FakeFaces(on=False, described=None)
    assert await SwapFaceDescriptions(faces_off).descriptions(["p1"]) is None

    # The pictures only for a guest on the other model: never asked for otherwise.
    assert faces_on.pictured == []
    same = await SwapFaceDescriptions(faces_on).descriptions(["p1"], peer_model="rev-1")
    assert same is not None and faces_on.pictured == []
    other = await SwapFaceDescriptions(faces_on).descriptions(["p1"], peer_model="rev-2")
    assert other is not None and faces_on.pictured == [["p1"]]
    assert [one.picture for one in other["p1"].faces] == ["/9j/ZDE=", None]


async def test_a_swaps_landing_holds_a_persons_faces_as_one_pack_and_never_twice() -> None:
    """The pack is built once per name and imported once: a second file of the same person in the
    same session hands the same descriptions, and re-importing would read as a new edition."""
    import struct

    from sift.composition import SwapFaceHolding
    from sift.slices.faces import packs, weights
    from sift.slices.swap.ingest import OfferedFace

    faces: Any = _FakeFaces()
    queue: Any = _FakeQueue()
    holding = SwapFaceHolding(faces, queue)
    revision = weights.pairing("accurate")[1].revision
    assert await holding.recognizer() == revision
    vector = struct.pack("<4f", 0.1, 0.2, 0.3, 0.4)
    offered = [OfferedFace(digest="d1", quality=0.9, vector=vector)]

    added = await holding.hold(
        pack="swap:S1:p1", person="Ada Example", recognizer=revision, dimension=4, faces=offered
    )
    again = await holding.hold(
        pack="swap:S1:p1", person="Ada Example", recognizer=revision, dimension=4, faces=offered
    )

    assert (added, again) == (2, 0)
    assert len(faces.imported) == 1 and queue.asked == 1
    pack = packs.read(faces.imported[0], expect_recognizer=revision)
    assert [one.name for one in pack.people] == ["Ada Example"]
    assert pack.people[0].faces[0].picture is None
    assert [round(x, 4) for x in pack.people[0].faces[0].vector] == [0.1, 0.2, 0.3, 0.4]

    off: Any = _FakeFaces(on=False)
    assert await SwapFaceHolding(off, queue).recognizer() is None


async def test_a_swaps_faces_from_the_other_model_go_in_as_pictures_to_describe_again() -> None:
    import struct

    from sift.composition import SwapFaceHolding
    from sift.slices.faces import packs
    from sift.slices.swap.ingest import OfferedFace

    faces: Any = _FakeFaces()
    offered = [
        OfferedFace(
            digest="d1",
            quality=0.9,
            vector=struct.pack("<2f", 0.1, 0.2),
            picture=b"\xff\xd8\xffd1",
        )
    ]
    queue: Any = _FakeQueue()
    await SwapFaceHolding(faces, queue).hold(
        pack="swap:S1:p1", person="Ada Example", recognizer="theirs", dimension=2, faces=offered
    )
    assert faces.asked[0]["other_model"] is True
    pack = packs.read(faces.imported[0], expect_recognizer="ours", other_model=True)
    assert pack.recognizer == "theirs"
    assert pack.people[0].faces[0].picture == b"\xff\xd8\xffd1"


async def test_a_pack_that_adds_no_face_asks_for_no_rematching() -> None:
    """Every face in the pack was here already, so nothing new can match: the whole-library
    rematch would be a pass over every face for no change, and it is not asked for."""
    import struct

    from sift.composition import SwapFaceHolding
    from sift.slices.faces import weights
    from sift.slices.swap.ingest import OfferedFace

    faces: Any = _FakeFaces(adds=0)
    queue: Any = _FakeQueue()
    revision = weights.pairing("accurate")[1].revision
    offered = [OfferedFace(digest="d1", quality=0.9, vector=struct.pack("<4f", 1, 0, 0, 0))]

    added = await SwapFaceHolding(faces, queue).hold(
        pack="swap:S1:p1", person="Ada Example", recognizer=revision, dimension=4, faces=offered
    )

    assert added == 0
    assert len(faces.imported) == 1 and queue.asked == 0
