# SPDX-License-Identifier: AGPL-3.0-or-later
"""A folder's files are read from the folder's own rows for a viewer who sees every file, so the
read costs the folder and not the library; a guest's read stays tied to their own files."""

from __future__ import annotations

import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
from typing import Any, cast

from sift.kernel.access import AllOf, AssetFilter, Viewer, Where
from sift.kernel.access.repository.read_files import FileReads
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.tests.access_helpers import _EPOCH
from sift.testing.fixtures import Actors


class _Counted:
    """A plain connection that counts the steps every read takes."""

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


async def _files(db: Database, root: str, folder: str | None, n: int) -> list[str]:
    made = []
    for _ in range(n):
        asset_id = new_id()
        await db.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', ?)",
            (asset_id, f"digest-{asset_id}", _EPOCH),
        )
        await db.execute(
            "INSERT INTO asset_locations"
            " (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id(), asset_id, root, folder, asset_id, "f.mp4", _EPOCH, _EPOCH),
        )
        made.append(asset_id)
    return made


async def _read(db: Database, viewer: Viewer, folder: str) -> tuple[int, int, list[str]]:
    """The folder's page and its total, as one count of steps and the answer."""
    counted = _Counted(db.path)
    try:
        asked = AssetFilter(where=AllOf((Where("folder", (0,)),)), folder_scope=((folder,),))
        page = await FileReads(cast(Any, counted), cast(Any, None)).visible_assets(
            viewer, limit=50, asset_filter=asked
        )
        return counted.steps, page.total, sorted(item.asset.id for item in page.items)
    finally:
        counted.connection.close()


async def test_a_folders_files_cost_the_admin_the_folder_not_the_library(
    actors: Actors, temp_db: Database
) -> None:
    root, folder = new_id(), new_id()
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (root, "root", "/library", _EPOCH),
    )
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, NULL, ?, ?)",
        (folder, root, "dusk", "dusk"),
    )
    inside = await _files(temp_db, root, folder, 3)
    await _files(temp_db, root, None, 200)
    await temp_db.execute("ANALYZE")
    before = await _read(temp_db, actors.admin, folder)
    await _files(temp_db, root, None, 400)
    await temp_db.execute("ANALYZE")
    after = await _read(temp_db, actors.admin, folder)
    assert before[1:] == after[1:] == (3, sorted(inside))
    assert after[0] == before[0]
