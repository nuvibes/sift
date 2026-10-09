# SPDX-License-Identifier: AGPL-3.0-or-later
"""A folder a file's History names is linked by the folder's id, never by its path."""

from __future__ import annotations

import pytest

from sift.kernel.access import Repository
from sift.kernel.access.history import history_of_asset
from sift.kernel.db import Database
from sift.kernel.tests.history_helpers import ASSET, ROOT, make_file, move_it
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.anyio


async def test_the_folder_a_file_arrived_in_is_linked_by_its_id(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_file(temp_db)
    await move_it(temp_db, to="holiday/2024/clip.mp4")
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, rel_path, name) VALUES ('f-old', ?, 'old', 'old')",
        (ROOT,),
    )

    added = next(
        event
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if event.kind == "added"
    )

    assert [(one.kind, one.id, one.href) for one in added.links if one.kind == "folder"] == [
        ("folder", "f-old", "/browse?in=f-old")
    ]


async def test_a_file_with_no_place_left_arrives_in_no_folder(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_file(temp_db)
    await temp_db.execute("DELETE FROM asset_locations WHERE asset_id = ?", (ASSET,))

    added = next(
        event
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if event.kind == "added"
    )

    assert [one for one in added.links if one.kind == "folder"] == []
