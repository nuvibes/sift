# SPDX-License-Identifier: AGPL-3.0-or-later
"""The component's tables: what a new library gets, and what a boot after the first leaves alone."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Imported for its side effect: the ledger's table, which the version 7 step writes.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import catalog
from sift.kernel.access.catalog import MADE_BY_A_PERSON
from sift.kernel.db import Connection, Database
from sift.slices.watermarks import schema, weights
from sift.slices.watermarks.store import Store

pytestmark = pytest.mark.anyio

_EPOCH = 1_700_000_000_000
_ASSET = "01HX0000000000000000000810"
_TABLES = "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'watermark_%'"


async def _parent(connection: Connection) -> None:
    """The table the watermark tables point at. Foreign keys are on, so it exists first."""
    await connection.execute(
        "CREATE TABLE assets (id TEXT PRIMARY KEY, identity TEXT NOT NULL UNIQUE, "
        "media_type TEXT NOT NULL, added_at INTEGER NOT NULL)"
    )
    await connection.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, 'one', 'video', ?)",
        (_ASSET, _EPOCH),
    )


async def test_a_fresh_database_gets_every_table(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        await _parent(connection)
        await schema.initialize(connection, on_disk=0)

    held = {str(row["name"]) for row in await temp_db.fetch_all(_TABLES)}
    assert held == {"watermark_scans", "watermark_reads", "watermark_refusals"}


async def test_a_second_boot_leaves_the_readings_alone(temp_db: Database) -> None:
    """The arm every boot after the first takes, with a scan row to show it was kept."""
    store = Store(temp_db)
    async with temp_db.write() as connection:
        await _parent(connection)
        await schema.initialize(connection, on_disk=0)
        await store.remember_on(
            connection, asset_id=_ASSET, revision=weights.REVISION, identity="one", found=0
        )

        await schema.initialize(connection, on_disk=schema.VERSION)

    rows = await temp_db.fetch_all("SELECT asset_id FROM watermark_scans")
    assert [str(row["asset_id"]) for row in rows] == [_ASSET]


async def test_an_old_filing_receipt_is_given_what_it_filed_under(tmp_path: Path) -> None:
    """A receipt from before the object was recorded folds with nothing, so its file would say its
    filing twice. The step names it from the receipt's payload, and a second run changes nothing."""
    database = Database(tmp_path / "marks.sqlite3", readers=1)
    await database.connect()
    try:
        await database.initialize_schema()
        async with database.write() as connection:
            site, named = await catalog.seed_site_username_on(
                connection, site="OnlyFans", name="riverbend", made=MADE_BY_A_PERSON
            )
            # The Site's "poster unknown" row, which a filing that named nobody lands on.
            unknown = "u-unknown"
            await connection.execute(
                "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, '', 0)",
                (unknown, site),
            )
            for receipt, username_id in (("r-unknown", None), ("r-named", named)):
                payload = {"kind": "filed", "username_id": username_id, "site": "OnlyFans"}
                await connection.execute(
                    "INSERT INTO workbench_decisions (id, queue, title, detail, payload,"
                    " decided_at, verb) VALUES (?, 'watermarks', 't', 'd', ?, 0, 'filed')",
                    (receipt, json.dumps(payload)),
                )
            await schema.initialize(connection, on_disk=6)
            await schema.initialize(connection, on_disk=6)

        rows = await database.fetch_all(
            "SELECT id, object_kind, object_id, object_name FROM workbench_decisions ORDER BY id"
        )
        assert [tuple(row) for row in rows if str(row["id"]).startswith("r-")] == [
            ("r-named", "username", named, None),
            ("r-unknown", "username", unknown, "OnlyFans"),
        ]
    finally:
        await database.close()
