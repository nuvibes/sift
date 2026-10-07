# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file whose every copy is marked missing is neither counted as work nor handed out as work.

The Build sheet, Activity's estimate and the Build's own walk read two statements, and the two must
describe one set, and both ask whether any copy is there, or a library with most of its files on a
disconnected drive would be told tens of thousands of hover previews are still to make, and a Build
over it would hand each missing file a task no product can do (see `_ASSET_IDS_PAGE`). A copy that
comes back (`mark_present`) is work again immediately.
"""

from __future__ import annotations

import pytest

from sift.kernel.content import ContentStore
from sift.kernel.content.identity import Lack
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.testing.fixtures import LibraryRoot

pytestmark = pytest.mark.unit

_EPOCH = 1_700_000_000


async def _file(database: Database, root: LibraryRoot, *, status: str) -> str:
    asset_id = new_id()
    await database.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, added_at, probed_at)"
        " VALUES (?, ?, 1, 'video', ?, ?)",
        (asset_id, f"digest-{asset_id}", _EPOCH, _EPOCH),
    )
    await database.execute(
        "INSERT INTO asset_locations"
        " (id, asset_id, root_id, rel_path, filename, status, first_seen_at, last_seen_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id(), asset_id, root.id, f"{asset_id}.mp4", f"{asset_id}.mp4", status, _EPOCH, _EPOCH),
    )
    return asset_id


async def test_a_file_with_no_copy_here_is_neither_counted_nor_walked(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot
) -> None:
    here = await _file(temp_db, library_root, status="present")
    gone = await _file(temp_db, library_root, status="missing")
    everything = Lack("1")

    counted = await content_store.count_lacking([everything])
    walked = (await content_store.asset_ids_page(limit=50)).ids

    assert walked == [here]
    assert counted.files == 1 and counted.each == (1,)

    # The known positive: the copy comes back, and the file is work again in both at the same time.
    await temp_db.execute(
        "UPDATE asset_locations SET status = 'present' WHERE asset_id = ?", (gone,)
    )
    assert sorted((await content_store.asset_ids_page(limit=50)).ids) == sorted([here, gone])
    assert (await content_store.count_lacking([everything])).files == 2
