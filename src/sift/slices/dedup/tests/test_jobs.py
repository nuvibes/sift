# SPDX-License-Identifier: AGPL-3.0-or-later
"""The scan as a background job: claimed under the name the queue looks for, and doing what a
scan does."""

from __future__ import annotations

import asyncio
import random
import threading
import time
from typing import Any

import pytest

from sift.kernel.content.duplicates import DuplicateReads, Fingerprint
from sift.kernel.db import Database
from sift.kernel.jobs import JobContext, JobQueue, registered_handlers
from sift.slices.dedup import schema
from sift.slices.dedup import service as service_module
from sift.slices.dedup.grouping import group_pairs
from sift.slices.dedup.jobs import DEDUP_SCAN, dedup_scan, register_handlers
from sift.slices.dedup.matcher import VIDEO_HEX, Matcher
from sift.slices.dedup.service import DedupService
from sift.slices.dedup.tests.conftest import Library
from sift.slices.dedup.tests.test_dedup import dials

pytestmark = pytest.mark.integration

_PHASH = "0f0f0f0f0f0f0f0f"
_NEAR = "0f0f0f0f0f0f0f0e"


async def test_the_scan_is_claimed_under_the_name_the_queue_uses(
    service: DedupService, clean_handlers: None
) -> None:
    """A job type nothing can execute is refused by the queue, so the name has to match."""
    register_handlers(service=service)

    assert DEDUP_SCAN in registered_handlers()


async def test_running_the_job_files_the_pairs(
    temp_db: Database,
    service: DedupService,
    managed: Library,
    add_file: Any,
    job_queue: JobQueue,
    clean_handlers: None,
) -> None:
    """The handler does what a scan does, and reports itself finished."""
    first = await add_file(managed, "one.jpg", "accepted.jpg")
    second = await add_file(managed, "two.png", "accepted.png")
    # Two photographs: a photograph and a video are never compared.
    await temp_db.execute("UPDATE assets SET phash = ? WHERE id = ?", (_PHASH, first.asset.id))
    await temp_db.execute("UPDATE assets SET phash = ? WHERE id = ?", (_NEAR, second.asset.id))

    # Registered first: the queue refuses a job type nothing can execute, which is the guard
    # doing its job rather than something to work around.
    register_handlers(service=service)
    await job_queue.enqueue(DEDUP_SCAN, {})

    # Claimed the way a worker claims it, so the job the handler is handed is a real queued job
    # rather than one built by hand: the progress it reports has somewhere to go.
    claimed = await job_queue.claim("worker-one")
    assert claimed is not None
    await dedup_scan(
        JobContext(job=claimed, worker_id="worker-one", queue=job_queue), service=service
    )

    assert len(await service.groups(dials())) == 1
    # The run's own sentence, which the Tasks row and Organize's Duplicates bar say as the last run.
    noted = await temp_db.fetch_one("SELECT note FROM jobs WHERE id = ?", (claimed.id,))
    assert noted is not None and noted["note"] == "Found 1 new pairs to review."


async def test_the_plan_is_what_the_sweep_files_and_writes_nothing(
    temp_db: Database, service: DedupService, managed: Library, add_file: Any
) -> None:
    """A dry run asks `plan`, and the sweep files exactly its pairs: planned twice, nothing is
    written; swept, the planned pair is filed; planned again, nothing is left to file."""
    first = await add_file(managed, "one.jpg", "accepted.jpg")
    second = await add_file(managed, "two.png", "accepted.png")
    await temp_db.execute("UPDATE assets SET phash = ? WHERE id = ?", (_PHASH, first.asset.id))
    await temp_db.execute("UPDATE assets SET phash = ? WHERE id = ?", (_NEAR, second.asset.id))

    planned = await service.plan()
    assert len(planned.pairs) == 1 and planned.compared == 2
    assert (await service.plan()).pairs == planned.pairs
    counted = await temp_db.fetch_one("SELECT COUNT(*) AS n FROM dedup_candidates")
    assert counted is not None and counted["n"] == 0

    assert await service.scan() == 1
    assert (await service.plan()).pairs == ()


# --- the corners ------------------------------------------------------------------------------


async def test_releasing_from_one_of_several_redundant_assets_finds_the_right_one(
    temp_db: Database, service: DedupService, managed: Library, add_file: Any, recorder: Any
) -> None:
    """Two files each sitting in two places. Releasing a copy of the second must not touch the
    first, which is the case a loop over redundancies gets wrong by stopping early."""
    first = await add_file(managed, "a-one.mp4", "accepted.mp4")
    await add_file(managed, "a-two.mp4", "accepted.mp4")
    second = await add_file(managed, "b-one.jpg", "accepted.jpg")
    await add_file(managed, "b-two.jpg", "accepted.jpg")

    entries = {entry.asset_id: entry for entry in await service.reclaim(limit=50, offset=0)}
    assert len(entries) == 2

    doomed = entries[second.asset.id].copies[1]
    from sift.kernel.access import Role
    from sift.testing.fixtures import create_user

    admin = await create_user(temp_db, Role.ADMIN)
    await service.release(second.asset.id, doomed.location_id, actor=admin)

    assert len(recorder.asked) == 1
    assert recorder.asked[0].asset_id == second.asset.id
    assert recorder.asked[0].asset_id != first.asset.id


def test_a_video_fingerprint_of_the_wrong_width_is_not_comparable() -> None:
    """A video fingerprint of the wrong width is not comparable, asserted at the comparison."""
    matcher = Matcher()
    full = "ab" * (VIDEO_HEX // 2)
    short = "ab" * ((VIDEO_HEX - 16) // 2)

    assert matcher._frames_apart(short, full) is None
    assert matcher._frames_apart(full, short) is None
    assert matcher._frames_apart(full, full) == 0


def test_two_videos_sharing_one_frame_are_looked_at_and_turned_down() -> None:
    """Videos sharing one frame are compared and turned down."""
    # Spread across the 64-bit space: sequential integers are all within a few bits.
    rng = random.Random(11)
    frames_a = [f"{rng.getrandbits(64):016x}" for _ in range(30)]
    frames_b = [frames_a[0]] + [f"{rng.getrandbits(64):016x}" for _ in range(29)]

    # A third video sharing a frame with each of the others, so the comparison has to reject one
    # candidate and carry on to the next rather than giving up at the first no.
    frames_c = [frames_a[1], frames_b[1]] + [f"{rng.getrandbits(64):016x}" for _ in range(28)]

    found = Matcher().find(
        [
            Fingerprint("a", "digest-a", "video", frames_a[0], "".join(frames_a)),
            Fingerprint("b", "digest-b", "video", frames_b[0], "".join(frames_b)),
            Fingerprint("c", "digest-c", "video", frames_c[0], "".join(frames_c)),
        ]
    )

    assert found == []


def test_three_copies_of_one_video_produce_every_pair_between_them() -> None:
    """Three copies of one video give every pair between them: each is a separate decision."""
    rng = random.Random(23)
    original = [f"{rng.getrandbits(64):016x}" for _ in range(30)]

    def re_encoded(seed: int) -> list[str]:
        noise = random.Random(seed)
        out = []
        for frame in original:
            value = int(frame, 16)
            for bit in noise.sample(range(63), 3):
                value ^= 1 << bit
            out.append(f"{value:016x}")
        return out

    videos = [original, re_encoded(1), re_encoded(2)]
    found = Matcher().find(
        [
            # GIFs, because the thirty-frame comparison is the GIF one: a video is judged by a
            # single number for the whole video.
            Fingerprint(name, f"digest-{name}", "gif", frames[0], "".join(frames))
            for name, frames in zip("abc", videos, strict=True)
        ]
    )

    assert {(pair.asset_a, pair.asset_b) for pair in found} == {("a", "b"), ("a", "c"), ("b", "c")}


def test_two_fingerprints_that_cannot_be_compared_do_not_agree() -> None:
    """Fingerprints that cannot be compared do not agree, asserted at the comparison."""
    matcher = Matcher()
    a_frame = "0123456789abcdef"
    a_video = a_frame * 30

    assert matcher._distance(a_frame, a_video) is None
    assert matcher._agree(a_frame, a_video) is False
    assert matcher._agree("", "") is False
    # And the comparison it is standing in for still works on things that are comparable.
    assert matcher._agree(a_frame, a_frame) is True


def test_two_videos_with_the_same_digest_never_become_a_pair() -> None:
    """Two videos with the same digest never become a pair, as for stills."""
    rng = random.Random(31)
    frames = [f"{rng.getrandbits(64):016x}" for _ in range(30)]
    videohash = "".join(frames)

    found = Matcher().find(
        [
            Fingerprint("a", "one-and-the-same", "gif", frames[0], videohash),
            Fingerprint("b", "one-and-the-same", "gif", frames[0], videohash),
        ]
    )

    assert found == []


def test_each_refusal_gets_its_own_answer() -> None:
    """Each refusal has its own status: 404 nothing there, 403 not allowed, 409 the disk says no."""
    from fastapi import status

    from sift.slices.dedup.router import _refusal
    from sift.slices.dedup.service import NotAllowed, NotFound, RemovalRefused

    assert _refusal(NotFound("gone")).status_code == status.HTTP_404_NOT_FOUND
    assert _refusal(NotAllowed("no")).status_code == status.HTTP_403_FORBIDDEN
    assert _refusal(RemovalRefused("read-only")).status_code == status.HTTP_409_CONFLICT
    # The sentence survives: it is the only thing telling somebody which of the three happened.
    assert _refusal(RemovalRefused("that folder is read-only")).detail == "that folder is read-only"


async def test_the_schema_is_not_rebuilt_over_answers_somebody_already_gave(
    temp_db: Database, service: DedupService, managed: Library, add_file: Any
) -> None:
    """The initializer leaves a schema already at its version, and the dismissals in it, alone."""
    first = await add_file(managed, "one.jpg", "accepted.jpg")
    second = await add_file(managed, "two.png", "accepted.png")
    # Two photographs: a photograph and a video are never compared.
    await temp_db.execute("UPDATE assets SET phash = ? WHERE id = ?", (_PHASH, first.asset.id))
    await temp_db.execute("UPDATE assets SET phash = ? WHERE id = ?", (_NEAR, second.asset.id))
    await service.scan()

    from sift.kernel.access import Role
    from sift.testing.fixtures import create_user

    (group,) = await service.groups(dials())
    (candidate_id,) = group.pairs
    admin = await create_user(temp_db, Role.ADMIN)
    await service.dismiss_group(group, actor=admin)

    async with temp_db.write() as connection:
        await schema.initialize(connection, on_disk=schema.VERSION)

    assert (await service.get(candidate_id)).status == "dismissed"


# --- the third kind of finding ----------------------------------------------------------------

#: The table at version two, written out as somebody upgraded once really has it.
_V2_CANDIDATES = """
CREATE TABLE dedup_candidates (
  id         TEXT PRIMARY KEY,
  asset_a    TEXT,
  asset_b    TEXT,
  method     TEXT CHECK(method IN ('phash','videohash','scene_phash')),
  distance   INTEGER,
  status     TEXT NOT NULL DEFAULT 'pending'
             CHECK(status IN ('pending','confirmed','dismissed')),
  created_at INTEGER NOT NULL,
  UNIQUE(asset_a, asset_b, method)
)
"""

_OLD_CANDIDATES = """
CREATE TABLE dedup_candidates (
  id         TEXT PRIMARY KEY,
  asset_a    TEXT,
  asset_b    TEXT,
  method     TEXT CHECK(method IN ('phash','videohash')),
  distance   INTEGER,
  status     TEXT NOT NULL DEFAULT 'pending'
             CHECK(status IN ('pending','confirmed','dismissed')),
  created_at INTEGER NOT NULL,
  UNIQUE(asset_a, asset_b, method)
)
"""


class _Handed(DuplicateReads):
    """Fingerprints handed straight to the service; the database, writer and job are real."""

    def __init__(self, database: Database, rows: list[Fingerprint]) -> None:
        super().__init__(database)
        self._rows = rows

    async def fingerprints(self) -> list[Fingerprint]:
        return self._rows


async def test_the_comparison_leaves_the_event_loop(
    temp_db: Database, recorder: Any, clock: Any
) -> None:
    """The comparison runs off the event loop: a watcher task wakes many times during the scan.
    Ten thousand videos spread over the 64-bit space, so nothing is filed and the fold is what is
    timed; the duration is asserted so a fast machine says so."""
    await temp_db.initialize_schema()
    rng = random.Random(7)
    rows = [
        Fingerprint(
            asset_id=f"asset-{position}",
            identity=f"digest-{position}",
            media_type="video",
            phash=None,
            videohash=None,
            video_phash=f"{rng.getrandbits(64):016x}",
        )
        for position in range(10_000)
    ]
    service = DedupService(temp_db, _Handed(temp_db, rows), recorder, clock=clock.now)

    woken = 0

    async def probe() -> None:
        nonlocal woken
        while True:
            await asyncio.sleep(0.005)
            woken += 1

    watching = asyncio.create_task(probe())
    # One turn, so the watcher is actually waiting before the fold starts rather than starting after
    # it has finished, which would count its wake-ups and prove nothing about the fold at all.
    await asyncio.sleep(0)
    began = time.perf_counter()
    filed = await service.scan()
    took = time.perf_counter() - began
    watching.cancel()

    assert filed == 0
    assert took > 0.1, "the fold has to last long enough for a count of wake-ups to mean anything"
    assert woken >= 3


async def test_the_cluster_fold_leaves_the_event_loop(
    temp_db: Database, recorder: Any, clock: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cluster fold behind the Duplicates screen runs off the event loop, asserted on the
    thread it ran on. Ten thousand real pairs behind the table's foreign keys, all plain pairs."""
    await temp_db.initialize_schema()
    pairs = 10_000
    async with temp_db.write() as connection:
        await connection.executemany(
            "INSERT INTO assets (id, identity, media_type, width, height, size_bytes, added_at)"
            " VALUES (?, ?, 'video', 1920, 1080, ?, ?)",
            [
                (f"asset-{position}", f"digest-{position}", 1_000_000 + position, position)
                for position in range(pairs * 2)
            ],
        )
        await connection.executemany(
            "INSERT INTO dedup_candidates"
            " (id, asset_a, asset_b, method, distance, duration_gap_ms, created_at)"
            " VALUES (?, ?, ?, 'video_phash', 0, 0, 1)",
            [
                (f"pair-{position}", f"asset-{position * 2}", f"asset-{position * 2 + 1}")
                for position in range(pairs)
            ],
        )
    service = DedupService(temp_db, DuplicateReads(temp_db), recorder, clock=clock.now)

    ran_on: list[str] = []
    # The real fold from its own module: the service's copy of the name is an import, not an
    # export, and mypy refuses to read one through the module that borrowed it.
    real = group_pairs

    def watched(*args: Any, **rest: Any) -> Any:
        ran_on.append(threading.current_thread().name)
        return real(*args, **rest)

    monkeypatch.setattr(service_module, "group_pairs", watched)

    began = time.perf_counter()
    groups = await service.groups(dials())
    took = time.perf_counter() - began

    assert len(groups) == pairs, "the fixture has to reach the fold for its thread to mean anything"
    assert ran_on, "the fold did not run at all"
    assert ran_on[0] != threading.main_thread().name, (
        f"the cluster fold ran on {ran_on[0]}, which is the event loop's own thread: "
        f"{took * 1000:.0f} ms of arithmetic with every screen in the application waiting on it"
    )


async def test_a_pair_filed_between_the_plan_and_the_write_is_not_counted_or_reset(
    temp_db: Database,
    service: DedupService,
    managed: Library,
    add_file: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another scan files the pair after this one planned it: this one counts nothing new and
    leaves the row, and the answer somebody gave it, exactly as they are."""
    first = await add_file(managed, "one.jpg", "accepted.jpg")
    second = await add_file(managed, "two.png", "accepted.png")
    await temp_db.execute("UPDATE assets SET phash = ? WHERE id = ?", (_PHASH, first.asset.id))
    await temp_db.execute("UPDATE assets SET phash = ? WHERE id = ?", (_NEAR, second.asset.id))
    planned = await service.plan()
    assert await service.scan() == 1
    await temp_db.execute("UPDATE dedup_candidates SET status = 'dismissed'")

    async def _planned_before() -> object:
        return planned

    monkeypatch.setattr(service, "plan", _planned_before)

    assert await service.scan() == 0
    rows = await temp_db.fetch_all("SELECT status FROM dedup_candidates")
    assert [row["status"] for row in rows] == ["dismissed"]
