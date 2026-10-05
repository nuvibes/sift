# SPDX-License-Identifier: AGPL-3.0-or-later
"""The walls of things filtered on their own rows, the O tally, the reads the shoots and
suggestions passes are built on, and the by-id reads.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

# Imported for their tables: the unnamed-face condition, `same_music`, the `enriched:` predicates
# and the ledger every concealment writes all read one, and the application always has them.
import sift.slices.faces.schema
import sift.slices.music.schema
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import (
    AssetFilter,
    ConstraintError,
    EntityNarrowing,
    Repository,
    Viewer,
    Where,
    asks_disagreements,
    attribute_to_person,
    ensure_site,
    seed_site_username,
)
from sift.kernel.access.catalog import (
    _NAMELESS_USERNAME_FILENAMES,
    MADE_BY_A_PERSON,
    by_sift,
    by_user,
    create_person_on,
    creators_with_loose_pictures,
    file_assets_under_site_on,
    filenames_for_nameless_usernames,
    loose_pictures_of,
    photo_sets_holding,
    unnamed_pictures_among,
)
from sift.kernel.access.constraints import (
    DISAGREEING,
    DISAGREES,
    ENTITY_FACETS,
    Not,
)
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.sorting import sort_key
from sift.kernel.tests.access_helpers import _EPOCH, _a_file_called, _migrated
from sift.kernel.vocabulary import VIA_DOWNLOAD, VIA_FILENAME, VIA_FOLDER, VIA_WATERMARK
from sift.testing.fixtures import Actors, World, hide

# --- the walls of THINGS, filtered on their own rows, and counted along one dimension


async def _person_row(database: Database, name: str, **columns: object) -> str:
    """Somebody with a record, with the columns a stash-box fills in."""
    person_id = new_id()
    await database.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
        (person_id, name, sort_key(name), 1_700_000_000),
    )
    for column, value in columns.items():
        # One statement per column: no SQL is built from a name, here as in the source.
        statements = {
            "hair_color": "UPDATE people SET hair_color = ? WHERE id = ?",
            "country": "UPDATE people SET country = ? WHERE id = ?",
            "birth_date": "UPDATE people SET birth_date = ? WHERE id = ?",
            "height_cm": "UPDATE people SET height_cm = ? WHERE id = ?",
        }
        await database.execute(statements[column], (value, person_id))
    return person_id


async def test_entity_facets_count_the_noun_and_not_its_files(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    """A facet on a wall of people counts PEOPLE: two blonde people on three files reads 2. The
    stash-box's upper case and a typed lower case are one value."""
    first = await _person_row(temp_db, "Neve Arbor", hair_color="BLONDE")
    second = await _person_row(temp_db, "Wren Halloway", hair_color="blonde")
    third = await _person_row(temp_db, "Cass Ivory", hair_color="RED")
    for person in (first, second, third):
        await temp_db.execute(
            "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (world.solo, person)
        )
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (world.twin, first)
    )

    counted = await access.people_facets(actors.admin, "hair_color")

    assert [(one.value, one.count) for one in counted] == [("BLONDE", 2), ("RED", 1)]
    # A word is readable, so no label is sent.
    assert all(one.label is None for one in counted)


async def test_an_entity_facet_is_counted_over_only_the_files_a_filter_reaches(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    """The panel describes the wall as looked at: a file filter is spliced in at the listing's seam,
    and with none the statement is used whole."""
    on_the_tagged_file = await _person_row(temp_db, "Neve Arbor", hair_color="BLONDE")
    elsewhere = await _person_row(temp_db, "Wren Halloway", hair_color="RED")
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)",
        (world.solo, on_the_tagged_file),
    )
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (world.twin, elsewhere)
    )

    whole = await access.people_facets(actors.admin, "hair_color")
    assert sorted((one.value, one.count) for one in whole) == [("BLONDE", 1), ("RED", 1)]

    # Only `world.solo` carries the tag, so the person on `world.twin` is not counted.
    narrowed = await access.people_facets(
        actors.admin, "hair_color", asset_filter=AssetFilter(where=Where("tags", (world.tag,)))
    )
    assert [(one.value, one.count) for one in narrowed] == [("BLONDE", 1)]


async def test_entity_facets_leave_out_a_person_the_vault_is_holding(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    """A concealed person is not counted: the count comes from the statement that decides the
    wall."""
    await _person_row(temp_db, "Neve Arbor", hair_color="RED")
    hidden = await _person_row(temp_db, "Wren Halloway", hair_color="RED")
    await hide(temp_db, "person", hidden, actors.admin.id)

    counted = await access.people_facets(actors.admin, "hair_color")

    assert [(one.value, one.count) for one in counted] == [("RED", 1)]


async def test_entity_narrowing_ands_two_keys_and_ors_a_repeated_one(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    """A repeated key is OR within a facet; two keys are AND across them."""
    both = await _person_row(temp_db, "Neve Arbor", hair_color="BLONDE", country="US")
    other_hair = await _person_row(temp_db, "Wren Halloway", hair_color="RED", country="US")
    other_country = await _person_row(temp_db, "Cass Ivory", hair_color="BLONDE", country="DE")
    await _person_row(temp_db, "Juno Marsh", hair_color="BLACK", country="US")

    narrowing = EntityNarrowing.of(
        "person", {"hair_color": ["BLONDE", "RED"], "country": ["us"]}, is_admin=True
    )
    page = await access.suggest_people(actors.admin, narrowing=narrowing)

    assert {one.id for one in page.items} == {both, other_hair}
    assert other_country not in {one.id for one in page.items}
    assert page.total == 2

    # The facet counts are of the same filtered set.
    counted = await access.people_facets(actors.admin, "country", narrowing=narrowing)
    assert [(one.value, one.count) for one in counted] == [("US", 2)]


async def test_the_one_facet_whose_values_are_handed_in_narrows_and_counts_the_same_set(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    """Stash-box disagreements, bound in from outside the statement, behave like any column: the
    row counting N selects those N."""
    disagreeing = await _person_row(temp_db, "Neve Arbor", hair_color="RED")
    await _person_row(temp_db, "Wren Halloway", hair_color="RED")
    await _person_row(temp_db, "Cass Ivory", hair_color="BLONDE")

    narrowing = EntityNarrowing.of(
        "person", {"disagrees": ["yes"]}, is_admin=True, disagreeing=[disagreeing]
    )
    page = await access.suggest_people(actors.admin, narrowing=narrowing)
    assert {one.id for one in page.items} == {disagreeing}

    counted = await access.people_facets(
        actors.admin,
        "disagrees",
        narrowing=EntityNarrowing.of("person", {}, is_admin=True, disagreeing=[disagreeing]),
    )
    # "no" is everybody else this viewer may see, read off the wall.
    everyone = await access.suggest_people(
        actors.admin,
        narrowing=EntityNarrowing.of("person", {}, is_admin=True, disagreeing=[disagreeing]),
    )
    assert sorted((one.value, one.count) for one in counted) == [
        ("no", everyone.total - 1),
        ("yes", 1),
    ]


async def test_a_wall_with_nothing_disagreeing_counts_everybody_under_the_other_row(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    """An EMPTY list of disagreeing ids is a real answer: the second row holds the whole wall."""
    await _person_row(temp_db, "Neve Arbor")
    await _person_row(temp_db, "Wren Halloway")

    narrowing = EntityNarrowing.of("person", {}, is_admin=True, disagreeing=[])
    counted = await access.people_facets(actors.admin, "disagrees", narrowing=narrowing)
    whole = await access.suggest_people(actors.admin, narrowing=narrowing)

    assert [(one.value, one.count) for one in counted] == [("no", whole.total)]


def test_the_ids_are_resolved_for_a_pick_or_for_a_count_and_for_nothing_else() -> None:
    """The disagreeing ids are resolved only for a pick or a count of that column: every other
    page of every wall must not pay for one enrichment plan per link."""
    assert asks_disagreements({"disagrees": ["yes"]}) is True
    assert asks_disagreements({}, "disagrees") is True

    assert asks_disagreements({}) is False
    assert asks_disagreements({"linked": ["yes"]}, "linked") is False
    # A key sent with nothing in it filters nothing.
    assert asks_disagreements({"disagrees": []}) is False


def test_a_disagreement_pick_without_the_ids_is_refused_rather_than_answered() -> None:
    """A disagreement pick without the ids is refused: bound empty it would blank the wall."""
    with pytest.raises(ConstraintError):
        EntityNarrowing.of("person", {"disagrees": ["yes"]}, is_admin=True)

    refused = EntityNarrowing.of("person", {"disagrees": ["yes"]}, is_admin=False)
    assert refused.predicate()[0] == "0"


def test_the_disagreeing_ids_are_bound_even_where_nothing_is_picked() -> None:
    """The disagreeing ids are bound even unpicked: the facet count reuses the wall's statement."""
    where, bound = EntityNarrowing.of("person", {}, is_admin=True, disagreeing=["p1"]).predicate()

    assert where == "1"
    assert bound["disagreeing"] == '["p1"]'


async def test_entity_ages_sit_where_their_birthdays_say(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    """Somebody born twenty-five years ago today is 25 and a day later 24; the boundary dates come
    from SQLite, so the clock cannot drift the assertion."""
    row = await temp_db.fetch_one(
        "SELECT date('now', '-25 years') AS exact,"
        " date('now', '-25 years', '+1 day') AS younger,"
        " date('now', '-18 years', '+1 day') AS child,"
        " date('now', '-50 years') AS oldest"
    )
    assert row is not None
    await _person_row(temp_db, "Neve Arbor", birth_date=str(row["exact"]))
    await _person_row(temp_db, "Wren Halloway", birth_date=str(row["younger"]))
    await _person_row(temp_db, "Cass Ivory", birth_date=str(row["oldest"]))
    # Too young for an adult library, and not a date: both absent.
    await _person_row(temp_db, "Juno Marsh", birth_date=str(row["child"]))
    await _person_row(temp_db, "Ash Ellery", birth_date="1990-00-00")

    counted = await access.people_facets(actors.admin, "age")

    assert sorted((one.value, one.count) for one in counted) == [
        ("24", 1),
        ("25", 1),
        ("50", 1),
    ]


async def test_entity_height_bands_sit_where_their_edges_say(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    """The two open ends are named and the ten-centimetre bands are closed at both edges."""
    await _person_row(temp_db, "Neve Arbor", height_cm=149)
    await _person_row(temp_db, "Wren Halloway", height_cm=150)
    await _person_row(temp_db, "Cass Ivory", height_cm=159)
    await _person_row(temp_db, "Juno Marsh", height_cm=199)
    await _person_row(temp_db, "Ash Ellery", height_cm=200)
    # Nobody's height.
    await _person_row(temp_db, "Robin Vale", height_cm=0)

    counted = await access.people_facets(actors.admin, "height_cm")

    assert sorted((one.value, one.count) for one in counted) == [
        ("150-159", 2),
        ("190-199", 1),
        ("200+", 1),
        ("<150", 1),
    ]

    # Picking the band returns exactly what it counted.
    narrowed = EntityNarrowing.of("person", {"height_cm": ["150-159"]}, is_admin=True)
    page = await access.suggest_people(actors.admin, narrowing=narrowed)
    assert page.total == 2


async def test_entity_facets_answer_for_every_wall_that_has_them(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    """One dimension of each of the other four walls PARSES and counts its own noun."""
    await temp_db.execute("UPDATE tags SET category = 'PLACE' WHERE id = ?", (world.tag,))
    await temp_db.execute(
        "UPDATE collections SET owner_id = ? WHERE id = ?", (actors.admin.id, world.collection)
    )

    tags = await access.tag_facets(actors.admin, "category")
    assert [(one.value, one.count) for one in tags] == [("PLACE", 1)]

    sites = await access.site_facets(actors.admin, "usernames")
    assert [(one.value, one.count) for one in sites] == [("yes", 1)]

    collections = await access.collection_facets(actors.admin, "mine")
    assert [(one.value, one.count) for one in collections] == [("yes", 1)]

    # Nothing shared: the sharing column is empty, never hidden.
    assert await access.photo_set_facets(actors.admin, "sharing") == []


async def test_every_wall_counts_every_facet_it_declares(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    """Every column a wall of things declares parses and counts its own noun; `disagrees` is bound
    by the route."""
    readers = {
        "person": access.people_facets,
        "site": access.site_facets,
        "tag": access.tag_facets,
        "collection": access.collection_facets,
        "photo_set": access.photo_set_facets,
        "song": access.song_facets,
    }
    for subject, facets in ENTITY_FACETS.items():
        for key in facets:
            if key != DISAGREES:
                await readers[subject](actors.admin, key)

    await temp_db.execute(
        "UPDATE collections SET created_by_kind = 'user', created_by_user_id = ? WHERE id = ?",
        (actors.admin.id, world.collection),
    )
    await temp_db.execute(
        "UPDATE photo_sets SET created_by_kind = 'sift', created_by_via = 'folder' WHERE id = ?",
        (world.photo_set,),
    )
    made = await access.collection_facets(actors.admin, "created")
    assert [(one.value, one.count) for one in made] == [("me", 1)]
    made = await access.photo_set_facets(actors.admin, "created")
    assert [(one.value, one.count) for one in made] == [("folder", 1)]
    covered = await access.tag_facets(actors.admin, "cover")
    assert {one.value for one in covered} <= {"yes", "no"}


async def test_an_age_span_kept_in_an_address_still_narrows_the_people_wall(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    """Ages are listed a year at a time; an address kept under older spans (`25-29`, `50+`) still
    narrows to those people."""
    row = await temp_db.fetch_one("SELECT date('now', '-25 years') AS exact")
    assert row is not None
    await _person_row(temp_db, "Neve Arbor", birth_date=str(row["exact"]))

    async def counted(*picks: str) -> int:
        narrowing = EntityNarrowing.of("person", {"age": list(picks)})
        return sum(
            one.count
            for one in await access.people_facets(actors.admin, "cover", narrowing=narrowing)
        )

    assert await counted("25") == 1
    assert await counted("25-29") == 1
    assert await counted("24-24") == 0
    assert await counted("50+") == 0
    assert await counted("20+") == 1


async def test_entity_facets_label_a_value_that_is_an_id(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    """A tag on a person is counted by ID and labelled by NAME: two tags can share a spelling."""
    person = await _person_row(temp_db, "Neve Arbor")
    await temp_db.execute(
        "INSERT INTO person_tags (person_id, tag_id) VALUES (?, ?)", (person, world.tag)
    )

    counted = await access.people_facets(actors.admin, "tags")

    named = [one for one in counted if one.value not in ("any", "none")]
    assert [(one.value, one.label, one.count) for one in named] == [(world.tag, "tag", 1)]


async def test_the_created_by_facet_counts_the_box_that_made_a_row_and_narrows_to_it(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    """WHO MADE a row, counted under the box's own WORD, so the filter means the same on every
    install. A box with no word, or a row made before makers were recorded, is no value."""
    made = await _person_row(temp_db, "Neve Arbor")
    by_hand = await _person_row(temp_db, "Wren Halloway")
    from_nowhere = await _person_row(temp_db, "Marlow Quill")
    for box, name, slug in (
        ("box-known", "A Box", "fansdb"),
        ("box-unknown", "Somebody's Own", None),
    ):
        await temp_db.execute(
            "INSERT INTO stash_boxes (id, name, endpoint, slug, created_at) VALUES (?, ?, ?, ?, 0)",
            (box, name, f"https://{box}.test/graphql", slug),
        )
    await temp_db.execute(
        "UPDATE people SET created_by_box_id = ? WHERE id = ?", ("box-known", made)
    )
    await temp_db.execute(
        "UPDATE people SET created_by_box_id = ? WHERE id = ?", ("box-unknown", from_nowhere)
    )

    counted = await access.people_facets(actors.admin, "created")

    assert [(one.value, one.count) for one in counted] == [("fansdb", 1)]

    narrowed = EntityNarrowing.of("person", {"created": ["fansdb"]})
    page = await access.suggest_people(actors.admin, narrowing=narrowed)
    assert {one.id for one in page.items} == {made}
    assert by_hand not in {one.id for one in page.items}
    assert from_nowhere not in {one.id for one in page.items}


async def test_created_by_counts_sift_and_the_asking_user_beside_the_boxes(
    temp_db: Database,
    access: Repository,
    actors: Actors,
) -> None:
    """A Sift pass and a person typing are makers of their own; "Me" is the row made by the user
    ASKING, worked out per user, and another user's rows are no value (that would publish who made
    what)."""
    by_sift = await _person_row(temp_db, "Pell Quorley")
    by_me = await _person_row(temp_db, "Sibyl Marrow")
    by_someone_else = await _person_row(temp_db, "Thessaly Rook")
    await temp_db.execute("UPDATE people SET created_by_kind = 'sift' WHERE id = ?", (by_sift,))
    await temp_db.execute(
        "UPDATE people SET created_by_kind = 'user', created_by_user_id = ? WHERE id = ?",
        (actors.admin.id, by_me),
    )
    await temp_db.execute(
        "UPDATE people SET created_by_kind = 'user', created_by_user_id = 'somebody-else'"
        " WHERE id = ?",
        (by_someone_else,),
    )

    counted = await access.people_facets(actors.admin, "created")

    assert dict((one.value, one.count) for one in counted) == {"sift": 1, "me": 1}

    for word, expected in (("sift", by_sift), ("me", by_me)):
        narrowed = EntityNarrowing.of("person", {"created": [word]})
        page = await access.suggest_people(actors.admin, narrowing=narrowed)
        assert {one.id for one in page.items} == {expected}


async def test_every_creating_write_records_the_pass_that_made_the_row(
    temp_db: Database,
    access: Repository,
) -> None:
    """A site, a person and a filing each say WHICH pass invented them, and the upsert's first
    answer
    stands: `DO NOTHING` keeps a site a person added theirs."""
    made = await ensure_site(temp_db, "Larkspur", made=by_sift(VIA_DOWNLOAD))
    row = await temp_db.fetch_one(
        "SELECT created_by_kind, created_by_via FROM sites WHERE id = ?", (made,)
    )
    assert row is not None
    assert (row["created_by_kind"], row["created_by_via"]) == ("sift", "download")

    # The same name from a person: the row keeps its answer.
    again = await ensure_site(temp_db, "larkspur", made=MADE_BY_A_PERSON)
    assert again == made
    row = await temp_db.fetch_one(
        "SELECT created_by_kind, created_by_via FROM sites WHERE id = ?", (made,)
    )
    assert row is not None
    assert (row["created_by_kind"], row["created_by_via"]) == ("sift", "download")

    # A typed site says 'user'.
    typed = await ensure_site(temp_db, "Quillon", made=by_user("admin-1"))
    row = await temp_db.fetch_one(
        "SELECT created_by_kind, created_by_via, created_by_user_id FROM sites WHERE id = ?",
        (typed,),
    )
    assert row is not None
    assert (row["created_by_kind"], row["created_by_via"]) == ("user", None)
    assert row["created_by_user_id"] == "admin-1"

    # The username upsert hands the same maker to the site.
    site, _username = await seed_site_username(
        temp_db, site="Verrow", name="nerith", made=by_sift(VIA_FILENAME)
    )
    row = await temp_db.fetch_one("SELECT created_by_via FROM sites WHERE id = ?", (site,))
    assert row is not None
    assert row["created_by_via"] == "filename"

    # The filing that invents a site asks for the maker separately (`file_assets_under_site_on`).
    async with temp_db.write() as connection:
        filed = await file_assets_under_site_on(
            connection,
            asset_ids=[],
            site="Thessaly Street",
            source="watermark",
            made=by_sift(VIA_WATERMARK),
        )
    row = await temp_db.fetch_one(
        "SELECT created_by_kind, created_by_via FROM sites WHERE id = ?", (filed,)
    )
    assert row is not None
    assert (row["created_by_kind"], row["created_by_via"]) == ("sift", "watermark")


async def test_a_person_invented_from_a_username_or_a_folder_says_which(
    temp_db: Database,
    access: Repository,
) -> None:
    """A person invented by a download and one from a folder name each say which road made them."""
    async with temp_db.write() as connection:
        from_a_folder = await create_person_on(
            connection, "Nuvella Brink", made=by_sift(VIA_FOLDER)
        )
    assert from_a_folder is not None
    row = await temp_db.fetch_one(
        "SELECT name, created_by_kind, created_by_via FROM people WHERE id = ?", (from_a_folder,)
    )
    assert row is not None
    assert (row["name"], row["created_by_kind"], row["created_by_via"]) == (
        "Nuvella Brink",
        "sift",
        "folder",
    )

    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, mime, size_bytes, added_at)"
        " VALUES ('asset-made', 'identity-made', 'video', 'video/mp4', 1, 0)"
    )
    who = await attribute_to_person(
        temp_db,
        asset_id="asset-made",
        name="wrennasable",
        create_if_unknown=True,
        made=by_sift(VIA_DOWNLOAD),
    )
    assert who is not None
    row = await temp_db.fetch_one(
        "SELECT created_by_kind, created_by_via FROM people WHERE id = ?", (who,)
    )
    assert row is not None
    assert (row["created_by_kind"], row["created_by_via"]) == ("sift", "download")


async def test_entity_narrowing_refuses_what_no_wall_declares() -> None:
    """A key no wall declares is refused: the route and the registry have come apart."""
    with pytest.raises(ConstraintError):
        EntityNarrowing.of("person", {"favourite_colour": ["blue"]})
    with pytest.raises(ConstraintError):
        EntityNarrowing.of("sandwich", {})


def _without_ids(predicate: tuple[str, dict[str, object]]) -> tuple[str, dict[str, object]]:
    """A predicate minus the disagreeing ids every wall statement binds."""
    statement, bound = predicate
    return statement, {key: value for key, value in bound.items() if key != DISAGREEING}


async def test_entity_narrowing_gives_an_admin_only_facet_to_nobody_else() -> None:
    """`sharing` asked by a non-admin matches NOTHING, never the whole wall."""
    refused = EntityNarrowing.of("collection", {"sharing": ["shared"]}, is_admin=False)
    assert _without_ids(refused.predicate()) == ("0", {})

    allowed = EntityNarrowing.of("collection", {"sharing": ["shared"]}, is_admin=True)
    statement, bound = allowed.predicate()
    assert "acl_grants" in statement
    # The disagreeing ids are always bound, so the picked value is the only OTHER binding.
    assert [value for key, value in bound.items() if key != DISAGREEING] == ['["shared"]']

    # Nothing at all is the whole wall.
    assert _without_ids(EntityNarrowing.of("collection", {}).predicate()) == ("1", {})


# --- the O tally, summed over one subject's files, for each of the subjects


async def _press(temp_db: Database, viewer: Viewer, asset_id: str, times: int) -> None:
    """Somebody's own counter on one file."""
    await temp_db.execute(
        "INSERT INTO asset_user_state (asset_id, user_id, o_count, updated_at)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(asset_id, user_id) DO UPDATE SET o_count = excluded.o_count",
        (asset_id, viewer.id, times, _EPOCH),
    )


async def test_every_entity_sums_this_users_o_tally_over_its_own_files(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Each subject answers what THIS user counted, a real sum: `twin` is in every one."""
    await temp_db.execute(
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (world.twin, world.tag)
    )
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (world.twin, world.person)
    )
    await temp_db.execute(
        "INSERT INTO collection_items (collection_id, asset_id, added_at) VALUES (?, ?, ?)",
        (world.collection, world.twin, _EPOCH),
    )
    await temp_db.execute(
        "INSERT INTO photo_set_items (photo_set_id, asset_id, position, added_at)"
        " VALUES (?, ?, ?, ?)",
        (world.photo_set, world.twin, 1, _EPOCH),
    )
    await temp_db.execute(
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
        (world.twin, world.username),
    )
    await _a_song_carried_by(temp_db, world)
    await _press(temp_db, actors.admin, world.solo, 3)
    await _press(temp_db, actors.admin, world.twin, 4)

    assert await access.o_count_of_person(actors.admin, world.person) == 7
    assert await access.o_count_of_site(actors.admin, world.site) == 7
    assert await access.o_count_of_tag(actors.admin, world.tag) == 7
    assert await access.o_count_of_collection(actors.admin, world.collection) == 7
    assert await access.o_count_of_photo_set(actors.admin, world.photo_set) == 7
    assert await access.o_count_of_song(actors.admin, world.song) == 7
    assert await access.o_count_of_song(actors.guest, world.song) == 0


async def _a_song_carried_by(temp_db: Database, world: World) -> None:
    """The world's song, heard in `solo` and `twin`."""
    await temp_db.execute(
        "INSERT INTO songs (id, name, name_sort, created_at) VALUES (?, 'tune', 'tune', 0)",
        (world.song,),
    )
    for asset_id in (world.solo, world.twin):
        await temp_db.execute(
            "INSERT INTO song_files (asset_id, song_id) VALUES (?, ?)", (asset_id, world.song)
        )


async def test_an_o_tally_is_this_users_own_and_reaches_no_file_it_may_not_see(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The ROWS summed are this viewer's and the FILES counted are those they may see."""
    await _press(temp_db, actors.admin, world.solo, 5)

    assert await access.o_count_of_tag(actors.admin, world.tag) == 5
    # A guest was shown nothing, so nought.
    assert await access.o_count_of_person(actors.guest, world.person) == 0
    assert await access.o_count_of_site(actors.guest, world.site) == 0
    assert await access.o_count_of_tag(actors.guest, world.tag) == 0
    assert await access.o_count_of_collection(actors.guest, world.collection) == 0
    assert await access.o_count_of_photo_set(actors.guest, world.photo_set) == 0


async def test_an_o_tally_asked_about_something_that_is_not_an_id_asks_the_database_nothing(
    access: Repository, actors: Actors, world: World
) -> None:
    """A non-id is refused, or NULL would sum the whole library."""
    assert await access.o_count_of_site(actors.admin, "../../etc") == 0
    assert await access.o_count_of_tag(actors.admin, "not-an-id") == 0
    assert await access.o_count_of_collection(actors.admin, "") == 0
    assert await access.o_count_of_photo_set(actors.admin, "not-an-id") == 0


async def test_a_network_sums_the_tally_of_every_label_under_it(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A network's tally rolls up its labels' files, which are never filed under it."""
    network = new_id()
    await temp_db.execute(
        "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
        (network, "Nightjar Media", sort_key("Nightjar Media"), 0),
    )
    await temp_db.execute("UPDATE sites SET parent_id = ? WHERE id = ?", (network, world.site))
    await _press(temp_db, actors.admin, world.solo, 6)

    assert await access.o_count_of_site(actors.admin, world.site) == 6
    assert await access.o_count_of_site(actors.admin, network) == 6


# --- the two reads the shoots and suggestions passes are built on, held by running them


async def _a_picture(temp_db: Database, name: str, *, media_type: str = "image") -> str:
    """One file of the given kind, with nothing said about it."""
    asset_id = new_id()
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, original_filename, added_at)"
        " VALUES (?, ?, ?, ?, 0)",
        (asset_id, new_id(), media_type, name),
    )
    return asset_id


async def test_the_widening_read_answers_the_pictures_that_carry_nobody(temp_db: Database) -> None:
    """The shoots pass's widening read runs: a bare picture is found, one carrying somebody, one in
    a Photo Set and a video are not."""
    await _migrated(temp_db)
    bare = await _a_picture(temp_db, "bare.jpg")
    carried = await _a_picture(temp_db, "carried.jpg")
    filed = await _a_picture(temp_db, "filed.jpg")
    moving = await _a_picture(temp_db, "moving.mp4", media_type="video")

    person = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, 0)",
        (person, "Esme Wrenfield", sort_key("Esme Wrenfield")),
    )
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (carried, person)
    )
    photo_set = new_id()
    await temp_db.execute(
        "INSERT INTO photo_sets (id, name, created_at) VALUES (?, ?, 0)", (photo_set, "A set")
    )
    await temp_db.execute(
        "INSERT INTO photo_set_items (photo_set_id, asset_id, position) VALUES (?, ?, 0)",
        (photo_set, filed),
    )

    found = await unnamed_pictures_among(temp_db, [bare, carried, filed, moving])

    assert found == {bare}
    # Asking about nothing is no crash.
    assert await unnamed_pictures_among(temp_db, []) == set()


async def test_the_photo_sets_holding_each_picture_are_answered_by_picture(
    temp_db: Database,
) -> None:
    """For each picture, the Photo Sets holding it, so a group already a set is not proposed."""
    await _migrated(temp_db)
    twice = await _a_picture(temp_db, "twice.jpg")
    once = await _a_picture(temp_db, "once.jpg")
    loose = await _a_picture(temp_db, "loose.jpg")
    first, second = sorted([new_id(), new_id()])
    for photo_set in (first, second):
        await temp_db.execute(
            "INSERT INTO photo_sets (id, name, created_at) VALUES (?, ?, 0)", (photo_set, "A set")
        )
    for photo_set, asset_id in ((second, twice), (first, twice), (second, once)):
        await temp_db.execute(
            "INSERT INTO photo_set_items (photo_set_id, asset_id, position) VALUES (?, ?, 0)",
            (photo_set, asset_id),
        )

    held = await photo_sets_holding(temp_db, [once, twice, loose, twice])

    # A picture in none is left out; each set is named once, in order.
    assert held == {twice: (first, second), once: (second,)}
    assert await photo_sets_holding(temp_db, []) == {}


async def test_the_username_number_read_matches_any_separator_after_the_name(
    temp_db: Database,
) -> None:
    """The username-number read is a PRE-FILTER, never narrower than the parser behind it: a dash
    is a separator as much as an underscore."""
    await _migrated(temp_db)
    root_id = new_id()
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, 0)",
        (root_id, "Library", "/library"),
    )
    _, username_id = await seed_site_username(
        temp_db, site="Instagram", name="wrenfield", made=MADE_BY_A_PERSON
    )
    for name in (
        "wrenfield_0001.jpg",
        "wrenfield-0002.jpg",
        "WRENFIELD-0003.JPG",
        "wrenfield",
        "wrenfiel-0004.jpg",
        "notwrenfield-0005.jpg",
    ):
        await _a_file_called(temp_db, root_id, name)

    found = await filenames_for_nameless_usernames(temp_db, 50)

    assert sorted(name for _, _, name in found) == [
        "WRENFIELD-0003.JPG",
        "wrenfield-0002.jpg",
        "wrenfield_0001.jpg",
    ]
    assert {got for got, _, _ in found} == {username_id}


async def test_the_folded_filename_index_is_put_back_on_every_boot(temp_db: Database) -> None:
    """The folded-filename index is a schema INVARIANT, put back on every boot: it sits on another
    component's table, whose rebuild drops it while the catalog's version stands."""
    await _migrated(temp_db)
    await temp_db.execute("DROP INDEX ix_loc_filename_folded")
    assert await _the_folded_index(temp_db) is None

    await temp_db.initialize_schema()

    assert await _the_folded_index(temp_db) is not None


async def test_the_username_number_read_is_shaped_for_that_index(temp_db: Database) -> None:
    """The username-number read spells the indexed EXPRESSION and carries no `LIKE` (a pattern built
    from a joined column defeats an index). The plan is not asserted: on six rows a planner rightly
    scans."""
    await _migrated(temp_db)
    index = await _the_folded_index(temp_db)
    assert index is not None

    assert "lower(filename)" in index, index
    assert "lower(l.filename)" in _NAMELESS_USERNAME_FILENAMES
    assert "LIKE" not in _NAMELESS_USERNAME_FILENAMES.upper()


async def _the_folded_index(temp_db: Database) -> str | None:
    """How the folded-filename index is written down, or nothing."""
    row = await temp_db.fetch_one(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
        ("ix_loc_filename_folded",),
    )
    return None if row is None else str(row["sql"])


async def test_the_shoots_pass_reads_a_creators_loose_pictures_in_name_order(
    temp_db: Database,
) -> None:
    """`creators_with_loose_pictures` and `loose_pictures_of` run, in name order: a gallery arrives
    numbered."""
    await _migrated(temp_db)
    person = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, 0)",
        (person, "Esme Wrenfield", sort_key("Esme Wrenfield")),
    )
    pictures = []
    for name in ("sitting-03.jpg", "sitting-01.jpg", "sitting-02.jpg"):
        asset_id = await _a_picture(temp_db, name)
        await temp_db.execute(
            "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (asset_id, person)
        )
        pictures.append((name, asset_id))
    # A video and a picture already in a set are not loose.
    moving = await _a_picture(temp_db, "clip.mp4", media_type="video")
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (moving, person)
    )
    grouped = await _a_picture(temp_db, "sitting-04.jpg")
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (grouped, person)
    )
    photo_set = new_id()
    await temp_db.execute(
        "INSERT INTO photo_sets (id, name, created_at) VALUES (?, ?, 0)", (photo_set, "A set")
    )
    await temp_db.execute(
        "INSERT INTO photo_set_items (photo_set_id, asset_id, position) VALUES (?, ?, 0)",
        (photo_set, grouped),
    )

    creators = await creators_with_loose_pictures(temp_db, least=3)
    assert [(one.person_id, one.name, one.loose) for one in creators] == [
        (person, "Esme Wrenfield", 3)
    ]
    # Nobody once the floor is above what they have.
    assert await creators_with_loose_pictures(temp_db, least=4) == []

    by_name = dict(pictures)
    assert await loose_pictures_of(temp_db, person, limit=10) == [
        by_name["sitting-01.jpg"],
        by_name["sitting-02.jpg"],
        by_name["sitting-03.jpg"],
    ]
    # The cap bounds a pass's time, never correctness.
    assert await loose_pictures_of(temp_db, person, limit=2) == [
        by_name["sitting-01.jpg"],
        by_name["sitting-02.jpg"],
    ]


# --- the by-id reads a page of names is resolved through


async def test_a_page_of_sites_by_id_is_the_wall_row_for_row_network_included(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """`visible_sites` filtered to one network still counts its labels' files."""
    network = new_id()
    await temp_db.execute(
        "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
        (network, "Nightjar Media", sort_key("Nightjar Media"), 0),
    )
    await temp_db.execute("UPDATE sites SET parent_id = ? WHERE id = ?", (network, world.site))
    for viewer in (actors.admin, actors.guest):
        wall = {one.id: one for one in (await access.list_sites(viewer, limit=100)).items}
        both = await access.visible_sites(viewer, [*wall, "not-an-id"])
        assert both == wall
        for site_id, row in wall.items():
            assert await access.visible_sites(viewer, [site_id]) == {site_id: row}
            assert await access.visible_site(viewer, site_id) == row
    label = (await access.visible_sites(actors.admin, [world.site]))[world.site]
    alone = await access.visible_sites(actors.admin, [network])
    assert alone[network].asset_count == label.asset_count > 0
    assert (await access.visible_sites(actors.guest, [network, world.site])) == {}


async def test_a_page_of_tags_by_id_is_the_wall_row_for_row(
    access: Repository, actors: Actors, world: World
) -> None:
    """`visible_tags` gives the wall's answer."""
    for viewer in (actors.admin, actors.guest):
        wall = {one.id: one for one in (await access.list_tags(viewer, limit=100)).items}
        assert viewer is actors.guest or world.tag in wall
        assert await access.visible_tags(viewer, [*wall, "not-an-id"]) == wall
        for tag_id, row in wall.items():
            assert await access.visible_tag(viewer, tag_id) == row


async def test_names_on_disk_is_the_present_copys_name_for_a_whole_list(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """What each file is called NOW, scoped: the imported name goes stale after a rename. `twin`'s
    name is its copy that would open."""
    await temp_db.execute(
        "UPDATE asset_locations SET rel_path = ?, filename = ? WHERE asset_id = ?",
        ("top/mid/leaf/Renamed.mp4", "Renamed.mp4", world.solo),
    )
    locations = await access.locations(actors.admin, world.twin)
    first = min(locations, key=lambda location: (location.first_seen_at, location.id))
    await temp_db.execute("UPDATE asset_locations SET status = 'missing' WHERE id = ?", (first.id,))

    names = await access.names_on_disk(actors.admin, [world.solo, world.twin, "not-an-id"])

    assert names[world.solo] == "Renamed.mp4"
    path = await access.locate(actors.admin, world.twin)
    assert path is not None and names[world.twin] == path.name
    assert "not-an-id" not in names
    # A file with no present copy is absent, like one the viewer may not have.
    await temp_db.execute(
        "UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?", (world.solo,)
    )
    assert world.solo not in await access.names_on_disk(actors.admin, [world.solo])
    assert await access.names_on_disk(actors.guest, [world.twin]) == {}


# --- the walls ordered by the bytes under each row


async def _a_second_of_each(temp_db: Database, world: World) -> dict[str, tuple[str, str]]:
    """Beside each of the world's things, holding `solo`, a second holding `twin` and `loose`: more
    files and fewer bytes. Answers each wall's (the world's, the second) pair."""
    for asset_id, size in ((world.solo, 9_000), (world.twin, 10), (world.loose, 10)):
        await temp_db.execute("UPDATE assets SET size_bytes = ? WHERE id = ?", (size, asset_id))
    second = {kind: new_id() for kind in ("tag", "person", "collection", "photo_set", "song")}
    second_site, second_username = new_id(), new_id()
    await temp_db.execute(
        "INSERT INTO songs (id, name, name_sort, created_at) VALUES (?, 'tune', 'tune', 0)",
        (world.song,),
    )
    await temp_db.execute(
        "INSERT INTO song_files (asset_id, song_id) VALUES (?, ?)", (world.solo, world.song)
    )
    rows = [
        ("INSERT INTO tags (id, name, name_sort, created_at) VALUES (?, 'b', 'b', 0)", "tag"),
        ("INSERT INTO people (id, name, name_sort, created_at) VALUES (?, 'b', 'b', 0)", "person"),
        (
            "INSERT INTO collections (id, name, name_sort, created_at) VALUES (?, 'b', 'b', 0)",
            "collection",
        ),
        (
            "INSERT INTO photo_sets (id, name, name_sort, created_at) VALUES (?, 'b', 'b', 0)",
            "photo_set",
        ),
        ("INSERT INTO songs (id, name, name_sort, created_at) VALUES (?, 'b', 'b', 0)", "song"),
    ]
    for statement, kind in rows:
        await temp_db.execute(statement, (second[kind],))
    await temp_db.execute(
        "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, 'b', 'b', 0)",
        (second_site,),
    )
    await temp_db.execute(
        "INSERT INTO usernames (id, site_id, name, name_sort, created_at) VALUES (?, ?, 'b', 'b', 0)",
        (second_username, second_site),
    )
    for position, asset_id in enumerate((world.twin, world.loose)):
        for statement, values in (
            ("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (second["tag"],)),
            ("INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (second["person"],)),
            (
                "INSERT INTO collection_items (asset_id, collection_id, added_at) VALUES (?, ?, 0)",
                (second["collection"],),
            ),
            (
                "INSERT INTO photo_set_items (asset_id, photo_set_id, position, added_at)"
                " VALUES (?, ?, ?, 0)",
                (second["photo_set"], position),
            ),
            ("INSERT INTO song_files (asset_id, song_id) VALUES (?, ?)", (second["song"],)),
            (
                "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
                (second_username,),
            ),
        ):
            await temp_db.execute(statement, (asset_id, *values))
    pairs = {kind: (str(getattr(world, kind)), second[kind]) for kind in second}
    return pairs | {
        "site": (world.site, second_site),
        "username": (world.username, second_username),
    }


async def _wall_of(
    access: Repository, viewer: Viewer, kind: str, sort: str, *, narrowed: bool = False
) -> list[str]:
    """The ids on one wall, in the order asked for; `narrowed` counts live through a filter."""
    more: dict[str, Any] = {}
    if narrowed:
        more = {"asset_filter": AssetFilter(where=Not(Where("favorite"))), "count_narrowed": True}
    reads: dict[str, Callable[[], Awaitable[Any]]] = {
        "person": lambda: access.suggest_people(viewer, limit=50, sort=sort, **more),
        "site": lambda: access.list_sites(viewer, limit=50, sort=sort, **more),
        "tag": lambda: access.list_tags(viewer, limit=50, sort=sort, **more),
        "collection": lambda: access.list_collections(viewer, limit=50, sort=sort),
        "photo_set": lambda: access.list_photo_sets(viewer, limit=50, sort=sort, **more),
        "song": lambda: access.list_songs(viewer, limit=50, sort=sort, **more),
        "username": lambda: access.list_usernames(viewer, limit=50, sort=sort),
    }
    page = await reads[kind]()
    return [one.id for one in page.items]


@pytest.mark.parametrize(
    "kind", ["person", "site", "tag", "collection", "photo_set", "song", "username"]
)
async def test_every_wall_orders_by_the_bytes_under_a_row_apart_from_its_count(
    access: Repository, actors: Actors, world: World, temp_db: Database, kind: str
) -> None:
    """The second row holds more files and fewer bytes, so the two size pairs disagree."""
    world_row, second_row = (await _a_second_of_each(temp_db, world))[kind]

    def pair(ids: list[str]) -> list[str]:
        return [one for one in ids if one in (world_row, second_row)]

    assert pair(await _wall_of(access, actors.admin, kind, "largest")) == [second_row, world_row]
    assert pair(await _wall_of(access, actors.admin, kind, "largest_total")) == [
        world_row,
        second_row,
    ]
    assert pair(await _wall_of(access, actors.admin, kind, "smallest_total")) == [
        second_row,
        world_row,
    ]


_WALL_KINDS = ["person", "site", "tag", "collection", "photo_set", "song", "username"]
_NARROWED_KINDS = ["person", "site", "tag", "photo_set", "song"]


@pytest.mark.parametrize(
    ("kind", "narrowed"),
    [(kind, False) for kind in _WALL_KINDS] + [(kind, True) for kind in _NARROWED_KINDS],
)
async def test_every_wall_orders_by_the_running_time_under_a_row_and_puts_none_last(
    access: Repository, actors: Actors, world: World, temp_db: Database, kind: str, narrowed: bool
) -> None:
    """The world's row holds one long file and the second two short ones; once the long one's
    time is gone, the world's row has none and is last under both orders."""
    world_row, second_row = (await _a_second_of_each(temp_db, world))[kind]

    async def pair(sort: str) -> list[str]:
        ids = await _wall_of(access, actors.admin, kind, sort, narrowed=narrowed)
        return [one for one in ids if one in (world_row, second_row)]

    for asset_id, ms in ((world.solo, 5_000), (world.twin, 1_000), (world.loose, 1_000)):
        await temp_db.execute("UPDATE assets SET duration_ms = ? WHERE id = ?", (ms, asset_id))
    assert await pair("longest_total") == [world_row, second_row]
    assert await pair("shortest_total") == [second_row, world_row]
    await temp_db.execute("UPDATE assets SET duration_ms = NULL WHERE id = ?", (world.solo,))
    assert await pair("longest_total") == [second_row, world_row]
    assert await pair("shortest_total") == [second_row, world_row]
