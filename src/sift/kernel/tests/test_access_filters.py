# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a query filtered to: Sites, free text, the orders a page is sorted by, badges, faces
nobody has named, and the writes made on a caller's connection.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import pytest

# Imported for their tables: the unnamed-face condition, `same_music`, the `enriched:` predicates
# and the ledger every concealment writes all read one, and the application always has them.
import sift.slices.faces.schema
import sift.slices.music.schema
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import (
    CONNECTORS,
    MATCHES_NOTHING,
    MAX_RATING,
    MIN_RATING,
    MIN_TEXT_TERM,
    NO_FILTER,
    PARAMETER,
    PREDICATES,
    AllOf,
    AnyOf,
    AssetFilter,
    ConstraintError,
    Effect,
    GrantMark,
    Not,
    ObjectType,
    Repository,
    Role,
    Viewer,
    Where,
    clamp_rating,
    fts_contains,
    fts_match,
    index_assets,
    link_asset_to_site,
)
from sift.kernel.access.catalog import (
    MADE_BY_A_PERSON,
    UNATTRIBUTED,
    add_alias_on,
    attribute_assets_on,
    clear_refusal_on,
    create_person_on,
    detach_person_on,
    file_assets_under_site_on,
    give_person_a_cover_on,
    link_username_to_person,
    people_named_on,
    person_is_bare_on,
    refuse_person_on,
    refused_for,
    remove_alias_on,
    remove_person_on,
    seed_site_username_on,
    unlink_username_from_person_on,
)
from sift.kernel.access.constraints import (
    EntityFacet,
)
from sift.kernel.access.repository import MAX_PAGE_SIZE
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.tests.access_helpers import (
    _EPOCH,
    CASES,
    VAULT_CASES,
    VaultCase,
    apply_grants,
    conceal,
)
from sift.testing.fixtures import Actors, World, create_user, hide

# --- what a query filtered to
#
# "Not filtering on this" is NULL and matches everything; "filtering on nothing" is an empty group
# and matches nothing. Confusing them is a search that widens.


def test_no_filter_constrains_nothing() -> None:
    """The plain grid: a condition every row satisfies, and every parameter bound to "no filter".

    NULL for all but the folder depth, whose unfiltered value is nought ("all the way"). Written as
    the two sets, so a later leaf whose empty value binds a zero fails here.
    """
    where, bound = NO_FILTER.predicate()
    assert where == "1"
    assert bound["folder_depth_direct"] == 0, "an unfiltered read must expand the whole subtree"
    rest = {name: value for name, value in bound.items() if name != "folder_depth_direct"}
    assert set(rest.values()) == {None}


def test_the_impossible_filter_is_a_choice_with_no_options() -> None:
    """A query that could not mean anything is a choice with no options, and emits false: true
    would turn every query the parser could not understand into the whole library."""
    where, _ = MATCHES_NOTHING.predicate()
    assert where == "0"
    assert AssetFilter(where=AllOf()).predicate()[0] == "1"


def test_a_name_that_resolved_to_nobody_is_kept_and_matches_nothing() -> None:
    """A name that resolved to nobody is kept as an empty group and matches nothing; dropped, the
    filter would let the whole library through."""
    where, bound = AssetFilter(where=Where("tags", ())).predicate()
    assert bound["p0"] == "[]"
    assert "json_each(:p0)" in where


def test_group_members_are_normalised_so_two_ways_of_asking_are_one_value() -> None:
    """Order and duplication inside a group carry no meaning, so they carry no difference."""
    one = Where("tags", ("b", "a", "b"))
    other = Where("tags", ("a", "b"))
    assert one == other
    assert AssetFilter(where=one).predicate()[1]["p0"] == '["a", "b"]'


def test_a_kind_of_media_that_names_nothing_is_refused() -> None:
    """An unknown kind is refused: the parser turns one into match-nothing long before here."""
    with pytest.raises(ConstraintError, match="not a kind of media"):
        Where("media_type", ("sculpture",))


def test_a_kind_of_media_that_names_something_is_accepted() -> None:
    """The other side of the refusal above, so that check cannot pass by rejecting everything."""
    accepted = Where("media_type", ("video", "image"))
    assert AssetFilter(where=accepted).predicate()[1]["p0"] == '["image", "video"]'


def test_a_condition_naming_no_template_is_refused() -> None:
    """A key naming no template is refused where the filter was built, never sent to SQLite."""
    with pytest.raises(ConstraintError, match="not a condition"):
        Where("tags; DROP TABLE assets", ("x",))


def test_a_condition_given_the_wrong_number_of_values_is_refused() -> None:
    """A template binds as many values as it has places; a different number is refused."""
    with pytest.raises(ConstraintError, match="binds"):
        Where("rating_min", (4, 5))
    with pytest.raises(ConstraintError, match="binds"):
        Where("favorite", (1,))


def test_a_facet_whose_narrowing_binds_no_array_of_values_is_refused_where_it_is_written() -> None:
    """A facet narrowing with no `{}` would bind nothing and match every row, and one with two would
    splice an unbound name; both are refused at import."""
    with pytest.raises(ConstraintError, match="exactly one array of values"):
        EntityFacet(value="p.country", narrow="p.country IS NOT NULL")
    with pytest.raises(ConstraintError, match="exactly one array of values"):
        EntityFacet(value="p.country", narrow="p.country IN {} OR p.country IN {}")

    allowed = EntityFacet(value="p.country", narrow="p.country IN {}")
    assert allowed.narrow.count("{}") == 1


def test_nothing_but_the_written_conditions_and_the_connectors_reaches_the_statement() -> None:
    """Nothing but the written conditions, their parameter names and the connectors reaches the
    statement: taken apart, the emitted text leaves nothing over, so no typed value got in."""
    crafted = AssetFilter(
        where=AllOf(
            (
                Where("tags", ("') OR 1=1 --", '"; DROP TABLE assets; --')),
                Not(Where("has_people")),
                AnyOf((Where("favorite"), Where("rating_min", (4,)), Where("folder", (0,)))),
                AnyOf(),
            )
        ),
        text="beach",
        folder_scope=(("f1",),),
    )
    residue, bound = crafted.predicate()
    residue = PARAMETER.sub("{}", residue)
    for template in sorted(PREDICATES.values(), key=len, reverse=True):
        residue = residue.replace(re.sub(r"\{\d+\}", "{}", template), "")
    for connector in CONNECTORS:
        residue = residue.replace(connector, "")
    assert residue == ""

    # The values are still there, bound.
    assert bound["p0"] == '["\\"; DROP TABLE assets; --", "\') OR 1=1 --"]'


def test_the_ranking_expression_is_a_list_so_that_it_can_be_empty() -> None:
    """A NULL match expression is a syntax error to the index, so the ranking reads a list, and no
    text is an empty list."""
    assert NO_FILTER.predicate()[1]["text_rank"] is None
    assert AssetFilter(text="beach").predicate()[1]["text_rank"] == '["\\"beach\\""]'


def test_free_text_is_reduced_to_its_words() -> None:
    """Whitespace carries nothing, so two spellings of the same search are one value."""
    assert AssetFilter(text="  beach   sunset ").text == "beach sunset"
    assert AssetFilter(text="   ").text is None
    assert AssetFilter(text="").text is None


def test_a_rating_outside_the_scale_is_clamped() -> None:
    """The ceiling is TEN, what is stored whatever the scale drawn: `rating:99+` is the top ones."""
    assert clamp_rating(99) == MAX_RATING
    assert clamp_rating(9) == 9
    assert clamp_rating(-3) == MIN_RATING
    assert clamp_rating(3) == 3


def test_the_text_term_floor_is_the_one_the_index_has() -> None:
    """Written down, because a caller explaining an empty answer reads it."""
    assert MIN_TEXT_TERM == 3


# --- sites this viewer may know about


async def test_a_site_is_offered_with_the_count_this_viewer_can_see(
    access: Repository, actors: Actors, world: World
) -> None:
    """A site is a grantable object, so which ones somebody may know of is a permission question."""
    assert [(row.id, row.asset_count) for row in await access.suggest_sites(actors.admin)] == [
        (world.site, 1)
    ]


async def test_a_site_nothing_visible_came_from_is_not_offered_at_all(
    access: Repository, actors: Actors, world: World
) -> None:
    """A guest who has been shown nothing is not told which sites the library has content from."""
    assert await access.suggest_sites(actors.guest) == []

    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    assert [(row.id, row.asset_count) for row in await access.suggest_sites(actors.guest)] == [
        (world.site, 1)
    ]


async def test_a_site_is_matched_by_the_start_of_its_name(
    access: Repository, actors: Actors, world: World
) -> None:
    assert [row.id for row in await access.suggest_sites(actors.admin, "sit")] == [world.site]
    assert await access.suggest_sites(actors.admin, "zz") == []


async def test_a_site_prefix_treats_a_wildcard_as_a_character(
    access: Repository, actors: Actors, world: World
) -> None:
    """`%` and `_` are wildcards to LIKE and ordinary characters in a name somebody typed."""
    assert await access.suggest_sites(actors.admin, "%") == []
    assert await access.suggest_sites(actors.admin, "_") == []


async def test_a_site_list_needs_at_least_one_row_and_is_capped(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The cap is tested with more sites than the cap, or it passes with the cap deleted."""
    with pytest.raises(ValueError, match="at least one row"):
        await access.suggest_sites(actors.admin, limit=0)

    for number in range(MAX_PAGE_SIZE + 5):
        await temp_db.execute(
            "INSERT INTO sites (id, name) VALUES (?, ?)", (new_id(), f"site-{number:04d}")
        )
    assert len(await access.suggest_sites(actors.admin, limit=10_000)) == MAX_PAGE_SIZE


# A site's `kind` is not declared; the column is read by nothing (see `schema.py`).


# --- free text on its way into the index


def test_free_text_becomes_a_match_expression_of_literals() -> None:
    """Every term is quoted, so text from a search box is DATA to FTS5 and never grammar."""
    assert fts_match("beach") == '"beach"'
    assert fts_match("beach sunset") == '"beach" AND "sunset"'
    assert fts_match('say "hi"') == '"say" AND """hi"""'
    # `NEAR` is four characters, so it stays here quoted as letters; `OR` is two, so it goes to the
    # scan.
    assert fts_match("NEAR OR beach") == '"NEAR" AND "beach"'
    assert fts_contains("NEAR OR beach") == '["OR"]'


def test_no_free_text_is_not_the_same_as_free_text_that_found_nothing() -> None:
    """None means the query is not filtering on text at all, and the statement reads it that way."""
    assert fts_match(None) is None
    assert fts_match("") is None
    assert fts_match("   ") is None


def test_a_term_too_short_for_the_index_goes_to_the_scan_instead(index: object = None) -> None:
    """A term shorter than the trigram index's three characters goes to a scan, never dropped:
    dropping would widen the search."""
    assert fts_match("ja") is None
    assert fts_contains("ja") == '["ja"]'

    assert fts_match("beach ja") == '"beach"'
    assert fts_contains("beach ja") == '["ja"]'

    assert fts_contains("beach sunset") is None


def test_a_short_term_has_its_wildcards_made_literal() -> None:
    """`%` and `_` are made literal: an unescaped underscore would match every asset."""
    assert fts_contains("_") == '["\\\\_"]'
    assert fts_contains("%") == '["\\\\%"]'
    assert fts_contains("a_") == '["a\\\\_"]'


def test_something_that_is_not_text_is_removed_rather_than_escaped() -> None:
    """A NUL or a lone surrogate cannot be quoted and occurs in no name, so it is dropped."""
    assert fts_match("bea\x00ch") == '"beach"'
    assert fts_match("beach\ud800") == '"beach"'
    assert fts_match("\x00 \x7f") is None
    assert fts_match("\udcff") is None


def test_text_nobody_could_match_is_refused_rather_than_ignored() -> None:
    """Text that reduces to nothing searchable is refused, so it narrows rather than widens: two
    NULLs would read as not filtering on text at all."""
    with pytest.raises(ConstraintError, match="nothing anything could be matched by"):
        AssetFilter(text="\x01\x02")
    with pytest.raises(ConstraintError, match="nothing anything could be matched by"):
        AssetFilter(text="\udcff")

    # Whitespace only is no text at all, as an empty box is.
    assert AssetFilter(text="   ").text is None
    assert AssetFilter(text="ja").text == "ja"


async def test_an_asset_with_nowhere_left_to_be_is_not_shown(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Removing a library takes its files off the screen; the asset rows stay, so re-adding the
    folder reconnects their tags, ratings and views."""
    before = await access.visible_assets(actors.admin)
    assert world.solo in {item.asset.id for item in before.items}

    await temp_db.execute("DELETE FROM asset_locations WHERE asset_id = ?", (world.solo,))

    after = await access.visible_assets(actors.admin)
    assert world.solo not in {item.asset.id for item in after.items}
    assert after.total == before.total - 1, "the count went with the row"


@pytest.mark.parametrize("case", VAULT_CASES, ids=[case.name for case in VAULT_CASES])
async def test_the_hidden_page_is_exactly_what_the_ordinary_page_left_out(
    case: VaultCase,
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
) -> None:
    """The Hidden screen shows the complement, and nothing but the complement, for every way a file
    can be concealed. As set arithmetic: the two pages add up to the whole library exactly once."""
    await conceal(temp_db, world, case, actors)
    unlocked = replace(actors.admin, show_hidden=True)
    everything = {world.solo, world.twin, world.loose}

    plain = await access.visible_assets(actors.admin, limit=50)
    ordinary = {item.asset.id for item in plain.items}
    hidden = {
        item.asset.id
        for item in (await access.visible_assets(unlocked, limit=50, hidden_only=True)).items
    }

    assert hidden == everything - ordinary, "the hidden page is not the complement of the grid"
    assert hidden, "this case conceals nothing, so it proves nothing about hiding"
    assert not (hidden & ordinary), "a file appeared on both pages at once"


@pytest.mark.parametrize("case", VAULT_CASES, ids=[case.name for case in VAULT_CASES])
async def test_asking_for_hidden_files_reveals_nothing_while_the_vault_is_shut(
    case: VaultCase,
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
) -> None:
    """`hidden_only` filters and never widens: with the vault shut it comes back EMPTY, not refused
    (the one bit the vault keeps back), and its total does not count what it withheld."""
    await conceal(temp_db, world, case, actors)

    page = await access.visible_assets(actors.admin, limit=50, hidden_only=True)

    assert page.items == [], "a shut vault handed over concealed rows for the asking"
    assert page.total == 0, "a shut vault counted what it would not show"


async def test_a_guest_asking_for_hidden_files_is_told_about_none_of_them(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """For a guest, who can never unlock, `hidden_only` is permanently inert."""
    await conceal(temp_db, world, VAULT_CASES[0], actors)

    page = await access.visible_assets(actors.guest, limit=50, hidden_only=True)

    assert page.items == []
    assert page.total == 0


async def _sortable(temp_db: Database) -> dict[str, str]:
    """Three visible assets that differ on every axis the sort offers, by a memorable name."""
    root_id = new_id()
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (root_id, "root", "/library", _EPOCH),
    )
    # name / added_at / size_bytes / duration_ms
    rows = [
        ("alpha", "Anvil.mp4", 300, 500, 90_000),
        ("beta", "Marble.mp4", 100, 900, 10_000),
        ("gamma", "Zephyr.mp4", 200, 100, 50_000),
    ]
    ids: dict[str, str] = {}
    for name, filename, added_at, size_bytes, duration_ms in rows:
        asset_id = new_id()
        ids[name] = asset_id
        await temp_db.execute(
            "INSERT INTO assets "
            "(id, identity, media_type, original_filename, size_bytes, duration_ms, added_at) "
            "VALUES (?, ?, 'video', ?, ?, ?, ?)",
            (asset_id, f"digest-{name}", filename, size_bytes, duration_ms, added_at),
        )
        await temp_db.execute(
            "INSERT INTO asset_locations "
            "(id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
            (new_id(), asset_id, root_id, filename, filename, _EPOCH, _EPOCH),
        )
    return ids


async def _order(access: Repository, viewer: Viewer, sort: str) -> list[str]:
    page = await access.visible_assets(viewer, limit=50, sort=sort)
    return [item.asset.id for item in page.items]


@pytest.mark.parametrize(
    ("sort", "order"),
    [
        # alpha added_at=300 size=500 duration=90000; beta 100/900/10000; gamma 200/100/50000.
        ("newest", ["alpha", "gamma", "beta"]),
        ("oldest", ["beta", "gamma", "alpha"]),
        ("name_az", ["alpha", "beta", "gamma"]),  # Anvil, Marble, Zephyr
        ("name_za", ["gamma", "beta", "alpha"]),
        ("longest", ["alpha", "gamma", "beta"]),  # duration 90000, 50000, 10000
        ("shortest", ["beta", "gamma", "alpha"]),
        ("largest", ["beta", "alpha", "gamma"]),  # size 900, 500, 100
        ("smallest", ["gamma", "alpha", "beta"]),
    ],
)
async def test_a_page_comes_back_in_the_order_asked_for(
    access: Repository, actors: Actors, temp_db: Database, sort: str, order: list[str]
) -> None:
    """Every sort the API offers arranges the same three files its own way."""
    ids = await _sortable(temp_db)

    got = await _order(access, actors.admin, sort)

    assert got == [ids[name] for name in order]


async def test_an_unknown_sort_falls_back_to_the_default_rather_than_erroring(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """The repository treats an unknown sort as the default; the router refuses it before here, but
    the resolver must not depend on that."""
    ids = await _sortable(temp_db)

    got = await _order(access, actors.admin, "by-vibes")

    assert got == await _order(access, actors.admin, "newest")
    assert set(got) == set(ids.values())


async def _opinion(
    temp_db: Database, viewer: Viewer, asset_id: str, favorite: int, rating: int | None
) -> None:
    """One viewer's heart and stars on one file."""
    await temp_db.execute(
        "INSERT INTO asset_user_state (asset_id, user_id, favorite, rating, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (asset_id, viewer.id, favorite, rating, _EPOCH),
    )


async def test_the_heart_then_the_stars_order_a_page_by_this_viewers_own(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """Hearted first, then most stars with the unrated last, each then newest; another's are not."""
    ids = await _sortable(temp_db)
    await _opinion(temp_db, actors.admin, ids["gamma"], 1, None)
    await _opinion(temp_db, actors.admin, ids["beta"], 0, 5)
    await _opinion(temp_db, actors.admin, ids["alpha"], 0, 2)
    await _opinion(temp_db, actors.guest, ids["alpha"], 1, 9)

    favorite = await _order(access, actors.admin, "favorite")
    rating = await _order(access, actors.admin, "rating")

    assert favorite == [ids["gamma"], ids["alpha"], ids["beta"]]
    assert rating == [ids["beta"], ids["alpha"], ids["gamma"]]


async def test_a_file_with_no_duration_sinks_rather_than_leading_longest(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """NULLS LAST keeps an image's missing duration, or an unrecorded size, off the front."""
    ids = await _sortable(temp_db)
    imageless = new_id()
    root = await temp_db.fetch_one("SELECT id FROM library_roots LIMIT 1")
    assert root is not None
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, original_filename, added_at) "
        "VALUES (?, ?, 'image', 'photo.jpg', ?)",
        (imageless, "digest-image", _EPOCH),
    )
    await temp_db.execute(
        "INSERT INTO asset_locations "
        "(id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, NULL, 'photo.jpg', 'photo.jpg', ?, ?)",
        (new_id(), imageless, root["id"], _EPOCH, _EPOCH),
    )

    longest = await _order(access, actors.admin, "longest")

    assert longest[-1] == imageless, "a durationless file led the longest sort"
    assert set(longest) == set(ids.values()) | {imageless}


# --- ordering by how well the text matched


async def _rankable(temp_db: Database) -> dict[str, str]:
    """Two files a search matches, where the better match (`surf.mp4`) is the OLDER one, so
    relevance and date disagree."""
    root_id = new_id()
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (root_id, "root", "/library", _EPOCH),
    )
    files = {
        "close": ("surf.mp4", _EPOCH),
        "distant": ("a_long_afternoon_of_surfing_and_walking_and_talking.mp4", _EPOCH + 1_000),
    }
    ids: dict[str, str] = {}
    for name, (filename, added) in files.items():
        asset_id = new_id()
        ids[name] = asset_id
        await temp_db.execute(
            "INSERT INTO assets (id, identity, media_type, original_filename, added_at) "
            "VALUES (?, ?, 'video', ?, ?)",
            (asset_id, f"digest-{name}", filename, added),
        )
        await temp_db.execute(
            "INSERT INTO asset_locations "
            "(id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
            (new_id(), asset_id, root_id, filename, filename, _EPOCH, _EPOCH),
        )
    await index_assets(temp_db, rebuild=True)
    return ids


async def test_a_text_search_can_be_ordered_by_how_well_it_matched(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """Closest first, a different order from newest first."""
    ids = await _rankable(temp_db)
    searched = AssetFilter(text="surf")

    closest = await access.visible_assets(actors.admin, asset_filter=searched, sort="relevance")
    assert [item.asset.id for item in closest.items] == [ids["close"], ids["distant"]]

    # By date the same two come back the other way round.
    newest = await access.visible_assets(actors.admin, asset_filter=searched, sort="newest")
    assert [item.asset.id for item in newest.items] == [ids["distant"], ids["close"]]


async def test_ordering_by_relevance_with_nothing_to_rank_is_the_ordinary_order(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """With no text every row ties and the grid's newest-first order holds, without a 500."""
    ids = await _rankable(temp_db)

    ranked = await access.visible_assets(actors.admin, sort="relevance")
    assert [item.asset.id for item in ranked.items] == [ids["distant"], ids["close"]]
    assert ranked.total == 2

    newest = await access.visible_assets(actors.admin, sort="newest")
    assert [item.asset.id for item in ranked.items] == [item.asset.id for item in newest.items]


async def test_a_file_the_text_did_not_match_sorts_behind_the_ones_it_did(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """An unmatched row has no score, and NULLS LAST keeps it from leading the page."""
    ids = await _rankable(temp_db)

    page = await access.visible_assets(
        actors.admin, asset_filter=AssetFilter(where=AllOf(), text=None), sort="relevance"
    )
    assert {item.asset.id for item in page.items} == set(ids.values())


# --- what a badge says
#
# `_asset_marks` runs the resolver's ladder for every user at once, so it is held to the resolver:
# if a file resolves visible for anybody, the badge says shared.


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
async def test_the_badge_agrees_with_the_resolver(
    case: dict[str, Any],
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
) -> None:
    await apply_grants(access, world, actors.guest.id, case["grants"], temp_db)

    reachable = []
    if case.get("grants_for_other_user"):
        stranger = await create_user(temp_db, Role.GUEST)
        await apply_grants(access, world, stranger.id, case["grants_for_other_user"], temp_db)
        reachable.append(stranger)

    asset_id = world.object_id(case["asset"])
    assert asset_id is not None

    anybody = case["guest"]
    for viewer in reachable:
        anybody = anybody or await access.can_view(viewer, asset_id)

    marks = await access._asset_marks([asset_id])
    assert marks.get(asset_id, GrantMark(shared=False, restricted=False)).shared is anybody


async def test_a_file_inside_a_restricted_folder_is_marked_restricted(
    access: Repository, actors: Actors, world: World
) -> None:
    """A restrict on a folder three levels up badges the file inside it."""
    await access.grant(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.RESTRICT)

    mark = (await access._asset_marks([world.solo]))[world.solo]
    assert mark.restricted is True
    assert mark.shared is False
    assert mark.restricted_here is False


async def test_a_file_shared_by_its_root_is_marked_shared_from_above(
    access: Repository, actors: Actors, world: World
) -> None:
    await access.grant(ObjectType.ROOT, world.root, actors.guest.id, Effect.SHARE)

    mark = (await access._asset_marks([world.solo]))[world.solo]
    assert (mark.shared, mark.shared_here) == (True, False)


async def test_a_decision_made_on_the_file_says_so(
    access: Repository, actors: Actors, world: World
) -> None:
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)

    mark = (await access._asset_marks([world.solo]))[world.solo]
    assert (mark.shared, mark.shared_here) == (True, True)


async def test_a_restrict_on_the_folder_beats_a_share_on_a_file_inside_it(
    access: Repository, actors: Actors, world: World
) -> None:
    """A restrict is absolute: a clip shared inside a restricted folder is concealed and badged
    restricted, and the share stays recorded for when the restrict comes off."""
    await access.grant(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.RESTRICT)
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)

    mark = (await access._asset_marks([world.solo]))[world.solo]
    assert (mark.shared, mark.restricted) == (False, True)
    assert mark.shared_here is True
    assert await access.can_view(actors.guest, world.solo) is False
    assert await access.can_view(actors.admin, world.solo) is True

    await access.revoke(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.RESTRICT)
    assert await access.can_view(actors.guest, world.solo) is True


async def test_a_share_on_a_sub_folder_does_not_undo_a_restrict_above_it(
    access: Repository, actors: Actors, world: World
) -> None:
    """A folder shared inside a restricted one hands over nothing."""
    await access.grant(ObjectType.FOLDER, world.top, actors.guest.id, Effect.RESTRICT)
    await access.grant(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.SHARE)

    assert await access.can_view(actors.guest, world.solo) is False
    assert await access.can_view_folder(actors.guest, world.leaf) is False
    assert await access.can_view(actors.admin, world.solo) is True
    folder_mark = (await access._folder_marks([world.leaf]))[world.leaf]
    assert (folder_mark.shared, folder_mark.restricted) == (False, True)


async def test_two_users_disagreeing_are_both_reported(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """One guest let in and another kept out: the shared and restricted flags are independent."""
    stranger = await create_user(temp_db, Role.GUEST)
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    await access.grant(ObjectType.FOLDER, world.leaf, stranger.id, Effect.RESTRICT)

    mark = (await access._asset_marks([world.solo]))[world.solo]
    assert (mark.shared, mark.restricted) == (True, True)


async def test_a_file_nobody_has_said_anything_about_has_no_mark(
    access: Repository, world: World
) -> None:
    # Absent rather than a row of falses, the shape the grid reads.
    assert await access._asset_marks([world.solo, world.twin]) == {}


async def test_marks_are_asked_for_in_one_read_and_answer_only_what_was_asked(
    access: Repository, actors: Actors, world: World
) -> None:
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    await access.grant(ObjectType.ITEM, world.twin, actors.guest.id, Effect.RESTRICT)

    assert set(await access._asset_marks([world.solo])) == {world.solo}
    assert set(await access._asset_marks([world.solo, world.twin])) == {world.solo, world.twin}


async def test_an_id_that_is_not_an_id_is_not_asked_about(access: Repository) -> None:
    # Anything that could not have been minted here is dropped before the statement.
    assert await access._asset_marks(["../../etc/passwd", ""]) == {}


async def test_only_an_admin_is_told_what_has_been_shared(
    access: Repository, actors: Actors, world: World
) -> None:
    """A guest is told nothing about grants, even those about them: their screen IS the answer."""
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)

    assert await access.visible_marks(actors.guest, ObjectType.ITEM, [world.solo]) == {}
    assert set(await access.visible_marks(actors.admin, ObjectType.ITEM, [world.solo])) == {
        world.solo
    }


async def test_a_tag_is_marked_by_what_was_said_about_the_tag(
    access: Repository, actors: Actors, world: World
) -> None:
    await access.grant(ObjectType.TAG, world.tag, actors.guest.id, Effect.SHARE)

    marks = await access.visible_marks(actors.admin, ObjectType.TAG, [world.tag])
    assert marks[world.tag].shared is True


async def test_a_folder_inside_a_shared_folder_is_marked_from_above(
    access: Repository, actors: Actors, world: World
) -> None:
    """Sharing a parent folder badges the child shared, hollow: not undoable from there."""
    await access.grant(ObjectType.FOLDER, world.top, actors.guest.id, Effect.SHARE)

    marks = await access._folder_marks([world.top, world.mid])
    assert (marks[world.top].shared, marks[world.top].shared_here) == (True, True)
    assert (marks[world.mid].shared, marks[world.mid].shared_here) == (True, False)


async def test_the_folder_badge_agrees_with_the_folder_resolver(
    access: Repository, actors: Actors, world: World
) -> None:
    """Folder badges are held to `visible_folders`, which decides what a guest is shown."""
    await access.grant(ObjectType.ROOT, world.root, actors.guest.id, Effect.SHARE)
    await access.grant(ObjectType.FOLDER, world.mid, actors.guest.id, Effect.RESTRICT)

    every = [world.top, world.mid, world.leaf, world.other]
    marks = await access._folder_marks(every)
    seen = {folder.id for folder in await access.visible_folders(actors.guest)}

    for folder_id in every:
        mark = marks.get(folder_id, GrantMark(shared=False, restricted=False))
        assert mark.shared is (folder_id in seen), folder_id

    assert marks[world.leaf].restricted is True
    assert marks[world.leaf].restricted_here is False


async def test_a_folder_nobody_has_said_anything_about_has_no_mark(
    access: Repository, world: World
) -> None:
    assert await access._folder_marks([world.top, world.leaf]) == {}


async def test_a_file_from_a_site_with_no_username_is_still_filed_under_that_site(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A site read off an address with no username in it still gets a username row, blank: every
    question about a site reaches the file through `asset_usernames`."""
    site_id = await link_asset_to_site(
        temp_db, asset_id=world.loose, site="bunkr", made=MADE_BY_A_PERSON
    )

    sites = {site.id: site for site in await access.suggest_sites(actors.admin)}
    assert site_id in sites
    assert sites[site_id].asset_count == 1
    rows = await temp_db.fetch_all(
        "SELECT a.name AS name FROM asset_usernames AS link "
        "JOIN usernames AS a ON a.id = link.username_id WHERE link.asset_id = ?",
        (world.loose,),
    )
    assert [row["name"] for row in rows] == [UNATTRIBUTED], (
        "a username was invented for a file whose address never named one"
    )


async def test_a_second_file_from_the_same_site_joins_the_one_row_rather_than_making_another(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Two files off one album host are one site."""
    first = await link_asset_to_site(
        temp_db, asset_id=world.loose, site="bunkr", made=MADE_BY_A_PERSON
    )
    second = await link_asset_to_site(
        temp_db, asset_id=world.twin, site="bunkr", made=MADE_BY_A_PERSON
    )

    assert first == second
    sites = {site.id: site for site in await access.suggest_sites(actors.admin)}
    assert sites[first].asset_count == 2
    rows = await temp_db.fetch_all("SELECT id FROM usernames WHERE site_id = ?", (first,))
    assert len(rows) == 1, "a second nameless username row was made for the same site"


# --- ordering by what a model made of the words


async def test_a_search_can_be_ordered_by_what_a_model_thought_of_it(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """The model's files and distances arrive as data; nothing in the statement knows the index."""
    ids = await _rankable(temp_db)
    by_meaning = AssetFilter(neighbours=((ids["distant"], 0.10), (ids["close"], 0.90)))

    page = await access.visible_assets(actors.admin, asset_filter=by_meaning, sort="similarity")

    assert [item.asset.id for item in page.items] == [ids["distant"], ids["close"]]


async def test_the_model_orders_and_never_narrows(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """A file the model said nothing about is still in the answer, behind the ones it did."""
    ids = await _rankable(temp_db)
    named_one_only = AssetFilter(neighbours=((ids["close"], 0.10),))

    page = await access.visible_assets(actors.admin, asset_filter=named_one_only, sort="similarity")

    assert [item.asset.id for item in page.items] == [ids["close"], ids["distant"]]
    assert page.total == 2


async def test_ordering_by_meaning_with_nothing_to_rank_is_the_ordinary_order(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """With the feature off, or no models, every file ties and the page is the ordinary grid."""
    ids = await _rankable(temp_db)

    ranked = await access.visible_assets(actors.admin, sort="similarity")

    assert [item.asset.id for item in ranked.items] == [ids["distant"], ids["close"]]
    assert ranked.total == 2

    newest = await access.visible_assets(actors.admin, sort="newest")
    assert [item.asset.id for item in ranked.items] == [item.asset.id for item in newest.items]


async def test_a_model_cannot_show_somebody_a_file_they_may_not_see(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """A model naming every file changes the ORDER a guest sees and not the SET."""
    ids = await _rankable(temp_db)
    everything = AssetFilter(neighbours=tuple((asset_id, 0.1) for asset_id in ids.values()))

    admin_page = await access.visible_assets(
        actors.admin, asset_filter=everything, sort="similarity"
    )
    guest_page = await access.visible_assets(
        actors.guest, asset_filter=everything, sort="similarity"
    )

    assert len(admin_page.items) == 2
    assert guest_page.items == []


# --- files carrying a face nobody has named
#
# One leaf inside the permission rules, so it can only filter what the viewer may already see.

UNNAMED_FACE = AssetFilter(where=Where("has_unnamed_face"))


async def _face_on(database: Database, asset_id: str, *, status: str = "open") -> str:
    pile_id, track_id = new_id(), new_id()
    await database.execute(
        "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at) "
        "VALUES (?, ?, X'00', 1, 0, 0)",
        (pile_id, status),
    )
    await database.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
        "pile_id, created_at) VALUES (?, ?, 0, 0, 1, 1.0, ?, 0)",
        (track_id, asset_id, pile_id),
    )
    return pile_id


async def test_an_unnamed_face_finds_the_file_that_has_one(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await _face_on(temp_db, world.solo)

    page = await access.visible_assets(actors.admin, asset_filter=UNNAMED_FACE)

    assert {item.asset.id for item in page.items} == {world.solo}
    assert page.total == 1


async def test_a_face_somebody_has_named_is_not_outstanding(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await _face_on(temp_db, world.solo)
    await temp_db.execute("UPDATE face_tracks SET person_id = ?", (world.person,))

    assert (await access.visible_assets(actors.admin, asset_filter=UNNAMED_FACE)).items == []


async def test_a_group_somebody_set_aside_does_not_come_back_as_work(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Faces in an ignored group are not offered again."""
    await _face_on(temp_db, world.solo, status="ignored")

    assert (await access.visible_assets(actors.admin, asset_filter=UNNAMED_FACE)).items == []


async def test_it_composes_with_the_other_filters(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await _face_on(temp_db, world.solo)
    await _face_on(temp_db, world.twin)

    both = AssetFilter(where=AllOf((Where("has_unnamed_face"), Where("tags", (world.tag,)))))
    page = await access.visible_assets(actors.admin, asset_filter=both)

    assert {item.asset.id for item in page.items} == {world.solo}

    neither = AssetFilter(where=AllOf((Where("has_unnamed_face"), Not(Where("has_unnamed_face")))))
    assert (await access.visible_assets(actors.admin, asset_filter=neither)).items == []


@pytest.mark.parametrize("case", VAULT_CASES, ids=[case.name for case in VAULT_CASES])
async def test_an_unnamed_face_never_reveals_a_concealed_file(
    case: VaultCase,
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
) -> None:
    """Every way a file can be concealed, asked about through this filter, as an admin.

    A face is on every file, so a concealed one going missing is the rules' doing.
    """
    for asset_id in (world.solo, world.twin, world.loose):
        await _face_on(temp_db, asset_id)
    await conceal(temp_db, world, case, actors)

    plain = await access.visible_assets(actors.admin, limit=50)
    visible = {item.asset.id for item in plain.items}
    assert visible != {world.solo, world.twin, world.loose}, "hiding this concealed no file at all"

    asked = await access.visible_assets(actors.admin, limit=50, asset_filter=UNNAMED_FACE)

    assert {item.asset.id for item in asked.items} == visible
    assert asked.total == len(visible)


async def test_one_persons_folder_files_with_a_face_still_unnamed(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The count under a person's faces: filed from a folder with a face waiting unnamed, and equal
    to the Files wall's own total."""
    for asset_id, source in ((world.solo, "folder"), (world.twin, None), (world.loose, "folder")):
        await temp_db.execute(
            "INSERT OR REPLACE INTO asset_people (asset_id, person_id, source) VALUES (?, ?, ?)",
            (asset_id, world.person, source),
        )
    await _face_on(temp_db, world.solo)
    await _face_on(temp_db, world.twin)
    await _face_on(temp_db, world.loose, status="ignored")
    asked = AssetFilter(where=Where("unnamed_face", (world.person,)))

    page = await access.visible_assets(actors.admin, asset_filter=asked)

    assert {item.asset.id for item in page.items} == {world.solo}
    assert await access.count_visible(actors.admin, asked) == 1
    await temp_db.execute(
        "UPDATE face_tracks SET person_id = ? WHERE asset_id = ?", (world.person, world.solo)
    )
    assert await access.count_visible(actors.admin, asked) == 0


# --- the groups of unclaimed faces, counted and paged in SQL where the permission rule lives


async def test_the_waiting_count_counts_groups_rather_than_faces(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Two faces of one person in one file are one group waiting, not two."""
    pile = await _face_on(temp_db, world.solo)
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
        "pile_id, created_at) VALUES (?, ?, 0, 0, 1, 1.0, ?, 0)",
        (new_id(), world.twin, pile),
    )

    assert (await access.waiting_piles(actors.admin, "open", limit=0, offset=0))[1] == 1


async def test_a_group_set_aside_is_not_waiting(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await _face_on(temp_db, world.solo, status="ignored")

    assert (await access.waiting_piles(actors.admin, "open", limit=0, offset=0))[1] == 0
    assert (await access.waiting_piles(actors.admin, "ignored", limit=0, offset=0))[1] == 1


async def test_a_group_made_only_of_concealed_files_is_neither_counted_nor_listed(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A group hidden from the viewer is left out of the wall and its total alike."""
    hidden = await _face_on(temp_db, world.loose)
    await hide(temp_db, "asset", world.loose, actors.admin.id)
    shown = await _face_on(temp_db, world.solo)

    page, total = await access.waiting_piles(actors.admin, "open", limit=10, offset=0)
    assert [pile for pile, _ in page] == [shown]
    assert total == 1
    assert await access.waiting_pile_position(actors.admin, "open", hidden) is None


async def test_a_page_of_waiting_groups_says_how_many_faces_each_one_shows(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Groups are ordered by the faces the viewer MAY SEE, from one statement with their counts."""
    # The quiet one is made FIRST, so id order disagrees with the order under test.
    quiet = await _face_on(temp_db, world.loose)
    busy = await _face_on(temp_db, world.solo)
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
        "pile_id, created_at) VALUES (?, ?, 0, 0, 1, 1.0, ?, 0)",
        (new_id(), world.twin, busy),
    )
    assert quiet < busy, "the ids have to disagree with the answer or this proves nothing"

    page, total = await access.waiting_piles(actors.admin, "open", limit=10, offset=0)

    assert page == [(busy, 2), (quiet, 1)]
    assert total == 2, "the total is over the same set the page is a page of"


async def test_the_page_of_waiting_groups_is_a_page_and_its_total_is_not(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The pager reads the total, not the page's length."""
    for asset_id in (world.solo, world.twin, world.loose):
        await _face_on(temp_db, asset_id)

    page, total = await access.waiting_piles(actors.admin, "open", limit=1, offset=1)

    assert len(page) == 1
    assert total == 3


async def test_asking_where_a_group_sits_answers_nothing_for_one_that_is_not_there(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A group that never existed, one this viewer sees nothing of, and text that is not an id get
    the same answer."""
    first = await _face_on(temp_db, world.solo)
    second = await _face_on(temp_db, world.twin)

    assert await access.waiting_pile_position(actors.admin, "open", first) in (0, 1)
    assert await access.waiting_pile_position(actors.admin, "open", second) in (0, 1)
    assert {
        await access.waiting_pile_position(actors.admin, "open", first),
        await access.waiting_pile_position(actors.admin, "open", second),
    } == {0, 1}
    assert await access.waiting_pile_position(actors.admin, "open", new_id()) is None
    assert await access.waiting_pile_position(actors.admin, "open", "not-an-id") is None


# --- the stash-box writes, on a caller's connection
#
# Attributing a folder is several writes that land together, and the write guard is not
# reentrant, so these take a connection the caller opened.


async def test_the_connection_taking_writes_do_what_their_siblings_do(
    access: Repository, world: World, temp_db: Database
) -> None:
    async with temp_db.write() as connection:
        assert await people_named_on(connection, "nerith") == []
        person_id = await create_person_on(connection, "nerith", made=MADE_BY_A_PERSON)
        assert person_id is not None
        assert await people_named_on(connection, "nerith") == [person_id]

        assert await create_person_on(connection, "   ", made=MADE_BY_A_PERSON) is None
        assert await people_named_on(connection, "  ") == []

        written = await attribute_assets_on(
            connection, asset_ids=[world.solo, world.twin], person_id=person_id
        )
        assert written == 2
        # Idempotent, so confirming the same folder twice is a no-op.
        again = await attribute_assets_on(connection, asset_ids=[world.solo], person_id=person_id)
        assert again == 0

        assert await add_alias_on(connection, person_id=person_id, alias="Nerith Reyd") is True
        assert await add_alias_on(connection, person_id=person_id, alias="nerith reyd") is False
        assert await add_alias_on(connection, person_id=person_id, alias="   ") is False

        _, username_id = await seed_site_username_on(
            connection, site="Quillhouse", name="nerith", made=MADE_BY_A_PERSON
        )
        assert (
            await link_username_to_person(connection, username_id=username_id, person_id=person_id)
            is True
        )

    assert (
        await access.resolve_alias_targets(Viewer(id="x", role=Role.ADMIN), "nerith")
    ) is not None


async def test_a_person_with_no_picture_is_given_one_and_a_person_with_one_keeps_it(
    world: World, temp_db: Database
) -> None:
    """A person gets a cover only when they have none: a folder agreeing with a name does not
    replace a choice. The condition is in the STATEMENT, so the second call changes nothing."""
    async with temp_db.write() as connection:
        person_id = await create_person_on(connection, "nerith", made=MADE_BY_A_PERSON)
        assert person_id is not None

        assert (
            await give_person_a_cover_on(connection, person_id=person_id, asset_id=world.solo)
            is True
        )
        assert (
            await give_person_a_cover_on(connection, person_id=person_id, asset_id=world.twin)
            is False
        )

        # An empty half is nobody, as `people_named_on` answers a blank word.
        assert await give_person_a_cover_on(connection, person_id="", asset_id=world.solo) is False
        assert await give_person_a_cover_on(connection, person_id=person_id, asset_id="") is False

    row = await temp_db.fetch_one("SELECT cover_asset_id FROM people WHERE id = ?", (person_id,))
    assert row is not None
    assert str(row["cover_asset_id"]) == world.solo


async def test_a_person_whose_picture_was_uploaded_is_not_given_a_file_instead(
    world: World, temp_db: Database
) -> None:
    """Having a cover asks about both pointers: an uploaded picture wins (`kernel/covers.py`)."""
    async with temp_db.write() as connection:
        person_id = await create_person_on(connection, "nerith", made=MADE_BY_A_PERSON)
        assert person_id is not None
        await connection.execute(
            "UPDATE people SET cover_upload_id = ? WHERE id = ?", ("an-upload", person_id)
        )

        assert (
            await give_person_a_cover_on(connection, person_id=person_id, asset_id=world.solo)
            is False
        )

    row = await temp_db.fetch_one(
        "SELECT cover_asset_id, cover_upload_id FROM people WHERE id = ?", (person_id,)
    )
    assert row is not None
    assert row["cover_asset_id"] is None
    assert str(row["cover_upload_id"]) == "an-upload"


async def test_filing_assets_under_a_site_reaches_them_through_a_username(
    world: World, temp_db: Database
) -> None:
    """A site recorded without a username row would leave the file findable by nothing."""
    async with temp_db.write() as connection:
        site_id = await file_assets_under_site_on(
            connection, asset_ids=[world.solo, world.twin], site="Sunsetter", made=MADE_BY_A_PERSON
        )

    row = await temp_db.fetch_one("SELECT id FROM sites WHERE name = ?", ("Sunsetter",))
    assert row is not None
    assert str(row["id"]) == site_id

    linked = await temp_db.fetch_all(
        "SELECT aa.asset_id FROM asset_usernames aa JOIN usernames ac ON ac.id = aa.username_id "
        "WHERE ac.site_id = ? AND ac.name = ?",
        (site_id, UNATTRIBUTED),
    )
    assert {str(one["asset_id"]) for one in linked} == {world.solo, world.twin}

    async with temp_db.write() as connection:
        await file_assets_under_site_on(
            connection, asset_ids=[world.solo], site="Sunsetter", made=MADE_BY_A_PERSON
        )
    rows = await temp_db.fetch_all("SELECT id FROM usernames WHERE site_id = ?", (site_id,))
    assert len(rows) == 1


async def test_a_filing_says_which_files_it_wrote_and_a_file_already_filed_is_not_among_them(
    world: World, temp_db: Database
) -> None:
    """`landed` lists only the links this call made, so an undo leaves earlier ones."""
    first: list[tuple[str, str]] = []
    async with temp_db.write() as connection:
        await file_assets_under_site_on(
            connection,
            asset_ids=[world.solo],
            site="Sunsetter",
            made=MADE_BY_A_PERSON,
            landed=first,
        )
    assert [asset for asset, _ in first] == [world.solo]

    again: list[tuple[str, str]] = []
    async with temp_db.write() as connection:
        await file_assets_under_site_on(
            connection,
            asset_ids=[world.solo, world.twin],
            site="Sunsetter",
            made=MADE_BY_A_PERSON,
            landed=again,
        )
    assert [asset for asset, _ in again] == [world.twin]
    assert {username for _, username in again} == {username for _, username in first}


async def test_a_username_that_already_names_somebody_is_not_reassigned(
    world: World, temp_db: Database
) -> None:
    """A folder name fills a gap and never overwrites an answer made on a People screen."""
    async with temp_db.write() as connection:
        first = await create_person_on(connection, "one", made=MADE_BY_A_PERSON)
        second = await create_person_on(connection, "two", made=MADE_BY_A_PERSON)
        assert first is not None and second is not None
        _, username_id = await seed_site_username_on(
            connection, site="Quillhouse", name="shared", made=MADE_BY_A_PERSON
        )
        assert await link_username_to_person(connection, username_id=username_id, person_id=first)
        assert not await link_username_to_person(
            connection, username_id=username_id, person_id=second
        )


# --- taking a person off a file, remembered: a folder is re-read whenever its faces change


async def test_a_person_taken_off_a_file_is_written_down(world: World, temp_db: Database) -> None:
    async with temp_db.write() as connection:
        await refuse_person_on(
            connection, asset_id=world.solo, person_id=world.person, now=1_700_000_000
        )

    assert await refused_for(temp_db, world.person, [world.solo, world.twin]) == {world.solo}


async def test_taking_the_same_person_off_twice_is_one_record(
    world: World, temp_db: Database
) -> None:
    """Removing the same person twice does not fail on the key already written."""
    async with temp_db.write() as connection:
        await refuse_person_on(connection, asset_id=world.solo, person_id=world.person, now=1)
        await refuse_person_on(connection, asset_id=world.solo, person_id=world.person, now=2)

    assert await refused_for(temp_db, world.person, [world.solo]) == {world.solo}


async def test_attributing_the_person_again_clears_the_record(
    world: World, temp_db: Database
) -> None:
    """Putting the person back clears the record, or it would go on blocking a pass."""
    async with temp_db.write() as connection:
        await refuse_person_on(connection, asset_id=world.solo, person_id=world.person, now=1)
        await clear_refusal_on(connection, asset_id=world.solo, person_id=world.person)

    assert await refused_for(temp_db, world.person, [world.solo]) == set()


async def test_a_record_belongs_to_one_person_and_one_file(world: World, temp_db: Database) -> None:
    """A record names one person and one file: two of each, so it cannot be right by accident."""
    async with temp_db.write() as connection:
        other_person = await create_person_on(connection, "somebody else", made=MADE_BY_A_PERSON)
        assert other_person is not None
        await refuse_person_on(connection, asset_id=world.solo, person_id=world.person, now=1)
        await refuse_person_on(connection, asset_id=world.twin, person_id=other_person, now=1)

    assert await refused_for(temp_db, world.person, [world.solo, world.twin]) == {world.solo}
    assert await refused_for(temp_db, other_person, [world.solo, world.twin]) == {world.twin}


async def test_asking_which_files_were_refused_out_of_none_asks_the_database_nothing(
    world: World, temp_db: Database
) -> None:
    """An empty list is an empty answer, not an `IN ()` syntax error."""
    assert await refused_for(temp_db, world.person, []) == set()


# --- taking a decision back, filtered to what the decision wrote, never to what is there now


async def test_the_undo_writes_take_back_exactly_what_a_decision_did(
    access: Repository, world: World, temp_db: Database
) -> None:
    """An undo filters to the rows the decision wrote: a file that already carried the person keeps
    them, an existing person survives, and a username pointed elsewhere since stays there."""
    async with temp_db.write() as connection:
        person_id = await create_person_on(connection, "nerith", made=MADE_BY_A_PERSON)
        assert person_id is not None
        await attribute_assets_on(
            connection, asset_ids=[world.solo, world.twin], person_id=person_id
        )
        await add_alias_on(connection, person_id=person_id, alias="Nerith Reyd")
        _, username_id = await seed_site_username_on(
            connection, site="Quillhouse", name="nerith", made=MADE_BY_A_PERSON
        )
        await link_username_to_person(connection, username_id=username_id, person_id=person_id)

        assert await person_is_bare_on(connection, person_id) is False

        assert (
            await detach_person_on(
                connection, asset_ids=[world.solo, world.twin], person_id=person_id
            )
            == 2
        )
        # Idempotent, so pressing undo twice takes nothing extra.
        assert await detach_person_on(connection, asset_ids=[world.solo], person_id=person_id) == 0

        assert await remove_alias_on(connection, person_id=person_id, alias="Nerith Reyd") is True
        assert await remove_alias_on(connection, person_id=person_id, alias="Nerith Reyd") is False
        assert await remove_alias_on(connection, person_id=person_id, alias="   ") is False

        assert (
            await unlink_username_from_person_on(
                connection, username_id=username_id, person_id=person_id
            )
            is True
        )
        assert (
            await unlink_username_from_person_on(
                connection, username_id=username_id, person_id=person_id
            )
            is False
        )

        # An undo removes a person it invented only when nothing is left on them.
        assert await person_is_bare_on(connection, person_id) is True
        assert await remove_person_on(connection, person_id) is True
        assert await remove_person_on(connection, person_id) is False
        assert await people_named_on(connection, "nerith") == []


async def test_a_person_somebody_has_since_used_survives_the_undo(
    access: Repository, world: World, temp_db: Database
) -> None:
    """A person with files put on them by hand since the decision is not removed by its undo."""
    async with temp_db.write() as connection:
        person_id = await create_person_on(connection, "nerith", made=MADE_BY_A_PERSON)
        assert person_id is not None
        await attribute_assets_on(connection, asset_ids=[world.solo], person_id=person_id)
        await attribute_assets_on(connection, asset_ids=[world.loose], person_id=person_id)

        await detach_person_on(connection, asset_ids=[world.solo], person_id=person_id)

        assert await person_is_bare_on(connection, person_id) is False
        assert await people_named_on(connection, "nerith") == [person_id]


async def test_a_username_pointed_at_somebody_else_is_not_unlinked_by_an_undo(
    access: Repository, world: World, temp_db: Database
) -> None:
    """An undo clears only the link to the person the decision named."""
    async with temp_db.write() as connection:
        named = await create_person_on(connection, "nerith", made=MADE_BY_A_PERSON)
        somebody_else = await create_person_on(connection, "tamsin", made=MADE_BY_A_PERSON)
        assert named is not None and somebody_else is not None
        _, username_id = await seed_site_username_on(
            connection, site="Quillhouse", name="nerith", made=MADE_BY_A_PERSON
        )
        await link_username_to_person(connection, username_id=username_id, person_id=somebody_else)

        assert (
            await unlink_username_from_person_on(
                connection, username_id=username_id, person_id=named
            )
            is False
        )

        assert (
            await link_username_to_person(
                connection, username_id=username_id, person_id=somebody_else
            )
            is False
        ), "the username was unlinked from the person it really named"
