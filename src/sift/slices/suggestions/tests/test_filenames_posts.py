# SPDX-License-Identifier: AGPL-3.0-or-later
"""What was posted together: the post a filename names, written down, and made into a Photo Set."""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
    seed_site_username,
)
from sift.kernel.db import Database
from sift.slices.suggestions.naming import (
    one_post,
    posted_in_filename,
    posts_among,
)
from sift.slices.suggestions.queue import FiledFromFilenamesQueue
from sift.slices.suggestions.service import (
    MadeSet,
    PostSets,
    SuggestionService,
)
from sift.slices.suggestions.store import Store
from sift.slices.suggestions.tests.conftest import (
    FakeFaces,
    FakePreferences,
    Library,
)
from sift.slices.suggestions.tests.test_filenames import _RECEIPTS, filings_of, posts_of
from sift.slices.workbench.store import Store as DecisionStore

#: Two pictures of one carousel and one file from a later post, by the same username.
#:
#: The three nineteen-digit numbers are all DIFFERENT, which is the whole point of these fixtures:
#: the site numbers every picture separately, so a rule that grouped on that number would make
#: three posts of one file each. The first two share the second the tool saved them in and the
#: third does not.
_FIRST = "orla_fennimore_3141592653_2718281828459045235_56432312663.jpg"


@pytest.fixture
async def decisions(temp_db: Database) -> DecisionStore:
    return DecisionStore(temp_db)


_SECOND = "orla_fennimore_3141592653_2718281828459045238_56432312663.jpg"


_LATER = "orla_fennimore_3141680302_2718281828459055326_56432312663.jpg"


class TestWhatWasPostedTogether:
    """The grouping rule, as pure work over the names. See `one_post`."""

    def test_the_account_and_the_second_are_what_two_files_must_share(self) -> None:
        first, second = posted_in_filename(_FIRST), posted_in_filename(_SECOND)
        assert first is not None and second is not None
        assert one_post(first) == one_post(second)

    def test_the_nineteen_digit_number_is_the_files_own_and_never_shared(self) -> None:
        """The finding, held as a test so it cannot quietly stop being true."""
        first, second = posted_in_filename(_FIRST), posted_in_filename(_SECOND)
        assert first is not None and second is not None
        assert first.media != second.media

    def test_a_later_post_by_the_same_account_is_a_different_post(self) -> None:
        first, later = posted_in_filename(_FIRST), posted_in_filename(_LATER)
        assert first is not None and later is not None
        assert one_post(first) != one_post(later)
        assert first.where == later.where

    def test_it_groups_by_account_then_by_post(self) -> None:
        readings = [
            (asset_id, posted_in_filename(name))
            for asset_id, name in (("a", _SECOND), ("b", _FIRST), ("c", _LATER))
        ]
        found = posts_among((one, two) for one, two in readings if two is not None)

        [(where, posts)] = found.items()
        assert where.name == "orla_fennimore"
        assert where.site == "Instagram"
        # The post is keyed by the SECOND, and its files come out ordered by the site's own
        # number for each of them, which for a carousel is the order the site made the pictures.
        # `b` is the lower of the two although it was read second.
        assert posts == {"3141592653": ["b", "a"], "3141680302": ["c"]}

    def test_one_file_in_two_places_is_one_file_in_its_post(self) -> None:
        """The read that feeds this is over PLACES, so it can hand back more rows than files."""
        one = posted_in_filename(_FIRST)
        assert one is not None
        assert posts_among([("a", one), ("a", one)]) == {one.where: {one.saved: ["a"]}}


class TestThePostIsWrittenDown:
    async def test_two_files_of_one_post_are_filed_under_the_same_post(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        first = await add_file(library, f"Unsorted/{_FIRST}")
        second = await add_file(library, f"Unsorted/{_SECOND}")
        later = await add_file(library, f"Unsorted/{_LATER}")

        assert await service.file_from_filenames() == 3

        assert await posts_of(temp_db, first.asset.id) == ["3141592653"]
        assert await posts_of(temp_db, second.asset.id) == ["3141592653"]
        assert await posts_of(temp_db, later.asset.id) == ["3141680302"]

    async def test_a_post_this_library_has_already_seen_part_of_is_not_stamped_again(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """The second half of a post arrives on a later scan and is filed with NO post.

        The filing is the same filing either way (the file is under the right username), and what
        is refused is the CLAIM that the library now holds this post whole. Anything later deriving
        a set from these rows reads the first pass's post and nothing else, which is the only
        reading that cannot make a set out of half of one.
        """
        first = await add_file(library, f"Unsorted/{_FIRST}")
        assert await service.file_from_filenames() == 1
        assert await posts_of(temp_db, first.asset.id) == ["3141592653"]

        second = await add_file(library, f"Unsorted/{_SECOND}")
        assert await service.file_from_filenames() == 1

        assert await posts_of(temp_db, second.asset.id) == [None]
        assert await filings_of(temp_db, second.asset.id) == await filings_of(
            temp_db, first.asset.id
        )


#: Four pictures of one carousel and a video from the same post: the mixed case.
_PICTURES = [
    f"orla_fennimore_3141592653_304648327269533132{n}_56432312663.jpg" for n in (1, 2, 3, 4)
]


class _Sets:
    """A stand-in for the photo-set seam, holding the rule's ANSWER rather than the rule.

    It records what it was asked to make and refuses the same cases the real one refuses, which is
    the part this slice's tests are about: what goes IN to a set, and what comes back out of an
    undo. Whether three pictures and a video derive nothing is the photo-set slice's own test.
    """

    def __init__(self, *, refuse: bool = False) -> None:
        self.made: list[tuple[tuple[str, ...], str]] = []
        self.forgotten: list[str] = []
        self._refuse = refuse

    async def derive(self, asset_ids: Sequence[str], name: str) -> MadeSet | None:
        if self._refuse or len(asset_ids) < 3:
            return None
        self.made.append((tuple(asset_ids), name))
        # The real maker names the set after what it was asked for, and answers with the name it
        # actually used. The stand-in does the same so the receipt's snapshot is exercised.
        return MadeSet(id=f"set-{len(self.made)}", name=name)

    async def forget(self, photo_set_id: str, by: object) -> None:
        self.forgotten.append(photo_set_id)


def _with_sets(
    store: Store,
    access: Repository,
    faces: FakeFaces,
    preferences: FakePreferences,
    decisions: DecisionStore,
    sets: _Sets,
) -> SuggestionService:
    return SuggestionService(
        store=store,
        access=access,
        faces=faces,
        preferences=preferences,
        recorder=decisions,
        sets=PostSets(derive=sets.derive, forget=sets.forget),
    )


class TestAPostBecomesAPhotoSet:
    async def test_four_pictures_of_one_post_are_offered_as_a_set_in_the_posted_order(
        self,
        store: Store,
        access: Repository,
        faces: FakeFaces,
        preferences: FakePreferences,
        decisions: DecisionStore,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """The order is the site's own numbering, and the name is the username.

        The files are added in the wrong order deliberately: what the set gets must be the order
        the post was made in and not the order a directory listing happened to hand over.
        """
        sets = _Sets()
        service = _with_sets(store, access, faces, preferences, decisions, sets)
        landed = {
            name: await add_file(library, f"Unsorted/{name}")
            for name in (_PICTURES[3], _PICTURES[0], _PICTURES[2], _PICTURES[1])
        }

        assert await service.file_from_filenames() == 4

        [(asset_ids, name)] = sets.made
        assert list(asset_ids) == [landed[one].asset.id for one in _PICTURES]
        assert name == "orla_fennimore"

    async def test_a_set_carries_its_own_undo_and_the_filings_keep_theirs(
        self,
        store: Store,
        access: Repository,
        faces: FakeFaces,
        preferences: FakePreferences,
        decisions: DecisionStore,
        admin: Viewer,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """Two kinds of decision on one card, and pressing one must not do the other's work.

        Taking back the SET unmakes the grouping and leaves every filing standing: somebody
        saying "these were not one post" is not saying "these are not from this username".
        """
        sets = _Sets()
        service = _with_sets(store, access, faces, preferences, decisions, sets)
        added = [await add_file(library, f"Unsorted/{one}") for one in _PICTURES]
        await service.file_from_filenames()

        queue = FiledFromFilenamesQueue(service)
        records, _ = await decisions.recent(limit=10, offset=0)
        about_the_set = [one for one in records if '"post_set"' in one.payload]
        assert len(about_the_set) == 1

        put_back = await queue.reverse(admin, about_the_set[0].id, about_the_set[0].payload)
        assert put_back is True

        assert sets.forgotten == ["set-1"]
        for one in added:
            assert await filings_of(store.database, one.asset.id) != []

    async def test_a_post_that_makes_no_set_writes_no_record_about_one(
        self,
        store: Store,
        access: Repository,
        faces: FakeFaces,
        preferences: FakePreferences,
        decisions: DecisionStore,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """A refusal is not a set with nothing in it: nothing is recorded and nothing to undo."""
        sets = _Sets(refuse=True)
        service = _with_sets(store, access, faces, preferences, decisions, sets)
        for one in _PICTURES:
            await add_file(library, f"Unsorted/{one}")

        assert await service.file_from_filenames() == 4

        assert sets.made == []
        records, _ = await decisions.recent(limit=10, offset=0)
        assert [one for one in records if '"post_set"' in one.payload] == []


class TestAPostSetWithNoRecord:
    async def test_a_service_that_records_nothing_still_makes_the_set(
        self,
        store: Store,
        access: Repository,
        faces: FakeFaces,
        preferences: FakePreferences,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """A pass that runs for nobody makes no decisions: the set is made and the files filed, and
        it is only the receipt (and so the undo) that is absent."""
        sets = _Sets()
        service = SuggestionService(
            store=store,
            access=access,
            faces=faces,
            preferences=preferences,
            sets=PostSets(derive=sets.derive, forget=sets.forget),
        )
        for one in _PICTURES:
            await add_file(library, f"Unsorted/{one}")

        assert await service.file_from_filenames() == 4

        assert len(sets.made) == 1
        assert await temp_db.fetch_all(_RECEIPTS) == []


class TestCatchingUpOnPostsFiledBefore:
    async def test_it_reads_the_names_again_and_makes_the_set(
        self,
        store: Store,
        access: Repository,
        faces: FakeFaces,
        preferences: FakePreferences,
        decisions: DecisionStore,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """A library filed by an older version: the filings are there and carry no post.

        The rows are put back to how that version left them (the post nulled out), which is the
        only honest way to build the fixture: this is exactly the state the catch-up exists for.
        """
        sets = _Sets()
        service = _with_sets(store, access, faces, preferences, decisions, sets)
        added = [await add_file(library, f"Unsorted/{one}") for one in _PICTURES]
        await service.file_from_filenames()
        sets.made.clear()
        async with store.write() as connection:
            await connection.execute("UPDATE asset_usernames SET post_id = NULL")

        assert await service.group_filings_into_posts() == 1

        [(asset_ids, name)] = sets.made
        assert list(asset_ids) == [one.asset.id for one in added]
        assert name == "orla_fennimore"
        assert await posts_of(store.database, added[0].asset.id) == ["3141592653"]

    async def test_it_converges(
        self,
        store: Store,
        access: Repository,
        faces: FakeFaces,
        preferences: FakePreferences,
        decisions: DecisionStore,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """Every row it answers for gains a post, so the second run has nothing to say.

        Without this the pass would make another set beside the first on every scan for ever.
        """
        sets = _Sets()
        service = _with_sets(store, access, faces, preferences, decisions, sets)
        for one in _PICTURES:
            await add_file(library, f"Unsorted/{one}")
        await service.file_from_filenames()
        async with store.write() as connection:
            await connection.execute("UPDATE asset_usernames SET post_id = NULL")
        assert await service.group_filings_into_posts() == 1

        assert await service.group_filings_into_posts() == 0

    async def test_a_stamp_that_is_no_moment_is_read_again_and_makes_no_set(
        self,
        store: Store,
        access: Repository,
        faces: FakeFaces,
        preferences: FakePreferences,
        decisions: DecisionStore,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """Files whose tool wrote no moment are one username's bucket, not one post: filed with no
        post, and the catch-up that reads them again finds none either."""
        sets = _Sets()
        service = _with_sets(store, access, faces, preferences, decisions, sets)
        added = [
            await add_file(
                library,
                f"Unsorted/orla_fennimore_0000000000_304648327269533132{n}_56432312663.jpg",
            )
            for n in (1, 2, 3)
        ]
        assert await service.file_from_filenames() == 3

        assert await service.group_filings_into_posts() == 0

        assert sets.made == []
        assert await posts_of(store.database, added[0].asset.id) == [None]

    async def test_a_post_whose_files_were_moved_to_another_username_is_left_alone(
        self,
        store: Store,
        access: Repository,
        faces: FakeFaces,
        preferences: FakePreferences,
        decisions: DecisionStore,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """This pass cannot file one post under two usernames, so reaching that means something
        else moved a file: a decision a filename does not get to overwrite."""
        sets = _Sets()
        service = _with_sets(store, access, faces, preferences, decisions, sets)
        added = [await add_file(library, f"Unsorted/{one}") for one in _PICTURES]
        await service.file_from_filenames()
        sets.made.clear()
        _, elsewhere = await seed_site_username(
            temp_db, site="Instagram", name="someone_else", made=MADE_BY_A_PERSON
        )
        async with store.write() as connection:
            await connection.execute("UPDATE asset_usernames SET post_id = NULL")
            await connection.execute(
                "UPDATE asset_usernames SET username_id = ? WHERE asset_id = ?",
                (elsewhere, added[0].asset.id),
            )

        assert await service.group_filings_into_posts() == 0

        assert sets.made == []
        assert await posts_of(store.database, added[1].asset.id) == [None]

    async def test_a_post_the_set_maker_refuses_is_written_down_and_makes_no_set(
        self,
        store: Store,
        access: Repository,
        faces: FakeFaces,
        preferences: FakePreferences,
        decisions: DecisionStore,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """The post goes on the filings either way (that half is the filename's answer), and a
        set refused is not counted as one made."""
        sets = _Sets()
        service = _with_sets(store, access, faces, preferences, decisions, sets)
        added = [await add_file(library, f"Unsorted/{one}") for one in _PICTURES]
        await service.file_from_filenames()
        async with store.write() as connection:
            await connection.execute("UPDATE asset_usernames SET post_id = NULL")
        refusing = _with_sets(store, access, faces, preferences, decisions, _Sets(refuse=True))

        assert await refusing.group_filings_into_posts() == 0

        assert await posts_of(store.database, added[0].asset.id) == ["3141592653"]

    async def test_the_scan_catches_up_on_posts_filed_before(
        self,
        store: Store,
        access: Repository,
        faces: FakeFaces,
        preferences: FakePreferences,
        decisions: DecisionStore,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """Under the same switch as the filing, on every pass: a library filed by an older version
        gets its sets from the next scan with nothing pressed."""
        sets = _Sets()
        service = _with_sets(store, access, faces, preferences, decisions, sets)
        for one in _PICTURES:
            await add_file(library, f"Unsorted/{one}")
        await service.file_from_filenames()
        sets.made.clear()
        async with store.write() as connection:
            await connection.execute("UPDATE asset_usernames SET post_id = NULL")

        await service.rebuild()

        assert len(sets.made) == 1
