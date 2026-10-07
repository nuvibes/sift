# SPDX-License-Identifier: AGPL-3.0-or-later
"""A folder is counted while its scan waits, whatever else is reading, and the scan agrees."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from sift.kernel.config import Settings
from sift.kernel.content import LibraryStore, Root
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobContext, JobQueue, JobState, SystemCapabilities
from sift.kernel.jobs.tuning import WAITED_ON_PRIORITY
from sift.slices.library_roots import jobs, taking_in
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.tests.conftest import RecordingReindexer, draw
from sift.testing.logs import uncached_log

pytestmark = pytest.mark.usefixtures("handlers")


def _files(directory: Path, *names: str) -> None:
    for name in names:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not read by a count")


async def _root(library_store: LibraryStore, tmp_path: Path, name: str) -> Root:
    directory = tmp_path / name
    directory.mkdir()
    return await library_store.create_root(name=name, abs_path=directory)


async def _counts(job_queue: JobQueue) -> list[tuple[str, int]]:
    rows = (await job_queue.list(job_type=jobs.SCAN_COUNT)).jobs
    return [(str(row.payload["scan_id"]), row.priority) for row in rows]


async def _run_count(
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    service: LibraryService,
    *,
    limits: dict[str, int] | None = None,
) -> JobContext:
    """Claim the next job, which must be a count, and run it to the end."""
    worker = new_id()
    job = await job_queue.claim(worker, limits=limits)
    assert job is not None and job.type == jobs.SCAN_COUNT, job
    context = JobContext(job=job, worker_id=worker, queue=job_queue, capabilities=capabilities)
    await jobs.count_scan(context, service=service)
    assert await job_queue.complete(job.id, worker)
    return context


async def test_a_count_writes_the_files_to_read_onto_the_waiting_scan(
    monkeypatch: pytest.MonkeyPatch,
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    service: LibraryService,
    root: Root,
    root_path: Path,
) -> None:
    _files(root_path, "a.jpg", "b.mp4", "deep/c.png", "notes.txt")
    scan_id = await jobs.queue_scan(job_queue, jobs.scan_shape(root.id), requested_by=None)
    assert await _counts(job_queue) == [(scan_id, WAITED_ON_PRIORITY)]

    uncached_log(monkeypatch, jobs)
    with capture_logs() as logs:
        context = await _run_count(job_queue, capabilities, service)

    scan = await job_queue.get(scan_id)
    assert scan is not None and scan.state is JobState.QUEUED and scan.units == 3
    done = await job_queue.get(context.job.id)
    assert done is not None and done.units == 0, "a count is not a file of the Scan run"
    assert done.note == "3 files to read."
    counted = [one for one in logs if one["event"] == "library.scan_counted"]
    assert counted and counted[0]["ahead"] is True and counted[0]["waited_seconds"] >= 0


async def test_the_count_ahead_is_the_count_the_scan_then_makes(
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    service: LibraryService,
    settings: Settings,
    reindexer: RecordingReindexer,
    root: Root,
    root_path: Path,
) -> None:
    """Counted as the scan decides: a file read before is not counted, a new path to it is."""
    draw(root_path / "kept.png", "testsrc2=size=32x32:rate=1")
    draw(root_path / "album/one.png", "testsrc2=size=48x32:rate=1")
    first = await jobs.queue_scan(job_queue, jobs.scan_shape(root.id), requested_by="someone")
    await _run_count(job_queue, capabilities, service)
    await _run_scan(job_queue, capabilities, settings, service, reindexer, first)
    draw(root_path / "album/two.png", "testsrc2=size=40x32:rate=1")
    shutil.copy(root_path / "kept.png", root_path / "album/copy.png")

    again = await jobs.queue_scan(job_queue, jobs.scan_shape(root.id), requested_by="someone")
    await _run_count(job_queue, capabilities, service)
    ahead = await job_queue.get(again)
    assert ahead is not None and ahead.units == 2
    await _run_scan(job_queue, capabilities, settings, service, reindexer, again)
    scanned = await job_queue.get(again)
    assert scanned is not None and scanned.units == ahead.units


async def _run_scan(
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    scan_id: str,
) -> None:
    worker = new_id()
    job = await job_queue.claim(worker, limits={taking_in.PROBE: 0})
    assert job is not None and job.id == scan_id, job
    context = JobContext(job=job, worker_id=worker, queue=job_queue, capabilities=capabilities)
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)
    assert await job_queue.complete(job.id, worker)


async def test_three_folders_are_counted_while_another_folders_read_holds_the_scans_share(
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    service: LibraryService,
    library_store: LibraryStore,
    tmp_path: Path,
) -> None:
    """The scans' share is one worker and a long read holds it, with its probes queued: before,
    no added folder is counted until that read ends; now each is counted on the next claim."""
    long_read = await _root(library_store, tmp_path, "first")
    await job_queue.enqueue(jobs.SCAN, jobs.scan_shape(long_read.id))
    assert await job_queue.claim("holding the read") is not None
    for n in range(20):
        await job_queue.enqueue(taking_in.PROBE, {"asset_id": f"asset-{n}"})
    added = [await _root(library_store, tmp_path, f"added-{n}") for n in range(3)]
    for n, one in enumerate(added):
        _files(Path(one.abs_path), *(f"clip-{i}.mp4" for i in range(n + 1)))
    share = {jobs.SCAN: 1}
    scans = [await job_queue.enqueue(jobs.SCAN, jobs.scan_shape(one.id)) for one in added]

    before: list[str] = []
    while (job := await job_queue.claim("before", limits=share)) is not None:
        before.append(job.type)
    assert before == [taking_in.PROBE] * 20, "every worker freed went to a probe, none to a count"

    for scan_id, one in zip(scans, added, strict=True):
        await jobs.count_ahead(job_queue, scan_id, jobs.scan_shape(one.id), at=None)
    for _ in added:
        await _run_count(job_queue, capabilities, service, limits=share)

    waiting = [await job_queue.get(scan_id) for scan_id in scans]
    assert [(one.state, one.units) for one in waiting if one is not None] == [
        (JobState.QUEUED, 1),
        (JobState.QUEUED, 2),
        (JobState.QUEUED, 3),
    ]
    assert await job_queue.claim("after", limits=share) is None, "a scan passed the share"


async def test_one_folder_is_counted_at_a_time(
    job_queue: JobQueue, library_store: LibraryStore, tmp_path: Path
) -> None:
    """A walk holds a place on its share while it lists, so two counts never list together."""
    for name in ("one", "two"):
        one = await _root(library_store, tmp_path, name)
        await jobs.queue_scan(job_queue, jobs.scan_shape(one.id), requested_by=None)

    first = await job_queue.claim("first")
    second = await job_queue.claim("second")

    assert first is not None and first.type == jobs.SCAN_COUNT
    assert second is not None and second.type == jobs.SCAN


async def test_a_scan_already_running_is_not_counted_again(
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    service: LibraryService,
    root: Root,
    root_path: Path,
) -> None:
    _files(root_path, "a.jpg")
    scan_id = await job_queue.enqueue(jobs.SCAN, jobs.scan_shape(root.id))
    assert await job_queue.claim("the scan") is not None
    await jobs.count_ahead(job_queue, scan_id, jobs.scan_shape(root.id), at=None)

    context = await _run_count(job_queue, capabilities, service)

    scan = await job_queue.get(scan_id)
    assert scan is not None and scan.units == 1
    done = await job_queue.get(context.job.id)
    assert done is not None and done.note is None


async def test_a_named_path_scan_is_not_counted(job_queue: JobQueue, root: Root) -> None:
    shape = {**jobs.scan_shape(root.id), "paths": ["a.jpg"]}
    await jobs.queue_scan(job_queue, shape, requested_by=None)
    assert await _counts(job_queue) == []


async def test_a_folder_inside_a_library_is_counted_alone(
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    service: LibraryService,
    library_store: LibraryStore,
    root: Root,
    root_path: Path,
) -> None:
    _files(root_path, "outside.jpg", "inside/a.jpg", "inside/b.jpg")
    folder = await library_store.upsert_folder(root.id, "inside")
    scan_id = await jobs.queue_scan(job_queue, jobs.scan_shape(root.id, folder), requested_by=None)

    await _run_count(job_queue, capabilities, service)

    scan = await job_queue.get(scan_id)
    assert scan is not None and scan.units == 2


async def test_a_count_says_so_when_the_library_folder_does_not_answer(
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    service: LibraryService,
    root: Root,
    root_path: Path,
) -> None:
    scan_id = await jobs.queue_scan(job_queue, jobs.scan_shape(root.id), requested_by=None)
    root_path.rmdir()

    context = await _run_count(job_queue, capabilities, service)

    scan = await job_queue.get(scan_id)
    assert scan is not None and scan.units == 1
    done = await job_queue.get(context.job.id)
    assert done is not None
    assert done.note == "The library folder gave no answer, so nothing was counted."


@pytest.mark.parametrize("gone", ["root", "folder"])
async def test_a_count_for_a_library_or_folder_that_has_gone_leaves_it_to_the_scan(
    monkeypatch: pytest.MonkeyPatch,
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    service: LibraryService,
    root: Root,
    gone: str,
) -> None:
    shape = (
        {"root_id": "01HX0000000000000000000009"}
        if gone == "root"
        else {
            "root_id": root.id,
            "folder_id": "01HX0000000000000000000009",
        }
    )
    scan_id = await jobs.queue_scan(job_queue, shape, requested_by=None)

    uncached_log(monkeypatch, jobs)
    with capture_logs() as logs:
        await _run_count(job_queue, capabilities, service)

    assert [one["event"] for one in logs if one["event"].startswith("library.")] == [
        "library.count_not_needed"
    ]
    scan = await job_queue.get(scan_id)
    assert scan is not None and scan.units == 1


async def test_every_part_of_a_whole_library_pass_is_counted(
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    library_store: LibraryStore,
    tmp_path: Path,
) -> None:
    for name in ("one", "two"):
        await _root(library_store, tmp_path, name)
    pass_id = await job_queue.enqueue(jobs.LIBRARY_SCAN, {}, requested_by="someone")
    job = await job_queue.claim("the pass")
    assert job is not None and job.id == pass_id
    context = JobContext(job=job, worker_id="the pass", queue=job_queue, capabilities=capabilities)

    await jobs.scan_everything(context)

    parts = {child.id for child in await job_queue.children(pass_id)}
    counts = await _counts(job_queue)
    assert {scan_id for scan_id, _priority in counts} == parts and len(parts) == 2
