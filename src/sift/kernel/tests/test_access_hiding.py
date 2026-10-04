# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live permissions, hiding, which is personal to a user, and the vault at every site that has
to agree about it.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

# Imported for their tables: the unnamed-face condition, `same_music`, the `enriched:` predicates
# and the ledger every concealment writes all read one, and the application always has them.
import sift.slices.faces.schema
import sift.slices.music.schema
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import (
    AssetFilter,
    Concealment,
    Effect,
    ObjectType,
    Repository,
    Role,
    Where,
    repository,
)
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.tests.access_helpers import (
    SUBTREE,
    VAULT_CASES,
    VaultCase,
    _access_ctes,
    _access_source,
    conceal,
    folder_named,
)
from sift.testing.fixtures import Actors, World, create_user, hide

# --- live permissions -----------------------------------------------------------------------


async def test_revoking_a_share_denies_the_next_request(
    access: Repository, actors: Actors, world: World
) -> None:
    await access.grant(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.SHARE)
    assert await access.can_view(actors.guest, world.solo) is True

    await access.revoke(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.SHARE)

    assert await access.can_view(actors.guest, world.solo) is False


async def test_adding_a_restrict_denies_the_next_request(
    access: Repository, actors: Actors, world: World
) -> None:
    await access.grant(ObjectType.GLOBAL, None, actors.guest.id, Effect.SHARE)
    assert await access.can_view(actors.guest, world.solo) is True

    await access.grant(ObjectType.FOLDER, world.mid, actors.guest.id, Effect.RESTRICT)

    assert await access.can_view(actors.guest, world.solo) is False


async def test_disabling_a_user_kills_their_live_session(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Disabled means disabled now: the viewer is rebuilt from the database on every request."""
    assert await access.load_viewer(actors.guest.id) is not None

    await temp_db.execute("UPDATE users SET disabled = 1 WHERE id = ?", (actors.guest.id,))

    assert await access.load_viewer(actors.guest.id) is None


async def test_a_role_change_applies_at_once(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await temp_db.execute("UPDATE users SET role = 'guest' WHERE id = ?", (actors.admin.id,))

    demoted = await access.load_viewer(actors.admin.id)
    assert demoted is not None
    assert demoted.role is Role.GUEST
    assert await access.can_view(demoted, world.solo) is False


async def test_an_admin_is_the_oldest_enabled_one(access: Repository, temp_db: Database) -> None:
    """A pass running as Sift reads through the oldest enabled admin, the same one every run."""
    first = await create_user(temp_db, Role.ADMIN)
    second = await create_user(temp_db, Role.ADMIN)
    assert await access.an_admin() == first.id

    await temp_db.execute("UPDATE users SET disabled = 1 WHERE id = ?", (first.id,))
    assert await access.an_admin() == second.id


async def test_an_instance_without_an_admin_has_nobody_for_a_pass_to_read_as(
    access: Repository, temp_db: Database
) -> None:
    await create_user(temp_db, Role.GUEST)
    assert await access.an_admin() is None


async def test_a_viewer_for_an_unknown_user_is_nobody(access: Repository) -> None:
    assert await access.load_viewer(new_id()) is None


# --- hiding, which is personal to a user ------------------------------------------------------


async def test_hiding_a_file_conceals_it_from_the_user_that_hid_it(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await hide(temp_db, "asset", world.solo, actors.admin.id)

    page = await access.visible_assets(actors.admin)
    assert world.solo not in {item.asset.id for item in page.items}
    assert page.total == 2
    assert await access.get_asset(actors.admin, world.solo) is None
    assert await access.can_view(actors.admin, world.solo) is False


async def test_actionable_of_separates_a_locked_vault_from_everything_else(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """`actionable_of` sorts a batch into actionable, concealed and unknown.

    Only the concealed pile is the viewer's to fix, a PIN away; a missing file and somebody else's
    stay one pile, or the answer tells a stranger which ids exist.
    """
    await hide(temp_db, "asset", world.solo, actors.admin.id)
    nowhere = new_id()

    answer = await access.actionable_of(actors.admin, [world.twin, world.solo, nowhere])

    assert answer.allowed == (world.twin,)
    assert answer.concealed == (world.solo,)
    assert answer.refused == (nowhere,)
    assert answer.skipped == 2


async def test_actionable_of_keeps_the_callers_order_and_counts_a_repeat_once(
    access: Repository, actors: Actors, world: World
) -> None:
    """Answers keep the asked order, and the same id twice is ONE item."""
    answer = await access.actionable_of(actors.admin, [world.twin, world.solo, world.twin])

    assert answer.allowed == (world.twin, world.solo)


async def test_actionable_of_allows_everything_once_the_vault_is_open(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Opening the vault changes the answer: a concealed row is concealed by the SESSION."""
    await hide(temp_db, "asset", world.solo, actors.admin.id)
    unlocked = replace(actors.admin, show_hidden=True)

    answer = await access.actionable_of(unlocked, [world.twin, world.solo])

    assert answer.concealed == ()
    assert set(answer.allowed) == {world.twin, world.solo}


async def test_actionable_of_treats_a_placeholder_as_out_of_reach_not_as_allowed(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A placeholder tile is on screen and still not actionable: `actionable_of` mirrors the strict
    `open_asset`, since tagging it would reveal it through a count."""
    await hide(temp_db, "asset", world.solo, actors.admin.id)
    keeps_the_tile = replace(actors.admin, concealment=Concealment.PLACEHOLDER)

    answer = await access.actionable_of(keeps_the_tile, [world.solo])

    assert answer.allowed == ()
    assert answer.concealed == (world.solo,)


async def test_memberships_tally_all_five_kinds_over_the_whole_selection(
    access: Repository, world: World, temp_db: Database
) -> None:
    """A picker's memberships come back as one answer about the SET, five kinds in one call, and an
    id carried by two files comes back as two."""
    await temp_db.execute(
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (world.twin, world.tag)
    )

    counted = await access.memberships_of([world.solo, world.twin, world.solo, ""])

    assert counted.tags == {world.tag: 2}
    assert counted.people == {world.person: 1}
    assert counted.sites == {world.site: 1}
    assert counted.collections == {world.collection: 1}
    assert counted.photo_sets == {world.photo_set: 1}


async def test_memberships_add_across_the_pages_a_wide_selection_is_cut_into(
    access: Repository, world: World, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A selection is cut into pages for SQLite's bindings, each page answering for itself only.

    Forced small so a tally that assigns rather than adds would show. Duplicates are dropped BEFORE
    the cut, or one file either side of a boundary would count twice.
    """
    await temp_db.execute(
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (world.twin, world.tag)
    )
    monkeypatch.setattr("sift.kernel.access.repository.read_files.MAX_PAGE_SIZE", 2)

    # `loose` carries nothing; it is here to push `twin` onto a second page.
    across = await access.memberships_of([world.solo, world.loose, world.twin])
    assert across.tags == {world.tag: 2}, "a page answered for the selection instead of for itself"

    repeated = await access.memberships_of([world.solo, world.solo, world.solo])
    assert repeated.tags == {world.tag: 1}, "one file was counted once per mention of it"


async def test_unlocking_reveals_it(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await hide(temp_db, "asset", world.solo, actors.admin.id)
    unlocked = replace(actors.admin, show_hidden=True)

    page = await access.visible_assets(unlocked)
    assert world.solo in {item.asset.id for item in page.items}
    assert await access.open_asset(unlocked, world.solo) is not None


async def test_hiding_a_folder_conceals_what_is_under_it(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await hide(temp_db, "folder", world.mid, actors.admin.id)

    page = await access.visible_assets(actors.admin)
    assert {item.asset.id for item in page.items} == {world.loose}


async def test_hiding_a_root_conceals_what_is_under_it(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await hide(temp_db, "root", world.root, actors.admin.id)

    page = await access.visible_assets(actors.admin)
    assert page.items == []


async def test_one_hidden_copy_conceals_the_file(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Hiding a folder that holds a copy of `twin` conceals the file, not just the copy."""
    await hide(temp_db, "folder", world.other, actors.admin.id)

    page = await access.visible_assets(actors.admin)
    assert world.twin not in {item.asset.id for item in page.items}


async def test_placeholder_mode_shows_the_tile_and_not_the_content(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A locked placeholder comes back from the list, or there is no tile to lock, and never from
    the content path."""
    await hide(temp_db, "asset", world.solo, actors.admin.id)
    viewer = replace(actors.admin, concealment=Concealment.PLACEHOLDER)

    page = await access.visible_assets(viewer)
    concealed = [item for item in page.items if item.asset.id == world.solo]
    assert len(concealed) == 1
    assert concealed[0].concealed is True

    assert await access.open_asset(viewer, world.solo) is None
    assert await access.can_view(viewer, world.solo) is False
    assert await access.locations(viewer, world.solo) == []


async def test_hiding_a_person_conceals_their_assets(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Hiding a person conceals their files for the user who hid them, as hiding a folder does."""
    await hide(temp_db, "person", world.person, actors.admin.id)

    page = await access.visible_assets(actors.admin)
    assert world.solo not in {item.asset.id for item in page.items}
    assert await access.get_asset(actors.admin, world.solo) is None
    assert await access.open_asset(actors.admin, world.solo) is None

    unlocked = replace(actors.admin, show_hidden=True)
    assert await access.open_asset(unlocked, world.solo) is not None


async def test_hiding_a_collection_conceals_its_assets(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await hide(temp_db, "collection", world.collection, actors.admin.id)

    page = await access.visible_assets(actors.admin)
    assert world.solo not in {item.asset.id for item in page.items}
    assert await access.can_view(actors.admin, world.solo) is False


async def test_hiding_a_tag_conceals_what_carries_it(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await hide(temp_db, "tag", world.tag, actors.admin.id)

    page = await access.visible_assets(actors.admin)
    assert world.solo not in {item.asset.id for item in page.items}


async def test_hiding_a_site_conceals_what_came_from_it(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A site reaches a file through two hops: its usernames, and a username on the file."""
    await hide(temp_db, "site", world.site, actors.admin.id)

    page = await access.visible_assets(actors.admin)
    assert world.solo not in {item.asset.id for item in page.items}


# --- and it is personal: what one user hides is plainly there for the next


@pytest.mark.parametrize(
    ("kind", "target"),
    [
        ("asset", "solo"),
        ("folder", "leaf"),
        ("root", "root"),
        ("person", "person"),
        ("collection", "collection"),
        ("tag", "tag"),
        ("site", "site"),
    ],
)
async def test_what_one_user_hides_stays_visible_to_another(
    kind: str,
    target: str,
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
) -> None:
    """Hidden by an admin and shared with a guest, the guest still sees it: to keep something from
    a guest, restrict it."""
    target_id = world.object_id(target)
    assert target_id is not None
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    await hide(temp_db, kind, target_id, actors.admin.id)

    assert await access.can_view(actors.admin, world.solo) is False
    assert await access.can_view(actors.guest, world.solo) is True
    assert world.solo in {
        item.asset.id for item in (await access.visible_assets(actors.guest)).items
    }


async def test_a_guest_hiding_something_does_not_conceal_it_from_the_admin(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A guest can keep a file off their own screen and cannot take it off anybody else's."""
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    await hide(temp_db, "asset", world.solo, actors.guest.id)

    assert await access.can_view(actors.guest, world.solo) is False
    assert await access.can_view(actors.admin, world.solo) is True


async def test_a_share_does_not_reveal_what_the_viewer_hid_themselves(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A share cannot put back on your screen what you chose to hide: the two answer different
    questions."""
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    await hide(temp_db, "asset", world.solo, actors.guest.id)

    assert await access.can_view(actors.guest, world.solo) is False


async def test_a_person_share_does_not_reveal_a_person_the_viewer_hid(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await access.grant(ObjectType.PERSON, world.person, actors.guest.id, Effect.SHARE)
    await hide(temp_db, "person", world.person, actors.guest.id)

    assert await access.can_view(actors.guest, world.solo) is False


async def test_a_hidden_person_shows_a_placeholder_in_placeholder_mode(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """In placeholder mode the tile stays, marked concealed, and the bytes do not come: the flag,
    not only the row's absence, carries the concealment."""
    await hide(temp_db, "person", world.person, actors.admin.id)
    viewer = replace(actors.admin, concealment=Concealment.PLACEHOLDER)

    page = await access.visible_assets(viewer)
    concealed = [item for item in page.items if item.asset.id == world.solo]
    assert len(concealed) == 1
    assert concealed[0].concealed is True
    assert await access.locate(viewer, world.solo) is None


async def test_placeholder_mode_works_for_a_guest_too(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A guest with a share who hides a file gets the same placeholder an admin gets."""
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    await hide(temp_db, "asset", world.solo, actors.guest.id)
    viewer = replace(actors.guest, concealment=Concealment.PLACEHOLDER)

    page = await access.visible_assets(viewer)
    concealed = [item for item in page.items if item.asset.id == world.solo]
    assert len(concealed) == 1
    assert concealed[0].concealed is True
    assert await access.open_asset(viewer, world.solo) is None


async def test_an_asset_in_two_hidden_collections_appears_once(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A file in two hidden collections is one placeholder tile with a total of one: a plain UNION
    ALL would count it twice."""
    second = new_id()
    await temp_db.execute(
        "INSERT INTO collections (id, name, created_at) VALUES (?, 'two', ?)", (second, 1)
    )
    await temp_db.execute(
        "INSERT INTO collection_items (collection_id, asset_id) VALUES (?, ?)",
        (second, world.solo),
    )
    await hide(temp_db, "collection", second, actors.admin.id)
    await hide(temp_db, "collection", world.collection, actors.admin.id)
    viewer = replace(actors.admin, concealment=Concealment.PLACEHOLDER)

    page = await access.visible_assets(viewer)
    assert [item.asset.id for item in page.items].count(world.solo) == 1
    assert page.total == 3


async def test_a_person_nobody_hid_conceals_nothing(
    access: Repository, actors: Actors, world: World
) -> None:
    """Having a person does not conceal a file; only this viewer hiding them does."""
    page = await access.visible_assets(actors.admin)
    assert world.solo in {item.asset.id for item in page.items}


async def test_a_logical_restrict_beats_a_share_on_a_different_membership(
    access: Repository, actors: Actors, world: World
) -> None:
    """A restrict on one membership and a share on another resolve to denied: one logical restrict
    anywhere wins, which a single grant cannot tell from every restrict."""
    await access.grant(ObjectType.TAG, world.tag, actors.guest.id, Effect.RESTRICT)
    await access.grant(ObjectType.COLLECTION, world.collection, actors.guest.id, Effect.SHARE)

    assert await access.can_view(actors.guest, world.solo) is False


async def test_two_logical_shares_still_reveal_the_asset(
    access: Repository, actors: Actors, world: World
) -> None:
    """Shares on two memberships reveal a file: they are not AND-ed."""
    await access.grant(ObjectType.TAG, world.tag, actors.guest.id, Effect.SHARE)
    await access.grant(ObjectType.PERSON, world.person, actors.guest.id, Effect.SHARE)

    assert await access.can_view(actors.guest, world.solo) is True


@pytest.mark.parametrize("case", VAULT_CASES, ids=[case.name for case in VAULT_CASES])
async def test_every_site_conceals_the_same_files(
    case: VaultCase,
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
) -> None:
    """A file the resolver conceals is counted by nothing.

    The viewer is an admin, for whom every grant answers yes, so only concealment removes a row.
    """
    await conceal(temp_db, world, case, actors)

    page = await access.visible_assets(actors.admin, limit=50)
    visible = {item.asset.id for item in page.items}

    # Something was concealed: a rule that conceals nothing agrees with itself perfectly.
    assert visible != {world.solo, world.twin, world.loose}, "hiding this concealed no file at all"

    for name, holds in SUBTREE.items():
        folder = await folder_named(access, actors.admin, world, name)
        if name in case.concealed_folders:
            assert folder is None, f"{name} is hidden and must be gone from the tree"
            continue
        assert folder is not None, f"{name} is not concealed and must still be in the tree"

        expected = sum(1 for asset in holds if world.object_id(asset) in visible)
        assert await access.folder_file_count(actors.admin, folder) == expected

    # The tag suggester copies the rule; `solo` is the only tagged file. A vaulted tag is gone from
    # the list, as a vaulted person is: a name beside a zero says what is kept back.
    counts = {tag.id: tag.asset_count for tag in await access.suggest_tags(actors.admin)}
    if case.kind == "tag":
        assert world.tag not in counts, "a hidden tag is gone from the list entirely"
    else:
        assert counts[world.tag] == (1 if world.solo in visible else 0)

    # The people suggester copies the rule too.
    people_counts = {p.id: p.asset_count for p in (await access.suggest_people(actors.admin)).items}
    if case.kind == "person":
        assert world.person not in people_counts, "a hidden person is gone from the list entirely"
    else:
        assert people_counts[world.person] == (1 if world.solo in visible else 0)

    # A person's position on the People wall answers by id, so a concealed person has NONE ("not for
    # you" and "not there" are one answer), and a visible one agrees with the wall's order.
    listed_people = [p.id for p in (await access.suggest_people(actors.admin)).items]
    where_person = await access.position_of_person(actors.admin, world.person)
    if case.kind == "person":
        assert where_person is None, (
            "a concealed person has a position, which is a way to ask whether they are there"
        )
    else:
        assert where_person == listed_people.index(world.person), (
            "a position disagreed with the order the People wall put the same people in"
        )

    sites = {p.id: p for p in await access.suggest_sites(actors.admin)}
    if case.kind == "site":
        assert world.site not in sites, "a hidden site is gone from the list entirely"
    else:
        assert sites[world.site].asset_count == (1 if world.solo in visible else 0)

    # The usernames wall copies the rule and can COUNT a file nobody may see; `solo` is the only
    # file under this username. Still listed at zero for an admin, as a site is.
    usernames = {one.id: one for one in (await access.list_usernames(actors.admin, limit=50)).items}
    if case.kind == "site":
        # Concealing a site takes its usernames with it.
        assert world.username not in usernames, (
            "a username on a concealed site is still on the wall"
        )
    else:
        assert usernames[world.username].asset_count == (1 if world.solo in visible else 0)

    # The collections lister copies it twice: the count and the cover.
    collections = {c.id: c for c in await access.visible_collections(actors.admin)}
    if case.kind == "collection":
        assert world.collection not in collections, (
            "a hidden collection is gone from the list entirely"
        )
    else:
        held = collections[world.collection]
        assert held.item_count == (1 if world.solo in visible else 0)
        assert held.cover_asset_id == (world.solo if world.solo in visible else None)

    # Naming files outright, as a group of unidentified faces does, is the grid with one more
    # conjunct. Asked for TWO of the three files: `loose` appearing says the ids were ignored, and
    # `solo` or `twin` missing says they replaced the concealment rule instead of joining it.
    asked = {world.solo, world.twin}
    named = await access.visible_assets(
        actors.admin,
        limit=50,
        asset_filter=AssetFilter(where=Where("assets", tuple(asked))),
    )
    assert {item.asset.id for item in named.items} == visible & asked, (
        "asking for files by name did not agree with the grid about what is concealed"
    )
    assert named.total == len(visible & asked), (
        "the count beside a named set counts what the page does not show"
    )

    # A file's position answers by id: a concealed file has NONE, and a visible one agrees with the
    # grid's order, since a position over a wider set would count the files skipped.
    listed = [item.asset.id for item in page.items]
    for asset_id in (world.solo, world.twin, world.loose):
        at = await access.position_of(actors.admin, asset_id)
        if asset_id in visible:
            assert at == listed.index(asset_id), (
                "a position disagreed with the order the grid put the same files in"
            )
        else:
            assert at is None, (
                "a concealed file has a position, which is a way to ask whether it is there"
            )


async def test_a_file_that_is_nowhere_is_counted_by_nothing(
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
) -> None:
    """An asset whose last copy has gone is counted by no site, and is still an asset.

    An asset row outlives its copies, so re-adding a folder reconnects its tags and views. The grid
    draws no tile for a file that is nowhere, and every count must agree with it.
    """
    await temp_db.execute("DELETE FROM asset_locations WHERE asset_id = ?", (world.solo,))

    page = await access.visible_assets(actors.admin, limit=50)
    visible = {item.asset.id for item in page.items}
    assert visible == {world.twin, world.loose}, "the grid still draws a file that is nowhere"

    # The asset row survives: losing a copy must not delete it.
    row = await temp_db.fetch_one("SELECT id FROM assets WHERE id = ?", (world.solo,))
    assert row is not None, "losing the last copy must not delete what Sift knows about the file"

    for name, holds in SUBTREE.items():
        folder = await folder_named(access, actors.admin, world, name)
        assert folder is not None
        expected = sum(1 for asset in holds if world.object_id(asset) in visible)
        assert await access.folder_file_count(actors.admin, folder) == expected

    counts = {tag.id: tag.asset_count for tag in await access.suggest_tags(actors.admin)}
    assert counts[world.tag] == 0, "the tag counts a file the grid will not show"

    people = {p.id: p.asset_count for p in (await access.suggest_people(actors.admin)).items}
    assert people[world.person] == 0, "the person counts a file the grid will not show"

    sites = {p.id: p for p in await access.suggest_sites(actors.admin)}
    assert sites[world.site].asset_count == 0, "the site counts a file the grid will not show"

    held = {c.id: c for c in await access.visible_collections(actors.admin)}[world.collection]
    assert held.item_count == 0, "the collection counts a file the grid will not show"
    # A cover is a picture OF the file.
    assert held.cover_asset_id is None, "a file that is nowhere is still being drawn as a cover"


async def test_a_file_that_is_nowhere_does_not_come_back_when_the_vault_is_open(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Unlocking does not bring back a file whose copies have gone: it was never concealed."""
    await temp_db.execute("DELETE FROM asset_locations WHERE asset_id = ?", (world.solo,))
    unlocked = replace(actors.admin, show_hidden=True)

    page = await access.visible_assets(unlocked, limit=50)
    assert {item.asset.id for item in page.items} == {world.twin, world.loose}

    counts = {tag.id: tag.asset_count for tag in await access.suggest_tags(unlocked)}
    assert counts[world.tag] == 0
    people = {p.id: p.asset_count for p in (await access.suggest_people(unlocked)).items}
    assert people[world.person] == 0


#: The rules that decide who may see a file, which only the stored verdict's maintainer spells; a
#: read joins `viewer_assets` and asks nothing else about permission.
_PERMISSION_PHRASES = (
    "grant_effect(",
    "global_scope(",
    "root_scope(",
    "folder_scope(",
    "location_scope(",
    "logical_vault(",
    "COALESCE(lo.denied, 0)",
    "COALESCE(ph.denied, 0)",
    "COALESCE(ph.vaulted, 0)",
)


def test_no_read_statement_carries_a_permission_rule() -> None:
    """No read statement carries a permission rule: the resolver is written once, in `visibility`,
    and every listing joins `viewer_assets`."""
    package = Path(repository.__file__).parent
    for path in sorted(package.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for phrase in _PERMISSION_PHRASES:
            assert phrase not in source, (
                f"{path.name} spells a permission rule ({phrase!r}); reads join viewer_assets"
            )


def test_every_listing_of_files_reads_the_stored_verdict() -> None:
    """Every statement here that lists files for a `:viewer` joins `viewer_assets`."""
    for name, body in _access_ctes():
        if "FROM assets a" in body and ":viewer" in body and "viewer_assets" not in body:
            raise AssertionError(f"`{name}` lists files for a viewer without the stored verdict")
    source = _access_source()
    assert source.count("JOIN viewer_assets v ON v.asset_id = a.id AND v.user_id = :viewer") >= 1
    assert source.count("JOIN viewer_assets v ON v.asset_id = ") >= 12, (
        "the entity walls stopped reading the verdict"
    )


#: A cover gate: the row's chosen picture, probed against the stored verdict for this viewer.
_COVER_GATE = re.compile(
    r"EXISTS \(SELECT 1 FROM viewer_assets cv WHERE cv\.asset_id = \w+\.(?:cover_asset_id|asset_id)"
    r" AND cv\.user_id = :viewer AND \((:reveal_named|:reveal) = 1 OR cv\.concealed = 0\)\)"
)


def test_a_cover_is_gated_on_PERMISSION_and_never_on_the_wall_s_narrowing() -> None:
    """A cover is gated on the stored verdict for the viewer, never on the wall's filter.

    Gated on `counted`, an album on a person's page would lose a picture that is not one of her
    files.
    """
    source = _access_source()
    gates = _COVER_GATE.findall(source)
    assert len(gates) >= 7, "the cover gates moved; this check is reading the wrong thing"
    assert "cover_asset_id IN (" not in source, "a cover is gated on a set rather than probed"
    for name, body in _access_ctes():
        if name == "counted":
            assert "cover" not in body, "the wall's narrowing decides a cover"


# --- how many copies there are, asserted here rather than counted in comments


def test_the_verdict_is_written_exactly_once() -> None:
    """One statement decides permission, and everything else reads its answers.

    The ladder is spelled once in `visibility`; the place rules are assembled from one list of
    fragments into the places table and the badge's in-place form, both held by the truth table.
    """
    from sift.kernel.access import visibility

    verdict_source = Path(visibility.__file__).read_text(encoding="utf-8")
    assert verdict_source.count("_VERDICT_ROWS = splice(") == 1
    assert verdict_source.count("ROOT_RESTRICT = ") == 1
    assert verdict_source.count("FOLDER_CHAIN_RESTRICT = ") == 1
    assert verdict_source.count("LADDER_ADMITS = ") == 1
    assert verdict_source.count("LADDER_RESTRICTS = ") == 1
    assert verdict_source.count("PHYSICAL_BITS = ") == 1
    assert verdict_source.count("_PLACE_ROWS = splice(") == 1
    assert verdict_source.count("PLACE_BITS_OF_COPIES = splice(") == 1
    everything_else = _access_source()
    for spelled_once in (
        "ROOT_RESTRICT = ",
        "FOLDER_CHAIN_RESTRICT = ",
        "FOLDER_CHAIN_HIDDEN = ",
        "LADDER_ADMITS = ",
    ):
        assert spelled_once not in everything_else, f"{spelled_once!r} is written a second time"
    # The ladder's rungs: a copy elsewhere stays behind the next rule change.
    for rung in ("& 4) = 4", "& 2) = 2 THEN 0"):
        assert rung not in everything_else, f"a rung of the ladder ({rung!r}) is written again"
    assert "ladder_admits(" in everything_else, "the badge no longer splices the ladder by name"
    assert everything_else.count("PLACE_RESTRICTED=PLACE_RESTRICTED") >= 2, (
        "the folder tree and the folder badges no longer splice the place rules by name"
    )


#: Words that were in the comments this test exists to keep out.
_ORDINALS = (
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
)


def test_no_comment_in_the_access_package_numbers_the_resolver() -> None:
    """No comment in the access package numbers the resolver's copies, which goes stale silently.

    Scoped to sentences that mention the resolver, so "the first present location wins" is fine.
    """
    text = " ".join(_access_source().split())
    offenders = [
        sentence
        for sentence in re.split(r"(?<=[.;])\s+", text)
        if "resolver" in sentence.lower()
        and any(re.search(rf"\b{word}\b", sentence, re.IGNORECASE) for word in _ORDINALS)
    ]
    assert not offenders, "a comment numbered the resolver again:\n" + "\n".join(offenders)
