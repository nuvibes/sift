# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stored verdict is the facts, after every kind of change the facts can undergo.

`viewer_assets` is what every listing reads instead of resolving permission itself, so the one
property everything rests on is that it never lags the tables it is derived from. The database
keeps it by trigger, and these put every input the verdict reads through a change and then ask
`differences` (a full recompute compared with what is stored) to answer with nothing.

Written against the world fixture rather than a fresh library, because the world has every shape
the rules care about: a file with two copies in two roots, a folder chain three deep, a file that
belongs to a tag, a person, a collection, a photo set and a site, and a file sitting in a root
with no folder at all.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

import pytest

from sift.kernel.access import Effect, ObjectType, Repository, visibility
from sift.kernel.access.visibility import VERSION
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.tests.access_helpers import VAULT_CASES, VaultCase, conceal
from sift.testing.fixtures import Actors, World, hide

pytestmark = pytest.mark.integration


async def _nothing_differs(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        found = await visibility.differences(connection)
    assert found == [], f"the stored verdict disagrees with the facts: {found[:5]}"


async def test_a_fresh_world_is_stored_exactly(temp_db: Database, world: World) -> None:
    """Every row the world's seeding wrote went through the triggers, and they agree."""
    await _nothing_differs(temp_db)


@pytest.mark.parametrize("case", VAULT_CASES, ids=[case.name for case in VAULT_CASES])
async def test_every_way_of_concealing_is_stored_exactly(
    case: VaultCase, temp_db: Database, world: World, actors: Actors
) -> None:
    """Each hide the truth table knows, written straight into the row the rule reads."""
    await conceal(temp_db, world, case, actors)
    await _nothing_differs(temp_db)
    target = world.object_id(case.target)
    assert target is not None
    await hide(temp_db, case.kind, target, actors.admin.id, hidden=False)
    await _nothing_differs(temp_db)


@pytest.mark.parametrize(
    ("object_type", "named"),
    [
        (ObjectType.GLOBAL, None),
        (ObjectType.ROOT, "root"),
        (ObjectType.FOLDER, "mid"),
        (ObjectType.ITEM, "solo"),
        (ObjectType.TAG, "tag"),
        (ObjectType.PERSON, "person"),
        (ObjectType.COLLECTION, "collection"),
        (ObjectType.PHOTO_SET, "photo_set"),
        (ObjectType.SITE, "site"),
    ],
    ids=lambda value: value.value if isinstance(value, ObjectType) else str(value),
)
async def test_every_kind_of_grant_is_stored_exactly(
    object_type: ObjectType,
    named: str | None,
    temp_db: Database,
    world: World,
    actors: Actors,
    access: Repository,
) -> None:
    """A share and a restrict on every kind of object, made and taken back."""
    object_id = None if named is None else world.object_id(named)
    for effect in (Effect.SHARE, Effect.RESTRICT):
        await access.grant(object_type, object_id, actors.guest.id, effect)
        await _nothing_differs(temp_db)
    for effect in (Effect.SHARE, Effect.RESTRICT):
        await access.revoke(object_type, object_id, actors.guest.id, effect)
        await _nothing_differs(temp_db)


async def test_copies_and_memberships_coming_and_going_are_stored_exactly(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """The writes an import and a tagging pass make, as plain rows. With users in the
    library: without them there are no stored rows, and every comparison passes over nothing."""
    await temp_db.execute(
        "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
        " status, first_seen_at, last_seen_at)"
        " VALUES ('L-new', ?, ?, ?, 'top/mid/leaf/new.jpg', 'new.jpg', 'present', 0, 0)",
        (world.loose, world.root, world.leaf),
    )
    await _nothing_differs(temp_db)
    await temp_db.execute(
        "UPDATE asset_locations SET folder_id = ? WHERE id = 'L-new'", (world.top,)
    )
    await _nothing_differs(temp_db)
    await temp_db.execute("DELETE FROM asset_locations WHERE id = 'L-new'")
    await _nothing_differs(temp_db)
    await temp_db.execute(
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (world.twin, world.tag)
    )
    await _nothing_differs(temp_db)
    await temp_db.execute(
        "DELETE FROM asset_tags WHERE asset_id = ? AND tag_id = ?", (world.twin, world.tag)
    )
    await _nothing_differs(temp_db)


async def test_a_folder_moved_and_removed_is_stored_exactly(
    temp_db: Database, world: World, actors: Actors, access: Repository
) -> None:
    """The ancestry follows a move, and a folder that becomes unreachable conceals its subtree."""
    await access.grant(ObjectType.FOLDER, world.mid, actors.guest.id, Effect.SHARE)
    await temp_db.execute("UPDATE folders SET parent_id = ? WHERE id = ?", (world.top, world.leaf))
    await _nothing_differs(temp_db)
    # Under one of its own descendants: a loop. Nothing under it is reachable any more.
    await temp_db.execute("UPDATE folders SET parent_id = ? WHERE id = ?", (world.leaf, world.top))
    await _nothing_differs(temp_db)
    assert await access.get_asset(actors.admin, world.solo) is None
    # Out of the loop again by hand. This is the one move the trigger cannot follow (a subtree
    # with no rows left cannot be given them back without recursion), and the boot pass is what
    # notices and rebuilds. The gap is asserted rather than hidden, so a trigger that one day CAN
    # follow it has to come and change this test.
    await temp_db.execute("UPDATE folders SET parent_id = NULL WHERE id = ?", (world.top,))
    async with temp_db.write() as connection:
        assert await visibility.differences(connection), "expected the ancestry to be behind"
        await visibility.keep_true(connection)
    await _nothing_differs(temp_db)
    assert await access.get_asset(actors.admin, world.solo) is not None
    await temp_db.execute("DELETE FROM folders WHERE id = ?", (world.leaf,))
    await _nothing_differs(temp_db)


async def test_what_a_foreign_key_takes_away_is_stored_exactly(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """A file, a root and a user ending through their own rows, with everything under them
    going by cascade. The counts are kept by statements the recompute runs, not by triggers on
    the rows themselves, so a cascade that removed rows behind the recompute's back would leave
    every count that mentioned them behind."""
    await hide(temp_db, "asset", world.twin, actors.admin.id)
    await temp_db.execute("DELETE FROM assets WHERE id = ?", (world.twin,))
    await _nothing_differs(temp_db)
    await temp_db.execute("DELETE FROM library_roots WHERE id = ?", (world.root_two,))
    await _nothing_differs(temp_db)
    await temp_db.execute("DELETE FROM users WHERE id = ?", (actors.guest.id,))
    await _nothing_differs(temp_db)


async def test_copies_under_a_folder_are_counted_exactly(
    temp_db: Database, world: World, actors: Actors, access: Repository
) -> None:
    """The copies present under each folder, per user: a copy going missing and coming back,
    a folder with children deleted, and the count the tree draws agreeing with the rows."""
    top = await access.get_folder(actors.admin, world.top)
    assert top is not None
    before = await access.folder_file_count(actors.admin, top)
    assert before is not None and before > 0
    await temp_db.execute(
        "UPDATE asset_locations SET status = 'missing' WHERE asset_id = ? AND folder_id = ?",
        (world.solo, world.leaf),
    )
    await _nothing_differs(temp_db)
    assert await access.folder_file_count(actors.admin, top) == before - 1
    await temp_db.execute(
        "UPDATE asset_locations SET status = 'present' WHERE asset_id = ? AND folder_id = ?",
        (world.solo, world.leaf),
    )
    await _nothing_differs(temp_db)
    assert await access.folder_file_count(actors.admin, top) == before
    await temp_db.execute("DELETE FROM folders WHERE id = ?", (world.top,))
    await _nothing_differs(temp_db)


async def test_a_user_arriving_and_changing_role_is_stored_exactly(
    temp_db: Database, world: World
) -> None:
    await temp_db.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at)"
        " VALUES ('U-new', 'newcomer', 'x', 'guest', 0)"
    )
    await _nothing_differs(temp_db)
    await temp_db.execute("UPDATE users SET role = 'admin' WHERE id = 'U-new'")
    await _nothing_differs(temp_db)
    await temp_db.execute("DELETE FROM users WHERE id = 'U-new'")
    await _nothing_differs(temp_db)


async def test_the_stored_counts_agree_with_the_rows(
    temp_db: Database, world: World, actors: Actors, access: Repository
) -> None:
    """`viewer_stats` is what an unfiltered total is answered from, so it is checked too."""
    await hide(temp_db, "asset", world.solo, actors.admin.id)
    permitted, concealed = await access.visible_counts(actors.admin)
    rows = await temp_db.fetch_all(
        "SELECT COUNT(*) AS n, COALESCE(SUM(concealed), 0) AS c FROM viewer_assets"
        " WHERE user_id = ?",
        (actors.admin.id,),
    )
    assert (permitted, concealed) == (int(rows[0]["n"]), int(rows[0]["c"]))
    assert concealed == 1
    page = await access.visible_assets(actors.admin)
    assert page.total == permitted - concealed
    revealed = replace(actors.admin, show_hidden=True)
    assert (await access.visible_assets(revealed)).total == permitted


async def test_the_repair_drops_a_trigger_an_earlier_build_left(
    temp_db: Database, world: World
) -> None:
    """A trigger under a name this build retired keeps running whatever it was written to do:
    the per-row counter an earlier build kept would count every row twice beside this build's
    statements. The boot pass treats one as a disagreement and rebuilds without it."""
    retired = "vis_stats_insert"
    assert retired in visibility.RETIRED_TRIGGERS
    await temp_db.execute(
        "CREATE TRIGGER vis_stats_insert AFTER INSERT ON viewer_assets BEGIN"
        " UPDATE viewer_stats SET permitted = permitted + 1 WHERE user_id = NEW.user_id; END"
    )
    async with temp_db.write() as connection:
        await visibility.keep_true(connection)
    names = await temp_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = ?", (retired,)
    )
    assert names == [], "the retired trigger was left running"
    await _nothing_differs(temp_db)


async def test_the_repair_rebuilds_what_a_missing_trigger_would_have_missed(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """The boot invariant: a trigger gone, a change missed, and the answers put back."""
    await temp_db.execute("DROP TRIGGER vis_asset_user_state_insert")
    await hide(temp_db, "asset", world.solo, actors.admin.id)
    async with temp_db.write() as connection:
        assert await visibility.differences(connection), "the drop should have opened a gap"
        await visibility.keep_true(connection)
    await _nothing_differs(temp_db)


async def test_a_pair_counts_the_files_two_things_share_and_the_vault_takes_its_part(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """One pair, read straight off the table: every permitted file carrying both, with the ones the
    vault holds back counted beside it rather than out of it: the reader decides which to use."""
    rows = await temp_db.fetch_all(
        "SELECT kind_a, id_a, kind_b, id_b, permitted, concealed FROM viewer_pair_counts"
        " WHERE user_id = ? ORDER BY permitted DESC LIMIT 1",
        (actors.admin.id,),
    )
    assert rows, "the admin sees files carrying two things, so there is a pair"
    pair = rows[0]
    await hide(temp_db, "asset", world.solo, actors.admin.id)
    await _nothing_differs(temp_db)
    after = await temp_db.fetch_all(
        "SELECT permitted, concealed FROM viewer_pair_counts WHERE user_id = ? AND kind_a = ?"
        " AND id_a = ? AND kind_b = ? AND id_b = ?",
        (actors.admin.id, pair["kind_a"], pair["id_a"], pair["kind_b"], pair["id_b"]),
    )
    assert int(after[0]["permitted"]) == int(pair["permitted"])


# --- what the verdict reads is what the triggers watch ---------------------------------------

#: A table named after FROM or JOIN in the verdict's own text.
_TABLE = re.compile(r"\b(?:FROM|JOIN)\s+([a-z_]+)\b")

#: A name the statement DECLARES for itself: `name(columns) AS (`, which is a CTE and not a table.
#:
#: Derived from the text like everything else here, rather than listed beside the fragment that
#: happens to carry one today. The reach a site has over its labels is spliced in as a recursive
#: CTE, so the verdict now names something no trigger could ever watch, and the next fragment to
#: bring one would otherwise have to be remembered about here. Requiring `AS (` immediately after
#: the parentheses is what keeps this from swallowing a function call.
_DECLARED = re.compile(r"\b([a-z_]+)\s*\([^)]*\)\s+AS\s*\(")

#: Tables the verdict reads that no trigger needs to watch, each with the reason.
_WATCHED_ELSEWHERE = {
    # The pairs source, never a fact.
    "users": "a user's arrival and role are watched; the rest of the row decides nothing",
    # Read for its root id only; a copy references its root and goes with it.
    "library_roots": "a root's existence is a copy's existence, and copies are watched",
    # The verdict is what the triggers write, not what they watch.
    "viewer_assets": "the answer",
    # Written by the triggers on `folders`; a fact derived from another that is watched.
    "folder_ancestry": "kept by the folder triggers, and checked against the tree at boot",
    # Scratch the recompute fills from the facts a moment before the verdict reads it.
    "visibility_places": "filled by the recompute that reads it, from tables that are watched",
}


def test_every_table_the_verdict_reads_carries_a_trigger() -> None:
    """Derived from the verdict's text, so a rule that starts reading a new table fails here
    until that table is watched. A hand-kept list beside the triggers would be the second copy
    this module exists to end."""
    # The verdict and the fill of the places it reads: the place rules moved into the fill, and
    # a table read only there is still one the verdict depends on.
    text = visibility._VERDICT_ROWS + visibility._PLACE_ROWS
    read = set(_TABLE.findall(text)) - set(_WATCHED_ELSEWHERE) - set(_DECLARED.findall(text))
    assert "users" in visibility.triggered_tables()
    # The statement really does declare one, so a regex that stopped matching would silently
    # widen the check rather than narrow it, which is the direction that passes quietly.
    assert "reach_up" in set(_DECLARED.findall(text))
    # ...and the tables that fragment reads are watched like any other.
    assert {"sites", "usernames", "asset_usernames"} <= visibility.triggered_tables()
    missing = sorted(read - visibility.triggered_tables())
    assert not missing, f"the verdict reads {missing} and no trigger watches them"


def test_the_verdict_is_one_statement_and_every_trigger_is_it() -> None:
    """Every trigger that writes rows writes them with the same text the backfill uses, and in
    this build only the shared steps write them: every other trigger calls a step."""
    verdict_shape = " ".join(visibility._VERDICT_ROWS.split()).split("FROM (<<PAIRS>>)")[0]
    built = visibility._built()
    for form, expected in ((built.triggers, 2), (built.version_13_triggers, 40)):
        writers = [ddl for _name, _table, ddl in form if "INSERT INTO viewer_assets" in ddl]
        assert len(writers) >= expected
        for ddl in writers:
            assert verdict_shape in " ".join(ddl.split()), "a trigger carries a different verdict"
    named = {name for name, _t, ddl in built.triggers if "INSERT INTO viewer_assets" in ddl}
    assert named == {"vis_recompute_give", "vis_recompute_user"}


def test_the_schema_carries_each_step_once() -> None:
    """Every connection parses the whole schema before its first statement, so the triggers'
    text is a cost on every connection. Each step of a recompute is written once and called."""
    built = visibility._built()
    shared = sum(len(ddl) for _name, _table, ddl in built.triggers)
    in_place = sum(len(ddl) for _name, _table, ddl in built.version_13_triggers)
    assert shared * 10 < in_place, f"{shared} bytes of trigger text against {in_place} in place"
    for name, _table, ddl in built.triggers:
        if not name.startswith("vis_recompute_"):
            assert len(ddl) < 4_000, f"{name} carries a step instead of calling it"


def _present(rows: Sequence[Any]) -> dict[str, str]:
    return {str(row["name"]): visibility._body_of(str(row["sql"])) for row in rows}


_VIS_TRIGGERS = "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'vis_%'"


async def _written_as_version_13(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        await visibility._drop_triggers(connection)
        for _name, _table, ddl in visibility._built().version_13_triggers:
            await connection.execute(ddl)


async def test_the_version_14_step_rewrites_the_triggers_and_keeps_every_answer(
    temp_db: Database, world: World, actors: Actors, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A library at 13 has every step written into every trigger. The step rewrites them to call
    the shared steps, rebuilds nothing (the rules are the same), and run again does nothing."""
    await hide(temp_db, "tag", world.tag, actors.guest.id)
    await _written_as_version_13(temp_db)
    stored = await temp_db.fetch_all("SELECT * FROM viewer_assets ORDER BY user_id, asset_id")

    async def no_rebuild(_connection: object) -> None:
        raise AssertionError("the step rebuilt the stored answers")

    monkeypatch.setattr(visibility, "refresh_everything", no_rebuild)
    for _ in range(2):
        async with temp_db.write() as connection:
            await visibility.initialize(connection, 13)
    wanted = {name: visibility._body_of(ddl) for name, _t, ddl in visibility.triggers()}
    assert _present(await temp_db.fetch_all(_VIS_TRIGGERS)) == wanted
    after = await temp_db.fetch_all("SELECT * FROM viewer_assets ORDER BY user_id, asset_id")
    assert [tuple(row) for row in after] == [tuple(row) for row in stored]
    monkeypatch.undo()
    await _nothing_differs(temp_db)
    await hide(temp_db, "tag", world.tag, actors.guest.id, hidden=False)
    await _nothing_differs(temp_db)


async def test_the_version_14_step_rebuilds_where_a_trigger_was_not_as_written(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """A trigger missing at 13 may have let a change go unseen, so the step rebuilds every answer
    rather than rewriting the triggers over a stored answer that may be behind."""
    await _written_as_version_13(temp_db)
    await temp_db.execute("DROP TRIGGER vis_asset_user_state_insert")
    await hide(temp_db, "asset", world.solo, actors.admin.id)
    async with temp_db.write() as connection:
        assert await visibility.differences(connection)
        await visibility.initialize(connection, 13)
    await _nothing_differs(temp_db)


async def test_a_grant_made_again_decides_nothing_and_one_changed_in_place_decides_both(
    temp_db: Database, world: World, actors: Actors, access: Repository
) -> None:
    """The re-grant's upsert changes no column the verdict reads, so no recompute runs (a step
    that ran would empty the staged pairs). A grant edited in place re-decides what it named
    before and what it names now."""
    await access.grant(ObjectType.TAG, world.tag, actors.guest.id, Effect.SHARE)
    await temp_db.execute("INSERT INTO visibility_pending (user_id, asset_id) VALUES ('u', 'a')")
    await access.grant(ObjectType.TAG, world.tag, actors.guest.id, Effect.SHARE)
    left = await temp_db.fetch_all("SELECT user_id FROM visibility_pending")
    assert [row["user_id"] for row in left] == ["u"], "a grant made again ran a recompute"
    await temp_db.execute("DELETE FROM visibility_pending")
    await temp_db.execute(
        "UPDATE acl_grants SET object_type = 'person', object_id = ?"
        " WHERE object_type = 'tag' AND object_id = ?",
        (world.person, world.tag),
    )
    await _nothing_differs(temp_db)
    await temp_db.execute("UPDATE acl_grants SET effect = 'restrict' WHERE object_type = 'person'")
    await _nothing_differs(temp_db)


async def test_a_hide_moved_to_another_thing_is_stored_exactly(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """A merge moves a hide from the person going to the one kept by changing the row's key and
    not its flag: both people's files are decided again."""
    kept = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, 'Odile Fenwick',"
        " 'odile fenwick', 0)",
        (kept,),
    )
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (world.twin, kept)
    )
    await hide(temp_db, "person", world.person, actors.admin.id)
    await _nothing_differs(temp_db)
    await temp_db.execute(
        "UPDATE OR IGNORE person_user_state SET person_id = ? WHERE person_id = ?",
        (kept, world.person),
    )
    await _nothing_differs(temp_db)


async def test_the_folder_move_trigger_keeps_the_ancestry_exact(
    temp_db: Database, world: World
) -> None:
    rows = await temp_db.fetch_all(
        "SELECT ancestor_id, depth FROM folder_ancestry WHERE folder_id = ? ORDER BY depth",
        (world.leaf,),
    )
    assert [(row["ancestor_id"], row["depth"]) for row in rows] == [
        (world.leaf, 0),
        (world.mid, 1),
        (world.top, 2),
    ]


# --- a write the engine ignores -----------------------------------------------------------------
#
# The engine fires a BEFORE trigger before it decides whether the row will land. A duplicate the
# statement asked to have ignored therefore fires the half that takes the file's rows and counts
# away and never the half that puts them back. Every membership write in the tree is idempotent
# in exactly this way, so this is the case that would empty a wall.


async def _stored(
    temp_db: Database,
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]], list[tuple[object, ...]]]:
    rows = await temp_db.fetch_all(
        "SELECT user_id, asset_id, concealed FROM viewer_assets ORDER BY user_id, asset_id"
    )
    stats = await temp_db.fetch_all(
        "SELECT user_id, permitted, concealed FROM viewer_stats ORDER BY user_id"
    )
    counts = await temp_db.fetch_all(
        "SELECT user_id, kind, object_id, permitted, concealed FROM viewer_entity_counts"
        " ORDER BY user_id, kind, object_id"
    )
    return [tuple(r) for r in rows], [tuple(r) for r in stats], [tuple(r) for r in counts]


@dataclass(frozen=True)
class Duplicate:
    """One watched membership table and the three ignored writes against it. `give_twin` is a
    real write, so that an update of the twin's row onto the solo's collides with a row that is
    there."""

    table: str
    named: str
    give_twin: str
    ignore_insert: str
    nothing_insert: str
    ignore_update: str


DUPLICATES = [
    Duplicate(
        "asset_tags",
        "tag",
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)",
        "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id) VALUES (?, ?)",
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
        "UPDATE OR IGNORE asset_tags SET asset_id = ? WHERE asset_id = ? AND tag_id = ?",
    ),
    Duplicate(
        "asset_people",
        "person",
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)",
        "INSERT OR IGNORE INTO asset_people (asset_id, person_id) VALUES (?, ?)",
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
        "UPDATE OR IGNORE asset_people SET asset_id = ? WHERE asset_id = ? AND person_id = ?",
    ),
    Duplicate(
        "collection_items",
        "collection",
        "INSERT INTO collection_items (asset_id, collection_id) VALUES (?, ?)",
        "INSERT OR IGNORE INTO collection_items (asset_id, collection_id) VALUES (?, ?)",
        "INSERT INTO collection_items (asset_id, collection_id) VALUES (?, ?)"
        " ON CONFLICT DO NOTHING",
        "UPDATE OR IGNORE collection_items SET asset_id = ?"
        " WHERE asset_id = ? AND collection_id = ?",
    ),
    Duplicate(
        "photo_set_items",
        "photo_set",
        "INSERT INTO photo_set_items (asset_id, photo_set_id) VALUES (?, ?)",
        "INSERT OR IGNORE INTO photo_set_items (asset_id, photo_set_id) VALUES (?, ?)",
        "INSERT INTO photo_set_items (asset_id, photo_set_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
        "UPDATE OR IGNORE photo_set_items SET asset_id = ? WHERE asset_id = ? AND photo_set_id = ?",
    ),
    Duplicate(
        "asset_usernames",
        "username",
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
        "INSERT OR IGNORE INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
        "UPDATE OR IGNORE asset_usernames SET asset_id = ? WHERE asset_id = ? AND username_id = ?",
    ),
]


@pytest.mark.parametrize("case", DUPLICATES, ids=[case.table for case in DUPLICATES])
async def test_an_ignored_duplicate_membership_write_changes_nothing(
    case: Duplicate, temp_db: Database, world: World, actors: Actors
) -> None:
    """The solo file already carries the membership. Filing it again three ways the engine
    ignores leaves every stored answer byte for byte as it was."""
    obj = world.object_id(case.named)
    await temp_db.execute(case.give_twin, (world.twin, obj))
    await _nothing_differs(temp_db)
    before = await _stored(temp_db)
    assert any(row[1] == world.solo for row in before[0]), "the file has stored rows to lose"
    await temp_db.execute(case.ignore_insert, (world.solo, obj))
    assert await _stored(temp_db) == before, "an ignored insert moved a stored answer"
    await temp_db.execute(case.nothing_insert, (world.solo, obj))
    assert await _stored(temp_db) == before, "a do-nothing insert moved a stored answer"
    await temp_db.execute(case.ignore_update, (world.solo, world.twin, obj))
    assert await _stored(temp_db) == before, "an ignored update moved a stored answer"
    await _nothing_differs(temp_db)


async def test_an_ignored_duplicate_copy_write_changes_nothing(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """The same for a copy on disk: a second row at a path already held, and an update that
    would move a copy onto another copy's path."""
    before = await _stored(temp_db)
    await temp_db.execute(
        "INSERT OR IGNORE INTO asset_locations (id, asset_id, root_id, folder_id, rel_path,"
        " filename, status, first_seen_at, last_seen_at)"
        " SELECT 'L-dup', asset_id, root_id, folder_id, rel_path, filename, 'present', 0, 0"
        "   FROM asset_locations WHERE asset_id = ?",
        (world.solo,),
    )
    assert await _stored(temp_db) == before, "an ignored copy insert moved a stored answer"
    copies = await temp_db.fetch_all(
        "SELECT id, root_id, rel_path FROM asset_locations WHERE asset_id = ? ORDER BY id",
        (world.twin,),
    )
    assert len(copies) == 2, "the twin has two copies"
    await temp_db.execute(
        "UPDATE OR IGNORE asset_locations SET root_id = ?, rel_path = ? WHERE id = ?",
        (copies[0]["root_id"], copies[0]["rel_path"], copies[1]["id"]),
    )
    assert await _stored(temp_db) == before, "an ignored copy update moved a stored answer"
    await _nothing_differs(temp_db)


async def test_the_declared_keys_are_the_tables_own(temp_db: Database, world: World) -> None:
    """Every key a counted kind declares is a key its table enforces, and every key the table
    enforces is declared: the guard is only as complete as this list."""
    for one in visibility.counted():
        declared = {tuple(key) for key in one.keys}
        enforced: set[tuple[str, ...]] = set()
        primary = await temp_db.fetch_all(
            "SELECT name, pk FROM pragma_table_info(?) WHERE pk > 0 ORDER BY pk", (one.table,)
        )
        if primary:
            enforced.add(tuple(str(row["name"]) for row in primary))
        for index in await temp_db.fetch_all(
            'SELECT name FROM pragma_index_list(?) WHERE "unique" = 1', (one.table,)
        ):
            columns = await temp_db.fetch_all(
                "SELECT name FROM pragma_index_info(?) ORDER BY seqno", (index["name"],)
            )
            enforced.add(tuple(str(row["name"]) for row in columns))
        assert declared == enforced, f"{one.table}: declared {declared}, enforced {enforced}"


def test_no_write_replaces_a_row_of_a_watched_table() -> None:
    """`OR REPLACE` deletes the row in the way without firing its delete trigger, so the counts
    that row carried would never be taken away. Nothing in the tree may write a watched table
    that way; a duplicate is refused, ignored or updated, and the guard covers all three."""
    from pathlib import Path

    watched = "|".join(sorted(visibility.triggered_tables()))
    replacing = re.compile(
        rf"(?:INSERT\s+OR\s+REPLACE\s+INTO|REPLACE\s+INTO)\s+(?:{watched})\b", re.I
    )
    source = Path(visibility.__file__).parents[2]
    offenders = [
        str(path.relative_to(source))
        for path in source.rglob("*.py")
        if "tests" not in path.parts and replacing.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"a REPLACE write into a watched table: {offenders}"


# --- the seams the module guards for itself ------------------------------------------------------


def test_a_counted_kind_is_registered_once_and_has_to_declare_its_keys() -> None:
    """A second registration under a kind already counted would count every file under it twice,
    a second kind over a table whose keys it disagrees about would guard that table's BEFORE halves
    two ways at once, and one with no keys would have unguarded BEFORE halves: the fault that
    empties a wall. All three are refused before anything is recorded, so the list is as it was."""
    before = visibility.counted()
    known = before[0]
    same_kind = visibility.Counted(known.kind, "x", "c", "some_other_table", keys=(("a",),))
    with pytest.raises(ValueError, match="already known"):
        visibility.register_counted(same_kind, component=visibility.COMPONENT)
    other_keys = visibility.Counted(
        "fresh", known.source, known.column, known.table, keys=(("not_its_key",),)
    )
    with pytest.raises(ValueError, match="disagree about the table's keys"):
        visibility.register_counted(other_keys, component=visibility.COMPONENT)
    keyless = visibility.Counted("fresh", "fresh_table", "c", "fresh_table")
    with pytest.raises(ValueError, match="must declare the table's keys"):
        visibility.register_counted(keyless, component=visibility.COMPONENT)
    assert visibility.counted() == before, "a refused registration left something behind"


def test_two_kinds_over_one_table_are_one_watcher_on_both_kinds_columns() -> None:
    """The triggers on a table re-decide the whole file, which moves every kind's count for it, so
    two kinds over one table are ONE set of triggers, firing on either kind's columns. A watcher
    that kept only the first kind's columns would leave the second kind's count behind whenever a
    write changed only a column the second one reads."""
    first = visibility.Counted("one", "t", "a", "shared", "UPDATE OF asset_id, a", keys=(("id",),))
    second = visibility.Counted("two", "t", "b", "shared", "UPDATE OF asset_id, b", keys=(("id",),))
    lone = visibility.Counted("three", "u", "c", "alone", keys=(("id",),))
    merged = visibility._watching([first, second, lone])
    assert sorted(merged) == ["alone", "shared"]
    assert merged["shared"].update == "UPDATE OF asset_id, a, b"
    assert merged["shared"].keys == (("id",),)
    assert merged["alone"] is lone
    # A bare UPDATE watches every column, so it is what the merge keeps.
    every = visibility.Counted("four", "t", "d", "shared", keys=(("id",),))
    assert visibility._watching([first, every])["shared"].update == "UPDATE"


def test_a_guard_cannot_be_built_over_no_keys() -> None:
    """The BEFORE half of a trigger is guarded on the table's keys, and a table declaring none
    would get a guard that is always open: the unguarded trigger, back under a guard's name."""
    with pytest.raises(ValueError, match="declares no keys"):
        visibility._not_already_held("asset_tags", (), updating=False)


async def test_a_trigger_this_build_has_never_known_is_reported_and_left_running(
    temp_db: Database, world: World
) -> None:
    """One under the module's own prefix that is neither this build's nor on the retired list:
    an earlier build's, or made by hand. This module never builds a DROP from a name it read at
    run time, so it is named at boot and left where it is, and the answers are rebuilt around it
    rather than trusted."""
    await temp_db.execute(
        "CREATE TRIGGER vis_from_nowhere AFTER INSERT ON viewer_stats BEGIN SELECT 1; END"
    )
    async with temp_db.write() as connection:
        await visibility.keep_true(connection)
    names = await temp_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = 'vis_from_nowhere'"
    )
    assert [row["name"] for row in names] == ["vis_from_nowhere"], "an unknown trigger was dropped"
    await _nothing_differs(temp_db)
    await temp_db.execute("DROP TRIGGER vis_from_nowhere")


_CUT_A_MARK = (
    "INSERT INTO loops (id, asset_id, start_ms, end_ms, name, created_by, created_at)"
    " VALUES (?, ?, 0, 1000, NULL, NULL, 0)"
)

_PAIRS_OF_A_MARK = (
    "SELECT kind_a, id_a, permitted, concealed FROM viewer_pair_counts"
    " WHERE user_id = ? AND kind_b = 'loop' AND id_b = ? ORDER BY kind_a, id_a"
)


async def test_a_mark_is_counted_with_every_thing_its_file_carries(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """A mark cut, concealed with its file, and deleted: each moves the pairs a card reads its Loops
    cell from, and each leaves the stored answers agreeing with the facts."""
    mark = new_id()
    await temp_db.execute(_CUT_A_MARK, (mark, world.solo))
    await _nothing_differs(temp_db)
    rows = await temp_db.fetch_all(_PAIRS_OF_A_MARK, (actors.admin.id, mark))
    reached = {(str(row["kind_a"]), str(row["id_a"])) for row in rows}
    assert {
        ("person", world.person),
        ("tag", world.tag),
        ("collection", world.collection),
        ("username", world.username),
    } <= reached, reached
    assert all(int(row["permitted"]) == 1 and int(row["concealed"]) == 0 for row in rows)

    await hide(temp_db, "asset", world.solo, actors.admin.id)
    await _nothing_differs(temp_db)
    rows = await temp_db.fetch_all(_PAIRS_OF_A_MARK, (actors.admin.id, mark))
    assert rows, "a concealed file's mark is still counted, beside the concealed number"
    assert all(int(row["concealed"]) == 1 for row in rows), "the vault's part was not moved"

    await temp_db.execute("DELETE FROM loops WHERE id = ?", (mark,))
    await _nothing_differs(temp_db)
    assert not await temp_db.fetch_all(_PAIRS_OF_A_MARK, (actors.admin.id, mark))


_PARTNERS = (
    "SELECT permitted, shown FROM viewer_partner_counts"
    " WHERE user_id = ? AND kind = ? AND object_id = ? AND partner_kind = ?"
)


async def test_a_pair_crossing_nought_moves_the_partner_totals_of_both_its_sides(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """Two marks cut from one file make two pairs with each thing the file carries, each alive on
    that file alone. Concealing the file takes both out of the vault-shut total and leaves them in
    the whole one; deleting a mark takes it out of both. Every step leaves the totals agreeing with
    the pairs.

    TWO marks and not one is what holds the trigger's own guard: a recompute takes both pairs to
    nought and gives both back, and the second to come back finds the total's row already made. A
    conflict clause there would be overridden by the upsert that wrote the pair, and that second
    crossing would fail with a UNIQUE error."""
    admin = actors.admin.id
    first, second = new_id(), new_id()
    await temp_db.execute(_CUT_A_MARK, (first, world.solo))
    await temp_db.execute(_CUT_A_MARK, (second, world.solo))
    await _nothing_differs(temp_db)
    before = await temp_db.fetch_all(_PARTNERS, (admin, "person", world.person, "loop"))
    assert before, "the person's file carries two marks, so the person has loop partners"
    whole, shown = int(before[0]["permitted"]), int(before[0]["shown"])
    assert whole >= 2 and shown >= 2
    mark_side = await temp_db.fetch_all(_PARTNERS, (admin, "loop", first, "person"))
    assert [(int(r["permitted"]), int(r["shown"])) for r in mark_side] == [(1, 1)]

    await hide(temp_db, "asset", world.solo, admin)
    await _nothing_differs(temp_db)
    mark_side = await temp_db.fetch_all(_PARTNERS, (admin, "loop", first, "person"))
    assert [(int(r["permitted"]), int(r["shown"])) for r in mark_side] == [(1, 0)]
    after = await temp_db.fetch_all(_PARTNERS, (admin, "person", world.person, "loop"))
    assert (int(after[0]["permitted"]), int(after[0]["shown"])) == (whole, shown - 2)

    await temp_db.execute("DELETE FROM loops WHERE id = ?", (first,))
    await _nothing_differs(temp_db)
    assert not await temp_db.fetch_all(_PARTNERS, (admin, "loop", first, "person"))
    after = await temp_db.fetch_all(_PARTNERS, (admin, "person", world.person, "loop"))
    assert int(after[0]["permitted"]) == whole - 1


_SITE_COUNT = (
    "SELECT permitted, concealed FROM viewer_entity_counts"
    " WHERE user_id = ? AND kind = 'site' AND object_id = ?"
)


async def _site_count(temp_db: Database, user: str, site: str) -> int:
    rows = await temp_db.fetch_all(_SITE_COUNT, (user, site))
    return int(rows[0]["permitted"]) if rows else 0


async def _a_site(temp_db: Database, name: str, parent: str | None = None) -> str:
    site = new_id()
    await temp_db.execute(
        "INSERT INTO sites (id, name, name_sort, created_at, parent_id) VALUES (?, ?, ?, 0, ?)",
        (site, name, name, parent),
    )
    return site


async def _a_username(temp_db: Database, site: str | None, name: str) -> str:
    username = new_id()
    await temp_db.execute(
        "INSERT INTO usernames (id, site_id, name, name_sort, created_at) VALUES (?, ?, ?, ?, 0)",
        (username, site, name, name),
    )
    return username


async def test_a_sites_files_are_counted_once_through_every_label_and_every_change(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """A Site's stored count is the number of FILES it reaches, across its usernames and every label
    under it (counted once however many usernames a file is filed under), and it follows the four
    ways that reach changes without a file moving: a username moving site, a label moving network,
    a username going and a site going. Each of those runs a foreign-key action or changes the
    column the count reads, which is what the BEFORE halves are for."""
    admin = actors.admin.id
    network = await _a_site(temp_db, "network")
    label = await _a_site(temp_db, "label", network)
    other = await _a_site(temp_db, "elsewhere")
    first = await _a_username(temp_db, label, "first")
    second = await _a_username(temp_db, label, "second")
    for username in (first, second):
        # The same file under two usernames of one label: one file, not two.
        await temp_db.execute(
            "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
            (world.twin, username),
        )
    await _nothing_differs(temp_db)
    assert await _site_count(temp_db, admin, label) == 1
    assert await _site_count(temp_db, admin, network) == 1

    # A username moving to another site: the label keeps the file through the other username.
    await temp_db.execute("UPDATE usernames SET site_id = ? WHERE id = ?", (other, first))
    await _nothing_differs(temp_db)
    assert await _site_count(temp_db, admin, other) == 1
    assert await _site_count(temp_db, admin, network) == 1

    # The label leaving its network: the network loses the file.
    await temp_db.execute("UPDATE sites SET parent_id = NULL WHERE id = ?", (label,))
    await _nothing_differs(temp_db)
    assert await _site_count(temp_db, admin, network) == 0
    await temp_db.execute("UPDATE sites SET parent_id = ? WHERE id = ?", (network, label))
    await _nothing_differs(temp_db)
    assert await _site_count(temp_db, admin, network) == 1

    # A username going, by a cascade that cannot see it: the label and the network lose the file.
    await temp_db.execute("DELETE FROM usernames WHERE id = ?", (second,))
    await _nothing_differs(temp_db)
    assert await _site_count(temp_db, admin, label) == 0
    assert await _site_count(temp_db, admin, network) == 0

    # A label going, with a username still on it: the network above it loses what it reached.
    third = await _a_username(temp_db, label, "third")
    await temp_db.execute(
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)", (world.solo, third)
    )
    assert await _site_count(temp_db, admin, network) == 1
    await temp_db.execute("DELETE FROM sites WHERE id = ?", (label,))
    await _nothing_differs(temp_db)
    assert await _site_count(temp_db, admin, network) == 0

    # A network going with a label under it: the label keeps its own count.
    label = await _a_site(temp_db, "label again", network)
    await temp_db.execute("UPDATE usernames SET site_id = ? WHERE id = ?", (label, third))
    await _nothing_differs(temp_db)
    await temp_db.execute("DELETE FROM sites WHERE id = ?", (network,))
    await _nothing_differs(temp_db)
    assert await _site_count(temp_db, admin, label) == 1


async def test_an_ignored_move_of_a_username_onto_a_taken_name_changes_nothing(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """`UPDATE OR IGNORE` onto a username the other site already has fires the BEFORE half and
    never the AFTER one; unguarded, the username's files would lose their rows and counts for
    good."""
    elsewhere = await _a_site(temp_db, "elsewhere")
    await _a_username(temp_db, elsewhere, "handle")
    await temp_db.execute(
        "UPDATE OR IGNORE usernames SET site_id = ? WHERE id = ?", (elsewhere, world.username)
    )
    await _nothing_differs(temp_db)
    assert await _site_count(temp_db, actors.admin.id, world.site) == 1


async def test_a_library_already_at_the_version_is_left_exactly_as_it_is(temp_db: Database) -> None:
    """The component's first step creates the pinned shape; a library recorded at the pin has nothing
    to do, and `initialize` says so by touching nothing: the same statements a second time would
    fail on tables that exist, or worse, remake what the triggers keep true."""
    before = await temp_db.fetch_all(
        "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
    )
    async with temp_db.write() as connection:
        await visibility.initialize(connection, VERSION)
    after = await temp_db.fetch_all("SELECT type, name, sql FROM sqlite_master ORDER BY type, name")
    assert [tuple(row) for row in after] == [tuple(row) for row in before]


_STORED_SIZE = (
    "SELECT permitted, concealed, permitted_bytes, concealed_bytes FROM viewer_entity_counts"
    " WHERE user_id = ? AND kind = ? AND object_id = ?"
)
_STORED_LIBRARY_SIZE = "SELECT permitted_bytes, concealed_bytes FROM viewer_stats WHERE user_id = ?"


async def _sized(temp_db: Database, user: str, kind: str, object_id: str) -> tuple[int, int]:
    rows = await temp_db.fetch_all(_STORED_SIZE, (user, kind, object_id))
    return (int(rows[0]["permitted_bytes"]), int(rows[0]["concealed_bytes"])) if rows else (0, 0)


async def test_the_stored_sizes_are_the_sizes_of_exactly_the_files_counted(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """Beside every stored count, the size of the files it counts: a file's size written after it
    was counted moves the sums, a file filed under two usernames of one Site adds its size to the
    Site once, the vault holds back its share of the bytes as it holds back its share of the files,
    and a kind that does not count files keeps nought."""
    admin = actors.admin.id
    sizes = {world.solo: 1_000, world.twin: 20_000, world.loose: 300_000}
    for asset, size in sizes.items():
        await temp_db.execute("UPDATE assets SET size_bytes = ? WHERE id = ?", (size, asset))
    await _nothing_differs(temp_db)
    library = await temp_db.fetch_all(_STORED_LIBRARY_SIZE, (admin,))
    assert int(library[0]["permitted_bytes"]) == sum(sizes.values())
    assert await _sized(temp_db, admin, "person", world.person) == (1_000, 0)

    label = await _a_site(temp_db, "label")
    for name in ("first", "second"):
        username = await _a_username(temp_db, label, name)
        await temp_db.execute(
            "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
            (world.twin, username),
        )
    await _nothing_differs(temp_db)
    assert await _sized(temp_db, admin, "site", label) == (20_000, 0)

    await hide(temp_db, "asset", world.solo, admin)
    await _nothing_differs(temp_db)
    assert await _sized(temp_db, admin, "person", world.person) == (1_000, 1_000)
    library = await temp_db.fetch_all(_STORED_LIBRARY_SIZE, (admin,))
    assert int(library[0]["concealed_bytes"]) == 1_000

    await temp_db.execute("UPDATE assets SET size_bytes = ? WHERE id = ?", (5_000, world.solo))
    await _nothing_differs(temp_db)
    assert await _sized(temp_db, admin, "person", world.person) == (5_000, 5_000)

    mark = new_id()
    await temp_db.execute(_CUT_A_MARK, (mark, world.solo))
    await _nothing_differs(temp_db)
    assert await _sized(temp_db, admin, "loop", mark) == (0, 0)


async def test_a_library_at_version_ten_is_given_its_sizes(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """The version 11 step: the byte columns where they are missing, then every stored answer
    rebuilt, so a library counted before sizes were stored says them from its first boot."""
    await temp_db.execute("UPDATE assets SET size_bytes = ? WHERE id = ?", (7_000, world.solo))
    async with temp_db.write() as connection:
        await visibility._drop_triggers(connection)
        for table in ("viewer_stats", "viewer_entity_counts"):
            for column in ("permitted_bytes", "concealed_bytes"):
                # Fixed names from the loop above: nothing from outside reaches the text.
                # nosemgrep: sift-no-string-built-sql
                await connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        await visibility.initialize(connection, 10)
    await _nothing_differs(temp_db)
    assert await _sized(temp_db, actors.admin.id, "person", world.person) == (7_000, 0)


@pytest.mark.parametrize("on_disk", [10, 11, 12])
async def test_each_step_that_counts_a_new_kind_rebuilds_every_stored_answer(
    temp_db: Database, world: World, actors: Actors, on_disk: int
) -> None:
    """Versions 12 and 13 made a song a counted, hidden and shared kind, so a library at 11 or 12
    has its stored answers rebuilt from the facts; at 10 the step over sizes does the same, and
    adds no byte column already there (a step stopped half way)."""
    await hide(temp_db, "tag", world.tag, actors.guest.id)
    async with temp_db.write() as connection:
        await connection.execute("DELETE FROM viewer_entity_counts")
        assert await visibility.differences(connection)
        await visibility.initialize(connection, on_disk)
    await _nothing_differs(temp_db)


async def test_the_library_total_says_the_size_of_what_it_counts(
    temp_db: Database, world: World, actors: Actors, access: Repository
) -> None:
    """Browse's own total, unfiltered, off the stored sizes: every file the viewer may see, the
    vault's share left out while it is shut, and in again once it is open."""
    admin = actors.admin
    sizes = {world.solo: 1_000, world.twin: 20_000, world.loose: 300_000}
    for asset, size in sizes.items():
        await temp_db.execute("UPDATE assets SET size_bytes = ? WHERE id = ?", (size, asset))
    whole = await access.visible_assets(admin, limit=1)
    assert (whole.total, whole.total_bytes) == (3, 321_000)
    await hide(temp_db, "asset", world.solo, admin.id)
    shut = await access.visible_assets(admin, limit=1)
    assert (shut.total, shut.total_bytes) == (2, 320_000)
    opened = await access.visible_assets(replace(admin, show_hidden=True), limit=1)
    assert (opened.total, opened.total_bytes) == (3, 321_000)
