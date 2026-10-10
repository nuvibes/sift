# SPDX-License-Identifier: AGPL-3.0-or-later
"""A share's file copied into a cache whose disk is full: the walk waits for room, says so, and
leaves nothing half-written; any other failed copy is still left for the next pass."""

from __future__ import annotations

import errno
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from sift.kernel import lanes
from sift.kernel.config import Settings
from sift.kernel.content import Root
from sift.kernel.content.mounts import Storage
from sift.kernel.db import Database
from sift.kernel.jobs import JobContext
from sift.kernel.jobs.failure_words import in_plain_words
from sift.kernel.jobs.registry import held_for
from sift.kernel.jobs.retrying import ROOM_WAIT, WaitingForSpace
from sift.kernel.tests.content_helpers import FIXTURES
from sift.slices.library_roots import jobs, taking_in
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.tests.conftest import RecordingReindexer

Context = Callable[..., Awaitable[JobContext]]


@pytest.fixture
def share(root_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The library folder as a share, so the take-in copies each picture into the cache."""

    def storage_for(path: Path) -> Storage:
        remote = root_path in (path, *path.parents)
        return Storage(key="\\\\nas\\library\\" if remote else "C:\\", remote=remote)

    monkeypatch.setattr(lanes, "storage_for", storage_for)
    lanes.install(lanes.StorageLanes(network_reads_at_once=1))
    try:
        yield
    finally:
        lanes.install(None)


def _picture(root_path: Path) -> None:
    (root_path / "in").mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "accepted.jpg", root_path / "in" / "accepted.jpg")


def _full(*_args: Any, **_kwargs: Any) -> Any:
    raise OSError(errno.ENOSPC, "No space left on device")


async def _assets(database: Database) -> int:
    return int((await database.fetch_all("SELECT count(*) AS n FROM assets", ()))[0]["n"])


def _scratch(settings: Settings) -> list[Path]:
    return sorted((settings.cache_dir / "incoming").glob("copy-*"))


async def test_a_copy_that_fills_the_cache_holds_the_walk_and_leaves_nothing_half_written(
    share: None,
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _picture(root_path)

    def fills(source: Path, target: Path, *, size: int) -> int:
        target.write_bytes(source.read_bytes()[: size // 2])
        _full()
        return size

    monkeypatch.setattr(taking_in, "copy_settled", fills)
    context = await context_for(jobs.SCAN, {"root_id": root.id})

    with pytest.raises(WaitingForSpace) as held:
        await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    assert held_for(held.value) == ROOM_WAIT, "held with its attempt handed back"
    assert in_plain_words(str(held.value)).startswith("It's waiting for space")
    assert _scratch(settings) == [], "the half-written copy is gone"
    assert await _assets(temp_db) == 0


async def test_a_cache_too_full_for_the_copys_folder_holds_the_walk(
    share: None,
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _picture(root_path)
    monkeypatch.setattr(tempfile, "mkdtemp", _full)
    context = await context_for(jobs.SCAN, {"root_id": root.id})

    with pytest.raises(WaitingForSpace):
        await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    assert await _assets(temp_db) == 0


async def test_a_copy_that_fails_for_another_reason_is_left_for_the_next_pass(
    share: None,
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _picture(root_path)

    def refused(*_args: Any, **_kwargs: Any) -> int:
        raise PermissionError(errno.EACCES, "Access is denied")

    monkeypatch.setattr(taking_in, "copy_settled", refused)
    context = await context_for(jobs.SCAN, {"root_id": root.id})

    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    assert await _assets(temp_db) == 0
    assert _scratch(settings) == []
