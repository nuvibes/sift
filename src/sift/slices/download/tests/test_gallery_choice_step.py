# SPDX-License-Identifier: AGPL-3.0-or-later
"""The v37 step: a download row no longer carries a paste's answer to grouping a gallery.

A downloaded gallery lands as files in its folder, so the column that held that answer goes. Every
row stays, with everything else it carried, and the step finds nothing to do on a table made after
it or when it runs a second time.
"""

from __future__ import annotations

from sift.kernel.db import Database
from sift.slices.download.schema import initialize_download
from sift.slices.download.service import DownloadService

#: The version this step upgrades from, written out: it describes a database in the world.
BEFORE = 36

_COLUMNS = "SELECT name FROM pragma_table_info('downloads')"


async def _columns(db: Database) -> set[str]:
    return {str(row["name"]) for row in await db.fetch_all(_COLUMNS)}


async def _as_before(db: Database) -> None:
    """The table as a library made before the step has it: the column, and a row answering it."""
    await db.execute(
        "ALTER TABLE downloads ADD COLUMN photo_sets INTEGER CHECK(photo_sets IN (0,1))"
    )
    await db.execute(
        "INSERT INTO downloads (id, url, url_hash, created_at, remember, photo_sets)"
        " VALUES ('d1', 'https://example.org/g/harbour', 'h1', 1, 0, 1)"
    )


async def test_the_step_drops_the_column_and_keeps_every_row(
    download_service: DownloadService, temp_db: Database
) -> None:
    """The service is asked for only so the library's tables are built."""
    del download_service
    await _as_before(temp_db)
    async with temp_db.write() as connection:
        await initialize_download(connection, BEFORE)
    assert "photo_sets" not in await _columns(temp_db)
    row = await temp_db.fetch_one("SELECT url, remember FROM downloads WHERE id = 'd1'")
    assert row is not None
    assert (row["url"], row["remember"]) == ("https://example.org/g/harbour", 0)


async def test_a_table_made_after_the_step_and_a_second_run_are_left_alone(
    download_service: DownloadService, temp_db: Database
) -> None:
    del download_service
    assert "photo_sets" not in await _columns(temp_db)
    for _ in range(2):
        async with temp_db.write() as connection:
            await initialize_download(connection, BEFORE)
    assert "photo_sets" not in await _columns(temp_db)
