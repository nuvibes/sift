# SPDX-License-Identifier: AGPL-3.0-or-later
"""A scan says what it has still to read, by media kind, and its progress is files read."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sift.kernel.config import Settings
from sift.kernel.content import Root
from sift.kernel.jobs import JobContext, JobQueue, SystemCapabilities
from sift.slices.library_roots import jobs
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.tests.conftest import RecordingReindexer, draw
from sift.slices.library_roots.tests.test_scan_count import _files, _run_count, _run_scan

pytestmark = pytest.mark.usefixtures("handlers")


async def _to_read(job_queue: JobQueue, job_id: str) -> dict[str, int] | None:
    (row,) = await job_queue._db.fetch_all("SELECT to_read FROM jobs WHERE id = ?", (job_id,))
    return None if row["to_read"] is None else dict(json.loads(row["to_read"]))


async def test_a_count_writes_the_kinds_to_read_and_the_folder_is_counted(
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    service: LibraryService,
    root: Root,
    root_path: Path,
) -> None:
    _files(root_path, "a.jpg", "b.mp4", "c.gif", "d.webp", "deep/e.zip", "notes.txt")
    scan_id = await jobs.queue_scan(job_queue, jobs.scan_shape(root.id), requested_by=None)
    assert (await job_queue.files_to_read([jobs.SCAN])).uncounted == 1

    await _run_count(job_queue, capabilities, service)

    # A `.webp` is a still until it is read, and an archive's members are stills.
    assert await _to_read(job_queue, scan_id) == {"image": 3, "video": 1, "gif": 1}
    unread = await job_queue.files_to_read([jobs.SCAN])
    assert unread.uncounted == 0
    assert unread.by_kind == {"image": 3.0, "video": 1.0, "gif": 1.0}


async def test_a_scans_progress_is_files_read_and_a_finished_read_leaves_nothing(
    monkeypatch: pytest.MonkeyPatch,
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    service: LibraryService,
    settings: Settings,
    reindexer: RecordingReindexer,
    root: Root,
    root_path: Path,
) -> None:
    """Before, three files already read and one new reported 0.225, 0.45, 0.675 and 0.9: a rescan
    looked most of the way done with its one file unread, and ended with a tenth left over."""
    for n in range(3):
        draw(root_path / f"still-{n}.png", f"testsrc2=size={32 + 8 * n}x32:rate=1")
    first = await jobs.queue_scan(job_queue, jobs.scan_shape(root.id), requested_by="someone")
    await _run_count(job_queue, capabilities, service)
    await _run_scan(job_queue, capabilities, settings, service, reindexer, first)
    draw(root_path / "new.png", "testsrc2=size=64x32:rate=1")
    again = await jobs.queue_scan(job_queue, jobs.scan_shape(root.id), requested_by="someone")
    await _run_count(job_queue, capabilities, service)
    reported: list[float] = []
    told = JobContext.report_progress

    async def recording(context: JobContext, fraction: float) -> None:
        reported.append(fraction)
        await told(context, fraction)

    monkeypatch.setattr(JobContext, "report_progress", recording)
    monkeypatch.setattr(jobs, "PROBE_HANDOUT_BATCH", 1)

    await _run_scan(job_queue, capabilities, settings, service, reindexer, again)

    assert len(reported) == 4 and set(reported) <= {0.0, 1.0} and 1.0 in reported
    assert await _to_read(job_queue, again) == {"image": 0}, "kept current as it reads"
