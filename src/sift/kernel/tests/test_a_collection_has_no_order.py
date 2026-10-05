# SPDX-License-Identifier: AGPL-3.0-or-later
"""A collection's files have no stored order: a wall of them is sorted like every wall of files."""

from __future__ import annotations

import pytest

import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import AssetFilter, default_covers, schema
from sift.kernel.access.repository.asset_orders import (
    DEFAULT_SORT,
    RELEVANCE,
    SIMILARITY,
    ordering_for,
)
from sift.kernel.db import Database
from sift.kernel.migrations import column_exists
from sift.testing.fixtures import World

pytestmark = pytest.mark.anyio

_HELD = "SELECT collection_id, asset_id, added_at FROM collection_items ORDER BY 1, 2"
_COVER_TRIGGERS = "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name LIKE ?"


def test_a_named_order_wins_and_words_or_a_ranking_choose_one_otherwise() -> None:
    ranked = (("a", 0.1),)
    assert ordering_for("oldest", AssetFilter(text="dusk")) == "oldest"
    assert ordering_for(RELEVANCE, AssetFilter(neighbours=ranked)) == SIMILARITY
    assert ordering_for(RELEVANCE, AssetFilter(text="dusk", neighbours=ranked)) == RELEVANCE
    assert ordering_for(None, AssetFilter(text="dusk")) == RELEVANCE
    assert ordering_for(None, AssetFilter(text="dusk"), by_meaning=True) == SIMILARITY
    assert ordering_for(None, AssetFilter(neighbours=ranked)) == SIMILARITY
    assert ordering_for(None, AssetFilter()) == DEFAULT_SORT


async def test_a_library_with_a_stored_order_loses_it_and_keeps_every_file(
    temp_db: Database, world: World
) -> None:
    """The cover rule's triggers read the column, so the step writes them again around the drop."""
    async with temp_db.write() as connection:
        await connection.execute("ALTER TABLE collection_items ADD COLUMN position INTEGER")
        await connection.execute("UPDATE collection_items SET position = 0")
        await connection.execute("DROP TRIGGER default_cover_collection_filed")
        await connection.execute(
            "CREATE TRIGGER default_cover_collection_filed AFTER INSERT ON collection_items"
            " BEGIN SELECT NEW.position; END"
        )
    held = [tuple(one) for one in await temp_db.fetch_all(_HELD)]
    assert held

    async with temp_db.write() as connection:
        await schema.initialize_catalog(connection, 90)
        assert not await column_exists(connection, "collection_items", "position")

    assert [tuple(one) for one in await temp_db.fetch_all(_HELD)] == held
    present = await temp_db.fetch_all(_COVER_TRIGGERS, ("default_cover_%",))
    written = {str(one["name"]): " ".join(str(one["sql"]).split()) for one in present}
    wanted = {
        name: " ".join(ddl.replace("IF NOT EXISTS ", "").split())
        for name, ddl in default_covers.triggers().items()
    }
    assert written == wanted
