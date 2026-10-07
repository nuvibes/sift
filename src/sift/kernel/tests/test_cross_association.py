# SPDX-License-Identifier: AGPL-3.0-or-later
"""One entity wall, filtered to the files another entity reaches: which tags are on this person,
which people turn up on this site.

The failure worth guarding is a WIDE answer: a filter that did not apply shows every tag in the
library with the library's counts, which looks correct. So each test asserts the unfiltered answer
first, and the counts alongside the names.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from sift.kernel.access import LoopView, Repository, Role, UsernamePage, Viewer
from sift.kernel.access.constraints import NO_FILTER, AssetFilter, Where
from sift.kernel.access.related import related_filter
from sift.kernel.access.repository import entities as ent
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore
from sift.kernel.db import Database
from sift.kernel.ids import new_id

pytestmark = pytest.mark.unit

_EPOCH = 1_700_000_000

#: Whoever is asking, as the viewer parameters all five entity statements bind alike.
_ADMIN: dict[str, object] = {
    "viewer": "u1",
    "is_admin": 1,
    "reveal": 1,
    "reveal_named": 1,
    "prefix": "",
    "like": "%",
    "person_id": None,
    "site_id": None,
    "tag_id": None,
    "collection_id": None,
    "photo_set_id": None,
    "loop_id": None,
    "loop_asset_id": None,
    "loop_tag": None,
    "entity_sort": "seen",
    "list_empty": 1,
    "limit": 50,
    "offset": 0,
}


@pytest.fixture
async def db(temp_db: Database) -> Database:
    """Two files, and a catalog arranged so that every filter has something to remove.

    `both` is on each file and `only-first` on one. The person is on the SECOND file and the photo
    set holds the FIRST, so "photo sets on that person's files" is rightly nothing.
    """
    await temp_db.initialize_schema()
    async with temp_db.write() as c:
        await c.execute(
            "INSERT INTO users (id, username, password_hash, role, created_at)"
            " VALUES ('u1', 'admin', 'x', 'admin', ?)",
            (_EPOCH,),
        )
        await c.execute(
            "INSERT INTO library_roots (id, name, abs_path, kind, created_at)"
            " VALUES ('r1', 'root', '/library/r1', 'local', ?)",
            (_EPOCH,),
        )
        await c.execute(
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name)"
            " VALUES ('f1', 'r1', NULL, 'shoot', 'shoot')"
        )
        for n in (1, 2):
            await c.execute(
                "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'image', ?)",
                (f"a{n}", f"digest-{n}", _EPOCH),
            )
            # A file that is nowhere is counted by none of these statements.
            await c.execute(
                "INSERT INTO asset_locations"
                " (id, asset_id, root_id, folder_id, rel_path, filename,"
                "  status, first_seen_at, last_seen_at)"
                " VALUES (?, ?, 'r1', 'f1', ?, ?, 'present', ?, ?)",
                (f"l{n}", f"a{n}", f"shoot/{n}.jpg", f"{n}.jpg", _EPOCH, _EPOCH),
            )
        await c.execute(
            "INSERT INTO tags (id, name, created_at) VALUES ('t1', 'both', ?), ('t2', 'only-first', ?)",
            (_EPOCH, _EPOCH),
        )
        # Columns named, so a column added by a migration does not break the insert.
        await c.execute(
            "INSERT INTO asset_tags (asset_id, tag_id) VALUES ('a1','t1'), ('a2','t1'), ('a1','t2')"
        )
        await c.execute(
            "INSERT INTO people (id, name, created_at) VALUES ('p1', 'somebody', ?)", (_EPOCH,)
        )
        await c.execute("INSERT INTO asset_people (asset_id, person_id) VALUES ('a2', 'p1')")
        await c.execute(
            "INSERT INTO photo_sets (id, name, origin, created_at)"
            " VALUES ('s1', 'Shoot', 'folder', ?)",
            (_EPOCH,),
        )
        await c.execute(
            "INSERT INTO photo_set_items (photo_set_id, asset_id, position) VALUES ('s1', 'a1', 0)"
        )
    return temp_db


#: A `:name` in a statement; `(?<![:\w])` keeps it off a doubled colon and a name mid-match.
_BOUND = re.compile(r"(?<![:\w]):([a-z_][a-z0-9_]*)")


def _every_binding(sql: str, supplied: dict[str, object]) -> dict[str, object]:
    """`supplied`, and None for every other parameter the statement names.

    The statement is the only thing that knows what it binds, so it is asked rather than mirrored
    by hand. None reads as "do not filter" in all five, which is an unfiltered wall.
    """
    return {name: None for name in _BOUND.findall(sql)} | supplied


def test_the_binding_scan_finds_what_the_statements_name() -> None:
    """A KNOWN POSITIVE: the binding scan finds the parameters a statement names."""
    found = set(_BOUND.findall(ent.people_query("")))
    assert {"viewer", "person_id", "person_ids", "limit"} <= found
    assert _every_binding("SELECT :a, :b", {"a": 7}) == {"a": 7, "b": None}


async def wall(
    db: Database,
    builder: Callable[[str], str],
    asset_filter: AssetFilter = NO_FILTER,
) -> list[tuple[str, int]]:
    """One entity wall as `(name, count)` pairs, filtered or not."""
    where, bound = asset_filter.predicate()
    # An admin lists rows with nothing under them on a PLAIN wall only, as the store binds it.
    narrowing = asset_filter is not NO_FILTER
    statement = builder(where)
    rows = await db.fetch_all(
        statement,
        _every_binding(statement, {**bound, **_ADMIN, "list_empty": 0 if narrowing else 1}),
    )
    # A wall of things counts FILES, of containers what is in them, read off the row.
    names = set(rows[0].keys()) if rows else set()
    counted = "asset_count" if "asset_count" in names else "item_count"
    return sorted((str(row["name"]), int(row[counted])) for row in rows)


#: One person, as a filter: the leaf the search box builds from `people:`.
ONE_PERSON = AssetFilter(where=Where("people", ("p1",)))
BOTH = AssetFilter(where=Where("tags", ("t1",)))
ONLY_FIRST = AssetFilter(where=Where("tags", ("t2",)))


async def test_every_entity_statement_parses_against_the_real_schema(db: Database) -> None:
    """Every assembled entity statement runs against the real schema: a `no such column` would look
    like a filter that matched nothing."""
    for builder in (
        ent.tags_query,
        ent.people_query,
        ent.collections_query,
        ent.sites_query,
        ent.photo_sets_query,
    ):
        await wall(db, builder)


async def test_the_unfiltered_tag_wall_has_both_tags_and_the_libraries_counts(db: Database) -> None:
    assert await wall(db, ent.tags_query) == [("both", 2), ("only-first", 1)]


async def test_tags_narrowed_to_one_persons_files_drop_the_tag_they_do_not_carry(
    db: Database,
) -> None:
    """A person's page lists the tags on their files: `only-first` goes, `both` stays.

    `both` reads TWO, its size in the library: membership is filtered, and the card says how big
    the thing itself is. Permission still applies to both.
    """
    assert await wall(db, ent.tags_query, ONE_PERSON) == [("both", 2)]


async def test_people_narrowed_to_a_tag_answer_who_is_on_the_files_carrying_it(
    db: Database,
) -> None:
    """The same question from the other end."""
    assert await wall(db, ent.people_query) == [("somebody", 1)]
    assert await wall(db, ent.people_query, BOTH) == [("somebody", 1)]
    assert await wall(db, ent.people_query, ONLY_FIRST) == []


async def test_photo_sets_narrowed_to_a_person_who_is_in_none_of_them(db: Database) -> None:
    """Nothing, which means something only beside the unfiltered line."""
    assert await wall(db, ent.photo_sets_query) == [("Shoot", 1)]
    assert await wall(db, ent.photo_sets_query, ONE_PERSON) == []


async def test_a_filter_naming_nothing_matches_nothing_rather_than_widening(db: Database) -> None:
    """An unresolvable name filters to nothing, never to everything: a name the viewer may not be
    told about resolves to an empty group for them."""
    nobody = AssetFilter(where=Where("people", ("no-such-person",)))
    assert await wall(db, ent.tags_query, nobody) == []


async def test_two_filters_both_apply(db: Database) -> None:
    """Two filters both apply: each alone matches something, together nothing."""
    from sift.kernel.access.constraints import AllOf

    both_ways = AssetFilter(where=AllOf((Where("people", ("p1",)), Where("tags", ("t2",)))))
    assert await wall(db, ent.tags_query, ONE_PERSON) != []
    assert await wall(db, ent.tags_query, ONLY_FIRST) != []
    assert await wall(db, ent.tags_query, both_ways) == []


async def test_every_related_leaf_reaches_sqlite_rather_than_only_python(db: Database) -> None:
    """Every `related_filter` leaf EXECUTES against a real statement: one bound as a bare string
    where the predicate reads `json_each` would be a 500. Whether each filters right is above."""
    for name, value in (
        ("person", "p1"),
        ("tag", "t1"),
        ("site", "l1"),
        ("collection", "c1"),
        ("photo_set", "s1"),
    ):
        narrowing = related_filter(**{name: value})
        assert narrowing is not NO_FILTER, f"{name} named nothing, so nothing was tested"
        for builder in (
            ent.tags_query,
            ent.people_query,
            ent.collections_query,
            ent.sites_query,
            ent.photo_sets_query,
            ent.loops_query,
        ):
            await wall(db, builder, narrowing)


def test_a_parameter_no_related_list_has_is_refused_rather_than_ignored() -> None:
    """A name this does not know is refused: ignored, the wall would hold the whole library."""
    with pytest.raises(ValueError, match="not a related-list parameter"):
        related_filter(sideways="p1")


def test_naming_nothing_is_the_same_no_filter_the_walls_check_for() -> None:
    """Naming nothing returns `NO_FILTER` itself: the walls compare identity, and an equal empty
    filter would hide every empty tag off the Tags screen."""
    assert related_filter(person=None, tag=None) is NO_FILTER


def test_two_things_together_both_narrow() -> None:
    """Two related names together both narrow."""
    both = related_filter(person="p1", site="l1")
    assert both is not NO_FILTER
    where, bound = both.predicate()
    assert list(bound) != []
    assert "json_each" in where


# --- the store's own photo-set and Loop reads, through the repository every route calls


@pytest.fixture
def access(db: Database, tmp_path: Path) -> Repository:
    """The repository over the same fixture; nothing here asks the content store for a path."""
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    return Repository(db, ContentStore(db, settings))


ADMIN = Viewer(id="u1", role=Role.ADMIN)


async def test_the_photo_set_wall_carries_the_scoped_total_beside_its_page(
    access: Repository,
) -> None:
    """The total rides on the row through a window function, from the same question as the rows."""
    page = await access.list_photo_sets(ADMIN, limit=10)

    assert [one.name for one in page.items] == ["Shoot"]
    assert page.total == 1
    assert page.items[0].item_count == 1


async def test_one_photo_set_by_id_says_the_same_as_the_wall_does(access: Repository) -> None:
    """The listing and the by-id lookup share one mapper."""
    one = await access.visible_photo_set(ADMIN, "s1")

    assert one is not None
    assert (one.id, one.name, one.origin, one.item_count) == ("s1", "Shoot", "folder", 1)


async def test_a_photo_set_id_naming_nothing_is_answered_with_nothing(
    access: Repository,
) -> None:
    """From outside, "no such set" and "not for you" are one answer."""
    assert await access.visible_photo_set(ADMIN, "01JZZZZZZZZZZZZZZZZZZZZZZZ") is None


async def test_a_page_of_photo_sets_cannot_be_empty_or_start_before_the_first_row(
    access: Repository,
) -> None:
    """A zero limit and a negative offset are refused, not answered with another page."""
    with pytest.raises(ValueError, match="at least one row"):
        await access.list_photo_sets(ADMIN, limit=0)
    with pytest.raises(ValueError, match="before the first row"):
        await access.list_photo_sets(ADMIN, offset=-1)


async def test_the_photo_set_wall_narrows_to_one_person_like_every_other_wall(
    access: Repository,
) -> None:
    """The related list through the repository: the person's files hold no set."""
    page = await access.list_photo_sets(ADMIN, asset_filter=related_filter(person="p1"))

    assert page.items == []
    assert page.total == 0


async def test_the_loop_wall_reads_its_page_and_a_mark_knows_how_long_it_runs(
    access: Repository, db: Database
) -> None:
    """A Loop's length is derived from its two ends, not stored as a third column to disagree."""
    async with db.write() as c:
        await c.execute(
            "INSERT INTO loops (id, asset_id, start_ms, end_ms, name, created_by, created_at)"
            " VALUES ('o1', 'a1', 1000, 5500, 'a bit', 'u1', ?)",
            (_EPOCH,),
        )

    page = await access.list_loops(ADMIN, limit=10)

    assert [one.id for one in page.items] == ["o1"]
    assert page.total == 1
    assert page.items[0].length_ms == 4500


async def test_a_mark_carries_the_tags_put_on_the_mark_itself(
    access: Repository, db: Database
) -> None:
    """A Loop's own tags are drawn, unpacked field by field from one column, so a separator read as
    part of a name would show."""
    async with db.write() as c:
        await c.execute(
            "INSERT INTO loops (id, asset_id, start_ms, end_ms, name, created_by, created_at)"
            " VALUES ('o1', 'a1', 0, 1000, 'a bit', 'u1', ?), "
            "        ('o2', 'a1', 2000, 3000, 'another', 'u1', ?)",
            (_EPOCH, _EPOCH),
        )
        await c.execute("INSERT INTO loop_tags (loop_id, tag_id) VALUES ('o1', 't1')")

    marks = {one.id: one for one in (await access.list_loops(ADMIN, limit=10)).items}

    assert [(tag.id, tag.name) for tag in marks["o1"].own_tags] == [("t1", "both")]
    assert marks["o2"].own_tags == (), (
        "a mark nobody tagged carries nothing, not an empty-named tag"
    )


async def test_the_loop_wall_narrows_to_one_file(access: Repository, db: Database) -> None:
    """The wall of Loops narrows to one file; an id naming no file matches nothing."""
    async with db.write() as c:
        await c.execute(
            "INSERT INTO loops (id, asset_id, start_ms, end_ms, name, created_by, created_at)"
            " VALUES ('o1', 'a1', 0, 1000, NULL, 'u1', ?), "
            "        ('o2', 'a2', 0, 1000, NULL, 'u1', ?)",
            (_EPOCH, _EPOCH),
        )

    assert [one.id for one in (await access.list_loops(ADMIN, asset_id="a1")).items] == ["o1"]
    assert (await access.list_loops(ADMIN, asset_id="nobody")).items == []


async def test_one_loop_by_id_and_one_that_was_never_minted(
    access: Repository, db: Database
) -> None:
    async with db.write() as c:
        await c.execute(
            "INSERT INTO loops (id, asset_id, start_ms, end_ms, name, created_by, created_at)"
            " VALUES ('o1', 'a1', 250, 1250, 'a bit', 'u1', ?)",
            (_EPOCH,),
        )

    one = await access.visible_loop(ADMIN, "o1")
    assert one is not None
    assert (one.asset_id, one.name, one.length_ms) == ("a1", "a bit", 1000)
    assert await access.visible_loop(ADMIN, "01JZZZZZZZZZZZZZZZZZZZZZZZ") is None


async def test_a_page_of_loops_cannot_be_empty_or_start_before_the_first_row(
    access: Repository,
) -> None:
    with pytest.raises(ValueError, match="at least one row"):
        await access.list_loops(ADMIN, limit=0)
    with pytest.raises(ValueError, match="before the first row"):
        await access.list_loops(ADMIN, offset=-1)


# --- the usernames wall


async def test_a_page_of_usernames_cannot_be_empty_or_start_before_the_first_row(
    access: Repository,
) -> None:
    """A zero limit and a negative offset are refused."""
    with pytest.raises(ValueError, match="at least one row"):
        await access.list_usernames(ADMIN, limit=0)
    with pytest.raises(ValueError, match="before the first row"):
        await access.list_usernames(ADMIN, offset=-1)


async def test_one_username_by_id_and_one_that_was_never_minted(
    access: Repository, db: Database
) -> None:
    """A username by id comes from the LIST's statement, filtered to one id, so the two agree."""
    # A real-shaped id: a by-id read checks the shape first.
    username_id = new_id()
    async with db.write() as c:
        await c.execute("INSERT INTO sites (id, name) VALUES ('s1', 'SomeSite')")
        await c.execute(
            "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, ?)",
            (username_id, "s1", "esmewrenfield", _EPOCH),
        )
        await c.execute(
            "INSERT INTO asset_usernames (asset_id, username_id) VALUES ('a1', ?)", (username_id,)
        )

    one = await access.visible_username(ADMIN, username_id)
    assert one is not None
    assert (one.name, one.site_name) == ("esmewrenfield", "SomeSite")
    assert await access.visible_username(ADMIN, "01JZZZZZZZZZZZZZZZZZZZZZZZ") is None


async def test_a_made_up_username_id_is_refused_before_the_statement_runs(
    access: Repository,
) -> None:
    """An id that could never have been minted is turned away before the statement, which reads
    NULL as "no filter"."""
    assert await access.visible_username(ADMIN, "not-an-id-at-all") is None


# --- a Loop that IS its file
#
# `LoopView.whole` decides how a row is drawn: a Loop spanning its whole file opens plain, as it
# would from Browse, not with the A-B pair armed. Tested here, where the code lives.


def _loop(**overrides: object) -> LoopView:
    fields: dict[str, object] = {
        "id": "l1",
        "asset_id": "a1",
        "name": None,
        "start_ms": 0,
        "end_ms": 10_000,
        "created_at": _EPOCH,
        "created_by": None,
        "media_type": "video",
        "duration_ms": 10_000,
    }
    fields.update(overrides)
    return LoopView(**fields)  # type: ignore[arg-type]


def test_a_mark_covering_the_whole_file_is_the_file() -> None:
    assert _loop().whole is True


def test_a_stretch_of_a_longer_video_is_not() -> None:
    assert _loop(start_ms=1_807, end_ms=3_538, duration_ms=900_000).whole is False


@pytest.mark.parametrize("start,end", [(250, 10_000), (0, 9_750), (250, 9_750)])
def test_the_ends_are_allowed_to_miss_by_a_quarter_of_a_second(start: int, end: int) -> None:
    """The ends may miss by a quarter of a second: the end is what the cut asked for, the duration
    what ffprobe measured after."""
    assert _loop(start_ms=start, end_ms=end).whole is True


@pytest.mark.parametrize("start,end", [(251, 10_000), (0, 9_749)])
def test_and_no_further_than_that(start: int, end: int) -> None:
    """And no further, from both ends."""
    assert _loop(start_ms=start, end_ms=end).whole is False


@pytest.mark.parametrize("duration", [None, 0])
def test_a_video_nobody_has_measured_reads_as_NOT_whole(duration: int | None) -> None:
    """A video never probed reads as NOT whole, or the marked piece would stop being drawn."""
    assert _loop(duration_ms=duration).whole is False


async def test_the_usernames_wall_counts_over_only_the_files_a_filter_reaches(
    access: Repository, db: Database
) -> None:
    """Plain, the username wall reads stored counts; filtered, it counts the files the filter
    reaches. The username on the first file alone is absent from the second file's person's wall."""
    username_id = new_id()
    async with db.write() as c:
        await c.execute("INSERT INTO sites (id, name) VALUES ('s1', 'SomeSite')")
        await c.execute(
            "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, ?)",
            (username_id, "s1", "esmewrenfield", _EPOCH),
        )
        await c.execute(
            "INSERT INTO asset_usernames (asset_id, username_id) VALUES ('a1', ?)", (username_id,)
        )

    def names(page: UsernamePage) -> list[tuple[str, int]]:
        return [(one.name, one.asset_count) for one in page.items]

    assert names(await access.list_usernames(ADMIN)) == [("esmewrenfield", 1)]
    assert names(await access.list_usernames(ADMIN, asset_filter=BOTH)) == [("esmewrenfield", 1)]
    assert names(await access.list_usernames(ADMIN, asset_filter=ONLY_FIRST)) == [
        ("esmewrenfield", 1)
    ]
    assert names(await access.list_usernames(ADMIN, asset_filter=ONE_PERSON)) == []
