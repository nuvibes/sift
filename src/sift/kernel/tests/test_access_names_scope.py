# SPDX-License-Identifier: AGPL-3.0-or-later
"""A name typed on a wall of things is tried, for anybody but an admin, only on what they may see."""

from __future__ import annotations

import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from sift.kernel.access import Effect, ObjectType, Repository, Viewer
from sift.kernel.access.repository.read_collections import CollectionReads
from sift.kernel.access.repository.read_people import PeopleReads
from sift.kernel.access.repository.read_photo_sets import PhotoSetReads
from sift.kernel.access.repository.read_sites import SiteReads
from sift.kernel.access.repository.read_songs import SongReads
from sift.kernel.access.repository.read_tags import TagReads
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.sorting import sort_key
from sift.kernel.tests.access_helpers import _EPOCH, carry_the_song
from sift.testing.fixtures import Actors, World
from sift.testing.library import _INSERT_SONG

#: The name every shared thing is given: it holds both words.
_SHARED = "zqxv zqxw shared"
_HIDDEN = 40

#: Each wall, by the World's name for its shared thing, in `_RENAMED`'s order.
_KINDS = {
    "tags": "tag",
    "people": "person",
    "sites": "site",
    "usernames": "username",
    "songs": "song",
    "collections": "collection",
    "photo_sets": "photo_set",
}


def _shared(world: World, wall: str) -> str:
    return str(getattr(world, _KINDS[wall]))


_RENAMED = (
    "UPDATE tags SET name = ?, name_sort = ? WHERE id = ?",
    "UPDATE people SET name = ?, name_sort = ? WHERE id = ?",
    "UPDATE sites SET name = ?, name_sort = ? WHERE id = ?",
    "UPDATE usernames SET name = ?, name_sort = ? WHERE id = ?",
    "UPDATE songs SET name = ?, name_sort = ? WHERE id = ?",
    "UPDATE collections SET name = ?, name_sort = ? WHERE id = ?",
    "UPDATE photo_sets SET name = ?, name_sort = ? WHERE id = ?",
)
_MADE = (
    "INSERT INTO tags (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
    "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
    "INSERT INTO collections (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
    "INSERT INTO photo_sets (id, name, name_sort, origin, created_at)"
    " VALUES (?, ?, ?, 'manual', ?)",
)
_PERSON = "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)"
_ALIAS = "INSERT INTO people_aliases (id, person_id, alias) VALUES (?, ?, ?)"
_USERNAME = (
    "INSERT INTO usernames (id, site_id, name, name_sort, created_at) VALUES (?, ?, ?, ?, ?)"
)


@pytest.fixture
async def library(access: Repository, actors: Actors, temp_db: Database, world: World) -> World:
    """One shared thing of each kind named `_SHARED`, beside many nobody shared named `zqxv`."""
    await carry_the_song(temp_db, world)
    for statement, wall in zip(_RENAMED, _KINDS, strict=True):
        await temp_db.execute(statement, (_SHARED, sort_key(_SHARED), _shared(world, wall)))
    for n in range(_HIDDEN):
        name = f"zqxv hidden {n}"
        for statement in _MADE:
            await temp_db.execute(statement, (new_id(), name, sort_key(name), _EPOCH))
        await temp_db.execute(_USERNAME, (new_id(), world.site, name, sort_key(name), _EPOCH))
        await temp_db.execute(_INSERT_SONG, (new_id(), name, sort_key(name)))
        # Half the people are found by their name, half by an alias alone.
        person = new_id()
        called = name if n % 2 else f"kept {n}"
        await temp_db.execute(_PERSON, (person, called, sort_key(called), _EPOCH))
        await temp_db.execute(_ALIAS, (new_id(), person, f"zqxv alias {n}"))
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    return world


class _Counted:
    """A plain connection that counts the statement steps every read takes."""

    def __init__(self, path: Any) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.steps = 0
        self.connection.set_progress_handler(self._step, 1)

    def _step(self) -> int:
        self.steps += 1
        return 0

    async def fetch_all(self, statement: Any, params: Any = ()) -> list[Any]:
        return self.connection.execute(getattr(statement, "sql", statement), params).fetchall()

    async def fetch_one(self, statement: Any, params: Any = ()) -> Any:
        rows = await self.fetch_all(statement, params)
        return rows[0] if rows else None


async def _ids(page: Awaitable[Any]) -> list[str]:
    return [one.id for one in (await page).items]


_Read = Callable[[Any, Viewer, str], Awaitable[list[str]]]
_NO_STORE: Any = None

#: Each wall's box, read as its route reads it.
_WALLS: dict[str, _Read] = {
    "tags": lambda db, v, w: _ids(TagReads(db, _NO_STORE).list_tags(v, w, anywhere=True)),
    "people": lambda db, v, w: _ids(PeopleReads(db, _NO_STORE).suggest_people(v, w, anywhere=True)),
    "sites": lambda db, v, w: _ids(SiteReads(db, _NO_STORE).list_sites(v, w, anywhere=True)),
    "usernames": lambda db, v, w: _ids(
        SiteReads(db, _NO_STORE).list_usernames(v, w, anywhere=True)
    ),
    "songs": lambda db, v, w: _ids(SongReads(db, _NO_STORE).list_songs(v, w, anywhere=True)),
    "collections": lambda db, v, w: _ids(
        CollectionReads(db, _NO_STORE).list_collections(v, w, anywhere=True)
    ),
    "photo_sets": lambda db, v, w: _ids(
        PhotoSetReads(db, _NO_STORE).list_photo_sets(v, w, anywhere=True)
    ),
}


async def _resolved(db: Any, viewer: Viewer, term: str) -> list[str]:
    return list((await PeopleReads(db, _NO_STORE).resolve_alias_targets(viewer, term)).person_ids)


async def _steps(db: Database, read: _Read, viewer: Viewer, word: str) -> tuple[int, list[str]]:
    counted = _Counted(db.path)
    try:
        found = await read(counted, viewer, word)
        return counted.steps, found
    finally:
        counted.connection.close()


@pytest.mark.parametrize("wall", sorted(_WALLS))
async def test_a_name_costs_a_guest_nothing_for_things_they_may_not_see(
    actors: Actors, library: World, temp_db: Database, wall: str
) -> None:
    """`zqxv` also names forty hidden things, `zqxw` none: the same steps, the same one thing."""
    read = _WALLS[wall]
    many = await _steps(temp_db, read, actors.guest, "zqxv")
    one = await _steps(temp_db, read, actors.guest, "zqxw")
    assert many[1] == one[1] == [_shared(library, wall)]
    assert many[0] == one[0]
    # The count sees the work: the admin's two answers differ.
    every = await _steps(temp_db, read, actors.admin, "zqxv")
    assert len(every[1]) > 1
    assert every[0] != (await _steps(temp_db, read, actors.admin, "zqxw"))[0]


async def test_a_name_in_a_query_costs_a_guest_nothing_for_people_they_may_not_see(
    actors: Actors, library: World, temp_db: Database
) -> None:
    """A hidden person's exact name or alias, against a name nobody has."""
    hidden = [
        await _steps(temp_db, _resolved, actors.guest, term)
        for term in ("zqxv hidden 1", "zqxv alias 2", "zqxw hidden 1")
    ]
    assert {steps for steps, _ in hidden} == {hidden[0][0]}
    assert [found for _, found in hidden] == [[], [], []]
    assert (await _steps(temp_db, _resolved, actors.guest, _SHARED))[1] == [library.person]
    admin = [
        await _steps(temp_db, _resolved, actors.admin, term)
        for term in ("zqxv hidden 1", "zqxv alias 2", "zqxw hidden 1")
    ]
    assert [len(found) for _, found in admin] == [1, 1, 0]
    assert admin[0][0] != admin[2][0]
