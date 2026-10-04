# SPDX-License-Identifier: AGPL-3.0-or-later
"""An incremental pass over the whole library pages through it, however many pages there are."""

from __future__ import annotations

import pytest

from sift.kernel.access.search_index import BATCH, index_assets
from sift.kernel.db import Database

pytestmark = pytest.mark.anyio


async def test_every_page_of_the_library_is_written(temp_db: Database, access: object) -> None:
    files = BATCH + 1
    async with temp_db.write() as connection:
        await connection.executemany(
            "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
            " VALUES (?, ?, 1, 'video', 0)",
            [(f"01HX{n:022d}", f"digest-{n}") for n in range(files)],
        )

    assert await index_assets(temp_db) == files
    indexed = await temp_db.fetch_all("SELECT COUNT(*) AS n FROM assets_fts_rows")
    assert int(indexed[0]["n"]) == files
