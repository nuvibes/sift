# SPDX-License-Identifier: AGPL-3.0-or-later
"""A wall of things under "Show a locked tile": a row only a hidden file puts there has no name.

`_LOCKED_TILE` in `repository/entities.py` is the rule. These put it to all five walls it is spliced
into, on the one library shape that makes it bite: `solo` is the only file of its person, its tag,
its collection, its photo set and its Site, so hiding `solo` leaves each of the five with every
file the viewer may see in the vault.

Three things must hold at once, and a rule that keeps two of them is the fault this file guards:
the row stays on the wall with its count (every other card counts it), it carries no name and
answers no typed word, and a read by id (the row's own page) still has the name.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

import pytest

from sift.kernel.access import Concealment, Repository, Viewer
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.sorting import sort_key
from sift.testing.fixtures import Actors, World, hide

#: One wall and the by-id read of the same kind: `(kind, the world's id for it, its real name,
#: the wall's rows for a viewer and a typed word, the row by id)`.
Wall = Callable[[Repository, Viewer, str], Awaitable[list[Any]]]
ById = Callable[[Repository, Viewer, str], Awaitable[Any]]


async def _people(access: Repository, viewer: Viewer, typed: str) -> list[Any]:
    return (await access.suggest_people(viewer, typed, limit=50)).items


async def _tags(access: Repository, viewer: Viewer, typed: str) -> list[Any]:
    return (await access.list_tags(viewer, typed, limit=50)).items


async def _sites(access: Repository, viewer: Viewer, typed: str) -> list[Any]:
    return (await access.list_sites(viewer, typed, limit=50)).items


async def _collections(access: Repository, viewer: Viewer, typed: str) -> list[Any]:
    return (await access.list_collections(viewer, typed, limit=50)).items


async def _photo_sets(access: Repository, viewer: Viewer, typed: str) -> list[Any]:
    return (await access.list_photo_sets(viewer, typed, limit=50)).items


WALLS: list[tuple[str, str, Wall, ById]] = [
    ("person", "person", _people, lambda a, v, i: a.visible_person(v, i)),
    ("tag", "tag", _tags, lambda a, v, i: a.visible_tag(v, i)),
    ("site", "site", _sites, lambda a, v, i: a.visible_site(v, i)),
    ("collection", "collection", _collections, lambda a, v, i: a.visible_collection(v, i)),
    ("photo_set", "photo set", _photo_sets, lambda a, v, i: a.visible_photo_set(v, i)),
]


def _count(row: Any) -> int:
    """A thing counts files; a container counts what is in it."""
    return int(row.asset_count) if hasattr(row, "asset_count") else int(row.item_count)


@pytest.mark.parametrize(("kind", "name", "wall", "by_id"), WALLS, ids=[w[0] for w in WALLS])
async def test_a_row_only_a_hidden_file_puts_on_the_wall_is_a_locked_tile(
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
    kind: str,
    name: str,
    wall: Wall,
    by_id: ById,
) -> None:
    """Listed, counted, nameless and unfindable with the vault shut, and named on its own page."""
    await hide(temp_db, "asset", world.solo, actors.admin.id)
    tiles = replace(actors.admin, concealment=Concealment.PLACEHOLDER)
    row_id = world.object_id(kind)
    assert row_id is not None

    listed = {row.id: row for row in await wall(access, tiles, "")}
    assert row_id in listed, "the row left the wall: every other card still counts it"
    tile = listed[row_id]
    assert tile.locked is True
    assert tile.name == "", "a locked tile said the name only the hidden file reveals"
    assert _count(tile) == 1, "the tile lost the count the card is drawn with"
    assert tile.cover_asset_id is None

    typed = name[:3]
    assert row_id not in {row.id for row in await wall(access, tiles, typed)}, (
        "typing the name found the padlock, which says the name out loud"
    )

    own = await by_id(access, tiles, row_id)
    assert own is not None and own.name == name and own.locked is False, (
        "the row's own page lost the name; only the walls withhold it"
    )

    unlocked = replace(tiles, show_hidden=True)
    opened = {row.id: row for row in await wall(access, unlocked, typed)}
    assert opened[row_id].name == name and opened[row_id].locked is False


async def test_under_leave_nothing_there_is_no_tile_to_draw(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Under "Show nothing" nothing the vault holds is counted, so the row has nought under it:
    a guest is never shown it, and an admin (whose wall lists rows with nothing under them)
    sees it named with a nought, which is what a row nothing was filed under looks like. A padlock
    there would be the one sign of the vault that mode promises not to give."""
    await hide(temp_db, "asset", world.solo, actors.admin.id)
    assert actors.admin.concealment is Concealment.FULLY_GONE

    listed = {row.id: row for row in await _people(access, actors.admin, "")}
    assert listed[world.person].locked is False
    assert (listed[world.person].name, listed[world.person].asset_count) == ("person", 0)


async def test_one_file_left_in_the_open_keeps_the_name(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Every file hidden, not some: a person with one file anybody may see is named by it."""
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (world.loose, world.person)
    )
    await hide(temp_db, "asset", world.solo, actors.admin.id)
    tiles = replace(actors.admin, concealment=Concealment.PLACEHOLDER)

    listed = {row.id: row for row in await _people(access, tiles, "per")}
    assert listed[world.person].name == "person" and listed[world.person].locked is False


async def test_a_locked_tile_sorts_after_every_named_row_by_name(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A padlock placed alphabetically among names says roughly what the name is."""
    other = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
        (other, "zed", sort_key("zed"), 1),
    )
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (world.loose, other)
    )
    await hide(temp_db, "asset", world.solo, actors.admin.id)
    tiles = replace(actors.admin, concealment=Concealment.PLACEHOLDER)

    page = await access.suggest_people(tiles, "", sort="name_az", limit=50)
    order = [row.id for row in page.items]
    assert order.index(other) < order.index(world.person)


async def test_a_song_only_a_hidden_file_carries_withholds_its_artists_with_its_name(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The artists a song credits say what the song is as surely as its name does, so a locked
    tile on the Songs wall carries none, and the song's own page still names them."""
    artist = new_id()
    await temp_db.execute(
        "INSERT INTO songs (id, name, name_sort, created_at) VALUES (?, 'Blue', 'blue', 0)",
        (world.song,),
    )
    await temp_db.execute(
        "INSERT INTO song_files (asset_id, song_id) VALUES (?, ?)", (world.solo, world.song)
    )
    await temp_db.execute(
        "INSERT INTO artists (id, name, name_sort, created_at) VALUES (?, ?, ?, 0)",
        (artist, "Hollis Danforth", sort_key("Hollis Danforth")),
    )
    await temp_db.execute(
        "INSERT INTO song_artists (song_id, artist_id, position) VALUES (?, ?, 0)",
        (world.song, artist),
    )
    await hide(temp_db, "asset", world.solo, actors.admin.id)
    tiles = replace(actors.admin, concealment=Concealment.PLACEHOLDER)

    listed = {row.id: row for row in (await access.list_songs(tiles)).items}
    tile = listed[world.song]
    assert tile.locked is True and tile.name == "" and tile.artists == ()

    own = await access.visible_song(tiles, world.song)
    assert own is not None and own.name == "Blue"
    assert own.artists == ((artist, "Hollis Danforth"),)
