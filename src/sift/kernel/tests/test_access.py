# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the resolver is allowed to say yes to.

The truth table is the deliverable. It is written out by hand in
`fixtures/access_truth_table.json` rather than generated, because a table produced by running
the resolver would agree with the resolver no matter what the resolver did.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest

# Imported for their tables: the unnamed-face condition, `same_music`, the `enriched:` predicates
# and the ledger every concealment writes all read one, and the application always has them.
import sift.slices.faces.schema
import sift.slices.music.schema
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import (
    SORT_KEYS,
    AccessError,
    AssetFilter,
    Effect,
    ObjectType,
    Repository,
    Role,
    Viewer,
    Where,
)
from sift.kernel.access.related import related_filter
from sift.kernel.access.repository import SHUFFLE_MODULUS
from sift.kernel.access.repository.assets import shuffle_of
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.sorting import sort_key
from sift.kernel.tests.access_helpers import (
    CASES,
    _a_shelf_of_files,
    _person,
    _shuffled,
    apply_grants,
)
from sift.testing.fixtures import Actors, World, create_user, hide

# --- the truth table ------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
async def test_the_truth_table(
    case: dict[str, Any],
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
) -> None:
    await apply_grants(access, world, actors.guest.id, case["grants"], temp_db)

    if case.get("grants_for_other_user"):
        stranger = await create_user(temp_db, Role.GUEST)
        await apply_grants(access, world, stranger.id, case["grants_for_other_user"], temp_db)

    asset_id = world.object_id(case["asset"])
    assert asset_id is not None

    assert await access.can_view(actors.guest, asset_id) is case["guest"]
    assert await access.can_view(actors.admin, asset_id) is case["admin"]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
async def test_the_list_agrees_with_the_check(
    case: dict[str, Any],
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
) -> None:
    """The grid and the single-asset fetch, one statement with different parameters, never
    disagree: a detail endpoint that did would serve an asset the grid withholds."""
    await apply_grants(access, world, actors.guest.id, case["grants"], temp_db)
    if case.get("grants_for_other_user"):
        stranger = await create_user(temp_db, Role.GUEST)
        await apply_grants(access, world, stranger.id, case["grants_for_other_user"], temp_db)

    asset_id = world.object_id(case["asset"])
    assert asset_id is not None

    page = await access.visible_assets(actors.guest, limit=50)
    listed = {item.asset.id for item in page.items}

    assert (asset_id in listed) is case["guest"]
    assert page.total == len(page.items)


def test_the_truth_table_covers_every_object_type() -> None:
    """A grant type nobody wrote a case for is a grant type nobody tested."""
    covered = {grant[0] for case in CASES for grant in case["grants"]}
    assert covered == {object_type.value for object_type in ObjectType}


# --- the parts of the rule worth stating on their own ---------------------------------------


async def test_a_guest_with_no_grants_sees_an_empty_library(
    access: Repository, actors: Actors, world: World
) -> None:
    page = await access.visible_assets(actors.guest)
    assert page.items == []
    assert page.total == 0


async def test_an_admin_sees_everything(access: Repository, actors: Actors, world: World) -> None:
    page = await access.visible_assets(actors.admin)
    assert {item.asset.id for item in page.items} == {world.solo, world.twin, world.loose}
    assert page.total == 3


async def test_the_count_counts_what_the_page_is_a_page_of(
    access: Repository, actors: Actors, world: World
) -> None:
    """Fetch one row of three and the total still says three: visibility is in the query, not
    applied to the rows afterwards."""
    await access.grant(ObjectType.GLOBAL, None, actors.guest.id, Effect.SHARE)
    await access.grant(ObjectType.FOLDER, world.other, actors.guest.id, Effect.RESTRICT)

    # `twin` has a copy in `other`, so the guest may see two of the three.
    first = await access.visible_assets(actors.guest, limit=1, offset=0)
    second = await access.visible_assets(actors.guest, limit=1, offset=1)
    third = await access.visible_assets(actors.guest, limit=1, offset=2)

    assert first.total == 2
    assert second.total == 2
    assert len(first.items) == 1
    assert len(second.items) == 1
    assert third.items == []

    seen = {first.items[0].asset.id, second.items[0].asset.id}
    assert seen == {world.solo, world.loose}


async def test_the_total_is_right_for_a_page_past_the_end(
    access: Repository, actors: Actors, world: World
) -> None:
    """A page past the last row reports the same total a full one would."""
    page = await access.visible_assets(actors.admin, limit=10, offset=99)
    assert page.items == []
    assert page.total == 3


async def test_a_page_size_over_the_ceiling_is_clamped(
    access: Repository, actors: Actors, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ask far over the ceiling gets the ceiling, and the count still reports every row."""
    # Patched where it is defined: `repository.read_files` is where the clamp is looked up.
    monkeypatch.setattr("sift.kernel.access.repository.read_files.MAX_PAGE_SIZE", 2)

    page = await access.visible_assets(actors.admin, limit=1000)

    assert len(page.items) == 2
    assert page.total == 3


async def test_one_seed_is_one_shuffle(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The same seed asked twice is the same order, so a link opens the arrangement it was sent
    in."""
    await _a_shelf_of_files(temp_db, world, 20)

    assert await _shuffled(access, actors, 12345, limit=50) == await _shuffled(
        access, actors, 12345, limit=50
    )


async def test_a_fresh_seed_is_a_fresh_shuffle(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Six seeds, six orders: pressing Random again must change what is on screen."""
    await _a_shelf_of_files(temp_db, world, 20)

    orders = {tuple(await _shuffled(access, actors, seed, limit=50)) for seed in range(6)}

    assert len(orders) == 6


async def test_a_shuffle_holds_still_while_the_page_is_turned(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The page taken in three goes is the page taken whole: the order is a function of the row,
    since `ORDER BY random()` would repeat and lose files across pages."""
    await _a_shelf_of_files(temp_db, world, 20)

    whole = await _shuffled(access, actors, 777, limit=50)
    turned = [
        asset_id
        for start in (0, 10, 20)
        for asset_id in await _shuffled(access, actors, 777, limit=10, offset=start)
    ]

    assert turned == whole
    assert len(whole) == 23


async def test_a_shuffle_nobody_named_holds_still_too(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A Random wall with no seed in its address falls back to the day and still pages: minting a
    seed on arrival would rewrite the address of every wall walked onto."""
    await _a_shelf_of_files(temp_db, world, 20)

    whole = await _shuffled(access, actors, None, limit=50)
    turned = [
        asset_id
        for start in (0, 10, 20)
        for asset_id in await _shuffled(access, actors, None, limit=10, offset=start)
    ]

    assert turned == whole


async def test_a_position_under_a_shuffle_is_a_position_in_that_shuffle(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Where a file sits is read under the SAME seed the page was built with, or a link lands near
    the right file rather than at it."""
    await _a_shelf_of_files(temp_db, world, 20)

    listed = await _shuffled(access, actors, 4242, limit=50)

    for index, asset_id in enumerate(listed):
        assert await access.position_of(actors.admin, asset_id, sort="random", seed=4242) == index


async def test_no_seed_walks_the_library_in_the_order_it_was_added(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A shuffle is mixed, not a stride: a multiply-and-remainder key walks neighbouring rowids for
    some seeds, and the steps between neighbours would take at most three values (the three-gap
    theorem)."""
    await _a_shelf_of_files(temp_db, world, 40)
    rows = await temp_db.fetch_all("SELECT id FROM assets ORDER BY rowid")
    added = {str(row["id"]): at for at, row in enumerate(rows)}

    for seed in range(50):
        order = [added[asset_id] for asset_id in await _shuffled(access, actors, seed, limit=50)]
        steps = [two - one for one, two in itertools.pairwise(order)]
        assert len(set(steps)) > 3, f"seed {seed} laid the shelf out as a lattice"
        neighbours = sum(1 for step in steps if abs(step) == 1)
        assert neighbours < len(order) // 4, (
            f"seed {seed} walked the shelf in the order it was added"
        )


def test_a_seed_bigger_than_the_space_is_folded_rather_than_trusted() -> None:
    """A seed is folded under 2^32, or SQLite multiplies in floating point and the order stops being
    total."""
    assert shuffle_of(SHUFFLE_MODULUS) == shuffle_of(0)
    assert shuffle_of(SHUFFLE_MODULUS + 99) == shuffle_of(99)
    for seed in (2**62, 2**200, SHUFFLE_MODULUS - 1):
        assert all(0 <= half < 2**32 for half in shuffle_of(seed))
    assert len({shuffle_of(seed) for seed in range(200)}) == 200, "two seeds share a shuffle"


async def test_a_folded_seed_is_the_shuffle_it_folded_onto(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The folded seed and the original shuffle the same way."""
    await _a_shelf_of_files(temp_db, world, 20)

    assert await _shuffled(access, actors, 99, limit=50) == await _shuffled(
        access, actors, SHUFFLE_MODULUS + 99, limit=50
    )


# --- where a file sits, which is what an address carries
#
# A page number does not survive being sent; the FILE does, and `position_of` turns it back into a
# place to start reading. Position and page come from one statement, so they cannot disagree.


@pytest.mark.parametrize("sort", sorted(SORT_KEYS))
async def test_a_position_is_where_the_page_actually_puts_it(
    sort: str, access: Repository, actors: Actors, world: World
) -> None:
    """Under every sort Sift offers, the position of a file is its index in the listing, the
    fall-through sorts included."""
    page = await access.visible_assets(actors.admin, limit=50, sort=sort)
    listed = [item.asset.id for item in page.items]
    assert len(listed) == 3

    for index, asset_id in enumerate(listed):
        assert await access.position_of(actors.admin, asset_id, sort=sort) == index


async def test_a_position_reverses_when_the_order_does(
    access: Repository, actors: Actors, world: World
) -> None:
    """Oldest-first puts a file as far from the end as newest-first from the start; every file here
    arrived at the same time, so the tiebreaker decides both."""
    total = (await access.visible_assets(actors.admin)).total

    for asset_id in (world.solo, world.twin, world.loose):
        newest = await access.position_of(actors.admin, asset_id, sort="newest")
        oldest = await access.position_of(actors.admin, asset_id, sort="oldest")
        assert newest is not None and oldest is not None
        assert newest + oldest == total - 1


async def test_a_position_counts_only_what_this_viewer_may_see(
    access: Repository, actors: Actors, world: World
) -> None:
    """A guest's position is a place in the guest's library, or it would count the files skipped."""
    await access.grant(ObjectType.GLOBAL, None, actors.guest.id, Effect.SHARE)
    await access.grant(ObjectType.FOLDER, world.other, actors.guest.id, Effect.RESTRICT)

    page = await access.visible_assets(actors.guest, limit=50)
    listed = [item.asset.id for item in page.items]
    assert len(listed) == 2

    for index, asset_id in enumerate(listed):
        assert await access.position_of(actors.guest, asset_id) == index


async def test_a_position_is_nothing_for_a_file_this_viewer_may_not_see(
    access: Repository, actors: Actors, world: World
) -> None:
    assert await access.position_of(actors.guest, world.solo) is None


async def test_a_position_is_nothing_for_a_file_that_is_not_there(
    access: Repository, actors: Actors, world: World
) -> None:
    """An unknown id gets the same answer a forbidden file does, so a link is no existence
    oracle."""
    assert await access.position_of(actors.admin, new_id()) is None
    assert await access.position_of(actors.guest, world.solo) is None


async def test_a_position_is_nothing_for_something_that_is_not_an_id(
    access: Repository, actors: Actors, world: World
) -> None:
    """Nonsense is refused before the statement, which would read NULL as "no file named"."""
    for nonsense in ("", "   ", "../../etc/passwd", "1 OR 1=1", "null", "None"):
        assert await access.position_of(actors.admin, nonsense) is None


async def test_a_persons_position_is_a_place_in_the_whole_wall_and_not_in_a_list_of_one(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """A person is ranked among everybody: the id names the row to RANK, never a filter, or every
    person would come back at position zero. So the person asked about is not first."""
    await _person(temp_db, "Ada Lovelace")
    second = await _person(temp_db, "Zelda Fitzgerald")

    wall = [one.id for one in (await access.suggest_people(actors.admin)).items]
    assert wall.index(second) == 1, "the wanted person has to be second, or the check is vacuous"

    assert await access.position_of_person(actors.admin, second) == 1


async def test_a_persons_position_is_taken_in_the_wall_the_file_filter_narrowed_to(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A tag's People tab is a filtered wall, and a position is a place in THAT wall.

    Bryn is on a file without the tag and sorts before the person asked about. She is on a file,
    not none, since an empty person would be missing from the unfiltered ranking anyway.
    """
    other = await _person(temp_db, "Bryn Calloway")
    wanted = await _person(temp_db, "Fenn Marchetti")
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (world.solo, wanted)
    )
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (world.twin, other)
    )
    tagged = related_filter(tag=world.tag)

    whole = [one.id for one in (await access.suggest_people(actors.admin, sort="name_az")).items]
    narrowed = [
        one.id
        for one in (
            await access.suggest_people(actors.admin, sort="name_az", asset_filter=tagged)
        ).items
    ]
    assert whole.index(wanted) != narrowed.index(wanted), "the check would be vacuous"

    at = await access.position_of_person(actors.admin, wanted, sort="name_az", asset_filter=tagged)
    assert at == narrowed.index(wanted)


async def test_a_persons_position_is_nothing_for_something_that_is_not_an_id(
    access: Repository, actors: Actors, world: World
) -> None:
    """The People wall refuses nonsense too: its statement reads a NULL id as "no filter"."""
    for nonsense in ("", "   ", "../../etc/passwd", "1 OR 1=1", "null", "None"):
        assert await access.position_of_person(actors.admin, nonsense) is None

    # A real id answers, so the refusal is the guard.
    assert await access.position_of_person(actors.admin, world.person) is not None


async def _photo_set_row(temp_db: Database, name: str) -> str:
    """One empty photo set, still on an admin's wall; both tie at nought, so the name decides."""
    photo_set_id = new_id()
    await temp_db.execute(
        "INSERT INTO photo_sets (id, name, name_sort, created_at) VALUES (?, ?, ?, 0)",
        (photo_set_id, name, sort_key(name)),
    )
    return photo_set_id


async def test_a_photo_sets_position_is_a_place_in_the_whole_wall_and_not_in_a_list_of_one(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """The People wall's property on Photo Sets: the id is ranked, never filtered to."""
    await _photo_set_row(temp_db, "Blue umbrellas")
    second = await _photo_set_row(temp_db, "Zinc rooftops")

    wall = [one.id for one in (await access.list_photo_sets(actors.admin, limit=50)).items]
    assert wall.index(second) == 1, "the wanted set has to be second, or the check is vacuous"

    assert await access.position_of_photo_set(actors.admin, second) == 1


async def test_a_photo_sets_position_is_nothing_for_one_this_viewer_may_not_be_shown(
    access: Repository, actors: Actors, temp_db: Database, world: World
) -> None:
    """A concealed set and an id never minted are INDISTINGUISHABLE: a number would confirm it."""
    assert await access.position_of_photo_set(actors.admin, world.photo_set) is not None

    await hide(temp_db, "photo_set", world.photo_set, actors.admin.id)

    assert await access.position_of_photo_set(actors.admin, world.photo_set) is None
    assert await access.position_of_photo_set(actors.admin, new_id()) is None


async def test_a_photo_sets_position_is_nothing_for_something_that_is_not_an_id(
    access: Repository, actors: Actors, world: World
) -> None:
    """The guard again: the wall's statement reads a NULL id as "no filter"."""
    for nonsense in ("", "   ", "../../etc/passwd", "1 OR 1=1", "null", "None"):
        assert await access.position_of_photo_set(actors.admin, nonsense) is None

    assert await access.position_of_photo_set(actors.admin, world.photo_set) is not None


# --- the four other walls that carry their position
#
# Each is a second statement with its own bound parameters, held to the Photo Sets trio: the id is
# ranked and never filtered to, a concealed row has no position, and nonsense is refused.


async def _tag_row(temp_db: Database, name: str) -> str:
    """One empty tag, still on an admin's wall."""
    tag_id = new_id()
    await temp_db.execute(
        "INSERT INTO tags (id, name, name_sort, created_at) VALUES (?, ?, ?, 0)",
        (tag_id, name, sort_key(name)),
    )
    return tag_id


async def test_a_tags_position_is_a_place_in_the_whole_wall_and_not_in_a_list_of_one(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """The Photo Sets wall's property, for tags."""
    await _tag_row(temp_db, "Blue umbrellas")
    second = await _tag_row(temp_db, "Zinc rooftops")

    wall = [one.id for one in (await access.list_tags(actors.admin, limit=50)).items]
    assert wall.index(second) == 1, "the wanted tag has to be second, or the check is vacuous"

    assert await access.position_of_tag(actors.admin, second) == 1


async def test_a_tags_position_is_nothing_for_one_this_viewer_may_not_be_shown(
    access: Repository, actors: Actors, temp_db: Database, world: World
) -> None:
    """A concealed tag and an id never minted answer the same."""
    assert await access.position_of_tag(actors.admin, world.tag) is not None

    await hide(temp_db, "tag", world.tag, actors.admin.id)

    assert await access.position_of_tag(actors.admin, world.tag) is None
    assert await access.position_of_tag(actors.admin, new_id()) is None


async def test_a_tags_position_is_nothing_for_something_that_is_not_an_id(
    access: Repository, actors: Actors, world: World
) -> None:
    """The guard: a NULL id reads as "no filter"."""
    for nonsense in ("", "   ", "../../etc/passwd", "1 OR 1=1", "null", "None"):
        assert await access.position_of_tag(actors.admin, nonsense) is None

    assert await access.position_of_tag(actors.admin, world.tag) is not None


async def _collection_row(temp_db: Database, name: str) -> str:
    """One collection with nothing in it."""
    collection_id = new_id()
    await temp_db.execute(
        "INSERT INTO collections (id, name, name_sort, created_at) VALUES (?, ?, ?, 0)",
        (collection_id, name, sort_key(name)),
    )
    return collection_id


async def test_a_collections_position_is_a_place_in_the_whole_wall_and_not_in_a_list_of_one(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """The Photo Sets wall's property, for collections."""
    await _collection_row(temp_db, "Almond mornings")
    second = await _collection_row(temp_db, "Zephyr evenings")

    wall = [one.id for one in (await access.list_collections(actors.admin, limit=50)).items]
    assert wall.index(second) == 1, (
        "the wanted collection has to be second, or the check is vacuous"
    )

    assert await access.position_of_collection(actors.admin, second) == 1


async def test_a_collections_position_is_nothing_for_one_this_viewer_may_not_be_shown(
    access: Repository, actors: Actors, temp_db: Database, world: World
) -> None:
    """A concealed collection and an id never minted answer the same."""
    assert await access.position_of_collection(actors.admin, world.collection) is not None

    await hide(temp_db, "collection", world.collection, actors.admin.id)

    assert await access.position_of_collection(actors.admin, world.collection) is None
    assert await access.position_of_collection(actors.admin, new_id()) is None


async def test_a_collections_position_is_nothing_for_something_that_is_not_an_id(
    access: Repository, actors: Actors, world: World
) -> None:
    """The guard, on the collections statement."""
    for nonsense in ("", "   ", "../../etc/passwd", "1 OR 1=1", "null", "None"):
        assert await access.position_of_collection(actors.admin, nonsense) is None

    assert await access.position_of_collection(actors.admin, world.collection) is not None


async def _username_row(
    temp_db: Database, world: World, name: str, *, filing: str, person: str | None = None
) -> str:
    """One username on the world's Site, filing one file, joined to `person` when given."""
    username_id = new_id()
    await temp_db.execute(
        "INSERT INTO usernames (id, site_id, name, name_sort, person_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, 0)",
        (username_id, world.site, name, sort_key(name), person),
    )
    await temp_db.execute(
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
        (filing, username_id),
    )
    return username_id


async def test_a_usernames_position_is_a_place_in_the_list_it_was_asked_of(
    access: Repository, actors: Actors, temp_db: Database, world: World
) -> None:
    """The waiting queue and the whole wall are two lists: `handle` is third on the wall and second
    in the queue, since the username before it on the wall has a person."""
    await _username_row(temp_db, world, "aardwick", filing=world.twin, person=world.person)
    await _username_row(temp_db, world, "bramble", filing=world.twin)

    wall = [one.id for one in (await access.list_usernames(actors.admin, limit=50)).items]
    queue = [
        one.id
        for one in (await access.list_usernames(actors.admin, limit=50, unattached=True)).items
    ]
    assert (wall.index(world.username), queue.index(world.username)) == (2, 1), (
        "the two lists have to disagree about `handle`, or the check is vacuous"
    )

    assert await access.position_of_username(actors.admin, world.username) == 2
    assert await access.position_of_username(actors.admin, world.username, unattached=True) == 1


async def test_a_usernames_position_is_taken_on_the_wall_narrowed_the_way_it_was(
    access: Repository, actors: Actors, temp_db: Database, world: World
) -> None:
    """Filtered to the files a filter reaches, as the listing is: `handle` files only `solo`, so it
    is not on a wall filtered to `twin` at all, and `bramble` is its first row."""
    bramble = await _username_row(temp_db, world, "bramble", filing=world.twin)
    narrowed = AssetFilter(where=Where("assets", (world.twin,)))

    assert await access.position_of_username(actors.admin, bramble, asset_filter=narrowed) == 0
    assert (
        await access.position_of_username(actors.admin, world.username, asset_filter=narrowed)
        is None
    )


async def test_a_usernames_position_is_nothing_for_one_this_viewer_may_not_be_shown(
    access: Repository, actors: Actors, temp_db: Database, world: World
) -> None:
    """Concealed, never minted and not an id answer the same; hiding the Site takes its
    usernames."""
    assert await access.position_of_username(actors.admin, world.username) is not None
    for nonsense in ("", "   ", "1 OR 1=1", "null"):
        assert await access.position_of_username(actors.admin, nonsense) is None
    assert await access.position_of_username(actors.admin, new_id()) is None

    await hide(temp_db, "site", world.site, actors.admin.id)

    assert await access.position_of_username(actors.admin, world.username) is None


async def _site_row(temp_db: Database, name: str) -> str:
    """One Site with nothing filed under it."""
    site_id = new_id()
    await temp_db.execute(
        "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, ?, ?, 0)",
        (site_id, name, sort_key(name)),
    )
    return site_id


async def test_a_sites_position_is_a_place_in_the_whole_wall_and_not_in_a_list_of_one(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """The Photo Sets wall's property, for Sites."""
    await _site_row(temp_db, "site-0001")
    second = await _site_row(temp_db, "site-0002")

    wall = [one.id for one in (await access.list_sites(actors.admin, limit=50)).items]
    assert wall.index(second) == 1, "the wanted Site has to be second, or the check is vacuous"

    assert await access.position_of_site(actors.admin, second) == 1


async def test_a_sites_position_is_nothing_for_one_this_viewer_may_not_be_shown(
    access: Repository, actors: Actors, temp_db: Database, world: World
) -> None:
    """A concealed Site and an id never minted answer the same."""
    assert await access.position_of_site(actors.admin, world.site) is not None

    await hide(temp_db, "site", world.site, actors.admin.id)

    assert await access.position_of_site(actors.admin, world.site) is None
    assert await access.position_of_site(actors.admin, new_id()) is None


async def test_a_sites_position_is_nothing_for_something_that_is_not_an_id(
    access: Repository, actors: Actors, world: World
) -> None:
    """The guard, on the Sites statement."""
    for nonsense in ("", "   ", "../../etc/passwd", "1 OR 1=1", "null", "None"):
        assert await access.position_of_site(actors.admin, nonsense) is None

    assert await access.position_of_site(actors.admin, world.site) is not None


async def _loop_row(temp_db: Database, asset_id: str, viewer: Viewer, name: str) -> str:
    """A Loop on one file."""
    loop_id = new_id()
    await temp_db.execute(
        "INSERT INTO loops (id, asset_id, start_ms, end_ms, name, created_by, created_at) "
        "VALUES (?, ?, 0, 4000, ?, ?, 0)",
        (loop_id, asset_id, name, viewer.id),
    )
    return loop_id


async def test_a_loops_position_is_a_place_in_the_whole_wall_and_not_in_a_list_of_one(
    access: Repository, actors: Actors, temp_db: Database, world: World
) -> None:
    """The Photo Sets wall's property on Loops, read off the wall's own order, with a check that the
    Loop asked about is not first."""
    await _loop_row(temp_db, world.solo, actors.admin, "Opening stretch")
    await _loop_row(temp_db, world.solo, actors.admin, "Closing stretch")

    wall = [one.id for one in (await access.list_loops(actors.admin, limit=50)).items]
    assert len(wall) >= 2, "two marks have to be on the wall, or the check is vacuous"

    assert await access.position_of_loop(actors.admin, wall[1]) == 1


async def test_a_loops_position_is_nothing_for_a_mark_of_a_file_this_viewer_may_not_see(
    access: Repository, actors: Actors, temp_db: Database, world: World
) -> None:
    """A Loop reaches a user only through its file: concealing the file takes it away, and then it
    answers as an id never minted."""
    marked = await _loop_row(temp_db, world.solo, actors.admin, "Opening stretch")
    assert await access.position_of_loop(actors.admin, marked) is not None

    await hide(temp_db, "asset", world.solo, actors.admin.id)

    assert await access.position_of_loop(actors.admin, marked) is None
    assert await access.position_of_loop(actors.admin, new_id()) is None


async def test_a_loops_position_is_nothing_for_something_that_is_not_an_id(
    access: Repository, actors: Actors, temp_db: Database, world: World
) -> None:
    """The guard, on the Loops statement."""
    marked = await _loop_row(temp_db, world.solo, actors.admin, "Opening stretch")
    for nonsense in ("", "   ", "../../etc/passwd", "1 OR 1=1", "null", "None"):
        assert await access.position_of_loop(actors.admin, nonsense) is None

    assert await access.position_of_loop(actors.admin, marked) is not None


async def test_the_batched_person_read_answers_exactly_what_the_one_at_a_time_form_does(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The batched person read answers exactly as `visible_person` does, for both viewers, a hidden
    person absent rather than blank."""
    second = await _person(temp_db, "Zelda Fitzgerald")
    hidden = await _person(temp_db, "Someone Concealed", hidden_by=actors.admin)
    asked = [world.person, second, hidden, new_id()]

    for viewer in (actors.admin, actors.guest):
        one_at_a_time = {
            person_id: found.name
            for person_id in asked
            if (found := await access.visible_person(viewer, person_id)) is not None
        }
        batched = await access.visible_people(viewer, asked)
        assert {found: one.name for found, one in batched.items()} == one_at_a_time

    # Hidden from the ADMIN, who is shown even empty people, so only the hiding can keep this one.
    assert hidden not in await access.visible_people(actors.admin, asked)
    assert second in await access.visible_people(actors.admin, asked), (
        "an ordinary person with nothing under them is still shown to an admin, or the line "
        "above proves nothing"
    )


async def test_the_batched_person_read_keeps_every_chunk_and_not_just_the_last(
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """More people than one read may ask about, answered as one dictionary: forced small, so a merge
    that assigns rather than updates would show."""
    monkeypatch.setattr("sift.kernel.access.repository.read_people.MAX_PAGE_SIZE", 2)
    everybody = [world.person] + [await _person(temp_db, f"Person {n}") for n in range(4)]

    found = await access.visible_people(actors.admin, everybody)

    assert set(found) == set(everybody), "every chunk is in the answer, not only the last"


async def test_the_batched_person_read_answers_nothing_for_ids_that_name_nobody(
    access: Repository, actors: Actors, world: World
) -> None:
    """Nonsense in the bound array matches nothing and takes nothing else with it: the statement's
    own answer, not a guard."""
    assert await access.visible_people(actors.admin, []) == {}
    assert await access.visible_people(actors.admin, ["", "   ", "1 OR 1=1", "null"]) == {}
    mixed = await access.visible_people(actors.admin, ["1 OR 1=1", world.person])
    assert set(mixed) == {world.person}, "a real id is still answered beside ones that are not"


async def test_a_position_is_a_place_in_the_narrowed_set(
    access: Repository, actors: Actors, world: World
) -> None:
    """A position is filtered as the page is: within the tag `solo` is first, and the other two have
    none, the answer a forbidden file gets."""
    assert await access.position_of(actors.admin, world.solo, tag_id=world.tag) == 0
    assert await access.position_of(actors.admin, world.twin, tag_id=world.tag) is None
    assert await access.position_of(actors.admin, world.loose, tag_id=world.tag) is None


async def test_a_position_and_an_offset_name_the_same_file(
    access: Repository, actors: Actors, world: World
) -> None:
    """Ask where a file is, ask for a page starting there, and the first row is that file."""
    for asset_id in (world.solo, world.twin, world.loose):
        at = await access.position_of(actors.admin, asset_id)
        assert at is not None
        page = await access.visible_assets(actors.admin, limit=1, offset=at)
        assert [item.asset.id for item in page.items] == [asset_id]


async def test_a_corrupt_global_grant_grants_nothing(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A global grant carrying an id is corrupt and never decides: the resolver reads the global
    effect from the null-id group only, so alone it is not blanket visibility."""
    await temp_db.execute(
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at) "
        "VALUES (?, 'global', 'junk', ?, 'share', ?)",
        (new_id(), actors.guest.id, 1),
    )

    assert await access.can_view(actors.guest, world.solo) is False
    assert await access.can_view(actors.guest, world.loose) is False


async def test_a_corrupt_global_share_cannot_override_a_real_restrict(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await access.grant(ObjectType.GLOBAL, None, actors.guest.id, Effect.RESTRICT)
    await temp_db.execute(
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at) "
        "VALUES (?, 'global', 'junk', ?, 'share', ?)",
        (new_id(), actors.guest.id, 1),
    )

    assert await access.can_view(actors.guest, world.solo) is False


async def test_a_deleted_object_takes_its_grants_with_it(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Grants naming a deleted tag are deleted with it: `object_id` names a table by its type, so
    no foreign key cascades."""
    await access.grant(ObjectType.TAG, world.tag, actors.guest.id, Effect.SHARE)
    assert await access.can_view(actors.guest, world.solo) is True

    await access.forget_object(ObjectType.TAG, world.tag)

    assert await access.grants_of(actors.guest.id) == []
    assert await access.can_view(actors.guest, world.solo) is False


async def test_a_deleted_objects_grants_move_only_their_holders_pictures(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Only a user a grant named sees differently once the object goes; nobody else re-fetches."""
    bystander = await create_user(temp_db, Role.GUEST)
    await access.grant(ObjectType.TAG, world.tag, actors.guest.id, Effect.SHARE)

    async def stamps() -> dict[str, int]:
        rows = await temp_db.fetch_all("SELECT id, cache_stamp FROM users")
        return {str(row["id"]): int(row["cache_stamp"]) for row in rows}

    before = await stamps()
    await access.forget_object(ObjectType.TAG, world.tag)
    await access.forget_object(ObjectType.PERSON, world.tag)
    after = await stamps()

    moved = {user for user, stamp in after.items() if stamp != before[user]}
    assert moved == {actors.guest.id}
    assert bystander.id not in moved


async def test_who_one_object_is_shared_with_reads_back_both_effects(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """`grants_on` lists both effects: a panel listing only shares would hide the restricts."""
    second_guest = await create_user(temp_db, Role.GUEST)
    await access.grant(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.SHARE)
    await access.grant(ObjectType.FOLDER, world.leaf, second_guest.id, Effect.RESTRICT)
    await access.grant(ObjectType.FOLDER, world.mid, actors.guest.id, Effect.SHARE)

    on_leaf = await access.grants_on(ObjectType.FOLDER, world.leaf)

    assert {(grant.subject_user_id, grant.effect) for grant in on_leaf} == {
        (actors.guest.id, Effect.SHARE),
        (second_guest.id, Effect.RESTRICT),
    }


async def test_an_object_nobody_was_given_reads_back_empty(
    access: Repository, world: World
) -> None:
    """No grant is private, an empty list, not an error."""
    assert await access.grants_on(ObjectType.FOLDER, world.leaf) == []


async def test_the_global_grant_is_readable_by_object_too(
    access: Repository, actors: Actors, world: World
) -> None:
    """The global object, which has no id, can be asked for by name."""
    await access.grant(ObjectType.GLOBAL, None, actors.guest.id, Effect.SHARE)

    on_global = await access.grants_on(ObjectType.GLOBAL, None)

    assert [grant.subject_user_id for grant in on_global] == [actors.guest.id]


async def test_asking_who_holds_a_grant_that_could_never_exist_is_refused(
    access: Repository, world: World
) -> None:
    """A read describing a grant that cannot be stored is refused, as the write is."""
    with pytest.raises(AccessError):
        await access.grants_on(ObjectType.GLOBAL, world.leaf)
    with pytest.raises(AccessError):
        await access.grants_on(ObjectType.FOLDER, None)


async def test_a_grant_that_could_never_match_is_refused(
    access: Repository, actors: Actors, world: World
) -> None:
    """An inert grant is worse than no grant: the sharing screen would list it as in force."""
    with pytest.raises(AccessError):
        await access.grant(ObjectType.FOLDER, None, actors.guest.id, Effect.SHARE)
    with pytest.raises(AccessError):
        await access.grant(ObjectType.GLOBAL, world.leaf, actors.guest.id, Effect.RESTRICT)


async def test_granting_twice_is_not_an_error(
    access: Repository, actors: Actors, world: World
) -> None:
    first = await access.grant(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.SHARE)
    again = await access.grant(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.SHARE)
    assert first.id == again.id
    assert len(await access.grants_of(actors.guest.id)) == 1


async def test_re_granting_a_global_is_idempotent_too(
    access: Repository, actors: Actors, world: World
) -> None:
    """Granting a global twice is idempotent: SQLite counts every NULL `object_id` as distinct, so
    the partial index has its own conflict target."""
    first = await access.grant(ObjectType.GLOBAL, None, actors.guest.id, Effect.SHARE)
    again = await access.grant(ObjectType.GLOBAL, None, actors.guest.id, Effect.SHARE)
    assert first.id == again.id
    globals_shared = [
        g
        for g in await access.grants_of(actors.guest.id)
        if g.object_type is ObjectType.GLOBAL and g.effect is Effect.SHARE
    ]
    assert len(globals_shared) == 1
