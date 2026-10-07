# SPDX-License-Identifier: AGPL-3.0-or-later
"""The default cover's pick is a seek on its kind's index, stopping at the first file that passes,
and a catalog at version 91 is given the indexes."""

from __future__ import annotations

from sift.kernel.access import default_covers, schema
from sift.kernel.db import Database
from sift.testing.fixtures import World

_INDEXES = ("ix_asset_people_first", "ix_collection_items_first", "ix_psi_first")


async def test_each_kinds_pick_reads_its_index_in_order(temp_db: Database, world: World) -> None:
    for kind, index in zip(default_covers._KINDS, _INDEXES, strict=True):
        pick = default_covers._first(kind)
        # Fixed names from the module: nothing from outside reaches the text.
        plan = await temp_db.fetch_all(
            f"EXPLAIN QUERY PLAN SELECT ({pick}) FROM {kind.table}"  # noqa: S608 # nosemgrep: sift-no-string-built-sql
        )
        steps = [str(row["detail"]) for row in plan]
        assert any(index in step for step in steps), (kind.word, steps)
        assert not any("TEMP B-TREE" in step for step in steps), (kind.word, steps)


async def test_a_catalog_at_version_91_is_given_the_picks_indexes(
    temp_db: Database, world: World
) -> None:
    for index in _INDEXES:
        # Fixed names from the module: nothing from outside reaches the text.
        await temp_db.execute(f"DROP INDEX {index}")  # nosemgrep: sift-no-string-built-sql
    for _ in range(2):
        async with temp_db.write() as connection:
            await schema.initialize_catalog(connection, 91)
    found = await temp_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE '%_first'"
    )
    assert {str(row["name"]) for row in found} >= set(_INDEXES)
