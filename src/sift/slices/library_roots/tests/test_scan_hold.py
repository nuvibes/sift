# SPDX-License-Identifier: AGPL-3.0-or-later
"""A big read on a share holds back none of its own per-file work: a file's work runs as it lands."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from sift.kernel import lanes
from sift.kernel.config import Settings
from sift.kernel.content import Root
from sift.kernel.content.mounts import Storage
from sift.kernel.jobs import JobContext, JobQueue, SystemCapabilities, register_handler
from sift.slices.library_roots import jobs, taking_in
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.tests.conftest import RecordingReindexer, draw

pytestmark = pytest.mark.usefixtures("handlers")


RELEASED = "test_released_work"


class HoldWatcher(RecordingReindexer):
    """At the first batch, per-file work made from a probe, as a probe makes it, and what another
    worker could claim while the read goes on."""

    def __init__(self, queue: JobQueue) -> None:
        super().__init__()
        self.queue = queue
        self.batches: list[int] = []
        self.holder: list[str | None] = []
        self.claimed: list[str | None] = []

    async def touched_many(self, asset_ids: Sequence[str]) -> None:
        if not self.batches:
            (scan,) = (await self.queue.list(job_type=jobs.SCAN)).jobs
            probe = (await self.queue.list(parent_id=scan.id, job_type=taking_in.PROBE)).jobs[0]
            await self.queue.enqueue(RELEASED, {}, parent_id=probe.id, priority=probe.priority)
        self.batches.append(len(asset_ids))
        self.holder.append(await self.queue.holder_of([RELEASED, taking_in.PROBE]))
        job = await self.queue.claim("another worker", limits={taking_in.PROBE: 0})
        self.claimed.append(None if job is None else job.type)
        await super().touched_many(asset_ids)


@pytest.fixture
def three_files(monkeypatch: pytest.MonkeyPatch, root_path: Path) -> None:
    for n in range(3):
        draw(root_path / f"shot-{n}.png", f"testsrc2=size={32 + 8 * n}x32:rate=1")
    monkeypatch.setattr(jobs, "PROBE_HANDOUT_BATCH", 2)
    monkeypatch.setattr(lanes, "storage_for", lambda _path: Storage(key="//nas/", remote=True))

    async def nothing(_context: JobContext) -> None:
        return None

    register_handler(RELEASED, nothing, name="Released work")


async def _scan(
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    settings: Settings,
    service: LibraryService,
    watcher: HoldWatcher,
    shape: dict[str, object],
) -> str:
    scan_id = await job_queue.enqueue(jobs.SCAN, shape)
    job = await job_queue.claim("the read", limits={taking_in.PROBE: 0})
    assert job is not None and job.id == scan_id
    context = JobContext(job=job, worker_id="the read", queue=job_queue, capabilities=capabilities)
    await jobs.scan(context, settings=settings, service=service, reindexer=watcher)
    return scan_id


@pytest.mark.usefixtures("three_files")
async def test_a_big_read_on_a_share_holds_none_of_its_own_work(
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    settings: Settings,
    service: LibraryService,
    root: Root,
) -> None:
    watcher = HoldWatcher(job_queue)

    await _scan(job_queue, capabilities, settings, service, watcher, jobs.scan_shape(root.id))

    assert watcher.batches == [2, 1], "the index hears each batch as it arrives"
    assert watcher.holder == [None, None], "nothing held while reading"
    assert watcher.claimed[0] == RELEASED, "a file's work is claimed while the read goes on"
    assert await job_queue.held_for_family_by_type() == {}
