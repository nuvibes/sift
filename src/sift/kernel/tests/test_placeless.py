# SPDX-License-Identifier: AGPL-3.0-or-later
"""The files with no place left, kept by the writes: every write that can give a file its first
place or take its last one is tried, and the kept rows are compared with a walk of the library."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from sift.kernel.content import placeless
from sift.kernel.db import Database

pytestmark = pytest.mark.integration

_EPOCH = 1_700_000_000

_WALKED = (
    "SELECT a.id FROM assets a"
    " WHERE NOT EXISTS (SELECT 1 FROM asset_locations l WHERE l.asset_id = a.id) ORDER BY a.id"
)


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "test.sqlite3")
    await database.connect()
    await database.initialize_schema()
    await database.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        ("root", "Pictures", "/library/root", _EPOCH),
    )
    try:
        yield database
    finally:
        await database.close()


async def _file(database: Database, asset_id: str, *paths: str) -> None:
    await database.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'image', ?)",
        (asset_id, f"digest-{asset_id}", _EPOCH),
    )
    for path in paths:
        await _place(database, asset_id, path)


async def _place(database: Database, asset_id: str, path: str) -> None:
    await database.execute(
        "INSERT INTO asset_locations (id, asset_id, root_id, rel_path, filename,"
        " first_seen_at, last_seen_at) VALUES (?, ?, 'root', ?, ?, ?, ?)",
        (f"at-{path}", asset_id, path, path, _EPOCH, _EPOCH),
    )


async def _kept(database: Database) -> list[str]:
    rows = await database.fetch_all("SELECT asset_id FROM placeless_assets ORDER BY asset_id")
    return [str(row["asset_id"]) for row in rows]


async def _walked(database: Database) -> list[str]:
    return [str(row["id"]) for row in await database.fetch_all(_WALKED)]


async def test_every_write_that_moves_a_files_last_place_moves_its_row(
    database: Database,
) -> None:
    await _file(database, "born-placeless")
    await _file(database, "two-places", "a.jpg", "b.jpg")
    await _file(database, "one-place", "c.jpg")
    await _file(database, "repointed", "d.jpg")
    await _file(database, "deleted", "e.jpg")
    assert await _kept(database) == await _walked(database) == ["born-placeless"]

    await database.execute("DELETE FROM asset_locations WHERE id = 'at-a.jpg'")
    await database.execute("DELETE FROM asset_locations WHERE id = 'at-c.jpg'")
    # A path that now holds other bytes: the location row names another file.
    await database.execute(
        "UPDATE asset_locations SET asset_id = 'born-placeless' WHERE id = 'at-d.jpg'"
    )
    await database.execute("DELETE FROM assets WHERE id = 'deleted'")
    assert await _kept(database) == await _walked(database) == ["one-place", "repointed"]

    await _place(database, "one-place", "c.jpg")
    await database.execute("DELETE FROM library_roots WHERE id = 'root'")
    assert await _kept(database) == await _walked(database)
    assert await _kept(database) == ["born-placeless", "one-place", "repointed", "two-places"]


async def test_the_stranded_read_asks_each_kept_row_for_its_place_again(
    database: Database,
) -> None:
    """A kept row that is wrong can only be one too many, never a placed file offered."""
    await _file(database, "placed", "a.jpg")
    await _file(database, "stranded")
    await database.execute("INSERT INTO placeless_assets (asset_id) VALUES ('placed')")

    rows = await database.fetch_all(placeless.STRANDED, (_EPOCH,))

    assert [str(row["id"]) for row in rows] == ["stranded"]


async def test_a_library_from_before_the_rows_is_filled_when_it_opens(
    database: Database,
) -> None:
    await _file(database, "placed", "a.jpg")
    await _file(database, "stranded")
    async with database.write() as connection:
        await connection.execute("DROP TABLE placeless_assets")
        await connection.execute(
            "DELETE FROM schema_version WHERE component = ?", (placeless.COMPONENT,)
        )

    await database.initialize_schema()

    assert await database.schema_version(placeless.COMPONENT) == placeless.VERSION
    assert await _kept(database) == ["stranded"]


async def test_a_library_already_at_the_version_is_left_as_it_is(database: Database) -> None:
    before = await database.fetch_all("SELECT asset_id FROM placeless_assets")
    async with database.write() as connection:
        await placeless.initialize(connection, on_disk=1)
    assert await database.fetch_all("SELECT asset_id FROM placeless_assets") == before
