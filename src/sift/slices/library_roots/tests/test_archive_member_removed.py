# SPDX-License-Identifier: AGPL-3.0-or-later
"""A picture removed from Sift out of a ZIP stays removed, through every later scan.

The archive still holds the picture and Sift does not rewrite it, so the scan is what has to
remember: without that the next pass would take it in again under a new id, and the removal would
be a press that did nothing. Driven through the real deleter and the real scan, on a real ZIP.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository, Role, Viewer
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, LibraryStore, Root
from sift.kernel.db import Database, Row
from sift.kernel.jobs import JobContext
from sift.slices.delete.service import Deleter, InsideAnArchive
from sift.slices.library_roots import jobs
from sift.slices.library_roots.service import REMOVED_FROM_SIFT, LibraryService
from sift.slices.library_roots.tests.conftest import RecordingReindexer
from sift.slices.library_roots.tests.test_archive_scan import gallery
from sift.testing.fixtures import create_user

Context = Callable[[str, dict[str, object]], Awaitable[JobContext]]


class _NoPlaybackCache:
    def discard_asset(self, asset_id: str) -> int:
        return 0


@pytest.fixture
def deleter(
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    access: Repository,
    service: LibraryService,
) -> Deleter:
    return Deleter(temp_db, content_store, library_store, access, _NoPlaybackCache(), service)


@pytest.fixture
async def admin(temp_db: Database) -> Viewer:
    return await create_user(temp_db, Role.ADMIN)


async def _scan(
    context_for: Context,
    root: Root,
    settings: Settings,
    service: LibraryService,
) -> None:
    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=RecordingReindexer())


async def _pictures(database: Database) -> dict[str, str]:
    rows = await database.fetch_all("SELECT rel_path, asset_id FROM asset_locations", ())
    return {str(row["rel_path"]): str(row["asset_id"]) for row in rows}


async def _remembered(service: LibraryService, root_id: str) -> list[Row]:
    """What the root is refusing, read as the Skipped screen's store reads it."""
    return await service._db.fetch_all(
        "SELECT * FROM scan_rejections WHERE root_id = ? ORDER BY rel_path LIMIT 10", (root_id,)
    )


async def test_a_picture_removed_from_sift_stays_gone_after_every_rescan(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    deleter: Deleter,
    temp_db: Database,
    admin: Viewer,
) -> None:
    """Two rescans, not one: the first is where a sweep that could not find the remembered path on
    disk would forget it, and the second is where the picture would then come back. The archive is
    touched between them, since a picture carries its archive's age and must not return with it."""
    gallery(root_path / "shoot.zip", ["01.png", "02.png", "03.png"], tmp_path)
    await _scan(context_for, root, settings, service)
    before = await _pictures(temp_db)
    assert len(before) == 3

    await deleter.remove(before["shoot.zip/02.png"], mode="sift", actor=admin)
    await _scan(context_for, root, settings, service)
    later = os.stat(root_path / "shoot.zip").st_mtime_ns + 5_000_000_000
    os.utime(root_path / "shoot.zip", ns=(later, later))
    await _scan(context_for, root, settings, service)

    after = await _pictures(temp_db)
    assert sorted(after) == ["shoot.zip/01.png", "shoot.zip/03.png"]
    assert after["shoot.zip/01.png"] == before["shoot.zip/01.png"]
    skipped = await _remembered(service, root.id)
    assert [(str(row["rel_path"]), str(row["reason"])) for row in skipped] == [
        ("shoot.zip/02.png", REMOVED_FROM_SIFT)
    ]


async def test_try_again_under_skipped_brings_the_picture_back(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    deleter: Deleter,
    temp_db: Database,
    admin: Viewer,
) -> None:
    """The way back is the scanner's own: forgetting the refusal puts the picture in front of the
    next scan, as for any file under Skipped."""
    gallery(root_path / "shoot.zip", ["01.png", "02.png"], tmp_path)
    await _scan(context_for, root, settings, service)
    await deleter.remove((await _pictures(temp_db))["shoot.zip/01.png"], mode="sift", actor=admin)
    await _scan(context_for, root, settings, service)
    assert sorted(await _pictures(temp_db)) == ["shoot.zip/02.png"]

    await service.forget_rejection(root_id=root.id, rel_path="shoot.zip/01.png")
    await _scan(context_for, root, settings, service)

    assert sorted(await _pictures(temp_db)) == ["shoot.zip/01.png", "shoot.zip/02.png"]


async def test_a_disk_delete_of_a_picture_in_a_zip_is_refused_and_a_rescan_finds_it_unchanged(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    deleter: Deleter,
    temp_db: Database,
    admin: Viewer,
) -> None:
    gallery(root_path / "shoot.zip", ["01.png", "02.png"], tmp_path)
    await _scan(context_for, root, settings, service)
    before = await _pictures(temp_db)
    archive = (root_path / "shoot.zip").read_bytes()

    with pytest.raises(InsideAnArchive):
        await deleter.remove(before["shoot.zip/01.png"], mode="disk", actor=admin)
    await _scan(context_for, root, settings, service)

    assert await _pictures(temp_db) == before
    assert (root_path / "shoot.zip").read_bytes() == archive
    assert await _remembered(service, root.id) == []
