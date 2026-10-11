# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pool around the queue: what the ledger is told, and that no handler knows one exists."""

from __future__ import annotations

import time

import pytest

from sift.kernel.content import ContentStore, LibraryStore
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import (
    JobContext,
    JobFailedPermanently,
    JobQueue,
    JobState,
    SystemCapabilities,
    WorkerPool,
    register_handler,
    registry,
    worker_pool,
)
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.ledger import Ledger, report_text
from sift.kernel.media_sources import NoReadableCopy
from sift.kernel.tests.jobs_helpers import (
    WORKER,
    Recorder,
    drain,
    noop_handler,
)
from sift.testing.fixtures import FakeClock

pytestmark = pytest.mark.usefixtures("clean_handlers")


# --- what the ledger is told: no handler knows a ledger exists -------------------------------


@pytest.mark.integration
async def test_a_pool_with_a_ledger_tells_it_what_each_job_was_about(
    job_queue: JobQueue,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One job about a video of a known size, saying it was about three files: the run opens with
    it, and counts three video files of that many bytes."""
    monkeypatch.setattr(registry, "_FAMILIES", {})
    units_seen: list[int] = []

    async def handler(context: JobContext) -> None:
        await context.set_units(3)
        units_seen.append(context.units)

    register_handler("probe", handler, name="Test job", family=Family.SCAN)
    asset_id = new_id()
    await temp_db.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, size_bytes, added_at)"
        " VALUES (?, ?, 1, 'video', 4096, 1700000000)",
        (asset_id, f"digest-{asset_id}"),
    )
    await job_queue.enqueue("probe", {"asset_id": asset_id})

    ledger = Ledger(temp_db, families_of=worker_pool.registered_families())
    pool = WorkerPool(
        job_queue,
        concurrency=1,
        poll_interval=0.01,
        capabilities=SystemCapabilities(content=content_store, library=library_store),
        ledger=ledger,
    )
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    assert units_seen == [3]
    run = ledger.open_run(Family.SCAN)
    assert run is not None
    assert run.jobs_done == 1 and run.jobs_failed == 0
    assert run.files["video"].n == 3 and run.files["video"].bytes == 4096
    assert run.worker_ms > 0


@pytest.mark.integration
async def test_the_ledger_is_told_what_a_job_ended_with_only_once_it_will_not_be_tried_again(
    job_queue: JobQueue,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "_FAMILIES", {})

    async def missing(_context: JobContext) -> None:
        raise NoReadableCopy("clip.mp4")

    async def gone(_context: JobContext) -> None:
        raise JobFailedPermanently("The folder stopped answering partway through the scan.")

    register_handler("probe", missing, name="Test job", family=Family.SCAN)
    register_handler("scan", gone, name="Test walk", family=Family.SCAN)
    await job_queue.enqueue("probe", {}, max_attempts=2)
    await job_queue.enqueue("scan", {})
    ledger = Ledger(temp_db, families_of=worker_pool.registered_families())
    pool = WorkerPool(
        job_queue,
        concurrency=1,
        poll_interval=0.01,
        capabilities=SystemCapabilities(content=content_store, library=library_store),
        ledger=ledger,
    )
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    run = ledger.open_run(Family.SCAN)
    assert run is not None and run.jobs_failed == 3
    assert sorted(run.ended_with.values()) == [1, 1]
    assert any(words.startswith("A file it needed wasn't there.") for words in run.ended_with)


async def test_a_done_jobs_note_reaches_the_report_and_a_failed_ones_does_not(
    job_queue: JobQueue, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(registry, "_FAMILIES", {})
    unread = (
        "1 folder stopped answering partway through, so nothing in it was marked missing or"
        " unreadable."
    )

    async def left_one(context: JobContext) -> None:
        await context.set_note(unread)

    async def gone(context: JobContext) -> None:
        await context.set_note("The folder stopped answering partway through the scan.")
        raise NoReadableCopy("clip.mp4")

    register_handler("scan", left_one, name="Test walk", family=Family.SCAN)
    register_handler("probe", gone, name="Test job", family=Family.SCAN)
    await job_queue.enqueue("scan", {})
    await job_queue.enqueue("probe", {}, max_attempts=2)
    ledger = Ledger(temp_db, families_of=worker_pool.registered_families())
    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01, ledger=ledger)
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()
    run = ledger.open_run(Family.SCAN)
    assert run is not None
    await ledger._write(run, int(time.time()))
    record = await ledger.get(run.id)
    assert record is not None
    said = report_text(record).splitlines()
    assert [line for line in said if "ended with" in line.lower() or "failed:" in line] == [
        f"Ended with: {unread}",
        "Why 1 failed: A file it needed wasn't there. It may have been moved or deleted, or its"
        " drive isn't connected.",
    ], "a failed attempt's note, retried or not, is never said"


@pytest.mark.integration
async def test_the_run_is_requested_by_the_user_on_the_job_and_children_carry_nobody(
    job_queue: JobQueue,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pool hands the claimed job's `requested_by` to the ledger, so the run names the person;
    the children the job hands out carry nobody, so another family's run is not named for a press
    that was not of it."""
    monkeypatch.setattr(registry, "_FAMILIES", {})
    children: list[str] = []

    async def walk(context: JobContext) -> None:
        children.append(await context.enqueue_child("thumbnail"))

    register_handler("scan", walk, name="Test job", family=Family.SCAN)

    async def thumbnail(context: JobContext) -> None:
        return None

    register_handler("thumbnail", thumbnail, name="Test job", family=Family.GENERATE)
    await job_queue.enqueue("scan", requested_by="acct-1")

    ledger = Ledger(temp_db, families_of=worker_pool.registered_families())
    pool = WorkerPool(
        job_queue,
        concurrency=1,
        poll_interval=0.01,
        capabilities=SystemCapabilities(content=content_store, library=library_store),
        ledger=ledger,
    )
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    scanned = ledger.open_run(Family.SCAN)
    assert scanned is not None and scanned.requested_by == "acct-1"
    generated = ledger.open_run(Family.GENERATE)
    assert generated is not None and generated.requested_by is None
    (child,) = children
    handed = await job_queue.get(child)
    assert handed is not None and handed.requested_by is None


@pytest.mark.integration
async def test_the_run_records_the_products_its_jobs_were_for(
    job_queue: JobQueue,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pool hands the ledger what a claimed job was FOR: a carrier's payload products, and for
    any other job the product its type is the maker of. So a Generate run of the music product is
    Music's run, not Generate's thumbnails'."""
    monkeypatch.setattr(registry, "_FAMILIES", {})

    async def nothing(context: JobContext) -> None:
        return None

    register_handler(
        "generate_file", nothing, name="Test job", family=Family.GENERATE, carries_products=True
    )
    register_handler("face_scan", nothing, name="Test job", family=Family.IDENTIFY)
    await job_queue.enqueue("generate_file", {"asset_id": "a1", "products": ["music"]})
    await job_queue.enqueue("face_scan", {"asset_id": "a1"})

    ledger = Ledger(
        temp_db,
        families_of=worker_pool.registered_families(),
        products_of={"face_scan": ("faces",)},
    )
    pool = WorkerPool(
        job_queue,
        concurrency=1,
        poll_interval=0.01,
        capabilities=SystemCapabilities(content=content_store, library=library_store),
        ledger=ledger,
    )
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    generated = ledger.open_run(Family.GENERATE)
    assert generated is not None and generated.made_for == {"music"}
    identified = ledger.open_run(Family.IDENTIFY)
    assert identified is not None and identified.made_for == {"faces"}


@pytest.mark.integration
async def test_work_handed_to_another_job_of_the_same_family_is_not_work_done(
    job_queue: JobQueue,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scan's walk says it is about the files it will read and then hands every one of them to a
    probe, and a probe is the same family, so counting both would make one file two files of
    progress, with the walk's whole count landing at the instant it ended. The walk here is about
    five files and hands out four, so it did one.

    The probes are counted too, one each, which is the half that belongs to the family's pace: what
    a scan has LEFT is files nothing has read yet, and reading one is what takes it off that pile.
    """
    monkeypatch.setattr(registry, "_FAMILIES", {})

    async def walk(context: JobContext) -> None:
        await context.set_units(5)
        for _ in range(4):
            await context.enqueue_child("probe")
        # A thumbnail is another family's work: asking for one is not doing part of it.
        await context.enqueue_child("thumbnail")

    register_handler("scan", walk, name="Test walk", family=Family.SCAN)
    register_handler("probe", Recorder().handler, name="Test probe", family=Family.SCAN)
    register_handler("thumbnail", Recorder().handler, name="Test build", family=Family.GENERATE)
    await job_queue.enqueue("scan")

    ledger = Ledger(temp_db, families_of=worker_pool.registered_families())
    pool = WorkerPool(
        job_queue,
        concurrency=1,
        poll_interval=0.01,
        capabilities=SystemCapabilities(content=content_store, library=library_store),
        ledger=ledger,
    )
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    run = ledger.open_run(Family.SCAN)
    assert run is not None
    # Five jobs ran in this family (the walk and its four probes), and the walk's five files are
    # not five files of progress. One from the walk, four from the probes.
    assert run.jobs_done == 5
    assert run.files_total == 5
    built = ledger.open_run(Family.GENERATE)
    assert built is not None and built.files_total == 1


@pytest.mark.integration
async def test_a_file_the_store_cannot_say_anything_about_is_counted_with_no_kind(
    job_queue: JobQueue,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A payload naming no file, one naming a file the store has no row for, and one naming a file
    the store cannot read at all: every job is counted, as a file of no known kind, and the read
    failing is never the job failing."""
    monkeypatch.setattr(registry, "_FAMILIES", {})
    register_handler("probe", Recorder().handler, name="Test job", family=Family.SCAN)
    ledger = Ledger(temp_db, families_of=worker_pool.registered_families())
    pool = WorkerPool(
        job_queue,
        concurrency=1,
        poll_interval=0.01,
        capabilities=SystemCapabilities(content=content_store, library=library_store),
        ledger=ledger,
    )

    nameless = await job_queue.enqueue("probe")
    missing = await job_queue.enqueue("probe", {"asset_id": "no-such-row"})
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    async def unreadable(asset_id: str) -> None:
        raise RuntimeError("the database went away")

    monkeypatch.setattr(content_store, "get", unreadable)
    unread = await job_queue.enqueue("probe", {"asset_id": "some-row"})
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    for job_id in (nameless, missing, unread):
        job = await job_queue.get(job_id)
        assert job is not None and job.state is JobState.DONE
    run = ledger.open_run(Family.SCAN)
    assert run is not None
    assert run.jobs_done == 3
    assert run.files["unknown"].n == 3 and run.files["unknown"].bytes == 0


async def test_the_payloads_still_in_the_queue_are_read_from_the_queue(temp_db: Database) -> None:
    """What a run was ASKED for, read off the rows rather than remembered by whoever queued it.

    The bar for a pass needs the products every live run of a family is making, and the pass queues
    a page at a time, so a handler holding that in memory answers wrongly the moment the process
    restarts, which is exactly when somebody looks. Reading the queue means the answer survives it.

    Waiting, running AND blocked, because all three are runs that have not finished: a blocked job
    left out would take its products off the denominator and the bar would jump.
    """
    await temp_db.initialize_schema()
    queue = JobQueue(temp_db, clock=FakeClock(1000).now)

    mine = noop_handler("a_pass_of_mine")
    waiting = await queue.enqueue(mine, {"products": ["thumbnails"]})
    running = await queue.enqueue(mine, {"products": ["fingerprints"]})
    await queue.claim(WORKER)
    await queue.enqueue("other_family", {"products": ["faces"]}, require_handler=False)

    live = await queue.live_payloads(mine)

    assert {tuple(one["products"]) for one in live} == {("thumbnails",), ("fingerprints",)}
    assert len(live) == 2, "the other family's run is on somebody else's bar"
    assert waiting != running
    # A family nothing has queued is an empty list rather than an absence to guard against.
    assert await queue.live_payloads("nobody_queued_this") == []
