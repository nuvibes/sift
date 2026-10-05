# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Loops wall asks a guest's typed name and tag only of Loops on files they may see."""

from __future__ import annotations

import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
from typing import Any

import pytest

from sift.kernel.access import Effect, ObjectType, Repository, Viewer
from sift.kernel.access.repository.read_loops import LoopReads
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.sorting import sort_key
from sift.kernel.tests.access_helpers import _EPOCH
from sift.testing.fixtures import Actors, World

_SHARED = "zqxv zqxw shared"
_HIDDEN = 40
_LOOP = (
    "INSERT INTO loops (id, asset_id, start_ms, end_ms, name, created_by, created_at)"
    " VALUES (?, ?, 0, 4000, ?, NULL, 0)"
)
_TAG = "INSERT INTO tags (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)"
_TAGGED = "INSERT INTO loop_tags (loop_id, tag_id) VALUES (?, ?)"
_NO_STORE: Any = None

#: Planner statistics; the first two lead SQLite to read the Loops before `viewer_assets`.
_SHAPES = {
    "ten_loops": ("10", "101742 20349 1"),
    "three_loops": ("3", "101742 20349 1"),
    "no_statistics": None,
}


class _Library:
    def __init__(self, shared: str, on_many: str, on_one: str) -> None:
        self.shared, self.on_many, self.on_one = shared, on_many, on_one


@pytest.fixture
async def library(access: Repository, actors: Actors, temp_db: Database, world: World) -> _Library:
    """One Loop the guest may see, carrying both tags; forty they may not, carrying one."""
    on_many, on_one = new_id(), new_id()
    for tag in (on_many, on_one):
        await temp_db.execute(_TAG, (tag, tag, sort_key(tag), _EPOCH))
    shared = new_id()
    await temp_db.execute(_LOOP, (shared, world.solo, _SHARED))
    for tag in (on_many, on_one):
        await temp_db.execute(_TAGGED, (shared, tag))
    for n in range(_HIDDEN):
        hidden = new_id()
        await temp_db.execute(_LOOP, (hidden, (world.twin, world.loose)[n % 2], f"zqxv hidden {n}"))
        await temp_db.execute(_TAGGED, (hidden, on_many))
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    return _Library(shared, on_many, on_one)


async def _shaped(temp_db: Database, shape: str) -> None:
    await temp_db.execute("ANALYZE")
    sized = _SHAPES[shape]
    if sized is None:
        await temp_db.execute("DELETE FROM sqlite_stat1")
        return
    loops, seen = sized
    await temp_db.execute("DELETE FROM sqlite_stat1 WHERE tbl IN ('loops', 'viewer_assets')")
    for row in (
        ("loops", "ix_loops_asset", f"{loops} 1 1"),
        ("loops", "sqlite_autoindex_loops_1", f"{loops} 1"),
        ("viewer_assets", "viewer_assets", seen),
    ):
        await temp_db.execute("INSERT INTO sqlite_stat1 (tbl, idx, stat) VALUES (?, ?, ?)", row)


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


async def _steps(
    db: Database, viewer: Viewer, called: str | None = None, tag: str | None = None
) -> tuple[int, list[str]]:
    counted: Any = _Counted(db.path)
    try:
        page = await LoopReads(counted, _NO_STORE).list_loops(
            viewer, limit=60, called=called, tag=tag
        )
        return counted.steps, [one.id for one in page.items]
    finally:
        counted.connection.close()


@pytest.mark.parametrize("shape", sorted(_SHAPES))
@pytest.mark.parametrize("ask", ["called", "tag"])
async def test_a_guest_pays_nothing_for_loops_on_files_they_may_not_see(
    actors: Actors, library: _Library, temp_db: Database, shape: str, ask: str
) -> None:
    """Forty hidden Loops answering the guest's word or tag cost exactly what none do."""
    await _shaped(temp_db, shape)
    many, one = (
        ({"called": "zqxv"}, {"called": "zqxw"})
        if ask == "called"
        else ({"tag": library.on_many}, {"tag": library.on_one})
    )
    hidden = await _steps(temp_db, actors.guest, **many)
    none = await _steps(temp_db, actors.guest, **one)
    assert hidden[1] == none[1] == [library.shared]
    assert hidden[0] == none[0]
    # The count sees the work: the admin's two answers differ.
    every = await _steps(temp_db, actors.admin, **many)
    assert len(every[1]) == _HIDDEN + 1
    assert every[0] != (await _steps(temp_db, actors.admin, **one))[0]
