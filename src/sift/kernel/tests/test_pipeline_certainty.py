# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a Generate counts is what it makes, a stale reading is work, and a lost card holds a job.

Three rules that decide whether a pass over a new library can reach nought and stay there: a count
of missing pictures leaves out the files no picture of that kind is made for; a kept reading under
an older version is still to do; and a job the machine cannot run just now waits without spending
its attempts.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from sift.kernel.content import ContentStore, DerivativeKind, lacks_derivative
from sift.kernel.content.identity import PROBE_VERSION_NOT_A_JPEG, ProbeKeep, made_for
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import (
    JobContext,
    JobHeld,
    JobQueue,
    JobState,
    WorkerPool,
    register_handler,
    worker_pool,
)
from sift.testing.fixtures import LibraryRoot

pytestmark = pytest.mark.unit

_EPOCH = 1_700_000_000

#: One of each shape a file can have: a photo, a GIF, a video, and a video with no running time.
_SHAPES = (("image", None), ("gif", 2000), ("video", 5000), ("video", 0))


async def _file(
    database: Database, root: LibraryRoot, media_type: str, duration: int | None
) -> str:
    asset_id = new_id()
    await database.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, duration_ms, added_at,"
        " probed_at) VALUES (?, ?, 1, ?, ?, ?, ?)",
        (asset_id, f"digest-{asset_id}", media_type, duration, _EPOCH, _EPOCH),
    )
    await database.execute(
        "INSERT INTO asset_locations"
        " (id, asset_id, root_id, rel_path, filename, status, first_seen_at, last_seen_at)"
        " VALUES (?, ?, ?, ?, ?, 'present', ?, ?)",
        (new_id(), asset_id, root.id, f"{asset_id}.bin", f"{asset_id}.bin", _EPOCH, _EPOCH),
    )
    return asset_id


@pytest.mark.parametrize(
    "kind", [DerivativeKind.THUMB, DerivativeKind.PREVIEW, DerivativeKind.SPRITE]
)
async def test_a_picture_nothing_makes_is_never_counted_as_missing(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot, kind: DerivativeKind
) -> None:
    files = {
        await _file(temp_db, library_root, media_type, duration): (media_type, duration)
        for media_type, duration in _SHAPES
    }
    wanted = {
        asset_id
        for asset_id, (media_type, duration) in files.items()
        if made_for(kind, media_type=media_type, duration_ms=duration)
    }
    term = lacks_derivative([kind])
    assert term is not None

    # The count and the page name the same files as the rule.
    assert (await content_store.count_lacking([term])).files == len(wanted)
    assert await content_store.lacking_derivative(kind, list(files)) == wanted

    # And made for every file it is made for, nought is left: a Generate can finish.
    for asset_id in wanted:
        await content_store.add_derivative(asset_id, kind, extension="jpg")
    assert (await content_store.count_lacking([term])).files == 0


def test_a_still_has_no_hover_clip_and_nothing_to_scrub() -> None:
    assert made_for(DerivativeKind.THUMB, media_type="image", duration_ms=None)
    assert not made_for(DerivativeKind.PREVIEW, media_type="image", duration_ms=None)
    assert not made_for(DerivativeKind.SPRITE, media_type="image", duration_ms=None)
    assert made_for(DerivativeKind.PREVIEW, media_type="gif", duration_ms=2000)
    assert not made_for(DerivativeKind.SPRITE, media_type="video", duration_ms=0)
    assert made_for(DerivativeKind.SPRITE, media_type="video", duration_ms=5000)


async def test_a_reading_kept_under_an_older_version_is_read_again(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot
) -> None:
    current = await _file(temp_db, library_root, "video", 5000)
    older = await _file(temp_db, library_root, "video", 5000)
    await content_store.keep_probe(current, ProbeKeep(body=b"{}", tool="t"))
    await content_store.keep_probe(
        older, ProbeKeep(body=b"{}", tool="t", version=PROBE_VERSION_NOT_A_JPEG - 1)
    )

    assert await content_store.assets_lacking_probe_rows(10) == [older]
    assert await content_store.assets_lacking_probe_rows_count() == 1


async def test_a_jpeg_photograph_kept_before_the_browser_s_turn_is_looked_at_again(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot
) -> None:
    photograph = await _file(temp_db, library_root, "image", None)
    await temp_db.execute("UPDATE assets SET mime = 'image/jpeg' WHERE id = ?", (photograph,))
    kept = ProbeKeep(body=b"{}", tool="t", version=PROBE_VERSION_NOT_A_JPEG)
    await content_store.keep_probe(photograph, kept)

    assert await content_store.assets_lacking_probe_rows(10) == [photograph]
    assert await content_store.probe_still_current(photograph)
    assert await content_store.assets_lacking_probe_rows(10) == []
    # A file with no reading kept is read, never marked.
    assert not await content_store.probe_still_current(
        await _file(temp_db, library_root, "image", None)
    )


class _CardStopped(Exception):
    """Stands for the model process losing its graphics card."""


@pytest.mark.integration
@pytest.mark.parametrize("raised", ["held", "declared"])
async def test_a_job_the_machine_cannot_run_just_now_waits_without_spending_an_attempt(
    job_queue: JobQueue, monkeypatch: pytest.MonkeyPatch, raised: str
) -> None:
    monkeypatch.setattr(worker_pool, "_HOLDS", {})
    worker_pool.hold_on(_CardStopped, seconds=60)

    async def lost_card(context: JobContext) -> None:
        if raised == "held":
            raise JobHeld("the graphics card stopped answering", retry_in=60)
        raise _CardStopped("the graphics card stopped answering")

    register_handler("face_scan_test", lost_card, name="Test job")
    job_id = await job_queue.enqueue("face_scan_test", max_attempts=3)

    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = await job_queue.get(job_id)
            assert job is not None
            if job.error is not None and job.state is JobState.QUEUED:
                break
            await asyncio.sleep(0.01)
    finally:
        await pool.stop()

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.QUEUED
    assert job.attempts == 0
    assert job.run_after is not None and job.run_after >= int(time.time()) + 50
    assert job.error == "the graphics card stopped answering"
