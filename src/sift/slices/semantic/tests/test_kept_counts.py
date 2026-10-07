# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file described, forgotten, or described by another model moves the kept count of what Smart
Search has left exactly as a walk of the library counts it."""

from __future__ import annotations

import pytest

from sift.kernel.config import Settings
from sift.kernel.content import ContentStore
from sift.kernel.content.backlog import Term, Totals
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.semantic import schema
from sift.slices.semantic.records import Records
from sift.testing.fixtures import LibraryRoot

pytestmark = pytest.mark.unit


class _Walks:
    async def totals(self, terms: list[Term]) -> list[Totals] | None:
        return None


async def test_each_write_of_a_description_moves_the_kept_count_as_the_walk(
    temp_db: Database, content_store: ContentStore, settings: Settings, library_root: LibraryRoot
) -> None:
    walker = ContentStore(temp_db, settings)
    walker._kept_counts = _Walks()  # type: ignore[assignment]
    records = Records(temp_db)
    ids = [new_id() for _ in range(3)]
    for asset_id in ids:
        await temp_db.execute(
            "INSERT INTO assets (id, identity, identity_version, media_type, added_at, probed_at)"
            " VALUES (?, ?, 1, 'image', 1, 1)",
            (asset_id, f"digest-{asset_id}"),
        )
        await temp_db.execute(
            "INSERT INTO asset_locations"
            " (id, asset_id, root_id, rel_path, filename, status, first_seen_at, last_seen_at)"
            " VALUES (?, ?, ?, ?, ?, 'present', 1, 1)",
            (new_id(), asset_id, library_root.id, f"{asset_id}.jpg", f"{asset_id}.jpg"),
        )

    async def lacking() -> int:
        lack = records.lack("now")
        await content_store.count_lacking([lack])
        await content_store._kept.settled()
        kept = (await content_store.count_lacking([lack])).files
        assert kept == (await walker.count_lacking([lack])).files
        return kept

    assert await lacking() == 3
    for asset_id in ids:
        await records.mark(asset_id, revision="now", frames=1, at_ms=1)
    assert await lacking() == 0
    await records.forget(ids[0])
    assert await lacking() == 1
    await records.mark(ids[1], revision="before", frames=1, at_ms=1)
    assert await lacking() == 2
    assert await records.forget_others("now") == 1
    assert await lacking() == 2
    await records.forget_all()
    assert await lacking() == 3
    await temp_db.execute(
        "INSERT INTO semantic_indexed (asset_id, revision, frames, indexed_at) VALUES (?, 'now', 1, 1)",
        (ids[2],),
    )
    assert await lacking() == 2


async def test_a_library_at_semantic_version_3_gets_the_marks(temp_db: Database) -> None:
    await temp_db.initialize_schema()
    async with temp_db.write() as connection:
        await connection.execute("DROP TRIGGER backlog_semantic_indexed_added")
        await schema.initialize(connection, on_disk=3)
    names = await temp_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'semantic_indexed'"
        " AND name LIKE 'backlog_%'"
    )
    assert len(names) == 3
