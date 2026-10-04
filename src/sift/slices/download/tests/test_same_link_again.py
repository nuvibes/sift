# SPDX-License-Identifier: AGPL-3.0-or-later
"""A download whose file was deleted leads to the file when the same link lands it again.

The row stays history either way. What it must not do is read as gone (struck through, leading
nowhere) while the very file it fetched is in the library under a later row of the same link: a
person who searches the name finds the file and is shown a row that says it is not there.
"""

from __future__ import annotations

from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.download.schema import initialize_download
from sift.slices.download.service import DownloadService
from sift.slices.download.sources import url_hash

_LINK = "https://cdn.example.org/attachments/1/2/harbour_light.jpg"
_NAME = "harbour_light.jpg"


async def _asset(db: Database) -> str:
    asset_id = new_id()
    await db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'image', 0)",
        (asset_id, f"hash-{asset_id}"),
    )
    return asset_id


async def _row(
    db: Database,
    *,
    url: str = _LINK,
    state: str = "running",
    asset_id: str | None = None,
    filename: str | None = None,
) -> str:
    download_id = new_id()
    await db.execute(
        "INSERT INTO downloads (id, url, url_hash, state, asset_id, filename, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 0)",
        (download_id, url, url_hash(url), state, asset_id, filename),
    )
    return download_id


async def _asset_of(db: Database, download_id: str) -> str | None:
    row = await db.fetch_one("SELECT asset_id FROM downloads WHERE id = ?", (download_id,))
    assert row is not None
    return None if row["asset_id"] is None else str(row["asset_id"])


async def _landed_then_deleted(db: Database, service: DownloadService) -> str:
    """A download that finished, and whose file was then deleted (the key sets the row's NULL)."""
    first = await _row(db)
    landed = await _asset(db)
    await service.mark_done(first, asset_id=landed, site=None, username=None, filename=_NAME)
    await db.execute("DELETE FROM assets WHERE id = ?", (landed,))
    assert await _asset_of(db, first) is None
    return first


async def test_the_same_link_landing_the_same_file_again_gives_the_old_row_that_file(
    download_service: DownloadService, temp_db: Database
) -> None:
    first = await _landed_then_deleted(temp_db, download_service)
    again = await _row(temp_db)
    kept = await _asset(temp_db)
    await download_service.mark_done(again, asset_id=kept, site=None, username=None, filename=_NAME)
    assert await _asset_of(temp_db, first) == kept
    assert await _asset_of(temp_db, again) == kept


async def test_a_different_file_from_the_same_link_leaves_the_old_row_gone(
    download_service: DownloadService, temp_db: Database
) -> None:
    """A link whose contents change lands a different name; the old row never fetched that."""
    first = await _landed_then_deleted(temp_db, download_service)
    again = await _row(temp_db)
    other = await _asset(temp_db)
    await download_service.mark_done(
        again, asset_id=other, site=None, username=None, filename="harbour_dusk.jpg"
    )
    assert await _asset_of(temp_db, first) is None


async def test_the_same_name_from_another_link_leaves_the_old_row_gone(
    download_service: DownloadService, temp_db: Database
) -> None:
    first = await _landed_then_deleted(temp_db, download_service)
    elsewhere = await _row(temp_db, url="https://cdn.example.org/attachments/9/9/harbour_light.jpg")
    other = await _asset(temp_db)
    await download_service.mark_done(
        elsewhere, asset_id=other, site=None, username=None, filename=_NAME
    )
    assert await _asset_of(temp_db, first) is None


async def test_the_step_gives_the_rows_from_before_their_files(
    download_service: DownloadService, temp_db: Database
) -> None:
    """Version 36, over rows written before the job kept them: the same link and the same name.

    The service is asked for only so the library's tables are built."""
    del download_service
    kept = await _asset(temp_db)
    gone = await _row(temp_db, state="done", filename=_NAME)
    landed = await _row(temp_db, state="done", asset_id=kept, filename=_NAME)
    renamed = await _row(temp_db, state="done", filename="harbour_dusk.jpg")
    elsewhere = await _row(
        temp_db, url="https://cdn.example.org/attachments/9/9/x.jpg", state="done", filename=_NAME
    )
    nameless = await _row(temp_db, state="done")
    async with temp_db.write() as connection:
        await initialize_download(connection, 35)
    assert await _asset_of(temp_db, gone) == kept
    assert await _asset_of(temp_db, landed) == kept
    for untouched in (renamed, elsewhere, nameless):
        assert await _asset_of(temp_db, untouched) is None
