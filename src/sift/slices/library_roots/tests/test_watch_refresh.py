# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the watcher does when a library folder is added, removed or moved while Sift runs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from sift.kernel.content import LibraryStore, Root
from sift.kernel.jobs import JobContext, JobQueue
from sift.kernel.ledger import Actor
from sift.slices.library_roots import jobs
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.watcher import LibraryWatcher

Context = Callable[..., Awaitable[JobContext]]


@pytest.fixture
async def watcher(
    library_store: LibraryStore, job_queue: JobQueue, handlers: None
) -> LibraryWatcher:
    return LibraryWatcher(library_store, job_queue, cooldown_seconds=0.0)


@pytest.fixture
def caught_up(watcher: LibraryWatcher, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The roots the watcher asks a catch-up for, in order."""
    asked: list[str] = []

    async def catch_up(root_id: str) -> None:
        asked.append(root_id)

    monkeypatch.setattr(watcher, "_catch_up", catch_up)
    return asked


async def _scans(queue: JobQueue, root_id: str) -> list[dict[str, object]]:
    page = await queue.list(job_type=jobs.SCAN, limit=100)
    return [job.payload for job in page.jobs if job.payload.get("root_id") == root_id]


async def test_a_folder_added_while_another_is_read_is_scanned_once_and_each_file_read_once(
    watcher: LibraryWatcher,
    root: Root,
    service: LibraryService,
    job_queue: JobQueue,
    context_for: Context,
    tmp_path: Path,
) -> None:
    """A free worker can take the new folder's catch-up before the add queues its walk: each file
    named there is read again by the walk."""
    await watcher.refresh()
    folder = tmp_path / "added"
    folder.mkdir()
    names = [f"still-{index:04}.jpg" for index in range(jobs.MOST_NAMED_PATHS + 6)]
    for name in names:
        (folder / name).write_bytes(b"\xff\xd8\xff")
    try:
        added = await service.add_root(abs_path=folder)
        await watcher.refresh()
        page = await job_queue.list(job_type=jobs.RECONCILE, limit=100)
        for queued in page.jobs:
            if queued.payload == {"root_id": added.id}:
                await jobs.reconcile(
                    await context_for(jobs.RECONCILE, queued.payload), service=service
                )
        await jobs.queue_scan(job_queue, {"root_id": added.id}, requested_by=None)
        scans = await _scans(job_queue, added.id)
    finally:
        await watcher.stop()

    reads = {name: sum(name in one.get("paths", names) for one in scans) for name in names}  # type: ignore[operator]
    assert (len(scans), max(reads.values())) == (1, 1)


async def test_adding_a_folder_leaves_the_other_watches_running_and_catches_none_up(
    watcher: LibraryWatcher,
    root: Root,
    service: LibraryService,
    caught_up: list[str],
    tmp_path: Path,
) -> None:
    await watcher.refresh()
    kept = watcher._watches[root.id]
    folder = tmp_path / "added"
    folder.mkdir()
    try:
        added = await service.add_root(abs_path=folder)
        await watcher.refresh()
        assert watcher._watches[root.id] is kept
        assert set(watcher._watches) == {root.id, added.id}
    finally:
        await watcher.stop()
    assert caught_up == [root.id], "only the first refresh catches a library up"


async def test_a_removed_folder_is_let_go_and_a_moved_one_watched_where_it_is_now(
    watcher: LibraryWatcher,
    root: Root,
    library_store: LibraryStore,
    service: LibraryService,
    caught_up: list[str],
    tmp_path: Path,
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    second = await service.add_root(abs_path=other)
    await watcher.refresh()
    _, handler = watcher._watches[root.id]
    moved = tmp_path / "moved"
    other.rename(moved)
    try:
        await library_store.delete_root(root.id, actor=Actor.sift("folder"))
        await service.repoint_root(second.id, moved)
        await watcher.refresh()
        assert not handler.live, "the removed folder's watch was not let go"
        assert list(watcher._watches) == [second.id]
        assert watcher._bases[second.id] == moved
    finally:
        await watcher.stop()
    assert sorted(caught_up) == sorted([root.id, second.id]), "a moved folder is read by its move"


async def test_a_removed_folder_whose_watch_was_being_attached_again_is_given_up(
    watcher: LibraryWatcher,
    root: Root,
    library_store: LibraryStore,
    caught_up: list[str],
) -> None:
    await watcher.refresh()
    _, handler = watcher._watches[root.id]
    watcher._watch_ended(root.id, handler)
    again = watcher._reattaching[root.id]
    try:
        await library_store.delete_root(root.id, actor=Actor.sift("folder"))
        await watcher.refresh()
        assert again.cancelled()
        assert watcher._reattaching == {} and watcher._watches == {}
    finally:
        await watcher.stop()
    assert caught_up == [root.id]


async def test_a_folder_that_could_not_be_watched_is_tried_again_and_caught_up(
    watcher: LibraryWatcher,
    root: Root,
    caught_up: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = watcher._observe
    monkeypatch.setattr(watcher, "_observe", lambda *_: None)
    await watcher.refresh()
    monkeypatch.setattr(watcher, "_observe", real)
    try:
        await watcher.refresh()
        assert root.id in watcher._watches
    finally:
        await watcher.stop()
    assert caught_up == [root.id, root.id]


async def test_two_refreshes_at_once_give_each_folder_one_watch(
    watcher: LibraryWatcher, root: Root
) -> None:
    try:
        await asyncio.gather(watcher.refresh(), watcher.refresh())
        assert len(watcher._observers) == 1
    finally:
        await watcher.stop()
