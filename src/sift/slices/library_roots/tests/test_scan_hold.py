# SPDX-License-Identifier: AGPL-3.0-or-later
"""A big read on a share holds back its own per-file work until its reads end."""

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
    """At the first batch, per-file work made from a probe, as a probe makes it."""

    def __init__(self, queue: JobQueue, *, fail: bool = False) -> None:
        super().__init__()
        self.queue = queue
        self.fail = fail
        self.batches: list[int] = []
        self.holder: list[str | None] = []
        self.probes_held: list[str | None] = []

    async def touched_many(self, asset_ids: Sequence[str]) -> None:
        if not self.batches:
            (scan,) = (await self.queue.list(job_type=jobs.SCAN)).jobs
            probe = (await self.queue.list(parent_id=scan.id, job_type=taking_in.PROBE)).jobs[0]
            await self.queue.enqueue(RELEASED, {}, parent_id=probe.id, priority=probe.priority)
        self.batches.append(len(asset_ids))
        self.holder.append(await self.queue.holder_of([RELEASED, taking_in.PROBE]))
        self.probes_held.append(await self.queue.holder_of([taking_in.PROBE]))
        if self.fail:
            raise RuntimeError("the read failed")
        await super().touched_many(asset_ids)


@pytest.fixture
def three_files(monkeypatch: pytest.MonkeyPatch, root_path: Path) -> None:
    for n in range(3):
        draw(root_path / f"shot-{n}.png", f"testsrc2=size={32 + 8 * n}x32:rate=1")
    monkeypatch.setattr(jobs, "SCAN_FIRST_FILES", 3)
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
async def test_a_big_read_on_a_share_holds_its_own_work_until_its_reads_end(
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    settings: Settings,
    service: LibraryService,
    root: Root,
) -> None:
    watcher = HoldWatcher(job_queue)

    scan_id = await _scan(
        job_queue, capabilities, settings, service, watcher, jobs.scan_shape(root.id)
    )

    assert watcher.batches == [2, 1], "the index hears each batch as it arrives"
    assert watcher.holder == [scan_id, None], "held while reading, lifted before the sweep"
    assert watcher.probes_held == [None, None], "its probes are the read"
    assert await job_queue.held_for_family_by_type() == {}


@pytest.mark.usefixtures("three_files")
@pytest.mark.parametrize("case", ["local disk", "named paths", "too few", "switched off"])
async def test_a_read_that_is_not_big_or_not_on_a_share_holds_nothing(
    monkeypatch: pytest.MonkeyPatch,
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    settings: Settings,
    service: LibraryService,
    root: Root,
    case: str,
) -> None:
    shape = jobs.scan_shape(root.id)
    if case == "local disk":
        monkeypatch.setattr(lanes, "storage_for", lambda _path: Storage(key="C:/", remote=False))
    elif case == "named paths":
        shape["paths"] = [f"shot-{n}.png" for n in range(3)]
    elif case == "too few":
        monkeypatch.setattr(jobs, "SCAN_FIRST_FILES", 4)
    else:
        monkeypatch.setattr(jobs, "HOLD_WHILE_READING", False)
    watcher = HoldWatcher(job_queue)

    await _scan(job_queue, capabilities, settings, service, watcher, shape)

    assert watcher.holder == [None, None]


@pytest.mark.usefixtures("three_files")
async def test_a_read_that_fails_lifts_its_hold(
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    settings: Settings,
    service: LibraryService,
    root: Root,
) -> None:
    watcher = HoldWatcher(job_queue, fail=True)

    with pytest.raises(RuntimeError):
        await _scan(job_queue, capabilities, settings, service, watcher, jobs.scan_shape(root.id))

    assert watcher.holder[0] is not None
    assert await job_queue.holder_of([RELEASED]) is None


@pytest.mark.usefixtures("three_files")
async def test_the_reads_last_probes_go_before_the_work_its_hold_released(
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    settings: Settings,
    service: LibraryService,
    root: Root,
) -> None:
    await _scan(
        job_queue, capabilities, settings, service, HoldWatcher(job_queue), jobs.scan_shape(root.id)
    )

    claimed = []
    while (job := await job_queue.claim("after the read")) is not None:
        claimed.append(job.type)
    assert claimed == [taking_in.PROBE] * 3 + [RELEASED]
