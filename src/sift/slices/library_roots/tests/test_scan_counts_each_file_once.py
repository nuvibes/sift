# SPDX-License-Identifier: AGPL-3.0-or-later
"""Partway through a read, each file the count found is in the library or still to read, not both."""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.kernel import lanes
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Root
from sift.kernel.jobs import JobContext, JobQueue, SystemCapabilities
from sift.slices.library_roots import jobs
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.tests.conftest import RecordingReindexer, draw
from sift.slices.library_roots.tests.test_scan_count import _run_count, _run_scan

pytestmark = pytest.mark.usefixtures("handlers")


async def test_halfway_through_a_read_no_file_is_counted_twice(
    monkeypatch: pytest.MonkeyPatch,
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    content_store: ContentStore,
    service: LibraryService,
    settings: Settings,
    reindexer: RecordingReindexer,
    root: Root,
    root_path: Path,
) -> None:
    for n in range(6):
        draw(root_path / f"still-{n}.png", f"testsrc2=size={32 + 8 * n}x32:rate=1")
    scan_id = await jobs.queue_scan(job_queue, jobs.scan_shape(root.id), requested_by="someone")
    await _run_count(job_queue, capabilities, service)
    seen: list[tuple[int, int]] = []

    async def every_file(context: JobContext, fraction: float) -> None:
        await context.set_progress(fraction)
        unread = await job_queue.files_to_read([jobs.SCAN])
        seen.append((await content_store.asset_count(), round(sum(unread.by_kind.values()))))

    monkeypatch.setattr(JobContext, "report_progress", every_file)
    monkeypatch.setattr(lanes, "reads_at_once", lambda _path: 1)

    await _run_scan(job_queue, capabilities, settings, service, reindexer, scan_id)

    assert (3, 3) in seen, seen
    assert {found + unread for found, unread in seen} == {6}, seen
