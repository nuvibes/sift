# SPDX-License-Identifier: AGPL-3.0-or-later
"""What several of the media jobs test files share: the context factory's type, the read run with
the fingerprint job it hands out, and two watchers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

# Registers the ledger's table, which a fingerprint write records an event into.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel import media
from sift.kernel import subprocess as kernel_subprocess
from sift.kernel.ids import new_id
from sift.kernel.jobs import (
    JobContext,
    JobQueue,
)
from sift.slices.media_jobs import jobs

Context = Callable[[str, dict[str, object]], Awaitable[JobContext]]


async def probe_and_fingerprint(context: JobContext, **kwargs: Any) -> None:
    """The read, then the fingerprint job it handed out, run as the pool would run them. The read
    does not hash: a test of what an arriving file ends up with runs both."""
    await jobs.probe(context, **kwargs)
    for child in await context.queue.children(context.job.id):
        if child.type != jobs.FINGERPRINT_FILE:
            continue
        worker = new_id()
        claimed = await context.queue.claim(worker)
        while claimed is not None and claimed.id != child.id:
            claimed = await context.queue.claim(worker)
        assert claimed is not None, "the fingerprint job the read handed out was not there"
        await jobs.fingerprint_arrival(
            JobContext(
                job=claimed,
                worker_id=worker,
                queue=context.queue,
                capabilities=context.capabilities,
            ),
            settings=kwargs["settings"],
        )


def watch_enqueues(job_queue: JobQueue, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The job types this pass starts, watched as they start: they are deduped enqueues with no
    parent, so the queue's children list would be empty whatever happened."""
    started: list[str] = []
    original = job_queue.enqueue

    async def noting(job_type: str, payload: object = None, **kwargs: object) -> str:
        started.append(job_type)
        return await original(job_type, payload, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(job_queue, "enqueue", noting)
    return started


def watch_launches(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every tool launch the kernel makes, in order, with the real tool still run."""
    seen: list[list[str]] = []
    real_run, real_capture = media.run, kernel_subprocess.capture

    async def counted_run(argv: list[str], **kwargs: Any) -> bytes:
        seen.append(argv)
        return await real_run(argv, **kwargs)

    async def counted_capture(argv: list[str], **kwargs: Any) -> bytes:
        seen.append(argv)
        return await real_capture(argv, **kwargs)

    monkeypatch.setattr(media, "run", counted_run)
    monkeypatch.setattr(kernel_subprocess, "capture", counted_capture)
    return seen
