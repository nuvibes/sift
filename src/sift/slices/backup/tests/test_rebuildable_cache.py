# SPDX-License-Identifier: AGPL-3.0-or-later
"""What is not in the backup can be made again: after a restore with the cache gone, each file's
identity, fingerprint and location are back, which is all a re-scan needs."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.slices.backup.service import BackupService
from sift.testing.fixtures import World

from .test_restore import wipe

pytestmark = pytest.mark.anyio


async def test_a_restore_with_no_cache_left_keeps_everything_needed_to_rebuild_it(
    backup: BackupService,
    prepared_db: Database,
    settings: Settings,
    world: World,
    tmp_path: Path,
) -> None:
    """With the cache directory deleted, a restore brings back each file's identity and location;
    a derivative's record is back or re-derivable."""
    thumbnail = settings.cache_dir / "thumbs" / f"{world.solo}.webp"
    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    thumbnail.write_bytes(b"a picture of a frame")
    await prepared_db.execute(
        "INSERT INTO derivatives (id, asset_id, kind, rel_cache_path, created_at) "
        "VALUES ('01D', ?, 'thumb', ?, 1)",
        (world.solo, f"thumbs/{world.solo}.webp"),
    )

    exported = tmp_path / "export.sqlite3"
    await backup.export_to(exported)

    await wipe(prepared_db)
    shutil.rmtree(settings.cache_dir)
    assert not settings.cache_dir.exists()

    await backup.restore(exported)

    # The identity. Without the fingerprint a re-scan would import the same file as a new asset
    # and every tag, rating and collection entry would be attached to something else.
    asset = await prepared_db.fetch_one("SELECT identity FROM assets WHERE id = ?", (world.solo,))
    assert asset is not None and asset["identity"]

    # Where it sits, so a scan of that folder recognizes the file rather than adding it.
    location = await prepared_db.fetch_one(
        "SELECT rel_path, root_id FROM asset_locations WHERE asset_id = ?", (world.solo,)
    )
    assert location is not None and location["rel_path"]
    assert await prepared_db.fetch_one(
        "SELECT abs_path FROM library_roots WHERE id = ?", (location["root_id"],)
    )

    # And the derivative is a row pointing at a file that is no longer there, which is the
    # ordinary, recoverable state, not a broken one. Nothing about the asset depends on it.
    derivative = await prepared_db.fetch_one(
        "SELECT rel_cache_path FROM derivatives WHERE asset_id = ?", (world.solo,)
    )
    assert derivative is not None
    assert not (settings.cache_dir / str(derivative["rel_cache_path"])).exists()


async def test_the_backup_carries_no_cache_bytes(
    backup: BackupService, settings: Settings, world: World, prepared_db: Database, tmp_path: Path
) -> None:
    """The backup holds a derivative's row and path, never its bytes."""
    picture = b"THUMBNAIL-PIXELS-WHICH-BELONG-IN-THE-CACHE"
    thumbnail = settings.cache_dir / "thumbs" / f"{world.solo}.webp"
    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    thumbnail.write_bytes(picture)
    await prepared_db.execute(
        "INSERT INTO derivatives (id, asset_id, kind, rel_cache_path, created_at) "
        "VALUES ('01D', ?, 'thumb', ?, 1)",
        (world.solo, f"thumbs/{world.solo}.webp"),
    )

    exported = tmp_path / "export.sqlite3"
    await backup.export_to(exported)

    assert picture not in exported.read_bytes()
