# SPDX-License-Identifier: AGPL-3.0-or-later
"""A plain wall reads its total off the stored totals and says what its own statement counts, for
every wall, every way a vault can stand and every way a thing can be hidden; the totals are kept
by the triggers, and a library at version 16 is given them."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace

import pytest

from sift.kernel.access import Concealment, Repository, Viewer, visibility, visibility_walls
from sift.kernel.access.repository import (
    SONG_SORT_KEYS,
    read_collections,
    read_people,
    read_photo_sets,
    read_sites,
    read_songs,
    read_tags,
    wall_people,
)
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.tests.access_helpers import carry_the_song
from sift.testing.fixtures import Actors, World, hide
from sift.testing.library import _INSERT_SONG, _LINK_ASSET_SONG

_READERS = (read_people, read_tags, read_sites, read_collections, read_photo_sets, read_songs)

Wall = Callable[[Viewer], Awaitable[object]]


def _walls(access: Repository, sort: str = "seen") -> dict[str, Wall]:
    return {
        "people": lambda v: access.suggest_people(v, limit=50, sort=sort),
        "tags": lambda v: access.list_tags(v, limit=50, sort=sort),
        "sites": lambda v: access.list_sites(v, limit=50, sort=sort),
        "collections": lambda v: access.list_collections(v, limit=50, sort=sort),
        "photo sets": lambda v: access.list_photo_sets(v, limit=50, sort=sort),
        "songs": lambda v: access.list_songs(v, limit=50, sort=sort),
        "usernames": lambda v: access.list_usernames(v, limit=50, sort=sort),
        "a page past the first": lambda v: access.suggest_people(v, limit=1, offset=1, sort=sort),
    }


def _ways(actors: Actors) -> list[Viewer]:
    return [
        one
        for base in (actors.admin, actors.guest)
        for one in (
            base,
            replace(base, show_hidden=True),
            replace(base, concealment=Concealment.PLACEHOLDER),
        )
    ]


async def _share_the_root(temp_db: Database, world: World, actors: Actors) -> None:
    await temp_db.execute(
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)"
        " VALUES (?, 'root', ?, ?, 'share', 0)",
        (new_id(), world.root, actors.guest.id),
    )


async def _a_second_of_each(temp_db: Database, world: World) -> None:
    """One more of each thing, on `twin`, so a wall with one thing hidden still has a row."""
    for made, linked in (
        ("INSERT INTO tags (id, name, name_sort, created_at) VALUES (?, 'b', 'b', 0)",
         "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)"),
        ("INSERT INTO people (id, name, name_sort, created_at) VALUES (?, 'b', 'b', 0)",
         "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)"),
        ("INSERT INTO collections (id, name, name_sort, created_at) VALUES (?, 'b', 'b', 0)",
         "INSERT INTO collection_items (asset_id, collection_id) VALUES (?, ?)"),
        ("INSERT INTO photo_sets (id, name, name_sort, created_at) VALUES (?, 'b', 'b', 0)",
         "INSERT INTO photo_set_items (asset_id, photo_set_id) VALUES (?, ?)"),
    ):  # fmt: skip
        thing = new_id()
        await temp_db.execute(made, (thing,))
        await temp_db.execute(linked, (world.twin, thing))
    site, username, song = new_id(), new_id(), new_id()
    await temp_db.execute(
        "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, 'b', 'b', 0)", (site,)
    )
    await temp_db.execute(
        "INSERT INTO usernames (id, site_id, name, name_sort, created_at) VALUES (?, ?, 'b', 'b', 0)",
        (username, site),
    )
    await temp_db.execute(
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)", (world.twin, username)
    )
    await temp_db.execute(_INSERT_SONG, (song, "b", "b"))
    await temp_db.execute(_LINK_ASSET_SONG, (world.twin, song))


async def _agree(walls: dict[str, Wall], actors: Actors, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, wall in walls.items():
        for viewer in _ways(actors):
            stored = await wall(viewer)
            with monkeypatch.context() as counted:
                for reader in _READERS:
                    counted.setattr(reader, "plain_order", lambda *_args: None)
                walked = await wall(viewer)
            assert stored == walked, (name, viewer.role, viewer.show_hidden, viewer.concealment)


async def _nothing_differs(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        found = await visibility.differences(connection)
    assert found == [], found[:5]


async def test_a_plain_walls_total_is_what_its_statement_counts(
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await carry_the_song(temp_db, world)
    await _a_second_of_each(temp_db, world)
    await _share_the_root(temp_db, world, actors)
    await _agree(_walls(access), actors, monkeypatch)
    # Each thing hidden in turn by each user, and a network above the Site hidden.
    network = new_id()
    await temp_db.execute(
        "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, 'Nightjar Media', 'nightjar media', 0)",
        (network,),
    )
    await temp_db.execute("UPDATE sites SET parent_id = ? WHERE id = ?", (network, world.site))
    for kind, thing in (
        ("person", world.person),
        ("tag", world.tag),
        ("site", network),
        ("collection", world.collection),
        ("photo_set", world.photo_set),
        ("song", world.song),
        ("asset", world.solo),
    ):
        for user in (actors.admin, actors.guest):
            await hide(temp_db, kind, thing, user.id)
            await _agree(_walls(access), actors, monkeypatch)
            await hide(temp_db, kind, thing, user.id, hidden=False)
    await _nothing_differs(temp_db)


async def test_every_order_of_a_plain_wall_is_the_walls_own(
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each order reads only its own terms, and the rows come back as the whole order puts them."""
    await carry_the_song(temp_db, world)
    await _a_second_of_each(temp_db, world)
    await _share_the_root(temp_db, world, actors)
    await hide(temp_db, "tag", world.tag, actors.admin.id)
    for sort in sorted(SONG_SORT_KEYS):
        await _agree(_walls(access, sort), actors, monkeypatch)


async def test_the_plain_wall_has_no_window_over_its_rows() -> None:
    assert "COUNT(*) OVER ()" not in wall_people.people_query("1", plain="seen")
    assert "viewer_wall_totals" in wall_people.people_query("1", plain="seen")
    assert "COUNT(*) OVER ()" in wall_people.people_query("1")


async def test_the_totals_move_with_a_hide_and_a_share(
    actors: Actors, world: World, temp_db: Database
) -> None:
    async def shown(kind: str) -> int:
        row = await temp_db.fetch_one(
            "SELECT shown FROM viewer_wall_totals WHERE user_id = ? AND kind = ?",
            (actors.guest.id, kind),
        )
        return 0 if row is None else int(row["shown"])

    assert await shown("person") == 0
    await _share_the_root(temp_db, world, actors)
    assert await shown("person") == 1
    await hide(temp_db, "person", world.person, actors.guest.id)
    assert await shown("person") == 0
    await _nothing_differs(temp_db)


async def test_a_network_shared_or_hidden_reaches_a_file_filed_under_its_label(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    network = new_id()
    await temp_db.execute(
        "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, 'Nightjar Media', 'nightjar media', 0)",
        (network,),
    )
    await temp_db.execute("UPDATE sites SET parent_id = ? WHERE id = ?", (network, world.site))
    await temp_db.execute(
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)"
        " VALUES (?, 'site', ?, ?, 'share', 0)",
        (new_id(), network, actors.guest.id),
    )
    assert await access.can_view(actors.guest, world.solo)
    await hide(temp_db, "site", network, actors.guest.id)
    assert await access.is_concealed(actors.guest.id, world.solo)
    await _nothing_differs(temp_db)


async def test_a_library_at_version_sixteen_is_given_the_walls_totals(
    temp_db: Database, world: World, actors: Actors, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The version 17 step: the totals made from the stored counts, the triggers rewritten,
    nothing rebuilt, and run again it changes nothing."""
    await _share_the_root(temp_db, world, actors)
    async with temp_db.write() as connection:
        await visibility._drop_triggers(connection)
        await connection.execute("DROP TABLE viewer_wall_totals")

    async def no_rebuild(_connection: object) -> None:
        raise AssertionError("the step rebuilt the stored answers")

    monkeypatch.setattr(visibility, "refresh_everything", no_rebuild)
    for _ in range(2):
        async with temp_db.write() as connection:
            await visibility.initialize(connection, 16)
    monkeypatch.undo()
    present = await temp_db.fetch_all(
        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'vis_%'"
    )
    wanted = {name: visibility._body_of(ddl) for name, _t, ddl in visibility.triggers()}
    assert {str(r["name"]): visibility._body_of(str(r["sql"])) for r in present} == wanted
    totals = await temp_db.fetch_all("SELECT * FROM viewer_wall_totals")
    assert totals
    await _nothing_differs(temp_db)
    assert {name for name, _t, _d in visibility_walls.triggers()} <= set(wanted)
