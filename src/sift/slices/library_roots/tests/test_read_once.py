# SPDX-License-Identifier: AGPL-3.0-or-later
"""On a share, a file's bytes cross the network once, and every read of the share takes its lane.

The share is stood in for: the library folder is said to be remote, real lanes are installed, and
every file opened under it is noted with whether the reader held a place in the lane. What is
asserted is what a share's owner pays for: how many times each file was opened, how many bytes
were read, and that the passes after the take-in find their bytes without the share at all.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from sift.kernel import lanes
from sift.kernel.archives import CopyChanged, copy_settled
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Root
from sift.kernel.content.mounts import Storage
from sift.kernel.jobs import JobContext
from sift.kernel.media_sources import resolve
from sift.kernel.tests.content_helpers import FIXTURES
from sift.slices.library_roots import jobs
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.tests.conftest import RecordingReindexer
from sift.slices.library_roots.tests.test_archive_scan import gallery

Context = Callable[..., Awaitable[JobContext]]

SHARE = Storage(key="\\\\nas\\library\\", remote=True)
DISK = Storage(key="C:\\", remote=False)

#: Every file opened under the watched folder, and whether its reader held a lane place then.
_OPENED: list[tuple[Path, bool]] = []
_WATCHED: list[Path] = []
_HOOKED: list[bool] = []


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if event != "open" or not _WATCHED or not isinstance(args[0], (str, bytes, os.PathLike)):
        return
    path = Path(os.fsdecode(args[0]))
    mode, flags = args[1], args[2]
    writing = (
        any(one in mode for one in "wax+")
        if isinstance(mode, str)
        else bool(flags & (os.O_WRONLY | os.O_RDWR))
    )
    if not writing and _WATCHED[0] in path.parents:
        _OPENED.append((path, lanes.holding(path)))


@pytest.fixture
def share(root_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[lanes.StorageLanes]:
    """The library folder as a two-place share, every open under it noted."""

    def storage_for(path: Path) -> Storage:
        return SHARE if root_path in (path, *path.parents) else DISK

    monkeypatch.setattr(lanes, "storage_for", storage_for)
    installed = lanes.StorageLanes(network_reads_at_once=2)
    lanes.install(installed)
    if not _HOOKED:
        sys.addaudithook(_audit)
        _HOOKED.append(True)
    _OPENED.clear()
    _WATCHED[:] = [root_path]
    try:
        yield installed
    finally:
        _WATCHED.clear()
        lanes.install(None)


def _put(root_path: Path) -> dict[str, Path]:
    files = {}
    for name in ("accepted.jpg", "accepted.mp4"):
        files[name] = root_path / "in" / name
        files[name].parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / name, files[name])
    return files


async def _scan(
    context_for: Context,
    root: Root,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    **payload: object,
) -> None:
    context = await context_for(jobs.SCAN, {"root_id": root.id, **payload})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)


async def test_on_a_share_each_file_is_read_once_in_the_lane_and_the_passes_never_return(
    share: lanes.StorageLanes,
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    content_store: ContentStore,
) -> None:
    files = _put(root_path)
    archive = gallery(root_path / "in" / "shoot.zip", ["01.png", "02.png"], tmp_path)
    members = 2

    await _scan(context_for, root, settings, service, reindexer)

    assert _OPENED, "nothing was read from the share"
    assert all(held for _, held in _OPENED), [path for path, held in _OPENED if not held]
    opened = [path for path, _ in _OPENED]
    for one in files.values():
        assert opened.count(one) == 1, f"{one.name} was opened {opened.count(one)} times"
    # Its index once, and once more for every picture together.
    assert opened.count(archive) == 2
    lane = share.lane_for(root_path / "in" / "x")
    assert lane.bytes_read >= sum(one.stat().st_size for one in files.values())

    assert len(reindexer.told) == len(files) + members
    for one in files.values():
        one.unlink()
    archive.unlink()
    for asset_id in reindexer.told:
        source = await resolve(content_store, asset_id)
        assert settings.cache_dir in source.path.parents, "a pass would read the share again"
        assert source.original == source.path
    by_name = {
        (await content_store.get(asset_id)).original_filename: asset_id  # type: ignore[union-attr]
        for asset_id in reindexer.told
    }
    picture = await content_store.get(by_name["accepted.jpg"])
    assert picture is not None and picture.turn_apart is False


async def test_a_scan_that_builds_nothing_copies_no_video(
    share: lanes.StorageLanes,
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    content_store: ContentStore,
) -> None:
    """Its passes read a video's identity and a probe's few hundred kilobytes, not the whole."""
    _put(root_path)

    await _scan(context_for, root, settings, service, reindexer, scan_only=True)

    kept = {
        (await content_store.get(asset_id)).media_type: await content_store.local_copy(asset_id)  # type: ignore[union-attr]
        for asset_id in reindexer.told
    }
    assert kept["image"] is not None
    assert kept["video"] is None
    assert all(held for _, held in _OPENED)


async def test_on_a_local_disk_nothing_is_copied(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    content_store: ContentStore,
) -> None:
    _put(root_path)

    await _scan(context_for, root, settings, service, reindexer)

    assert len(reindexer.told) == 2
    assert not (settings.cache_dir / ContentStore.LOCAL_COPIES).exists()
    for asset_id in reindexer.told:
        assert (await resolve(content_store, asset_id)).path.is_relative_to(root_path)


@pytest.mark.parametrize("failure", [CopyChanged("still being written"), OSError("gone")])
async def test_a_file_that_will_not_copy_is_left_for_the_next_pass(
    failure: OSError,
    share: lanes.StorageLanes,
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _put(root_path)

    def refused(*_args: object, **_kwargs: object) -> int:
        raise failure

    monkeypatch.setattr("sift.slices.library_roots.taking_in.copy_settled", refused)
    await _scan(context_for, root, settings, service, reindexer)

    assert reindexer.told == []
    assert await service.rejections_of_root(root.id) == {}
    assert not [one for one in (settings.cache_dir / "incoming").rglob("*") if one.is_file()]


async def test_a_scan_somebody_asked_for_reads_ahead_of_a_whole_walk(
    share: lanes.StorageLanes,
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scan of a moved folder or a named file must not wait out every read a walk of the same
    share has queued: it reads at the kind above the walk's."""
    _put(root_path)
    ranks: list[int] = []
    real = copy_settled

    def noting(*args: Any, **kwargs: Any) -> int:
        ranks.append(lanes._RANK.get())
        return real(*args, **kwargs)

    monkeypatch.setattr("sift.slices.library_roots.taking_in.copy_settled", noting)
    await _scan(context_for, root, settings, service, reindexer)
    shutil.copy(FIXTURES / "accepted.png", root_path / "in" / "arrived.png")
    await _scan(context_for, root, settings, service, reindexer, paths=["in/arrived.png"])

    assert ranks == [lanes.FIRST, lanes.FIRST, lanes.ASKED]
