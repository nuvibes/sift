# SPDX-License-Identifier: AGPL-3.0-or-later
"""A pass whose run failed says so on its own row until a later run makes it good."""

from __future__ import annotations

import sys

import pytest

from sift.kernel.db import Database
from sift.kernel.jobs import JobContext, JobQueue, JobState, register_handler
from sift.kernel.jobs.failure_words import in_plain_words
from sift.kernel.jobs.families import Family
from sift.slices.media_jobs.router import _failed_runs

ROUTER = sys.modules[_failed_runs.__module__]

WALK = "test_pass_failures_walk"
GONE = (
    "The folder stopped answering partway through the scan, so nothing in it was marked missing"
    " or unreadable. Scan it again once it's back."
)


@pytest.fixture
async def queue(temp_db: Database, monkeypatch: pytest.MonkeyPatch) -> JobQueue:
    await temp_db.initialize_schema()
    now = [1_000.0]

    def clock() -> float:
        now[0] += 10
        return now[0]

    async def walk(_context: JobContext) -> None:
        return None

    register_handler(WALK, walk, name="Walking", family=Family.SCAN)
    monkeypatch.setattr(ROUTER, "_run_types", lambda: {Family.SCAN: [WALK]})
    return JobQueue(temp_db, clock=clock)


async def _ran(queue: JobQueue, payload: dict[str, object], error: str | None = None) -> None:
    job_id = await queue.enqueue(WALK, payload)
    job = await queue.claim("worker")
    assert job is not None and job.id == job_id
    if error is None:
        assert await queue.complete(job_id, "worker")
    else:
        assert await queue.fail(job_id, "worker", error, permanent=True) is JobState.FAILED


async def test_a_failed_scan_stands_until_a_later_walk_of_its_folder_is_done(
    queue: JobQueue,
) -> None:
    await _ran(queue, {"root_id": "shows"}, GONE)
    said = (1, in_plain_words(GONE))
    assert await _failed_runs(queue) == {"scan": said}

    await _ran(queue, {"root_id": "shows", "paths": ["a.png"]})
    await _ran(queue, {"root_id": "films"})
    assert await _failed_runs(queue) == {"scan": said}, "named files or another folder"

    await _ran(queue, {"root_id": "shows"})
    assert await _failed_runs(queue) == {}


async def test_a_pass_with_no_failed_run_says_nothing(queue: JobQueue) -> None:
    await _ran(queue, {"root_id": "shows"})
    assert await _failed_runs(queue) == {}


RESTARTED = "Sift was restarted while this job was running"


async def test_a_failure_over_a_folder_no_longer_in_the_library_does_not_stand(
    queue: JobQueue,
) -> None:
    await _ran(queue, {"root_id": "gone"}, GONE)
    await _ran(queue, {"root_id": "shows"}, GONE)
    assert await _failed_runs(queue, roots={"shows"}) == {"scan": (1, in_plain_words(GONE))}

    await _ran(queue, {"root_id": "shows"})
    assert await _failed_runs(queue, roots={"shows"}) == {}


async def test_a_restart_is_made_good_by_any_later_scan_of_its_folder(queue: JobQueue) -> None:
    await _ran(queue, {"root_id": "shows"}, RESTARTED)
    await _ran(queue, {"root_id": "films"}, GONE)
    await _ran(queue, {"root_id": "shows", "paths": ["a.png"]})
    await _ran(queue, {"root_id": "films", "paths": ["b.png"]})
    assert await _failed_runs(queue, roots={"shows", "films"}) == {
        "scan": (1, in_plain_words(GONE))
    }
