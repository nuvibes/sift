# SPDX-License-Identifier: AGPL-3.0-or-later
"""Catalog 86: a library from the build before learns the Stash import's word for a Photo Set."""

from __future__ import annotations

import pytest

from sift.kernel.access.schema import initialize_catalog
from sift.kernel.db import Database
from sift.kernel.migrations import check_allows, widen_a_check

pytestmark = pytest.mark.integration

#: The fragment the step widens, read back the other way to stand a library at 85.
_AT_85 = "origin IN ('manual','download','folder','archive','filename','shoot')"
_AT_86 = "origin IN ('manual','download','folder','archive','filename','shoot','stash_library')"

_A_SET = "INSERT INTO photo_sets (id, name, origin, created_at) VALUES (?, 'harbor', ?, 1)"


async def test_catalog_86_lets_a_set_say_a_stash_library_made_it(temp_db: Database) -> None:
    """Twice is once, and a set already there stays."""
    await temp_db.initialize_schema()
    async with temp_db.write() as connection:
        await widen_a_check(connection, "photo_sets", was=_AT_86, now=_AT_85)
        assert not await check_allows(connection, "photo_sets", "stash_library")
        await connection.execute(_A_SET, ("set-0", "shoot"))
        for _ in range(2):
            await initialize_catalog(connection, 85)
        assert await check_allows(connection, "photo_sets", "stash_library")
        await connection.execute(_A_SET, ("set-1", "stash_library"))
    rows = await temp_db.fetch_all("SELECT id, origin FROM photo_sets ORDER BY id")
    assert [(row["id"], row["origin"]) for row in rows] == [
        ("set-0", "shoot"),
        ("set-1", "stash_library"),
    ]
