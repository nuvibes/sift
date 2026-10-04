# SPDX-License-Identifier: AGPL-3.0-or-later
"""A waiting table kept true at boot: the triggers put back, the rows repaired from the rule.

A migration that rebuilds `assets` takes the triggers on it with it and nothing fails, so the table
drifts. The boot check is what finds that and puts it right, and it has to leave alone a trigger it
did not write.
"""

from __future__ import annotations

import pytest

from sift.kernel.access import waiting as waiting_tables
from sift.kernel.access.waiting import (
    Waiting,
    differences,
    keep_true,
    register_waiting,
    registered,
    start,
)
from sift.kernel.db import Database
from sift.testing.fixtures import World

pytestmark = pytest.mark.anyio

SPEC = Waiting(table="probe_waiting", done="probe_done", filled=("width",))


@pytest.fixture
async def waiting(temp_db: Database, world: World) -> Database:
    async with temp_db.write() as connection:
        await connection.execute(
            "CREATE TABLE probe_waiting (asset_id TEXT PRIMARY KEY"
            " REFERENCES assets(id) ON DELETE CASCADE)"
        )
        await connection.execute("CREATE TABLE probe_done (asset_id TEXT PRIMARY KEY)")
        await start(connection, SPEC)
    await temp_db.execute("UPDATE assets SET width = 8 WHERE id = ?", (world.solo,))
    return temp_db


async def test_a_rebuild_that_took_the_triggers_is_repaired_at_boot(
    waiting: Database, world: World
) -> None:
    async with waiting.read() as connection:
        assert await differences(connection, SPEC) == []

    # A rebuild of `assets` took one trigger, and a file moved while it was gone.
    await waiting.execute("DROP TRIGGER probe_waiting_file_changed")
    await waiting.execute("UPDATE assets SET width = 8 WHERE id = ?", (world.twin,))
    # And somebody else left a trigger under this table's name, which is not this module's to drop.
    await waiting.execute(
        "CREATE TRIGGER probe_waiting_left_here AFTER INSERT ON probe_done BEGIN SELECT 1; END"
    )
    async with waiting.read() as connection:
        assert await differences(connection, SPEC) == [("missing", world.twin)]

    async with waiting.write() as connection:
        await keep_true(connection, SPEC)
        assert await differences(connection, SPEC) == []
        # Nothing wrong the second time, and nothing done.
        await keep_true(connection, SPEC)

    left = await waiting.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = 'probe_waiting_left_here'"
    )
    assert len(left) == 1
    rows = await waiting.fetch_all("SELECT asset_id FROM probe_waiting ORDER BY asset_id")
    assert [row["asset_id"] for row in rows] == sorted([world.solo, world.twin])


def test_a_waiting_table_is_declared_once(
    clean_registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(waiting_tables, "_registered", {})
    register_waiting(SPEC)
    assert registered() == (SPEC,)
    with pytest.raises(ValueError, match="already declared"):
        register_waiting(SPEC)
