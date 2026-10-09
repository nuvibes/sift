# SPDX-License-Identifier: AGPL-3.0-or-later
"""A scan's writes per file: a heartbeat by the clock, a count that lands as it grows, and nothing
written for a file that has not changed."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Root
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import STOP_TO_CANCEL, JobCanceled, JobContext, JobQueue, SystemCapabilities
from sift.slices.library_roots import jobs, taking_in
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.tests.conftest import RecordingReindexer, draw

Context = Callable[..., Awaitable[JobContext]]

#: Long enough that no beat falls due inside a test.
NEVER = 3_600.0


def pictures(root_path: Path, count: int) -> None:
    for number in range(count):
        draw(root_path / f"p{number}.png", f"testsrc2=size={32 + 2 * number}x32")


async def assets_in(database: Database) -> int:
    rows = await database.fetch_all("SELECT COUNT(*) AS c FROM assets")
    return int(rows[0]["c"])


async def test_a_rescan_beats_by_the_clock_not_once_per_file(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each heartbeat is a turn of the one writer, so a pass over thousands of unchanged files
    beating per file queued thousands of writes behind everything else."""
    pictures(root_path, 6)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    monkeypatch.setattr(taking_in, "BEAT_SECONDS", NEVER)
    beats: list[str] = []
    beat = job_queue.beat

    async def counted(job_id: str, worker_id: str) -> object:
        beats.append(job_id)
        return await beat(job_id, worker_id)

    monkeypatch.setattr(job_queue, "beat", counted)

    second = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(second, settings=settings, service=service, reindexer=reindexer)

    assert beats == [second.job.id]


async def test_an_unchanged_file_with_nothing_to_forget_writes_nothing(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    content_store: ContentStore,
    reindexer: RecordingReindexer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The delete of a passing moment's verdicts is asked only where one exists."""
    pictures(root_path, 3)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    forgotten: list[str] = []

    async def forget(asset_id: str) -> int:  # pragma: no cover (asked only when it is wrong)
        forgotten.append(asset_id)
        return 0

    monkeypatch.setattr(content_store, "forget_transient_verdicts", forget)

    second = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(second, settings=settings, service=service, reindexer=reindexer)

    assert forgotten == []


async def test_the_count_lands_while_the_scan_is_still_deciding(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scan claimed before its count shows the files it has decided so far at each beat, rather
    than one file until every file is decided."""
    pictures(root_path, 4)
    monkeypatch.setattr(taking_in, "BEAT_SECONDS", 0.0)
    context = await context_for(jobs.SCAN, {"root_id": root.id})
    written: list[int] = []
    set_units = job_queue.set_units

    async def recorded(job_id: str, worker_id: str, units: int) -> bool:
        written.append(units)
        return await set_units(job_id, worker_id, units)

    monkeypatch.setattr(job_queue, "set_units", recorded)

    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    assert written == [2, 3, 4]


async def test_a_count_made_ahead_is_never_lowered_by_the_one_growing(
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    reindexer: RecordingReindexer,
    handlers: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The count job's total stays on screen until the scan's own replaces it at the end."""
    pictures(root_path, 3)
    monkeypatch.setattr(taking_in, "BEAT_SECONDS", 0.0)
    job_id = await job_queue.enqueue(jobs.SCAN, {"root_id": root.id})
    assert await job_queue.set_waiting_units(job_id, 3)
    worker_id = new_id()
    job = await job_queue.claim(worker_id)
    assert job is not None
    assert job.id == job_id
    context = JobContext(job=job, worker_id=worker_id, queue=job_queue, capabilities=capabilities)
    written: list[int] = []
    set_units = job_queue.set_units

    async def recorded(job_id: str, worker_id: str, units: int) -> bool:
        written.append(units)
        return await set_units(job_id, worker_id, units)

    monkeypatch.setattr(job_queue, "set_units", recorded)

    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    assert written == [3]


async def test_a_cancel_heard_between_beats_stops_the_scan(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the worker pool's own beat heard is obeyed at the next file, with no write to hear it."""
    pictures(root_path, 2)
    monkeypatch.setattr(taking_in, "BEAT_SECONDS", NEVER)
    context = await context_for(jobs.SCAN, {"root_id": root.id})
    context.told_to_stop(STOP_TO_CANCEL)

    with pytest.raises(JobCanceled):
        await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    assert await assets_in(temp_db) == 0
