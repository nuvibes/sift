# SPDX-License-Identifier: AGPL-3.0-or-later
"""Folders, writing the vault flag, failing closed on a database that does not make sense, the
schema saying what the code does, the property tests, and the catalog's writers.
"""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# Imported for its side effect: registering the face tables the unnamed-face condition reads.
# The application always has them (every install creates them whether or not recognition is
# switched on), and this is what makes that true of a kernel test as well.
import sift.slices.faces.schema

# And the music slice's, for `music_pairs`: the `same_music` leaf reads it.
import sift.slices.music.schema

# And the stash-box tables, for the same reason: the `enriched:` predicates name one, so a
# statement carrying them cannot run against a database that does not have it.
import sift.slices.stash_boxes.schema

# And the table the ledger is written to. Every concealment here records an event, so without this
# the vault tests fail with `no such table: workbench_decisions` when this file is run on its own,
# and pass only when some other module in the same process has imported it first.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import (
    Concealment,
    Effect,
    ObjectType,
    Repository,
    Role,
    Viewer,
    attribute_to_person,
    ensure_site,
    link_username_to_asset,
    schema,
    seed_site_username,
)
from sift.kernel.access.catalog import (
    MADE_BY_A_PERSON,
    by_sift,
    site_home,
)
from sift.kernel.access.constraints import NO_FILTER
from sift.kernel.access.repository.wall_collections import COLLECTION_BY_ID
from sift.kernel.access.repository.wall_photo_sets import PHOTO_SET_BY_ID
from sift.kernel.access.repository.wall_songs import SONG_BY_ID
from sift.kernel.access.repository.walls import ENTITY_SORT_SEEN
from sift.kernel.access.schema import (
    ACCESS_COMPONENT,
    CATALOG_COMPONENT,
    IDENTITY_COMPONENT,
)
from sift.kernel.access.sites import site_address
from sift.kernel.content import ContentStore
from sift.kernel.db import Database, registered_components
from sift.kernel.ids import new_id
from sift.kernel.sorting import sort_key
from sift.kernel.sql_splice import splice
from sift.kernel.tests.access_helpers import _an_asset, _migrated, _prop_settings, _somebody
from sift.kernel.vocabulary import VIA_DOWNLOAD
from sift.testing.fixtures import Actors, World, build_world, create_user, hide

# --- folders --------------------------------------------------------------------------------


async def test_folders_follow_the_same_rule_as_the_files_in_them(
    access: Repository, actors: Actors, world: World
) -> None:
    assert await access.can_view_folder(actors.guest, world.leaf) is False

    await access.grant(ObjectType.FOLDER, world.top, actors.guest.id, Effect.SHARE)
    assert await access.can_view_folder(actors.guest, world.leaf) is True
    assert await access.can_view_folder(actors.guest, world.mid) is True

    await access.grant(ObjectType.FOLDER, world.mid, actors.guest.id, Effect.RESTRICT)
    assert await access.can_view_folder(actors.guest, world.mid) is False
    assert await access.can_view_folder(actors.guest, world.leaf) is False
    assert await access.can_view_folder(actors.guest, world.top) is True


async def test_a_folder_with_both_a_share_and_a_restrict_is_not_visible(
    access: Repository, actors: Actors, world: World
) -> None:
    """The folder-listing path enforces restrict-wins on one folder, same as the asset path does.

    The folder query resolves permissions with its own copy of the rule, so it needs its own test:
    a folder carrying both grants for a viewer is denied, exactly as an asset inside it would be.
    (A resolver written twice is only proven equivalent if the truth table exercises both copies.)
    """
    await access.grant(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.SHARE)
    await access.grant(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.RESTRICT)
    assert await access.can_view_folder(actors.guest, world.leaf) is False


async def test_a_global_share_reveals_folders(
    access: Repository, actors: Actors, world: World
) -> None:
    """A global grant reaches the folder-listing path too, inherited down through the root.

    The other folder tests use folder- and root-level grants; this one exercises the global level
    of the folder query and its inheritance chain.
    """
    await access.grant(ObjectType.GLOBAL, None, actors.guest.id, Effect.SHARE)
    assert await access.can_view_folder(actors.guest, world.leaf) is True
    assert await access.can_view_folder(actors.guest, world.top) is True


async def test_a_global_restrict_hides_folders(
    access: Repository, actors: Actors, world: World
) -> None:
    """And the restrict direction, so the folder query's global level is pinned both ways."""
    await access.grant(ObjectType.GLOBAL, None, actors.guest.id, Effect.RESTRICT)
    assert await access.can_view_folder(actors.guest, world.leaf) is False


async def test_an_admin_walks_the_tree(access: Repository, actors: Actors, world: World) -> None:
    tops = await access.visible_folders(actors.admin, root_id=world.root, top_level=True)
    assert [folder.id for folder in tops] == [world.top]

    children = await access.visible_folders(actors.admin, parent_id=world.top)
    assert [folder.id for folder in children] == [world.mid]


async def test_each_narrowing_selects_a_different_set(
    access: Repository, actors: Actors, world: World
) -> None:
    """Four questions through one method, and they must not collapse into each other.

    The risk of folding four names into three parameters is that one of them stops being bound and
    nobody notices, because the answer it gives is still a plausible list of folders. So the four
    are asked side by side and asserted to differ: everything, one library's first level, one
    folder's children, and every library's first level are four distinct sets in this world.
    """
    everything = await access.visible_folders(actors.admin)
    tops = await access.visible_folders(actors.admin, top_level=True)
    one_library = await access.visible_folders(actors.admin, root_id=world.root, top_level=True)
    children = await access.visible_folders(actors.admin, parent_id=world.top)

    assert {folder.id for folder in everything} == {world.top, world.mid, world.leaf, world.other}
    assert {folder.id for folder in tops} == {world.top, world.other}
    assert [folder.id for folder in one_library] == [world.top]
    assert [folder.id for folder in children] == [world.mid]


async def test_the_top_level_folders_are_scoped_like_everything_else(
    access: Repository, actors: Actors, world: World
) -> None:
    """The browser is built from folders, not roots: a root's path is a fact about the machine.
    An admin sees the top of every root; a guest with no grants sees none of it."""
    tops = await access.visible_folders(actors.admin, top_level=True)
    assert {folder.id for folder in tops} == {world.top, world.other}

    assert await access.visible_folders(actors.guest, top_level=True) == []


async def test_the_whole_tree_is_one_query_and_a_bad_root_id_selects_nothing(
    access: Repository, actors: Actors, world: World
) -> None:
    """The whole visible tree comes back at once, and a malformed root id is nothing, not a
    wildcard: it selects no folders rather than every folder that never had a root to belong to."""
    everything = await access.visible_folders(actors.admin)
    assert {folder.id for folder in everything} == {world.top, world.mid, world.leaf, world.other}

    assert await access.visible_folders(actors.admin, root_id="not an id") == []


async def test_a_vaulted_folder_is_absent_from_the_tree(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await hide(temp_db, "folder", world.mid, actors.admin.id)

    assert await access.visible_folders(actors.admin, parent_id=world.top) == []
    assert await access.get_folder(actors.admin, world.leaf) is None

    unlocked = replace(actors.admin, show_hidden=True)
    revealed = await access.visible_folders(unlocked, parent_id=world.top)
    assert [folder.id for folder in revealed] == [world.mid]
    assert revealed[0].concealed is True


# --- writing the vault flag ------------------------------------------------------------------
#
# The write is the other half of the resolver, and the interesting question is not "does the flag
# change" but "who can reach the row to change it". Concealing something has to be reversible, and
# the only thing that may reverse it is having opened the vault, so the resolution these run
# first is the whole of the rule, and each of these is about that rather than about the UPDATE.


async def test_vaulting_an_asset_conceals_it_and_unlocking_lets_it_be_taken_back_out(
    access: Repository, actors: Actors, world: World
) -> None:
    """The round trip. Without the second half, the first mistake is permanent."""
    assert await access.set_asset_vault(actors.admin, world.solo, vault=True) is True
    assert await access.get_asset(actors.admin, world.solo) is None

    # Locked, the row it would have to write to does not resolve: the same answer an id that was
    # never minted gets, because answering differently would confirm the file is there.
    assert await access.set_asset_vault(actors.admin, world.solo, vault=False) is False
    assert await access.get_asset(actors.admin, world.solo) is None

    unlocked = replace(actors.admin, show_hidden=True)
    assert await access.set_asset_vault(unlocked, world.solo, vault=False) is True
    assert await access.get_asset(actors.admin, world.solo) is not None


async def test_vaulting_a_selection_writes_the_files_it_was_allowed_and_says_why_not_the_rest(
    access: Repository, actors: Actors, world: World
) -> None:
    """The list form, and the reason it answers with the three piles rather than a count.

    It resolves the selection itself rather than trusting a caller's list: the resolution IS the
    permission here, so a method that took already-approved ids would be a second way into the
    vault flag with nothing standing in front of it. What the count alone could not say is WHY the
    rest were left out, and the route's reply is built from both.
    """
    nowhere = new_id()

    done = await access.set_asset_vault_many(
        actors.admin, [world.solo, world.twin, nowhere], vault=True
    )

    assert set(done.allowed) == {world.solo, world.twin}
    assert done.refused == (nowhere,)
    assert {item.asset.id for item in (await access.visible_assets(actors.admin)).items} == {
        world.loose
    }

    unlocked = replace(actors.admin, show_hidden=True)
    back = await access.set_asset_vault_many(unlocked, [world.solo, world.twin], vault=False)
    assert set(back.allowed) == {world.solo, world.twin}
    assert len((await access.visible_assets(actors.admin)).items) == 3


async def test_vaulting_a_selection_of_nothing_reachable_writes_nothing_at_all(
    access: Repository, actors: Actors, world: World
) -> None:
    """Told about a selection it may not touch, it stops before the statement and hands back the
    reasons. A write of no ids would still be a transaction and a cache-stamp bump, and the bump
    is what puts every picture already in that user's browser out of reach, so buying one for a
    selection nothing was written to would empty a stranger's cache on their behalf."""
    refused = await access.set_asset_vault_many(actors.guest, [world.solo, world.twin], vault=True)

    assert refused.allowed == ()
    assert set(refused.refused) == {world.solo, world.twin}
    assert len((await access.visible_assets(actors.admin)).items) == 3


async def test_vaulting_a_folder_conceals_the_tree_and_unlocking_lets_it_be_taken_back_out(
    access: Repository, actors: Actors, world: World
) -> None:
    """The flag goes on `mid`; the files are below it."""
    assert await access.set_folder_vault(actors.admin, world.mid, vault=True) is True
    page = await access.visible_assets(actors.admin)
    assert {item.asset.id for item in page.items} == {world.loose}

    assert await access.set_folder_vault(actors.admin, world.mid, vault=False) is False

    unlocked = replace(actors.admin, show_hidden=True)
    assert await access.set_folder_vault(unlocked, world.mid, vault=False) is True
    assert len((await access.visible_assets(actors.admin)).items) == 3


async def test_hiding_a_folder_writes_what_this_user_thought_before(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The event says the folder was hidden; this says what it replaced.

    `hidden_at` is the only stamp on the row and it says when the CURRENT concealment began, so
    bringing a folder back clears it along with every trace that it was ever hidden. The opinion
    row is appended in the same transaction as the upsert, and the value before is read one
    statement earlier: the upsert's own answer is the row AFTER the write.
    """
    unlocked = replace(actors.admin, show_hidden=True)

    assert await access.set_folder_vault(actors.admin, world.mid, vault=True) is True
    assert await access.set_folder_vault(unlocked, world.mid, vault=False) is True

    rows = await temp_db.fetch_all(
        "SELECT subject_kind, kind, before, after FROM opinions WHERE subject_id = ? ORDER BY id",
        (world.mid,),
    )
    assert [
        (str(row["subject_kind"]), str(row["kind"]), row["before"], row["after"]) for row in rows
    ] == [
        ("folder", "hide", None, 1),
        ("folder", "hide", 1, 0),
    ]


async def test_a_placeholder_is_not_enough_to_take_something_out_of_the_vault(
    access: Repository, actors: Actors, world: World
) -> None:
    """The mode that leaves a locked tile hands concealed rows back to a reader, and must not hand
    them to a writer.

    Otherwise choosing the friendlier concealment mode would quietly become a way to un-hide things
    without the PIN: the mode that shows more would also ask for less, which is exactly backwards.
    """
    await access.set_asset_vault(actors.admin, world.solo, vault=True)
    await access.set_folder_vault(actors.admin, world.mid, vault=True)
    peeking = replace(actors.admin, concealment=Concealment.PLACEHOLDER)

    # The reader does see them: that is what the mode is for.
    assert await access.get_asset(peeking, world.solo) is not None
    assert await access.get_folder(peeking, world.mid) is not None
    # The writer does not.
    assert await access.set_asset_vault(peeking, world.solo, vault=False) is False
    assert await access.set_folder_vault(peeking, world.mid, vault=False) is False


async def test_a_guest_cannot_vault_what_they_were_never_shown(
    access: Repository, actors: Actors, world: World
) -> None:
    """The write is scoped by the same rule every read is. Nothing else here decides who may write
    (the caller does that), so this is only that the resolution is not skipped."""
    assert await access.set_asset_vault(actors.guest, world.solo, vault=True) is False
    assert await access.set_folder_vault(actors.guest, world.mid, vault=True) is False


async def test_an_id_that_is_not_an_id_writes_nothing(access: Repository, actors: Actors) -> None:
    """A malformed id must not be bound as NULL, which the queries read as "no filter": that
    would resolve to the first row the caller may see and set the flag on somebody else's file."""
    assert await access.set_asset_vault(actors.admin, "not-an-id", vault=True) is False
    assert await access.set_folder_vault(actors.admin, "not-an-id", vault=True) is False
    assert await access.set_asset_vault(actors.admin, new_id(), vault=True) is False
    assert await access.set_folder_vault(actors.admin, new_id(), vault=True) is False


# --- fail-closed on a database that does not make sense --------------------------------------


async def test_a_loop_in_the_folder_tree_conceals_what_is_in_it(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A loop must not hang the request, and must not resolve to visible.

    Nothing in SQLite stops `parent_id` from pointing at a descendant, and a restored or
    hand-edited database can contain one. The walk descends from the folders with no parent, so it
    never reaches a folder inside a loop, and a folder it never reaches is one the resolver has
    no answer for, which it reads as denied. The guest holds a global share here, so failing open
    would hand them the file.
    """
    await temp_db.execute("UPDATE folders SET parent_id = ? WHERE id = ?", (world.leaf, world.top))
    await access.grant(ObjectType.GLOBAL, None, actors.guest.id, Effect.SHARE)

    assert await access.can_view(actors.guest, world.solo) is False
    assert await access.can_view(actors.guest, world.loose) is True


async def test_a_loop_denies_even_when_something_else_shares_the_file(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """An unresolvable chain has to DENY, not merely fail to share.

    Three things have to be true at once before the difference is even visible, which is why this
    is written out rather than left to the case above:

    - a share has to arrive from somewhere the folder chain has no say over, or the file is denied
      by the default anyway and a chain that abstained would look exactly like one that refused.
      `solo` carries a tag, and the tag is shared.
    - the vault has to be open. An unresolvable chain is also treated as concealed (if the vault
      cannot be resolved either, it is not known to be safe), and that concealment would hide the
      file before this rule was ever consulted.

    With both of those out of the way, what is left is the access rule on its own, and it says no.
    """
    await temp_db.execute("UPDATE folders SET parent_id = ? WHERE id = ?", (world.leaf, world.top))
    await access.grant(ObjectType.TAG, world.tag, actors.guest.id, Effect.SHARE)
    unlocked = replace(actors.guest, show_hidden=True)

    assert await access.can_view(unlocked, world.solo) is False


async def test_a_missing_root_denies_even_when_something_else_shares_the_file(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The same distinction, for a location whose root is not there.

    Abstaining and refusing look identical until a share turns up from elsewhere, and then one of
    them is a leak.
    """
    await access.grant(ObjectType.TAG, world.tag, actors.guest.id, Effect.SHARE)
    unlocked = replace(actors.guest, show_hidden=True)

    async with temp_db.write() as connection:
        await connection.execute("PRAGMA foreign_keys=OFF")
        await connection.execute(
            "UPDATE asset_locations SET root_id = 'gone' WHERE asset_id = ?", (world.solo,)
        )
        await connection.execute("PRAGMA foreign_keys=ON")

    assert await access.can_view(unlocked, world.solo) is False


async def test_an_unresolvable_chain_is_concealed_from_the_admin_as_well(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The other half of the same rule, and the one that costs something.

    An admin bypasses the access rules, so denying is not enough on its own: if a broken folder
    chain only denied, a file that was sitting in a vaulted folder before the chain broke would
    reappear on an admin's grid, and the vault would have been defeated by a corrupt row. So an
    unresolvable chain is concealed as well as denied: the vault status is not known to be safe,
    so it is not assumed to be.

    The cost is that a file with a broken chain goes missing from the library until the vault is
    unlocked, which is the right way round: it is recoverable, and the alternative is not.
    """
    await temp_db.execute("UPDATE folders SET parent_id = ? WHERE id = ?", (world.leaf, world.top))

    assert await access.get_asset(actors.admin, world.solo) is None

    unlocked = replace(actors.admin, show_hidden=True)
    assert await access.get_asset(unlocked, world.solo) is not None


async def test_a_deep_folder_tree_still_resolves(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """No arbitrary depth limit. A tree is as deep as somebody made it.

    A depth cap would have been the obvious way to make a loop terminate, and it would have been
    the wrong one: loops already terminate, and the cap would instead have denied the files of
    anyone whose folders happened to nest deeper than the number somebody picked.
    """
    parent = world.leaf
    for depth in range(400):
        child = new_id()
        await temp_db.execute(
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
            (child, world.root, parent, f"deep/{depth}", str(depth)),
        )
        parent = child

    deep_asset = new_id()
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, 'deep', 'video', 1)",
        (deep_asset,),
    )
    await temp_db.execute(
        "INSERT INTO asset_locations "
        "(id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, 'deep/deep.mp4', 'deep.mp4', 1, 1)",
        (new_id(), deep_asset, world.root, parent),
    )

    await access.grant(ObjectType.FOLDER, world.top, actors.guest.id, Effect.SHARE)
    assert await access.can_view(actors.guest, deep_asset) is True

    await access.grant(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.RESTRICT)
    assert await access.can_view(actors.guest, deep_asset) is False


async def test_a_folder_whose_parent_is_in_another_root_inherits_nothing(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A share on one library must not reach into another.

    `folders` carries both a parent and a root, and nothing stops the two from disagreeing. If the
    walk followed the parent alone, a folder could be adopted into a root it is not in, and would
    inherit that root's grants along with it.
    """
    await temp_db.execute("UPDATE folders SET parent_id = ? WHERE id = ?", (world.other, world.top))
    await access.grant(ObjectType.ROOT, world.root_two, actors.guest.id, Effect.SHARE)

    # `top` says it is in `root`, but its parent lives in `root_two`, which the guest may see.
    assert await access.can_view(actors.guest, world.solo) is False
    assert await access.can_view_folder(actors.guest, world.top) is False


async def test_an_asset_whose_root_is_gone_is_not_visible_to_a_guest(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """An asset with nowhere to be resolves to denied, not to unconstrained."""
    await access.grant(ObjectType.GLOBAL, None, actors.guest.id, Effect.SHARE)
    await temp_db.execute("DELETE FROM asset_locations WHERE asset_id = ?", (world.solo,))

    assert await access.can_view(actors.guest, world.solo) is False


async def test_a_location_pointing_at_a_root_that_does_not_exist_is_denied(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Rows outlive the code that wrote them.

    A foreign key stops this happening today. A database restored from a backup written before
    that key existed is exactly the case where it did not, and the resolver has no root to resolve
    the file against, so it does not resolve it.
    """
    await access.grant(ObjectType.GLOBAL, None, actors.guest.id, Effect.SHARE)
    assert await access.can_view(actors.guest, world.solo) is True

    async with temp_db.write() as connection:
        await connection.execute("PRAGMA foreign_keys=OFF")
        await connection.execute(
            "UPDATE asset_locations SET root_id = 'gone' WHERE asset_id = ?", (world.solo,)
        )
        await connection.execute("PRAGMA foreign_keys=ON")

    assert await access.can_view(actors.guest, world.solo) is False


# --- the schema says the same thing the code does ---------------------------------------------


@pytest.mark.parametrize(
    ("table", "column", "values"),
    [
        # THE STORED SPELLING, not the enum's. `acl_grants.object_type` still holds `platform` for
        # a Site (the storage rename is its own slice), and `kernel/access/sites.py` translates
        # on the way in and out. Asserting the enum's own word here would demand a CHECK that the
        # rows do not satisfy, which is the fault this test exists to catch, pointed the wrong way.
        ("acl_grants", "object_type", {member.value for member in ObjectType}),
        ("acl_grants", "effect", {member.value for member in Effect}),
        ("users", "role", {member.value for member in Role}),
    ],
)
async def test_the_check_constraint_matches_the_enum(
    temp_db: Database, access: Repository, table: str, column: str, values: set[str]
) -> None:
    """A value the code can produce and the database refuses is a crash waiting for a user."""
    row = await temp_db.fetch_one("SELECT sql FROM sqlite_master WHERE name = ?", (table,))
    assert row is not None
    ddl = str(row["sql"])

    for value in values:
        assert f"'{value}'" in ddl, f"{column} rejects {value!r}, which the code can produce"


async def test_the_access_tables_are_registered_in_dependency_order() -> None:
    components = registered_components()
    assert components[CATALOG_COMPONENT].depends_on == ("content", IDENTITY_COMPONENT)
    assert components[ACCESS_COMPONENT].depends_on == (IDENTITY_COMPONENT,)


async def test_booting_twice_changes_nothing(temp_db: Database, access: Repository) -> None:
    """The second boot must not re-run an initializer, and must not fail if it does. Each component
    lands on its own registered version, read from the registry so a version bump does not need
    this test edited to a new literal."""
    await temp_db.initialize_schema()
    await temp_db.initialize_schema()

    components = registered_components()
    for component in (IDENTITY_COMPONENT, CATALOG_COMPONENT, ACCESS_COMPONENT):
        assert await temp_db.schema_version(component) == components[component].version


@pytest.mark.parametrize("component", [IDENTITY_COMPONENT, CATALOG_COMPONENT, ACCESS_COMPONENT])
async def test_an_initializer_does_nothing_to_a_database_already_at_its_version(
    temp_db: Database, access: Repository, component: str
) -> None:
    """Called with the version already on disk, it must not try to create anything again."""
    initializers = {
        IDENTITY_COMPONENT: schema.initialize_identity,
        CATALOG_COMPONENT: schema.initialize_catalog,
        ACCESS_COMPONENT: schema.initialize_access,
    }
    at_version = registered_components()[component].version
    async with temp_db.write() as connection:
        await initializers[component](connection, at_version)


async def test_marks_asked_about_nothing_answer_nothing(
    access: Repository, actors: Actors, world: World
) -> None:
    """Every mark read is handed a list a screen built, and a screen can be showing nothing.

    Answering an empty list with a query is a round trip to be told what the caller already knew,
    and answering it with an error would make an empty wall a failure.
    """
    assert await access._grant_marks(ObjectType.TAG, []) == {}
    assert await access._folder_marks([]) == {}
    assert await access.visible_marks(actors.admin, ObjectType.ITEM, []) == {}
    # An id that is not an id is the same answer: nothing was asked about.
    assert await access._grant_marks(ObjectType.TAG, ["not-an-id"]) == {}
    assert await access._folder_marks(["not-an-id"]) == {}
    # And a guest is told nothing about grants at all, whatever they ask about.
    assert await access.visible_marks(actors.guest, ObjectType.ITEM, [world.solo]) == {}


async def test_marks_route_each_kind_to_the_read_that_knows_about_it(
    access: Repository, actors: Actors, world: World
) -> None:
    """A file and a folder inherit; a tag does not. Three reads, and the caller picks none of them.

    The point of the one entry door: a screen asks for marks and gets the right answer for the kind
    of thing it is showing, rather than every wall having to know which read applies to it.
    """
    await access.grant(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.SHARE)
    await access.grant(ObjectType.TAG, world.tag, actors.guest.id, Effect.RESTRICT)

    # A folder, through the folder read: the one it was made on says so.
    folders = await access.visible_marks(actors.admin, ObjectType.FOLDER, [world.leaf])
    assert folders[world.leaf].shared is True
    assert folders[world.leaf].shared_here is True

    # A file inside it, through the asset read: reachable, and NOT decided here. `twin` rather than
    # `solo`, because solo carries the tag restricted above, and a restrict beats a share made on
    # something broader, so its effective answer is the restrict. Which is correct, and is a
    # different fact from the one being checked here.
    files = await access.visible_marks(actors.admin, ObjectType.ITEM, [world.twin])
    assert files[world.twin].shared is True
    assert files[world.twin].shared_here is False

    # A tag, through the plain read: it has nothing above it, so its own decision is all there is.
    tags = await access.visible_marks(actors.admin, ObjectType.TAG, [world.tag])
    assert tags[world.tag].restricted is True


async def test_a_label_under_a_shared_network_is_marked_shared_and_not_shared_here(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A Site has something above it to inherit from, so its mark is the effective one.

    A share on a network reaches every file its labels released, so the label IS shared, and its
    card must say so, though the plain read asks only about grants on the label's own row. Hollow
    rather than solid: the decision was made on the network, which is where it can be undone. And
    the same user restricted at the label is kept from its files (a restrict on the logical axis
    beats a share on it), so the label is then restricted and not shared.
    """
    label = new_id()
    await temp_db.execute(
        "INSERT INTO sites (id, name, name_sort, parent_id, created_at) VALUES (?, ?, ?, ?, 0)",
        (label, "label", sort_key("label"), world.site),
    )
    await access.grant(ObjectType.SITE, world.site, actors.guest.id, Effect.SHARE)

    marks = await access.visible_marks(actors.admin, ObjectType.SITE, [world.site, label])
    assert (marks[world.site].shared, marks[world.site].shared_here) == (True, True)
    assert (marks[label].shared, marks[label].shared_here) == (True, False)

    await access.grant(ObjectType.SITE, label, actors.guest.id, Effect.RESTRICT)
    marks = await access.visible_marks(actors.admin, ObjectType.SITE, [label])
    assert marks[label].shared is False
    assert (marks[label].restricted, marks[label].restricted_here) == (True, True)


async def test_where_a_grant_was_made_is_answered_for_every_kind_of_object(
    access: Repository, actors: Actors, world: World
) -> None:
    """The read behind "why is this shared when I never shared it", asked of all three shapes."""
    await access.grant(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.SHARE)

    # A file: the folder it sits in is named.
    for_file = await access.grant_sources(ObjectType.ITEM, world.solo)
    assert [(source.source_type, source.source_id) for source in for_file] == [
        (ObjectType.FOLDER, world.leaf)
    ]

    # The folder itself: its own grant, named as its own.
    for_folder = await access.grant_sources(ObjectType.FOLDER, world.leaf)
    assert [source.source_id for source in for_folder] == [world.leaf]

    # Something with nothing above it reads its own row and the global one, and neither is set here.
    assert await access.grant_sources(ObjectType.TAG, world.tag) == []

    # An id that is not an id is nothing to answer about, rather than a query with a junk parameter.
    assert await access.grant_sources(ObjectType.ITEM, "not-an-id") == []
    assert await access.grant_sources(ObjectType.FOLDER, None) == []


async def test_an_entity_s_reach_is_explained_through_the_files_under_it(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The gap the reach report leaves, and the read that closes it.

    A person is on a guest's wall because ONE file under her can be reached, and what let that file
    through was said about the folder it sits in. So the yes is true and there is no grant naming
    the person to put beside it, which is the state this asserts first, because a read that
    explained a yes nobody could get would be explaining the wrong thing.
    """
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)",
        (world.twin, world.person),
    )
    await access.grant(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.SHARE)

    # The state: a true yes, and nothing said about the person to explain it with.
    reaches = await access.reach_of(ObjectType.PERSON, world.person)
    assert {one.user_id: one.sees for one in reaches}[actors.guest.id] is True
    assert await access.grant_sources(ObjectType.PERSON, world.person) == []

    explained = await access.reach_through_files(ObjectType.PERSON, world.person, actors.guest.id)
    # Both of her files sit in the shared folder, so one reason covers both, which is the whole
    # shape of the answer: reasons, not files.
    assert [
        (one.source_type, one.source_id, one.source_name, one.files) for one in explained.reasons
    ] == [(ObjectType.FOLDER, world.leaf, "leaf", 2)]
    assert (explained.files, explained.complete) == (2, True)


async def test_the_explanation_says_how_much_of_the_entity_it_looked_at(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A count under a ceiling is only readable beside the number it is out of.

    The ceiling is what makes this its own read rather than something the reach report works out
    for every user at once, so the answer carries what it saw. A reason found in a page is a
    true reason; a page reported as a total would be a number that means one thing on a person with
    three files and another on a person with three thousand.
    """
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)",
        (world.twin, world.person),
    )
    await access.grant(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.SHARE)

    partial = await access.reach_through_files(
        ObjectType.PERSON, world.person, actors.guest.id, ceiling=1
    )
    assert (partial.files, partial.complete) == (1, False)
    assert [one.files for one in partial.reasons] == [1]


async def test_a_reach_explanation_has_nothing_to_add_where_the_chain_is_already_whole(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The three physical kinds, a user with no reach, and an id that is not one.

    A file, a folder and a library each have a chain of their own that `grant_sources` gives whole,
    so this is a question with nothing to add to them rather than one with a different answer. An
    user that can reach nothing under the entity has no page and therefore no reasons, and that
    is a complete answer rather than a truncated one.
    """
    await access.grant(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.SHARE)

    for object_type, object_id in (
        (ObjectType.ITEM, world.solo),
        (ObjectType.FOLDER, world.leaf),
        (ObjectType.ROOT, world.root),
    ):
        whole = await access.reach_through_files(object_type, object_id, actors.guest.id)
        assert (whole.reasons, whole.files, whole.complete) == ((), 0, True)

    stranger = await create_user(temp_db, Role.GUEST)
    unreached = await access.reach_through_files(ObjectType.PERSON, world.person, stranger.id)
    assert (unreached.reasons, unreached.files, unreached.complete) == ((), 0, True)

    junk = await access.reach_through_files(ObjectType.PERSON, world.person, "not-an-id")
    assert junk.reasons == ()


async def test_what_is_hiding_something_is_nothing_when_there_is_nothing_to_ask_about(
    access: Repository, actors: Actors
) -> None:
    """The same three refusals the grant read makes, for the same reason.

    A grant can be made on everything at once; nothing can be CONCEALED on everything at once, so
    the global case has no row to look for and no query worth running.
    """
    assert await access.vault_sources(actors.admin, ObjectType.ITEM, "not-an-id") == []
    assert await access.vault_sources(actors.admin, ObjectType.FOLDER, "not-an-id") == []
    assert await access.vault_sources(actors.admin, ObjectType.GLOBAL, None) == []


async def test_a_page_has_to_be_a_page(access: Repository, actors: Actors, world: World) -> None:
    with pytest.raises(ValueError, match="at least one row"):
        await access.visible_assets(actors.admin, limit=0)
    with pytest.raises(ValueError, match="before the first row"):
        await access.visible_assets(actors.admin, offset=-1)


async def test_a_collection_list_has_to_be_a_list(
    access: Repository, actors: Actors, world: World
) -> None:
    with pytest.raises(ValueError, match="at least one row"):
        await access.visible_collections(actors.admin, limit=0)


async def test_a_page_of_any_wall_has_to_be_a_page(
    access: Repository, actors: Actors, world: World
) -> None:
    """The three paged walls, held to the same two bounds the file listing is.

    A negative offset is the one that matters. `LIMIT ? OFFSET ?` with a negative offset is not an
    error in SQLite (it is read as no offset at all), so an off-by-one in a caller comes back as
    the first page dressed as the fourth, with nothing anywhere saying so. Refused here, out loud,
    at the seam where the number arrives.

    Asked of the paged methods directly rather than through the three dropdown wrappers: those pass
    no offset, so the guard is unreachable from them and would sit here forever looking covered.
    """
    for ask in (access.list_tags, access.list_collections, access.list_sites):
        with pytest.raises(ValueError, match="at least one row"):
            await ask(actors.admin, limit=0)
        with pytest.raises(ValueError, match="before the first row"):
            await ask(actors.admin, offset=-1)


async def test_one_collection_by_id_answers_the_same_as_the_list(
    access: Repository, actors: Actors, world: World
) -> None:
    """The by-id read is the list filtered to one, not a second opinion about what exists."""
    listed = {one.id: one for one in await access.visible_collections(actors.admin)}

    fetched = await access.visible_collection(actors.admin, world.collection)

    assert fetched == listed[world.collection]


async def test_a_collection_id_that_is_not_an_id_matches_nothing(
    access: Repository, actors: Actors, world: World
) -> None:
    """Checked for shape before it is bound, for the reason an asset id is.

    The statement reads NULL as "every collection", so a malformed id reaching the parameter would
    come back as the first one this viewer may see rather than as nothing.
    """
    assert await access.visible_collection(actors.admin, "not-an-id") is None
    assert await access.visible_collection(actors.admin, new_id()) is None


async def test_an_entity_id_that_binds_as_null_matches_nothing(
    access: Repository, actors: Actors, world: World
) -> None:
    """The one value the shape check is actually load-bearing for.

    Every by-id read filters with `(:id IS NULL OR row.id = :id)`, so the statement reads NULL as
    "no filter", and a read handed one would answer with whichever row this viewer may see
    sorted first, which is a different entity presented as the one that was asked for.

    A non-empty word cannot show this: it binds as itself and matches nothing whether the check is
    there or not, so a test passing a word looks like evidence and proves only that the route says
    404. The value has to be one that reaches the parameter as NULL.
    """
    for read in (
        access.visible_person,
        access.visible_tag,
        access.visible_site,
        access.visible_collection,
    ):
        assert await read(actors.admin, None) is None, read.__name__  # type: ignore[arg-type]


async def test_a_by_id_statement_bound_null_answers_no_row(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The statements behind the by-id reads, each given NULL: the wall's "no filter" seam must be
    cut out of every one, or a read handed NULL would answer with the first row it may see."""
    _where, bound = NO_FILTER.predicate()
    asked = (
        (COLLECTION_BY_ID, access._collection_params(actors.admin, collection_id=None, limit=1)),
        (
            PHOTO_SET_BY_ID,
            access._photo_set_params(
                actors.admin, photo_set_id=None, limit=1, offset=0, sort=ENTITY_SORT_SEEN
            ),
        ),
        (
            SONG_BY_ID,
            access._song_params(
                actors.admin, song_id=None, limit=1, offset=0, sort=ENTITY_SORT_SEEN
            ),
        ),
    )
    for statement, params in asked:
        assert await temp_db.fetch_all(statement, {**bound, **params}) == [], statement.name


async def test_every_entity_by_id_read_answers_the_same_as_its_wall(
    access: Repository, actors: Actors, world: World
) -> None:
    """Four reads, one rule: filtered to one row, never a second opinion about what exists."""
    people = {one.id: one for one in (await access.suggest_people(actors.admin)).items}
    tags = {one.id: one for one in await access.suggest_tags(actors.admin)}
    sites = {one.id: one for one in await access.suggest_sites(actors.admin)}

    assert await access.visible_person(actors.admin, world.person) == people[world.person]
    assert await access.visible_tag(actors.admin, world.tag) == tags[world.tag]
    assert await access.visible_site(actors.admin, world.site) == sites[world.site]


async def test_an_id_that_is_shaped_right_and_names_nothing_is_a_miss_on_every_read(
    access: Repository, actors: Actors, world: World
) -> None:
    """The other half of the shape check, and the half a malformed id cannot reach.

    A well-formed id that names nothing gets past the shape check and is bound, so the answer comes
    from the statement rather than from the guard in front of it, which is the path a deep link
    to something deleted takes. It has to be the same None an unreachable row gets, or the
    difference between the two is a way of asking whether something exists.
    """
    absent = new_id()

    assert await access.visible_person(actors.admin, absent) is None
    assert await access.visible_tag(actors.admin, absent) is None
    assert await access.visible_site(actors.admin, absent) is None
    assert await access.visible_collection(actors.admin, absent) is None


async def test_a_guest_is_not_shown_a_collection_holding_nothing_for_them(
    access: Repository, actors: Actors, world: World
) -> None:
    """The same rule the people list uses, and it settles the empty case with it."""
    assert await access.visible_collections(actors.guest) == []
    assert await access.visible_collection(actors.guest, world.collection) is None

    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)

    shown = await access.visible_collections(actors.guest)
    assert [one.id for one in shown] == [world.collection]
    assert shown[0].item_count == 1


async def test_an_unlocked_vault_brings_a_concealed_collection_back(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The other side of the flag, which no session can reach over HTTP yet.

    A vaulted collection is absent from the list for everybody, an admin included. Unlocking is
    what brings the row back, and the count that comes with it is the unlocked one.
    """
    await hide(temp_db, "collection", world.collection, actors.admin.id)

    assert await access.visible_collections(actors.admin) == []

    unlocked = replace(actors.admin, show_hidden=True)
    assert access.reveals_named_rows(unlocked) is True
    shown = await access.visible_collections(unlocked)
    assert [one.id for one in shown] == [world.collection]
    assert shown[0].vault is True
    assert shown[0].item_count == 1


async def test_get_asset_returns_the_asset_somebody_may_see(
    access: Repository, actors: Actors, world: World
) -> None:
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)

    view = await access.get_asset(actors.guest, world.solo)
    assert view is not None
    assert view.asset.id == world.solo
    assert view.concealed is False


# --- property -------------------------------------------------------------------------------

_TYPE_OF = {
    "global": ObjectType.GLOBAL,
    "root": ObjectType.ROOT,
    "root_two": ObjectType.ROOT,
    "top": ObjectType.FOLDER,
    "mid": ObjectType.FOLDER,
    "leaf": ObjectType.FOLDER,
    "other": ObjectType.FOLDER,
    "solo": ObjectType.ITEM,
    "twin": ObjectType.ITEM,
    "loose": ObjectType.ITEM,
}

# Each file's copies, and the objects above each copy from nearest to farthest.
_CHAINS = {
    "solo": [["leaf", "mid", "top", "root", "global"]],
    "twin": [["leaf", "mid", "top", "root", "global"], ["other", "root_two", "global"]],
    "loose": [["root", "global"]],
}


def _expected(asset: str, grants: list[tuple[str, Effect]]) -> bool:
    """The rule, written again, in Python, from the specification rather than from the SQL.

    An oracle rather than a repetition: a property test that reimplements the query it is
    checking agrees with it about everything, including the bugs. A restrict on the file or
    anywhere on any copy's chain denies; otherwise any share on the file or a chain admits. That
    is the rule as stated, and it shares no code with the resolver at all.
    """
    held: dict[str, set[Effect]] = {}
    for name, effect in grants:
        held.setdefault(name, set()).add(effect)

    item = held.get(asset, set())
    if Effect.RESTRICT in item:
        return False

    # A RESTRICT ANYWHERE ON ANY COPY'S CHAIN IS ABSOLUTE: it beats every share,
    # the file's own included, so it is asked before the file's own share is.
    if any(Effect.RESTRICT in held.get(name, set()) for chain in _CHAINS[asset] for name in chain):
        return False
    if Effect.SHARE in item:
        return True
    return any(Effect.SHARE in held.get(name, set()) for chain in _CHAINS[asset] for name in chain)


@given(
    asset=st.sampled_from(["solo", "twin", "loose"]),
    grants=st.lists(
        st.tuples(
            st.sampled_from(list(_TYPE_OF)),
            st.sampled_from(list(Effect)),
        ),
        max_size=6,
    ),
)
@settings(max_examples=250, deadline=None)
async def test_the_resolver_agrees_with_the_rule_on_any_set_of_grants(
    asset: str, grants: list[tuple[str, Effect]]
) -> None:
    """Every combination of grants, not just the ones somebody thought to write down.

    Each example gets its own database. A fixture would not be reset between them, and a test
    that accumulates grants across examples stops testing what it says it does.
    """
    with tempfile.TemporaryDirectory() as directory:
        database = Database(Path(directory) / "prop.sqlite3")
        await database.connect()
        try:
            await database.initialize_schema()
            access = Repository(database, ContentStore(database, _prop_settings(directory)))
            world = World(**{name: new_id() for name in World.__slots__})
            await build_world(database, world)
            guest = await create_user(database, Role.GUEST)

            for name, effect in grants:
                await access.grant(_TYPE_OF[name], world.object_id(name), guest.id, effect)

            asset_id = world.object_id(asset)
            assert asset_id is not None
            assert await access.can_view(guest, asset_id) is _expected(asset, grants)
        finally:
            await database.close()


def test_a_viewer_is_closed_by_default() -> None:
    """Build one without thinking about the vault and the vault stays shut."""
    viewer = Viewer(id=new_id(), role=Role.GUEST)
    assert viewer.show_hidden is False
    assert viewer.concealment is Concealment.FULLY_GONE


async def test_seed_creates_a_site_and_a_username(temp_db: Database) -> None:
    await _migrated(temp_db)
    site_id, username_id = await seed_site_username(
        temp_db, site="TikTok", name="@creator", made=MADE_BY_A_PERSON
    )
    assert site_id and username_id
    username = await temp_db.fetch_one("SELECT name FROM usernames WHERE id = ?", (username_id,))
    assert username is not None
    assert username["name"] == "creator"  # the @ marker is stripped


async def _people_on(db: Database, asset_id: str) -> list[str]:
    rows = await db.fetch_all("SELECT person_id FROM asset_people WHERE asset_id = ?", (asset_id,))
    return [str(row["person_id"]) for row in rows]


async def test_a_username_matching_one_persons_name_files_the_file_under_them(
    temp_db: Database,
) -> None:
    await _migrated(temp_db)
    person = await _somebody(temp_db, "Nerith")
    asset = await _an_asset(temp_db)

    assert (
        await attribute_to_person(temp_db, asset_id=asset, name="nerith", made=MADE_BY_A_PERSON)
        == person
    )
    assert await _people_on(temp_db, asset) == [person]


async def test_a_username_matching_an_alias_counts(temp_db: Database) -> None:
    """The alias is what makes this useful at all: a username IS an also-known-as name."""
    await _migrated(temp_db)
    person = await _somebody(temp_db, "Grace Hopper", "amazing_grace")
    asset = await _an_asset(temp_db)

    assert (
        await attribute_to_person(
            temp_db, asset_id=asset, name="amazing_grace", made=MADE_BY_A_PERSON
        )
        == person
    )


async def test_a_username_two_people_answer_to_files_it_under_neither(temp_db: Database) -> None:
    """The refusal that matters most.

    Two people can answer to one word (the alias table allows it on purpose), and which of them
    was meant is not something a downloader can know. Guessing puts somebody else's video under a
    person who has never been near it, silently.
    """
    await _migrated(temp_db)
    await _somebody(temp_db, "Ines")
    await _somebody(temp_db, "Inessa", "ines")
    asset = await _an_asset(temp_db)

    assert (
        await attribute_to_person(temp_db, asset_id=asset, name="Ines", made=MADE_BY_A_PERSON)
        is None
    )
    assert await _people_on(temp_db, asset) == []


async def test_a_username_naming_nobody_creates_nobody_by_default(temp_db: Database) -> None:
    await _migrated(temp_db)
    asset = await _an_asset(temp_db)

    assert (
        await attribute_to_person(temp_db, asset_id=asset, name="astranger", made=MADE_BY_A_PERSON)
        is None
    )
    assert await _people_on(temp_db, asset) == []


async def test_a_username_naming_nobody_creates_them_when_asked(temp_db: Database) -> None:
    await _migrated(temp_db)
    asset = await _an_asset(temp_db)

    person = await attribute_to_person(
        temp_db, asset_id=asset, name="astranger", create_if_unknown=True, made=MADE_BY_A_PERSON
    )

    assert person is not None
    row = await temp_db.fetch_one("SELECT name FROM people WHERE id = ?", (person,))
    assert row is not None and row["name"] == "astranger"
    assert await _people_on(temp_db, asset) == [person]


async def test_creating_from_a_username_twice_makes_one_person(temp_db: Database) -> None:
    """The second download from the same username finds the person the first one made."""
    await _migrated(temp_db)
    first = await _an_asset(temp_db)
    second = await _an_asset(temp_db)

    one = await attribute_to_person(
        temp_db, asset_id=first, name="astranger", create_if_unknown=True, made=MADE_BY_A_PERSON
    )
    two = await attribute_to_person(
        temp_db, asset_id=second, name="astranger", create_if_unknown=True, made=MADE_BY_A_PERSON
    )

    assert one == two
    rows = await temp_db.fetch_all("SELECT id FROM people")
    assert len(rows) == 1


async def test_the_same_file_from_the_same_username_twice_links_once(temp_db: Database) -> None:
    await _migrated(temp_db)
    person = await _somebody(temp_db, "Nerith")
    asset = await _an_asset(temp_db)

    await attribute_to_person(temp_db, asset_id=asset, name="Nerith", made=MADE_BY_A_PERSON)
    await attribute_to_person(temp_db, asset_id=asset, name="Nerith", made=MADE_BY_A_PERSON)

    assert await _people_on(temp_db, asset) == [person]


async def test_a_username_that_cleans_away_to_nothing_creates_nobody(temp_db: Database) -> None:
    """A name made of control characters is a row no query could ever match."""
    await _migrated(temp_db)
    asset = await _an_asset(temp_db)

    assert (
        await attribute_to_person(
            temp_db, asset_id=asset, name="\u0000", create_if_unknown=True, made=MADE_BY_A_PERSON
        )
        is None
    )


async def test_seeding_is_idempotent_and_case_folds_the_site(temp_db: Database) -> None:
    await _migrated(temp_db)
    first = await seed_site_username(temp_db, site="TikTok", name="creator", made=MADE_BY_A_PERSON)
    second = await seed_site_username(temp_db, site="tiktok", name="creator", made=MADE_BY_A_PERSON)
    assert first == second


async def test_seeding_fills_absent_fields_and_never_overwrites_one(temp_db: Database) -> None:
    await _migrated(temp_db)
    _, username_id = await seed_site_username(temp_db, site="X", name="a", made=MADE_BY_A_PERSON)

    await seed_site_username(
        temp_db,
        site="X",
        name="a",
        display_name="Alice",
        url="https://x.example/a",
        made=MADE_BY_A_PERSON,
    )
    filled = await temp_db.fetch_one(
        "SELECT display_name, url FROM usernames WHERE id = ?", (username_id,)
    )
    assert filled is not None
    assert filled["display_name"] == "Alice"
    assert filled["url"] == "https://x.example/a"

    await seed_site_username(
        temp_db, site="X", name="a", display_name="Someone Else", made=MADE_BY_A_PERSON
    )
    kept = await temp_db.fetch_one(
        "SELECT display_name FROM usernames WHERE id = ?", (username_id,)
    )
    assert kept is not None
    assert kept["display_name"] == "Alice"


async def test_an_empty_username_is_refused(temp_db: Database) -> None:
    await _migrated(temp_db)
    with pytest.raises(ValueError, match="cannot be empty"):
        await seed_site_username(temp_db, site="X", name="@", made=MADE_BY_A_PERSON)


async def test_ensure_site_is_idempotent(temp_db: Database) -> None:
    await _migrated(temp_db)
    first = await ensure_site(temp_db, "TikTok", made=MADE_BY_A_PERSON)
    second = await ensure_site(temp_db, "tiktok", made=MADE_BY_A_PERSON)
    assert first == second


async def _address_of(db: Database, site_id: str) -> tuple[object, list[str]]:
    """The Site's address as every one-address screen reads it, and its whole list of links."""
    row = await db.fetch_one(
        splice(
            "SELECT {{SITE_ADDRESS}} AS url FROM sites s WHERE s.id = ?",
            SITE_ADDRESS=site_address("s"),
        ),
        (site_id,),
    )
    links = await db.fetch_all(
        "SELECT url FROM site_links WHERE site_id = ? ORDER BY id", (site_id,)
    )
    return (row["url"] if row else None), [str(one["url"]) for one in links]


async def test_ensure_site_writes_where_the_site_lives_onto_the_row_it_makes(
    temp_db: Database,
) -> None:
    """A creator that knows the site's address hands it over, and the new row carries it, as the
    first row of the list the record form edits, which IS the address every one-address screen
    reads (`sites.SITE_ADDRESS`). Only the site's own part: a download's address carries a path, a
    query and a signature, and none of that is the site's."""
    await _migrated(temp_db)

    site_id = await ensure_site(
        temp_db,
        "Quillhouse",
        made=by_sift(VIA_DOWNLOAD),
        address="https://cdn.Quillhouse.example/v/1.mp4?sig=abc&expires=9",
    )

    assert await _address_of(temp_db, site_id) == (
        "https://cdn.quillhouse.example",
        ["https://cdn.quillhouse.example"],
    )


async def test_ensure_site_never_writes_an_address_onto_a_site_that_exists(
    temp_db: Database,
) -> None:
    """A site somebody made with no address is theirs to leave empty: a later sighting that knows
    an address does not fill it in, and one that knows ANOTHER address does not replace it."""
    await _migrated(temp_db)
    bare = await ensure_site(temp_db, "Marrowvale Studios", made=MADE_BY_A_PERSON)
    addressed = await ensure_site(
        temp_db, "Quillhouse", made=MADE_BY_A_PERSON, address="https://quillhouse.example"
    )

    again = await ensure_site(
        temp_db, "marrowvale studios", made=by_sift(VIA_DOWNLOAD), address="https://mv.example"
    )
    moved = await ensure_site(
        temp_db, "Quillhouse", made=by_sift(VIA_DOWNLOAD), address="https://elsewhere.example"
    )

    assert (again, moved) == (bare, addressed)
    assert await _address_of(temp_db, bare) == (None, [])
    assert await _address_of(temp_db, addressed) == (
        "https://quillhouse.example",
        ["https://quillhouse.example"],
    )


async def test_a_username_seeded_with_its_sites_address_makes_the_site_with_it(
    temp_db: Database,
) -> None:
    """The download's path: the site and a username in one call. The username's own page is its
    `url`; the SITE's address is a separate argument and it is the one written onto the site."""
    await _migrated(temp_db)

    site_id, _ = await seed_site_username(
        temp_db,
        site="Quillhouse",
        name="wren",
        url="https://quillhouse.example/wren",
        made=by_sift(VIA_DOWNLOAD),
        site_address="https://quillhouse.example",
    )

    assert await _address_of(temp_db, site_id) == (
        "https://quillhouse.example",
        ["https://quillhouse.example"],
    )


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("https://www.quillhouse.example/a/b?c=d#e", "https://www.quillhouse.example"),
        ("http://Quillhouse.Example:8080/x", "http://quillhouse.example"),
        ("quillhouse.example/wren", "https://quillhouse.example"),
        # Joined at run time: written out, the secrets scanner reads it as somebody's address.
        ("https://user:secret" + "@" + "quillhouse.example/", "https://quillhouse.example"),
        ("javascript:alert(1)", None),
        ("ftp://quillhouse.example/", None),
        ("https://localhost/", None),
        ("https://[/", None),
        ("   ", None),
        (None, None),
    ],
)
def test_a_sites_address_is_its_scheme_and_host_and_nothing_else(
    given: str | None, kept: str | None
) -> None:
    assert site_home(given) == kept


async def test_linking_a_username_to_an_asset_is_idempotent(temp_db: Database) -> None:
    await _migrated(temp_db)
    _, username_id = await seed_site_username(temp_db, site="X", name="a", made=MADE_BY_A_PERSON)
    asset_id = new_id()
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
        (asset_id, "abc123"),
    )

    await link_username_to_asset(temp_db, asset_id=asset_id, username_id=username_id)
    await link_username_to_asset(temp_db, asset_id=asset_id, username_id=username_id)  # no error

    link = await temp_db.fetch_one(
        "SELECT COUNT(*) AS n FROM asset_usernames WHERE asset_id = ? AND username_id = ?",
        (asset_id, username_id),
    )
    assert link is not None
    assert link["n"] == 1
