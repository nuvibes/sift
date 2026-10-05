# SPDX-License-Identifier: AGPL-3.0-or-later
"""Counting a dimension, the reads a screen makes for a whole list, a page continued after a row,
the pins read by themselves, and how far below a folder a scope reaches.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

# Imported for their tables: the unnamed-face condition, `same_music`, the `enriched:` predicates
# and the ledger every concealment writes all read one, and the application always has them.
import sift.slices.faces.schema
import sift.slices.music.schema
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import (
    AllOf,
    AssetFilter,
    Concealment,
    ConstraintError,
    Effect,
    Enrichment,
    FolderDepth,
    Not,
    ObjectType,
    Repository,
    Role,
    Viewer,
    Where,
    attribute_to_person,
    like_anywhere,
    link_asset_to_site,
    link_username_to_asset,
    link_username_to_asset_on,
    music_filter,
    seed_site_username,
    title_filter,
)
from sift.kernel.access.catalog import (
    MADE_BY_A_PERSON,
    count_files_filed_from,
    filenames_for_nameless_usernames,
    filenames_of_unfiled_files,
    seed_site_username_on,
    set_username_numbers,
)
from sift.kernel.access.constraints import (
    same_music_where,
)
from sift.kernel.access.repository.read_folders import _FILED_COUNTS
from sift.kernel.access.repository.views import _optional_text
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.tests.access_helpers import (
    _EPOCH,
    _a_file_called,
    _a_shelf_of_files,
    _access_ctes,
    _an_asset,
    _migrated,
    _seed_asset,
    _shuffled,
    _somebody,
)
from sift.testing.fixtures import Actors, World, hide

# --- counting a dimension


async def test_a_facet_counts_only_what_the_viewer_could_open(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A facet groups the rows the grid would show, by the same statement and filter, so a guest
    sees the counts of what a guest can open."""
    everything = await access.facet_counts(actors.admin, "media")
    assert {row.value: row.count for row in everything}["video"] == 3

    assert await access.facet_counts(actors.guest, "media") == []

    # Filtered, the count follows the filter.
    tagged = AssetFilter(where=Where("tags", (world.tag,)))
    counted = await access.facet_counts(actors.admin, "media", asset_filter=tagged)
    assert {row.value: row.count for row in counted} == {"video": 1}


async def test_a_walls_total_counts_what_the_filter_finds_for_this_viewer(
    access: Repository, actors: Actors, world: World
) -> None:
    """A Files-wall link's number is the wall's own total under the same filter."""
    tagged = AssetFilter(where=Where("tags", (world.tag,)))

    assert await access.count_visible(actors.admin, AssetFilter()) == 3
    assert await access.count_visible(actors.admin, tagged) == 1
    page = await access.visible_assets(actors.admin, asset_filter=tagged)
    assert page.total == 1
    assert await access.count_visible(actors.guest, AssetFilter()) == 0


async def test_a_size_band_and_the_filter_it_writes_hold_the_same_files(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A size band's row and its filter agree at the cut: the cuts are `<`, so a file of exactly
    ten megabytes is in the band ABOVE, and the half-open value the row writes returns exactly what
    it counted. Sizes are seeded because a null size is left out of the dimension."""
    await temp_db.execute("UPDATE assets SET size_bytes = ? WHERE id = ?", (1024, world.solo))
    # Exactly ten megabytes, binary (`size:` scales by 1024): the FIRST byte of the second band.
    await temp_db.execute(
        "UPDATE assets SET size_bytes = ? WHERE id = ?", (10 * 1024**2, world.twin)
    )
    await temp_db.execute(
        "UPDATE assets SET size_bytes = ? WHERE id = ?", (2 * 1024**3, world.loose)
    )

    counted = {row.value: row.count for row in await access.facet_counts(actors.admin, "size")}
    assert counted == {"0..<1mb": 1, "10mb..<100mb": 1, "1gb..<5gb": 1}, (
        "the file on the cut is in the wrong band, or a band is counting nothing"
    )

    # `1mb..<10mb` returns the file under the cut and not the one on it.
    under = await access.visible_assets(
        actors.admin, asset_filter=AssetFilter(where=Where("size_max", (10 * 1024**2 - 1,)))
    )
    assert {item.asset.id for item in under.items} == {world.solo}


async def test_a_filename_filter_matches_the_characters_that_were_typed(
    access: Repository, actors: Actors, world: World
) -> None:
    """`_` is a wildcard to LIKE and an ordinary character in a filename: one helper escapes it,
    beside the predicate that binds it."""
    pattern = like_anywhere("solo")
    found = await access.visible_assets(
        actors.admin, asset_filter=AssetFilter(where=Where("filename", (pattern, pattern)))
    )
    assert {item.asset.id for item in found.items} == {world.solo}

    escaped = like_anywhere("s_lo")
    assert (
        await access.visible_assets(
            actors.admin, asset_filter=AssetFilter(where=Where("filename", (escaped, escaped)))
        )
    ).items == []


async def _person_of(db: Database, username_id: str) -> str | None:
    row = await db.fetch_one("SELECT person_id FROM usernames WHERE id = ?", (username_id,))
    return None if row is None or row["person_id"] is None else str(row["person_id"])


async def test_resolving_a_username_also_says_the_USERNAME_is_that_person(
    temp_db: Database,
) -> None:
    """Filing a download links the username to its person too, or the Usernames queue keeps
    asking."""
    await _migrated(temp_db)
    person = await _somebody(temp_db, "Nerith")
    asset = await _an_asset(temp_db)
    _, username = await seed_site_username(
        temp_db, site="TikTok", name="nerith", made=MADE_BY_A_PERSON
    )

    assert (
        await attribute_to_person(
            temp_db, asset_id=asset, name="nerith", username_id=username, made=MADE_BY_A_PERSON
        )
        == person
    )
    assert await _person_of(temp_db, username) == person


async def test_a_username_that_CREATED_its_person_is_that_person_too(temp_db: Database) -> None:
    """A username that created its person is that person by construction."""
    await _migrated(temp_db)
    asset = await _an_asset(temp_db)
    _, username = await seed_site_username(
        temp_db, site="TikTok", name="astranger", made=MADE_BY_A_PERSON
    )

    person = await attribute_to_person(
        temp_db,
        asset_id=asset,
        name="astranger",
        username_id=username,
        create_if_unknown=True,
        made=MADE_BY_A_PERSON,
    )
    assert person is not None
    assert await _person_of(temp_db, username) == person


async def test_an_AMBIGUOUS_username_leaves_it_unclaimed(temp_db: Database) -> None:
    """When two people answer to the spelling, nothing is filed and the username stays queued."""
    await _migrated(temp_db)
    await _somebody(temp_db, "Ines")
    await _somebody(temp_db, "Inessa", "ines")
    asset = await _an_asset(temp_db)
    _, username = await seed_site_username(
        temp_db, site="TikTok", name="Ines", made=MADE_BY_A_PERSON
    )

    assert (
        await attribute_to_person(
            temp_db, asset_id=asset, name="Ines", username_id=username, made=MADE_BY_A_PERSON
        )
        is None
    )
    assert await _person_of(temp_db, username) is None


async def test_a_username_somebody_already_answered_for_is_not_reassigned(
    temp_db: Database,
) -> None:
    """`AND person_id IS NULL`: a download never overrules a join somebody made by hand."""
    await _migrated(temp_db)
    theirs = await _somebody(temp_db, "Someone Else")
    await _somebody(temp_db, "Nerith")
    asset = await _an_asset(temp_db)
    _, username = await seed_site_username(
        temp_db, site="TikTok", name="nerith", made=MADE_BY_A_PERSON
    )
    await temp_db.execute("UPDATE usernames SET person_id = ? WHERE id = ?", (theirs, username))

    await attribute_to_person(
        temp_db, asset_id=asset, name="nerith", username_id=username, made=MADE_BY_A_PERSON
    )
    assert await _person_of(temp_db, username) == theirs


def test_a_title_filter_narrows_the_title_dimension() -> None:
    """`title_filter` owns the title dimension's name and its escaping, so no caller copies them."""
    where, bound = title_filter("beach").predicate()

    assert "title" in where
    assert bound["p0"] == "%beach%"


def test_a_track_filter_narrows_the_music_dimension() -> None:
    """`music_filter` narrows the music facet: the files carrying a track are what scope it."""
    where, bound = music_filter("  plain jane  ").predicate()

    assert "music" in where
    assert bound["p0"] == "%plain jane%"


def test_the_same_music_leaf_binds_the_id_and_refuses_anything_else() -> None:
    """The file's id is bound at every place the predicate names it, and a non-id is refused."""
    one = "01HX0000000000000000000001"
    where, bound = AssetFilter(where=same_music_where(f"  {one} ")).predicate()
    assert "music_pairs" in where
    assert one not in where
    assert set(bound.values()) >= {one}
    for wrong in ("beach", "", "01HX000000000000000000000!"):
        with pytest.raises(ConstraintError, match="same_music"):
            same_music_where(wrong)
    with pytest.raises(ConstraintError, match="same_music"):
        Where("same_music", (1, 2, 3))


_A_PAIR = """
INSERT INTO music_pairs (a_id, b_id, ber, offset_s, windows, matching, computed_at)
VALUES (MIN(?, ?), MAX(?, ?), 0.1, 0, 5, 5, 1)
"""


async def test_the_same_music_leaf_is_one_hop_both_ways_and_never_through_a_hidden_file(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    """solo - twin - loose: each end reaches the other through twin, both directions of the pair
    table, never itself. With twin hidden, the chain through it is cut."""
    for first, second in ((world.solo, world.twin), (world.twin, world.loose)):
        await temp_db.execute(_A_PAIR, (first, second, first, second))

    async def group(viewer: Viewer, of: str, *, negated: bool = False) -> set[str]:
        leaf = same_music_where(of)
        page = await access.visible_assets(
            viewer, limit=50, asset_filter=AssetFilter(where=Not(leaf) if negated else leaf)
        )
        return {item.asset.id for item in page.items}

    assert await group(actors.admin, world.solo) == {world.twin, world.loose}
    assert await group(actors.admin, world.loose) == {world.twin, world.solo}
    assert await group(actors.admin, world.solo, negated=True) == {world.solo}
    assert await group(actors.guest, world.solo) == set()
    await hide(temp_db, "asset", world.twin, actors.admin.id)
    assert await group(actors.admin, world.solo) == set()


def test_both_filters_escape_what_was_typed() -> None:
    """Both filters escape what was typed, through `like_anywhere`."""
    assert title_filter("my_clip").predicate()[1]["p0"] == like_anywhere("my_clip")
    assert music_filter("my_clip").predicate()[1]["p0"] == like_anywhere("my_clip")


async def test_a_username_renamed_on_the_site_is_the_same_username(temp_db: Database) -> None:
    """A username renamed on its site is the same row: the site's number outranks the name, as a
    folder is recognised by its contents."""
    await _migrated(temp_db)
    _, first = await seed_site_username(
        temp_db,
        site="Instagram",
        name="harlowquin",
        number="40000000001",
        made=MADE_BY_A_PERSON,
    )
    _, again = await seed_site_username(
        temp_db,
        site="Instagram",
        name="harlowq",
        number="40000000001",
        made=MADE_BY_A_PERSON,
    )
    assert again == first, "a rename made a second username"
    row = await temp_db.fetch_one("SELECT name FROM usernames WHERE id = ?", (first,))
    assert row is not None
    assert row["name"] == "harlowq", "the row kept the name it used to have"


async def test_two_numbers_are_two_people_however_alike_the_names_are(temp_db: Database) -> None:
    """`midnight_ivy` and `midnight_ivyy` have different numbers, so they are two usernames."""
    await _migrated(temp_db)
    _, one = await seed_site_username(
        temp_db,
        site="Instagram",
        name="midnight_ivy",
        number="4000000002",
        made=MADE_BY_A_PERSON,
    )
    _, other = await seed_site_username(
        temp_db,
        site="Instagram",
        name="midnight_ivyy",
        number="40000000003",
        made=MADE_BY_A_PERSON,
    )
    assert one != other


async def test_a_number_learned_later_fills_a_blank_and_never_replaces_one(
    temp_db: Database,
) -> None:
    """A row whose number disagrees with a fetch is a question, since numbers do not change."""
    await _migrated(temp_db)
    _, username_id = await seed_site_username(
        temp_db, site="Instagram", name="someone", made=MADE_BY_A_PERSON
    )
    await seed_site_username(
        temp_db,
        site="Instagram",
        name="someone",
        number="12345",
        made=MADE_BY_A_PERSON,
    )
    row = await temp_db.fetch_one("SELECT number FROM usernames WHERE id = ?", (username_id,))
    assert row is not None
    assert row["number"] == "12345"


async def test_one_sites_number_never_lands_on_another_sites_username(temp_db: Database) -> None:
    """A number is written onto the username of its own site only: the same word on another site
    is another person, and a number written into a blank cannot be corrected."""
    await _migrated(temp_db)
    _, on_instagram = await seed_site_username(
        temp_db, site="Instagram", name="pixieoaks", made=MADE_BY_A_PERSON
    )
    _, on_tiktok = await seed_site_username(
        temp_db, site="TikTok", name="pixieoaks", made=MADE_BY_A_PERSON
    )
    filled = await set_username_numbers(temp_db, {on_instagram: "40000000001"})

    assert filled == 1
    numbers = {
        str(row["id"]): row["number"]
        for row in await temp_db.fetch_all("SELECT id, number FROM usernames", ())
    }
    assert numbers[on_instagram] == "40000000001"
    assert numbers[on_tiktok] is None, "the other site's username was given a number it never had"


async def test_a_username_whose_name_has_not_changed_is_left_alone(temp_db: Database) -> None:
    """A fetch whose number and name both match writes nothing, so the row is not touched."""
    await _migrated(temp_db)
    _, first = await seed_site_username(
        temp_db,
        site="Instagram",
        name="harlowquin",
        number="40000000001",
        made=MADE_BY_A_PERSON,
    )
    _, again = await seed_site_username(
        temp_db,
        site="Instagram",
        name="harlowquin",
        number="40000000001",
        made=MADE_BY_A_PERSON,
    )

    assert again == first
    rows = await temp_db.fetch_all("SELECT name FROM usernames", ())
    assert [str(row["name"]) for row in rows] == ["harlowquin"]


async def test_a_number_with_nothing_to_write_it_on_is_skipped(temp_db: Database) -> None:
    """A blank number or username is skipped: a number is only written into a blank, for ever."""
    await _migrated(temp_db)
    _, username_id = await seed_site_username(
        temp_db, site="Instagram", name="someone", made=MADE_BY_A_PERSON
    )

    assert await set_username_numbers(temp_db, {username_id: "", "": "40000000001"}) == 0

    row = await temp_db.fetch_one("SELECT number FROM usernames WHERE id = ?", (username_id,))
    assert row is not None
    assert row["number"] is None


async def test_the_upsert_on_a_connection_survives_a_rename_too(temp_db: Database) -> None:
    """The review screen's upsert matches on the number too, read from the folder's filenames."""
    await _migrated(temp_db)
    async with temp_db.write() as connection:
        _, first = await seed_site_username_on(
            connection,
            site="Instagram",
            name="harlowquin",
            number="40000000001",
            made=MADE_BY_A_PERSON,
        )
    async with temp_db.write() as connection:
        _, again = await seed_site_username_on(
            connection,
            site="Instagram",
            name="harlowq",
            number="40000000001",
            made=MADE_BY_A_PERSON,
        )
    assert again == first
    row = await temp_db.fetch_one("SELECT name FROM usernames WHERE id = ?", (first,))
    assert row is not None
    assert row["name"] == "harlowq"


async def test_a_username_full_of_wildcards_is_still_a_prefix(temp_db: Database) -> None:
    """A username full of underscores is matched as a prefix, escaped: as a LIKE pattern it would
    match somebody else's files."""
    await _migrated(temp_db)
    root_id = new_id()
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, 0)",
        (root_id, "Library", "/library"),
    )
    _, username_id = await seed_site_username(
        temp_db, site="Instagram", name="a_b", made=MADE_BY_A_PERSON
    )
    await _a_file_called(temp_db, root_id, "axb_123456789.jpg")
    await _a_file_called(temp_db, root_id, "a_b_123456789.jpg")

    found = await filenames_for_nameless_usernames(temp_db, 50)
    assert [name for _, _, name in found] == ["a_b_123456789.jpg"]
    assert {got for got, _, _ in found} == {username_id}


async def test_the_unfiled_read_answers_only_files_under_no_site(temp_db: Database) -> None:
    """The filename pass reads present files with no `asset_usernames` row, whole, and a file that
    has been filed drops out, so the set shrinks with every write."""
    await _migrated(temp_db)
    root_id = new_id()
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, 0)",
        (root_id, "Library", "/library"),
    )
    await _a_file_called(temp_db, root_id, "one.jpg")
    await _a_file_called(temp_db, root_id, "two.jpg")

    before = await filenames_of_unfiled_files(temp_db)
    assert sorted(name for _, name in before) == ["one.jpg", "two.jpg"]

    _, username_id = await seed_site_username(
        temp_db, site="Instagram", name="one", made=MADE_BY_A_PERSON
    )
    filed = next(one for one, name in before if name == "one.jpg")
    await link_username_to_asset(temp_db, asset_id=filed, username_id=username_id)

    after = await filenames_of_unfiled_files(temp_db)
    assert [name for _, name in after] == ["two.jpg"]


async def test_a_file_filed_twice_is_offered_once(temp_db: Database) -> None:
    """`NOT EXISTS`, not a LEFT JOIN, so a file under two usernames is offered once."""
    await _migrated(temp_db)
    root_id = new_id()
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, 0)",
        (root_id, "Library", "/library"),
    )
    await _a_file_called(temp_db, root_id, "one.jpg")
    await _a_file_called(temp_db, root_id, "two.jpg")
    [(asset_id, _name)] = [
        one for one in await filenames_of_unfiled_files(temp_db) if one[1] == "one.jpg"
    ]
    for site in ("Instagram", "Fansly"):
        _, username_id = await seed_site_username(
            temp_db, site=site, name="one", made=MADE_BY_A_PERSON
        )
        await link_username_to_asset(temp_db, asset_id=asset_id, username_id=username_id)

    assert [name for _, name in await filenames_of_unfiled_files(temp_db)] == ["two.jpg"]


async def test_a_filing_on_a_connection_says_whether_it_wrote_anything(temp_db: Database) -> None:
    """A filing on a connection says whether it wrote anything, so an undo removes only what the
    pass filed."""
    await _migrated(temp_db)
    asset_id = await _seed_asset(temp_db)
    _, username_id = await seed_site_username(
        temp_db, site="Instagram", name="one", made=MADE_BY_A_PERSON
    )
    async with temp_db.write() as connection:
        first = await link_username_to_asset_on(
            connection, asset_id=asset_id, username_id=username_id, source="filename"
        )
        again = await link_username_to_asset_on(
            connection, asset_id=asset_id, username_id=username_id, source="filename"
        )
    assert (first, again) == (True, False)


async def test_the_count_of_a_pass_s_filings_is_by_the_word_it_wrote(
    temp_db: Database, actors: Actors, world: World
) -> None:
    """Files filed by a pass are counted DISTINCT, by its source word, and only those the reader may
    be shown: a shut vault keeps its size back."""
    asset_id = world.solo
    other = world.twin
    for site in ("Instagram", "Fansly"):
        _, username_id = await seed_site_username(
            temp_db, site=site, name="one", made=MADE_BY_A_PERSON
        )
        await link_username_to_asset(
            temp_db, asset_id=asset_id, username_id=username_id, source="filename"
        )
    _, by_hand = await seed_site_username(temp_db, site="Bunkr", name="two", made=MADE_BY_A_PERSON)
    await link_username_to_asset(temp_db, asset_id=other, username_id=by_hand)

    # The pair the filing pass binds (`service.FILED_FROM_A_NAME`).
    assert await count_files_filed_from(temp_db, actors.admin, ("filename", "metadata")) == 1
    assert await count_files_filed_from(temp_db, actors.admin, ("folder", "folder")) == 0
    assert await count_files_filed_from(temp_db, actors.guest, ("filename", "metadata")) == 0

    await hide(temp_db, "asset", asset_id, actors.admin.id)
    assert await count_files_filed_from(temp_db, actors.admin, ("filename", "metadata")) == 0
    unlocked = replace(actors.admin, show_hidden=True)
    assert await count_files_filed_from(temp_db, unlocked, ("filename", "metadata")) == 1


async def test_a_site_with_no_named_poster_still_has_its_row(temp_db: Database) -> None:
    """A site's empty-name username is a SENTINEL, one per site (`UNIQUE(site_id, name)`): it is how
    a file with a known site and an unknown poster reaches the site. It is not junk."""
    await _migrated(temp_db)
    asset_id = await _seed_asset(temp_db)
    await link_asset_to_site(temp_db, asset_id=asset_id, site="Bunkr", made=MADE_BY_A_PERSON)
    await link_asset_to_site(temp_db, asset_id=asset_id, site="Bunkr", made=MADE_BY_A_PERSON)
    rows = await temp_db.fetch_all("SELECT name FROM usernames WHERE trim(name) = ''")
    assert len(list(rows)) == 1, "one nameless row per site, and never a second"


async def test_a_username_that_is_only_spaces_is_refused(temp_db: Database) -> None:
    """A name that is only spaces is refused where a name is being SUPPLIED."""
    await _migrated(temp_db)
    for nothing in ("", "   "):
        with pytest.raises(ValueError, match="cannot be empty"):
            await seed_site_username(temp_db, site="Instagram", name=nothing, made=MADE_BY_A_PERSON)


async def test_a_by_id_read_handed_nothing_answers_nothing_rather_than_the_first_row(
    access: Repository, actors: Actors, world: World
) -> None:
    """Each permission-scoped point read refuses an empty id: its statement binds NULL to mean
    EVERY row, so an absent path parameter would hand back the first row the viewer may see."""
    assert await access.waiting_pile_position(actors.admin, "open", "") is None
    assert await access.set_folder_vault(actors.admin, "", vault=True) is False
    assert await access.visible_username(actors.admin, "") is None
    assert await access.grant_sources(ObjectType.ITEM, "") == []
    assert await access.vault_sources(actors.admin, ObjectType.ITEM, "") == []
    assert await access.vault_sources(actors.admin, ObjectType.FOLDER, "") == []
    # A real id still answers.
    assert await access.vault_sources(actors.admin, ObjectType.FOLDER, world.mid) == []


def test_a_row_that_does_not_carry_a_column_reads_it_as_none() -> None:
    """`_optional_text` reads a column that is not there as None."""
    assert _optional_text({"note": "kept"}, "note") == "kept"  # type: ignore[arg-type]
    assert _optional_text({"note": None}, "note") is None  # type: ignore[arg-type]
    assert _optional_text({"nothing_like_it": "x"}, "note") is None  # type: ignore[arg-type]


# --- the reads a screen makes for a whole list
#
# Each is the batch form of a single read above: it agrees with it, leaves out an id this viewer
# may not see, and answers a list with nothing valid in it with nothing.


async def test_the_stored_counts_are_nothing_for_a_user_that_has_none(
    access: Repository,
) -> None:
    """A user with no verdict rows sees nothing: a pair of zeros, not an error."""
    nobody = Viewer(id=new_id(), role=Role.GUEST)
    assert await access.visible_counts(nobody) == (0, 0)


async def test_standing_of_answers_for_the_files_this_viewer_may_see(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Files on a screen in one read: absent when refused or unknown, and concealed ones only when
    the grid would show them."""
    await hide(temp_db, "asset", world.solo, actors.admin.id)
    asked = [world.solo, world.twin, world.loose, world.twin, "not-an-id", new_id()]

    assert await access.standing_of(actors.admin, asked) == {world.twin: False, world.loose: False}
    unlocked = replace(actors.admin, show_hidden=True)
    assert await access.standing_of(unlocked, asked) == {
        world.solo: True,
        world.twin: False,
        world.loose: False,
    }
    placeholders = replace(actors.admin, concealment=Concealment.PLACEHOLDER)
    assert (await access.standing_of(placeholders, asked))[world.solo] is True
    assert await access.standing_of(actors.guest, asked) == {}
    assert await access.standing_of(actors.admin, []) == {}


async def test_visible_folders_of_is_the_batch_form_of_get_folder(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The folder batch agrees with the single read for every viewer; nothing valid, no read."""
    await hide(temp_db, "folder", world.mid, actors.admin.id)
    asked = [world.top, world.mid, world.leaf, world.other, world.top, "not-an-id", new_id()]

    for viewer in (actors.admin, replace(actors.admin, show_hidden=True), actors.guest):
        found = await access.visible_folders_of(viewer, asked)
        one_at_a_time = {
            folder_id: folder
            for folder_id in dict.fromkeys(asked)
            if (folder := await access.get_folder(viewer, folder_id)) is not None
        }
        assert found == one_at_a_time
    assert set(await access.visible_folders_of(actors.admin, asked)) == {world.top, world.other}
    assert await access.visible_folders_of(actors.admin, ["", ""]) == {}


async def test_folder_summaries_count_what_the_viewer_may_see_under_each_folder(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """`folder_file_count` for a list: only folders holding something visible come back."""
    empty = new_id()
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
        (empty, world.root, world.top, "top/empty", "empty"),
    )
    asked = [world.top, world.mid, world.leaf, world.other, empty, world.leaf, "not-an-id"]

    found = await access.folder_summaries(actors.admin, asked)
    assert found == {world.top: 2, world.mid: 2, world.leaf: 2, world.other: 1}
    for folder_id, files in found.items():
        folder = await access.get_folder(actors.admin, folder_id)
        assert folder is not None
        assert files == await access.folder_file_count(actors.admin, folder)

    await hide(temp_db, "asset", world.solo, actors.admin.id)
    assert await access.folder_summaries(actors.admin, [world.leaf]) == {world.leaf: 1}
    unlocked = replace(actors.admin, show_hidden=True)
    assert await access.folder_summaries(unlocked, [world.leaf]) == {world.leaf: 2}
    assert await access.folder_summaries(actors.admin, []) == {}


async def test_folder_covers_pick_the_newest_file_the_viewer_may_see_under_each_folder(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A picture for each folder: the latest visible arrival under it."""
    await temp_db.execute("UPDATE assets SET added_at = ? WHERE id = ?", (10, world.solo))
    await temp_db.execute("UPDATE assets SET added_at = ? WHERE id = ?", (40, world.twin))
    empty = new_id()
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
        (empty, world.root, world.top, "top/empty", "empty"),
    )
    asked = [world.top, world.other, empty, "not-an-id"]

    assert await access.folder_covers(actors.admin, asked) == {
        world.top: world.twin,
        world.other: world.twin,
    }
    await hide(temp_db, "asset", world.twin, actors.admin.id)
    assert await access.folder_covers(actors.admin, asked) == {world.top: world.solo}
    unlocked = replace(actors.admin, show_hidden=True)
    assert await access.folder_covers(unlocked, asked) == {
        world.top: world.twin,
        world.other: world.twin,
    }
    assert await access.folder_covers(actors.admin, []) == {}


async def test_forget_items_drops_every_grant_naming_the_files_that_ended(
    access: Repository, actors: Actors, world: World
) -> None:
    """Forgetting files in bulk drops only their grants."""
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    await access.grant(ObjectType.ITEM, world.twin, actors.guest.id, Effect.SHARE)
    await access.grant(ObjectType.FOLDER, world.other, actors.guest.id, Effect.RESTRICT)
    assert await access.can_view(actors.guest, world.solo) is True

    await access.forget_items([])
    await access.forget_items([""])
    assert len(await access.grants_of(actors.guest.id)) == 3

    await access.forget_items([world.solo, world.solo, "not-an-id", "", world.twin, new_id()])

    left = await access.grants_of(actors.guest.id)
    assert [(grant.object_type, grant.object_id) for grant in left] == [
        (ObjectType.FOLDER, world.other)
    ]
    assert await access.can_view(actors.guest, world.solo) is False


# --- a page continued after a row


async def test_a_page_continued_after_a_row_is_the_page_the_offset_would_have_given(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """`after` names the last row of the previous page and seeks on the sort's index rather than
    counting an offset; arrivals are spread so the seek is not decided by ids alone."""
    for asset_id, added_at in ((world.solo, 10), (world.twin, 20), (world.loose, 30)):
        await temp_db.execute("UPDATE assets SET added_at = ? WHERE id = ?", (added_at, asset_id))

    first = await access.visible_assets(actors.admin, limit=1, sort="newest")
    assert [item.asset.id for item in first.items] == [world.loose]
    by_offset = await access.visible_assets(actors.admin, limit=1, offset=1, sort="newest")
    continued = await access.visible_assets(actors.admin, limit=1, sort="newest", after=world.loose)
    assert [item.asset.id for item in continued.items] == [world.twin]
    assert [item.asset.id for item in continued.items] == [
        item.asset.id for item in by_offset.items
    ]
    the_rest = await access.visible_assets(actors.admin, sort="oldest", after=world.twin)
    assert [item.asset.id for item in the_rest.items] == [world.loose]


async def test_a_shuffle_continued_after_a_row_is_the_rest_of_that_shuffle(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A shuffle continues from the last row seen, so new arrivals cannot repeat a row."""
    await _a_shelf_of_files(temp_db, world, 20)
    whole = await _shuffled(access, actors, 4242, limit=50)
    first = await _shuffled(access, actors, 4242, limit=10)
    by_offset = await _shuffled(access, actors, 4242, limit=10, offset=10)

    continued = await access.visible_assets(
        actors.admin, sort="random", seed=4242, limit=10, after=first[-1]
    )
    assert [item.asset.id for item in continued.items] == by_offset

    for index in range(20):
        asset_id = new_id()
        await temp_db.execute(
            "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
            " VALUES (?, ?, 1, 'video', 0)",
            (asset_id, f"digest-later-{index}-{asset_id}"),
        )
        await temp_db.execute(
            "INSERT INTO asset_locations"
            " (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)"
            " VALUES (?, ?, ?, NULL, ?, ?, 0, 0)",
            (new_id(), asset_id, world.root, f"later-{index}.mp4", f"later-{index}.mp4"),
        )
    later = await access.visible_assets(
        actors.admin, sort="random", seed=4242, limit=50, after=first[-1]
    )
    seen_later = {item.asset.id for item in later.items}
    assert not seen_later & set(first)
    assert seen_later & set(whole) == set(whole) - set(first)


async def test_a_page_continued_after_a_row_the_viewer_may_not_see_starts_from_nothing(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A stale bookmark (vaulted since, never minted, never shown) gets the empty page."""
    await hide(temp_db, "asset", world.loose, actors.admin.id)
    for viewer, after in (
        (actors.admin, world.loose),
        (actors.admin, new_id()),
        (actors.guest, world.solo),
    ):
        page = await access.visible_assets(viewer, limit=1, sort="newest", after=after)
        assert (page.items, page.total) == ([], 0)


async def test_a_wall_with_something_arranged_in_front_of_the_sort_cannot_be_continued(
    access: Repository, actors: Actors, world: World
) -> None:
    """Pins, a photo set's order and expression sorts cannot be seeked, and asking is refused; a
    collection has no order of its own, so it continues as any wall does."""
    continued = await access.visible_assets(
        actors.admin, collection_id=world.collection, after=world.solo
    )
    assert continued.items == []
    for arranged in (
        {"pinned_first": True},
        {"photo_set_id": world.photo_set},
        {"sort": "relevance"},
    ):
        with pytest.raises(ValueError, match="page by offset"):
            await access.visible_assets(actors.admin, after=world.solo, **arranged)


# --- three reads the screens are built from


async def test_which_of_the_three_ways_wrote_to_a_file_is_asked_of_one_row(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The marks on a file's own screen use the predicates the `enriched:` filter does: a stash-box
    answer, a face Sift matched, a folder name filing a person, in any combination."""
    assert await access.enriched_by(world.solo) == []

    # What makes it a stash-box's doing is the source a pass writes onto the pairing.
    await temp_db.execute(
        "UPDATE asset_people SET source = 'stash_box' WHERE asset_id = ? AND person_id = ?",
        (world.solo, world.person),
    )
    assert await access.enriched_by(world.solo) == [Enrichment(via="stash")]

    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id, source) VALUES (?, ?, 'folder')"
        " ON CONFLICT(asset_id, person_id) DO UPDATE SET source = 'folder'",
        (world.twin, world.person),
    )
    assert await access.enriched_by(world.twin) == [Enrichment(via="folder")]

    # A face only proposed has enriched nothing.
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, person_id,"
        " attribution, created_at) VALUES (?, ?, 0, 0, 1, 0.9, ?, 'matched', 0)",
        (new_id(), world.loose, world.person),
    )
    assert await access.enriched_by(world.loose) == [Enrichment(via="faces")]


async def test_a_stash_box_mark_with_no_answer_behind_it_names_the_box_its_filing_names(
    access: Repository, world: World, temp_db: Database
) -> None:
    """A box's filing that outlived its answer is named by the filing's `box_id`."""
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, slug, created_at)"
        " VALUES ('box-fans', 'FansDB', 'https://example.invalid/fans', 'fansdb', 0)"
    )
    await temp_db.execute(
        "UPDATE asset_people SET source = 'stash_box' WHERE asset_id = ? AND person_id = ?",
        (world.solo, world.person),
    )
    assert await access.enriched_by(world.solo) == [Enrichment(via="stash")]

    await temp_db.execute(
        "UPDATE asset_people SET box_id = 'box-fans' WHERE asset_id = ? AND person_id = ?",
        (world.solo, world.person),
    )
    assert await access.enriched_by(world.solo) == [
        Enrichment(via="stash", name="FansDB", box="fansdb")
    ]


async def test_a_stash_box_mark_names_the_box_that_applied_the_match(
    access: Repository, world: World, temp_db: Database
) -> None:
    """WHICH box, one entry per box that recognised the file, newest decision first."""
    for box_id, name, slug in (
        ("box-older", "PMVStash", "pmvstash"),
        ("box-newer", "StashDB", None),
    ):
        await temp_db.execute(
            "INSERT INTO stash_boxes (id, name, endpoint, slug, created_at) VALUES (?, ?, ?, ?, 0)",
            (box_id, name, f"https://example.invalid/{box_id}", slug),
        )
    for box_id, decided in (("box-older", 100), ("box-newer", 200)):
        await temp_db.execute(
            "INSERT INTO asset_stash_box_matches"
            " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
            " VALUES (?, ?, 'remote', '[]', 'certain', 'applied', 0, ?)",
            (world.solo, box_id, decided),
        )

    # The box's own word beside its name, null where Sift has none for it.
    assert await access.enriched_by(world.solo) == [
        Enrichment(via="stash", name="StashDB"),
        Enrichment(via="stash", name="PMVStash", box="pmvstash"),
    ]

    # A match still waiting names nothing; with no applied answer left the file is still marked,
    # without a box name, agreeing with the `enriched:stash` count.
    await temp_db.execute(
        "UPDATE asset_stash_box_matches SET state = 'waiting' WHERE asset_id = ?", (world.solo,)
    )
    await temp_db.execute(
        "UPDATE asset_people SET source = 'stash_box' WHERE asset_id = ? AND person_id = ?",
        (world.solo, world.person),
    )
    assert await access.enriched_by(world.solo) == [Enrichment(via="stash")]


async def test_a_file_that_is_not_a_file_is_marked_by_nothing(access: Repository) -> None:
    """A non-file is marked by nothing: an empty id is turned back before the statement (NULL means
    every row), any other string matches nothing. Never a raise, which would cost the picture."""
    assert await access.enriched_by("") == []
    assert await access.enriched_by("../../etc/passwd") == []
    assert await access.enriched_by(new_id()) == []


async def test_how_many_of_a_folders_files_a_pass_filed_under_somebody(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Filed counts go through the folder's SUBTREE and this viewer's verdict, and only a PASS's
    filings (`source IS NOT NULL`) count."""
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id, source) VALUES (?, ?, 'folder')"
        " ON CONFLICT(asset_id, person_id) DO UPDATE SET source = 'folder'",
        (world.solo, world.person),
    )

    # `solo` is three levels under `top`; every ancestor counts it.
    counts = await access.filed_counts(actors.admin, [(world.top, world.person)])
    assert counts == {(world.top, world.person): 1}
    assert await access.filed_counts(actors.admin, [(world.leaf, world.person)]) == {
        (world.leaf, world.person): 1
    }

    assert await access.filed_counts(actors.guest, [(world.top, world.person)]) == {}


async def test_a_filed_count_asked_about_nothing_valid_asks_the_database_nothing(
    access: Repository, actors: Actors, world: World
) -> None:
    """An empty dictionary for an empty list, and a non-id never reaches the statement."""
    assert await access.filed_counts(actors.admin, []) == {}
    assert await access.filed_counts(actors.admin, [("../../etc", world.person)]) == {}
    assert await access.filed_counts(actors.admin, [(world.top, "not-an-id")]) == {}
    assert (
        await access.filed_counts(
            actors.admin, [(world.top, world.person), (world.top, world.person)]
        )
        == {}
    )


async def test_a_filed_count_keeps_the_viewers_verdict_a_filter_and_not_a_join(
    actors: Actors, world: World, temp_db: Database
) -> None:
    """The viewer's verdict is an EXISTS filter on this statement, never a join: joined, the planner
    can start from `viewer_assets` and walk the whole library per pair. The shape is asserted, not
    the plan: a fixture has no stale statistics to choose the bad plan from."""
    await temp_db.fetch_all(
        _FILED_COUNTS, (json.dumps([[world.top, world.person]]), actors.admin.id)
    )
    where, _, guard = _FILED_COUNTS.partition("EXISTS")
    assert guard, "the filed counts read no longer asks the viewer's verdict as an EXISTS"
    assert "viewer_assets" not in where, (
        "the filed counts read joins viewer_assets, which lets the planner start from one"
        " user's whole library"
    )
    assert "viewer_assets" in guard


# --- the pins, read by themselves
#
# A curated wall reads the pinned rows with a query of their own and excludes them from the walk:
# ordering by the pin would make the sort an expression no index answers.


async def _pin(database: Database, asset_id: str, viewer: Viewer) -> None:
    await database.execute(
        "INSERT INTO asset_user_state (asset_id, user_id, pinned, updated_at) VALUES (?, ?, 1, ?)"
        " ON CONFLICT(asset_id, user_id) DO UPDATE SET pinned = 1",
        (asset_id, viewer.id, _EPOCH),
    )


async def test_a_wall_that_curates_floats_the_pin_and_counts_it_once(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The pin comes first and the file appears ONCE; the total counts the whole set."""
    ordinary = await access.visible_assets(actors.admin, limit=50, sort="newest")
    everything = [item.asset.id for item in ordinary.items]
    assert len(everything) > 1, "this needs more than one file to say anything"
    last = everything[-1]

    await _pin(temp_db, last, actors.admin)
    curated = await access.visible_assets(actors.admin, limit=50, sort="newest", pinned_first=True)

    shown = [item.asset.id for item in curated.items]
    assert shown[0] == last, "the pinned file did not float"
    assert shown.count(last) == 1, "the pinned file came back twice: once from each read"
    assert sorted(shown) == sorted(everything), "the walk lost or gained a row"
    assert curated.total == ordinary.total


async def test_a_page_that_is_all_pins_never_walks_at_all(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A page wholly inside the pins does not ask the walk at all, and is still right."""
    await _pin(temp_db, world.solo, actors.admin)

    page = await access.visible_assets(actors.admin, limit=1, sort="newest", pinned_first=True)

    assert [item.asset.id for item in page.items] == [world.solo]
    assert page.total > 1, "the total is the whole set, not the page"


async def test_a_wall_that_does_not_curate_leaves_the_pin_where_it_was(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Browse puts no pin in front: `pinned_first` is the wall's answer, not the file's."""
    ordinary = await access.visible_assets(actors.admin, limit=50, sort="newest")
    last = [item.asset.id for item in ordinary.items][-1]

    await _pin(temp_db, last, actors.admin)

    again = await access.visible_assets(actors.admin, limit=50, sort="newest")
    assert [item.asset.id for item in again.items][-1] == last


async def test_one_more_condition_carries_everything_the_filter_already_had(
    access: Repository, actors: Actors, world: World
) -> None:
    """`also` narrows rather than replaces: the words, the folder scope and the depth are kept."""
    scoped = AssetFilter(text="clip", folder_scope=((world.leaf,),))
    narrowed = scoped.also(Where("pinned"))

    assert narrowed.text == scoped.text
    assert narrowed.folder_scope == scoped.folder_scope

    where, bound = narrowed.predicate()
    plain, plain_bound = scoped.predicate()
    assert where != plain, "the extra condition was not applied"
    assert len(where) > len(plain), "the extra condition replaced the filter rather than narrowing"
    # Every parameter already bound is still bound.
    assert set(plain_bound) <= set(bound)
    assert scoped.predicate()[0] == plain

    assert narrowed.folder_depth == scoped.folder_depth


# --- how far below a named folder the scope reaches


def _in_folder(folder_id: str, depth: FolderDepth = FolderDepth.SUBTREE) -> AssetFilter:
    """`in:<folder>` as the compiler builds it: the scope expands, the leaf matches against it."""
    return AssetFilter(
        where=AllOf((Where("folder", (0,)),)),
        folder_scope=((folder_id,),),
        folder_depth=depth,
    )


async def test_a_folder_scope_reaches_the_whole_subtree_by_default(
    access: Repository, actors: Actors, world: World
) -> None:
    """`in:` means a folder and everything under it; `top` holds nothing of its own."""
    page = await access.visible_assets(actors.admin, limit=50, asset_filter=_in_folder(world.top))
    assert {item.asset.id for item in page.items} == {world.solo, world.twin}


async def test_a_direct_folder_scope_stops_at_the_folder_it_names(
    access: Repository, actors: Actors, world: World
) -> None:
    """A direct scope is what is IN the folder: empty over `top`, both files over `leaf`."""
    empty = await access.visible_assets(
        actors.admin,
        limit=50,
        asset_filter=_in_folder(world.top, FolderDepth.DIRECT),
    )
    assert [item.asset.id for item in empty.items] == []

    here = await access.visible_assets(
        actors.admin,
        limit=50,
        asset_filter=_in_folder(world.leaf, FolderDepth.DIRECT),
    )
    assert {item.asset.id for item in here.items} == {world.solo, world.twin}


async def test_a_direct_folder_scope_narrows_a_wall_of_things_as_well(
    access: Repository, actors: Actors, world: World
) -> None:
    """The entity statements read the depth too: a tag counts nought directly in `top`."""
    under = {
        tag.id: tag.asset_count
        for tag in (await access.list_tags(actors.admin, asset_filter=_in_folder(world.top))).items
    }
    assert under[world.tag] == 1

    directly = {
        tag.id: tag.asset_count
        for tag in (
            await access.list_tags(
                actors.admin, asset_filter=_in_folder(world.top, FolderDepth.DIRECT)
            )
        ).items
    }
    assert directly.get(world.tag, 0) == 0


async def test_a_direct_scope_matches_a_file_whose_other_copy_is_below(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A copy directly in the folder counts, whatever the file's other copies: `twin` gets a second
    copy in `mid`, found although its first is two levels down."""
    await temp_db.execute(
        "INSERT INTO asset_locations "
        "(id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, 'mid/twin.mp4', 'twin.mp4', 1, 1)",
        (new_id(), world.twin, world.root, world.mid),
    )

    page = await access.visible_assets(
        actors.admin, limit=50, asset_filter=_in_folder(world.mid, FolderDepth.DIRECT)
    )
    assert {item.asset.id for item in page.items} == {world.twin}


def test_a_depth_nobody_could_read_is_refused_rather_than_defaulted() -> None:
    """A depth that is neither is refused, never defaulted to the wider subtree."""
    with pytest.raises(ConstraintError):
        AssetFilter(folder_depth="everything")  # type: ignore[arg-type]


def test_every_folder_expansion_reads_the_depth() -> None:
    """Every `in_scope` CTE in the package reads the depth, or one wall would expand the subtree."""
    found = [body for name, body in _access_ctes() if name == "in_scope"]
    assert len(found) >= 8, "the folder expansion has moved; this count is the thing being held"
    for body in found:
        assert ":folder_depth_direct = 0" in body, "a folder expansion that ignores the depth"
