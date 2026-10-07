# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pool around the queue: reconfiguring, the ledger it tells, who goes first, readiness."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from sift.kernel.content import ContentStore, LibraryStore
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import (
    Handler,
    JobContext,
    JobFailedPermanently,
    JobQueue,
    JobState,
    Readiness,
    Switchboard,
    SystemCapabilities,
    WorkerPool,
    Workspaces,
    in_claim_order,
    register_handler,
    worker_pool,
)
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.ledger import Ledger, report_text
from sift.kernel.jobs.tuning import (
    BACKGROUND_PRIORITY,
    DEFAULT_PRIORITY,
    WAITED_ON_PRIORITY,
)
from sift.kernel.tests.jobs_helpers import (
    OTHER_WORKER,
    WORKER,
    Recorder,
    _a_job,
    drain,
    noop_handler,
)
from sift.testing.fixtures import FakeClock

pytestmark = pytest.mark.usefixtures("clean_handlers")


# --- live reconfigure: growing and shrinking the pool while it runs ---------------------------
#
# The pool resizes itself from the performance settings without a restart. What has to hold through
# a resize is exactly what holds everywhere else in this file: no job is run twice, and no job is
# dropped. Growing is the easy half: more workers claim more of the queue. Shrinking is where the
# care is: a worker being taken away is holding a job, and it must finish that job, not abandon it
# to be redone at the next boot. These drive a real pool against a real queue and watch the counts.


class Concurrency:
    """A handler that blocks until released, and remembers the most it ever ran together.

    Blocking is the whole trick: a worker that claims one of these is stuck on it until the test
    lets go, so `active` is exactly how many workers are busy right now and `peak` is the widest the
    pool ever got. That is how "the pool grew to three" or "the cap held it to one" is asserted
    without reaching inside the pool to count its tasks.
    """

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.release = asyncio.Event()

    async def handler(self, context: JobContext) -> None:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await self.release.wait()
        finally:
            self.active -= 1


async def wait_until(predicate: Callable[[], bool], *, give_up_after: float = 5.0) -> None:
    """Spin until a predicate holds, or fail. The reconfigure is asynchronous (workers spawn and
    claim on the event loop), so the test waits for the effect rather than assuming it is instant."""
    deadline = time.monotonic() + give_up_after
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the condition never became true")


def _swap_handler(job_type: str, handler: Handler) -> None:
    """Replace a registered handler in place, which `register_handler` refuses to do on purpose.

    A couple of the reconfigure tests run one blocking batch and then a second, non-blocking one of
    the same type to measure the pool's new width. That means swapping the handler mid-test, which
    is a thing production never does and the registry rightly rejects, so it is done here through
    the registry's own dict, the same way the download and search suites do."""
    worker_pool._HANDLERS[job_type] = handler


@pytest.mark.integration
async def test_growing_the_pool_puts_more_workers_on_the_queue(job_queue: JobQueue) -> None:
    """Reconcile up, and the extra workers pick up the jobs that were waiting."""
    work = Concurrency()
    register_handler("probe", work.handler, name="Test job")
    for index in range(3):
        await job_queue.enqueue("probe", {"asset_id": f"0{index}"})

    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    try:
        await wait_until(lambda: work.active == 1)  # one worker, one job, two waiting
        pool.reconcile(concurrency=3)
        await wait_until(lambda: work.active == 3)  # the two new workers took the waiting jobs
        assert pool.concurrency == 3
        work.release.set()
        await drain(job_queue)
    finally:
        work.release.set()
        await pool.stop()

    assert work.peak == 3


@pytest.mark.integration
@pytest.mark.regression
async def test_a_worker_shrunk_away_finishes_the_job_in_its_hand(job_queue: JobQueue) -> None:
    """The property that makes shrinking safe: drain, never abandon.

    Two workers each hold a job. The pool is cut to one underneath them. The worker being retired is
    mid-job, and the whole point is that it runs that job to completion rather than being cancelled
    and leaving it `running` for the next boot to redo. Both jobs must finish DONE. A retire that
    cancelled instead of stopping would turn this red while every other pool test stayed green.
    """
    work = Concurrency()
    register_handler("probe", work.handler, name="Test job")
    first = await job_queue.enqueue("probe", {"asset_id": "a"})
    second = await job_queue.enqueue("probe", {"asset_id": "b"})

    pool = WorkerPool(job_queue, concurrency=2, poll_interval=0.01)
    await pool.start()
    try:
        await wait_until(lambda: work.active == 2)  # both workers busy
        pool.reconcile(concurrency=1)  # one is now retiring, but still holds its job
        assert pool.concurrency == 1
        work.release.set()  # let both finish
        await drain(job_queue)
    finally:
        work.release.set()
        await pool.stop()

    for job_id in (first, second):
        job = await job_queue.get(job_id)
        assert job is not None
        assert job.state is JobState.DONE  # neither was dropped


@pytest.mark.integration
async def test_after_a_shrink_only_the_kept_workers_run(job_queue: JobQueue) -> None:
    """Once the retired workers have drained, the pool really is narrower.

    A shrink that only stopped new claims but left the old workers running would pass the drain test
    above and still be wrong: the machine would stay as loaded as before. So this releases the
    in-flight jobs, lets the retired workers exit, then measures a fresh burst: it must run one
    wide, not three.
    """
    work = Concurrency()
    register_handler("probe", work.handler, name="Test job")
    for index in range(3):
        await job_queue.enqueue("probe", {"asset_id": f"first-{index}"})

    pool = WorkerPool(job_queue, concurrency=3, poll_interval=0.01)
    await pool.start()
    try:
        await wait_until(lambda: work.active == 3)
        pool.reconcile(concurrency=1)
        work.release.set()
        await drain(job_queue)

        # A fresh burst, timed on its own and blocking so its width is observable. The retired
        # workers have exited; only the one kept worker is left, so only one of these three can run
        # at a time. A shrink that had not really taken would show three here.
        after = Concurrency()
        _swap_handler("probe", after.handler)
        for index in range(3):
            await job_queue.enqueue("probe", {"asset_id": f"second-{index}"})
        await wait_until(lambda: after.active == 1)
        await asyncio.sleep(0.1)  # give any second worker the chance to prove it exists
        assert after.active == 1
        after.release.set()
        await drain(job_queue)
    finally:
        work.release.set()
        after.release.set()
        await pool.stop()

    assert after.peak == 1


@pytest.mark.integration
async def test_a_lifted_cap_lets_more_of_a_type_run_at_the_same_time(job_queue: JobQueue) -> None:
    """Limits are live too. A type capped at one runs one; lift the cap and the next batch runs wide."""
    work = Concurrency()
    register_handler("transcode", work.handler, name="Test job")
    for index in range(3):
        await job_queue.enqueue("transcode", {"asset_id": f"capped-{index}"})

    pool = WorkerPool(job_queue, concurrency=3, poll_interval=0.01, limits={"transcode": 1})
    await pool.start()
    try:
        await wait_until(lambda: work.active == 1)
        # Held to one however many workers are free.
        await asyncio.sleep(0.1)
        assert work.active == 1
        work.release.set()
        await drain(job_queue)

        lifted = Concurrency()
        _swap_handler("transcode", lifted.handler)
        pool.reconcile(limits={})  # cap gone
        assert pool.limits == {}
        for index in range(3):
            await job_queue.enqueue("transcode", {"asset_id": f"free-{index}"})
        await wait_until(lambda: lifted.active == 3)  # all three together now
        lifted.release.set()
        await drain(job_queue)
    finally:
        work.release.set()
        lifted.release.set()
        await pool.stop()

    assert work.peak == 1  # the cap held
    assert lifted.peak == 3  # lifting it let all three run


@pytest.mark.integration
async def test_reconcile_refuses_a_pool_of_none_and_a_cap_of_zero(job_queue: JobQueue) -> None:
    """The same refusals the constructor makes, made live, and a refused cap changes nothing.

     A zero worker count would stop the library dead; a negative cap is not a smaller one. Both are
     rejected, and the limits check runs before anything is rebound, so a bad batch leaves the caps
     exactly as they were rather than half-applied. (A cap of zero is legitimate: it pauses a type,
    and is covered above.)
    """
    pool = WorkerPool(job_queue, concurrency=2, poll_interval=0.01, limits={"transcode": 1})
    await pool.start()
    try:
        with pytest.raises(ValueError, match="at least one worker"):
            pool.reconcile(concurrency=0)
        with pytest.raises(ValueError, match="negative"):
            pool.reconcile(limits={"transcode": -1})
        assert pool.limits == {"transcode": 1}  # untouched by the refused change
        assert pool.concurrency == 2
    finally:
        await pool.stop()


@pytest.mark.integration
async def test_reconciling_to_the_same_numbers_changes_nothing(job_queue: JobQueue) -> None:
    """Idempotence: the property that makes it safe to poll on a timer.

    Called with the count already in force, no worker is spawned and none is retired: the very same
    worker tasks are still there afterwards. Without this the supervisor would churn the whole pool
    every few seconds for no reason.
    """
    noop_handler("probe")
    pool = WorkerPool(job_queue, concurrency=2, poll_interval=0.01)
    await pool.start()
    try:
        before = {id(worker.task) for worker in pool._workers}
        pool.reconcile(concurrency=2)
        after = {id(worker.task) for worker in pool._workers}
        assert before == after
        assert pool.concurrency == 2
    finally:
        await pool.stop()


@pytest.mark.integration
async def test_reconcile_after_stop_does_nothing(job_queue: JobQueue) -> None:
    """A reconfigure racing a shutdown must not spawn a worker nothing will ever wait for."""
    noop_handler("probe")
    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    await pool.stop()

    pool.reconcile(concurrency=5)  # the settings changed as the app was going down

    assert pool._workers == []  # nothing was started behind stop()'s back


@pytest.mark.integration
async def test_a_flurry_of_reconciles_settles_on_the_last_one(job_queue: JobQueue) -> None:
    """Several resizes in a row leave the pool at the final number, with nothing leaked.

    Shrink then grow then shrink, back to back. The end state is what the last call asked for, the
    retired workers drain and are reaped rather than piling up, and a burst confirms the running
    width really is the final number.
    """
    work = Concurrency()
    register_handler("probe", work.handler, name="Test job")

    pool = WorkerPool(job_queue, concurrency=4, poll_interval=0.01)
    await pool.start()
    try:
        pool.reconcile(concurrency=6)
        pool.reconcile(concurrency=2)
        pool.reconcile(concurrency=3)
        assert pool.concurrency == 3
        assert len(pool._workers) == 3

        for index in range(3):
            await job_queue.enqueue("probe", {"asset_id": f"0{index}"})
        await wait_until(lambda: work.active == 3)
        work.release.set()
        await drain(job_queue)

        # The workers retired along the way have exited and been forgotten, not left to accumulate.
        pool.reconcile(concurrency=3)  # a reap happens at the top of any reconcile
        await wait_until(lambda: len(pool._retiring) == 0)
    finally:
        work.release.set()
        await pool.stop()

    assert work.peak == 3


@pytest.mark.integration
async def test_the_supervisor_converges_the_pool_on_what_the_reader_says(
    job_queue: JobQueue,
) -> None:
    """The whole point, wired the way it ships: a reader is polled and the pool follows it.

    The reader stands in for the performance settings. Move the number it returns and the pool grows
    or shrinks to match within a poll or two: no push, no restart. The first read is made to raise
    once, because a bad settings row must not take the supervisor down: it logs and the next tick
    still converges.
    """
    noop_handler("probe")

    target = {"concurrency": 2}
    exploded = {"once": True}

    async def reader() -> tuple[int, dict[str, int]]:
        if exploded["once"]:
            exploded["once"] = False
            raise RuntimeError("the settings row was unreadable this once")
        return target["concurrency"], {}

    pool = WorkerPool(
        job_queue, concurrency=2, poll_interval=0.01, read_config=reader, reconcile_interval=0.02
    )
    await pool.start()
    try:
        target["concurrency"] = 4
        await wait_until(lambda: pool.concurrency == 4)  # survived the raise, then grew
        target["concurrency"] = 1
        await wait_until(lambda: pool.concurrency == 1)  # and shrank
    finally:
        await pool.stop()


@pytest.mark.integration
async def test_a_shutdown_that_begins_mid_wait_ends_the_supervisor_without_reconciling(
    job_queue: JobQueue,
) -> None:
    """The supervisor spends nearly all its life asleep, so that is where a shutdown finds it.

    It waits on the STOP EVENT with the reconcile interval as a timeout, which means the wait ends
    two ways and they mean opposite things: the timeout says another interval has passed and it is
    time to read the settings, and the event says the pool is going away and nothing should be read
    at all. Reading them the same way would send one last query at a database that is closing.

    The interval here is long enough that the timeout cannot win, so the branch taken is the one
    being checked rather than whichever fired first. The reader raises if it is ever called, which
    is what makes "did not reconcile" an assertion rather than a hope.
    """
    noop_handler("probe")

    async def never_asked() -> tuple[int, dict[str, int]]:
        raise AssertionError("the settings were read while the pool was shutting down")

    pool = WorkerPool(
        job_queue, concurrency=1, poll_interval=0.01, read_config=never_asked, reconcile_interval=30
    )
    await pool.start()
    # Parked in the wait by now, and thirty seconds from leaving it any other way.
    await asyncio.sleep(0.05)

    await pool.stop()

    assert pool.concurrency == 1


# --- who goes first ------------------------------------------------------------------------------


@pytest.mark.integration
async def test_work_somebody_is_waiting_for_goes_before_a_library_wide_pass(
    job_queue: JobQueue,
) -> None:
    """The whole point of the priority band.

    With every job written at the default, the order would be by arrival alone, and a download
    pasted seconds after a face sweep started would wait in line behind every file that sweep had
    queued.
    """
    handler = noop_handler()
    for _ in range(5):
        await job_queue.enqueue(handler)
    waited_on = await job_queue.enqueue(handler, priority=WAITED_ON_PRIORITY)

    claimed = await job_queue.claim(WORKER)

    assert claimed is not None
    assert claimed.id == waited_on


@pytest.mark.integration
async def test_two_of_the_same_kind_still_run_oldest_first(job_queue: JobQueue) -> None:
    """The band decides between kinds; it does not reorder one kind among itself."""
    handler = noop_handler()
    first = await job_queue.enqueue(handler, priority=WAITED_ON_PRIORITY)
    await job_queue.enqueue(handler, priority=WAITED_ON_PRIORITY)

    claimed = await job_queue.claim(WORKER)

    assert claimed is not None
    assert claimed.id == first


# --- progress from inside a loop ------------------------------------------------------------------


async def test_reported_progress_is_written_rarely_and_the_last_step_always(
    job_queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once per file over a library is a write transaction per file on the one write connection.
    A loop reports through `report_progress`, which writes at most once an interval, and always
    for the finish, so the bar still arrives at the end."""
    noop_handler("scan")
    job_id = await job_queue.enqueue("scan", {"root_id": "r"})
    job = await job_queue.claim(WORKER)
    assert job is not None
    context = JobContext(job=job, worker_id=WORKER, queue=job_queue)
    written: list[float] = []
    real = job_queue.set_progress

    async def counting(job_id: str, worker_id: str, fraction: float) -> bool:
        written.append(fraction)
        return await real(job_id, worker_id, fraction)

    monkeypatch.setattr(job_queue, "set_progress", counting)

    for step in range(1, 101):
        await context.report_progress(step / 100)

    # The first step (nothing written before), then the finish; the ninety-eight between them fell
    # inside the interval and were not written.
    assert written == [0.01, 1.0]
    stored = await job_queue.get(job_id)
    assert stored is not None and stored.progress == 1.0


@pytest.mark.integration
async def test_a_handler_that_can_never_succeed_fails_once_and_says_why(
    job_queue: JobQueue,
) -> None:
    async def unplugged(context: JobContext) -> None:
        raise JobFailedPermanently("the library folder did not answer")

    register_handler("scan", unplugged, name="Test job")
    job_id = await job_queue.enqueue("scan", max_attempts=3)

    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.FAILED
    assert job.attempts == 1, "no retry, because no retry can fix it"
    assert job.error == "the library folder did not answer"


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
    monkeypatch.setattr(worker_pool, "_FAMILIES", {})
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
    monkeypatch.setattr(worker_pool, "_FAMILIES", {})

    async def missing(_context: JobContext) -> None:
        raise FileNotFoundError("clip.mp4")

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
    monkeypatch.setattr(worker_pool, "_FAMILIES", {})
    unread = (
        "1 folder stopped answering partway through, so nothing in it was marked missing or"
        " unreadable."
    )

    async def left_one(context: JobContext) -> None:
        await context.set_note(unread)

    async def gone(context: JobContext) -> None:
        await context.set_note("The folder stopped answering partway through the scan.")
        raise FileNotFoundError("clip.mp4")

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
    monkeypatch.setattr(worker_pool, "_FAMILIES", {})
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
    monkeypatch.setattr(worker_pool, "_FAMILIES", {})

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
    monkeypatch.setattr(worker_pool, "_FAMILIES", {})

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
    monkeypatch.setattr(worker_pool, "_FAMILIES", {})
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


# --- a collapse must not take urgency away, and a whole-library pass runs alone ------------------


@pytest.mark.integration
async def test_a_collapse_onto_a_waiting_job_raises_it_to_the_more_urgent_priority(
    job_queue: JobQueue,
) -> None:
    """The dedupe hands back the row that is already waiting; that row must not be the weaker ask.

    A whole-root walk has one payload, so a press and the watcher ask the same question in the same
    words, which is right, and which means a press can land on a row the machine queued at the
    machine's priority. Handed that row's id unchanged, the person would wait behind the whole queue
    and nothing would say so, because the id that came back really is the scan they asked for.
    """
    handler = noop_handler()
    machine = await job_queue.enqueue(handler, {"root_id": "01HQ"}, dedupe=True)
    pressed = await job_queue.enqueue(
        handler, {"root_id": "01HQ"}, dedupe=True, priority=WAITED_ON_PRIORITY
    )

    assert pressed == machine, "the two asked for the same work and did not collapse"
    page = await job_queue.list(job_type=handler)
    assert [row.priority for row in page.jobs] == [WAITED_ON_PRIORITY]


@pytest.mark.integration
async def test_a_collapse_never_takes_urgency_away(job_queue: JobQueue) -> None:
    """The known negative, and the direction matters: background work collapsing onto a row
    somebody is waiting on must not slow that row down."""
    handler = noop_handler()
    pressed = await job_queue.enqueue(
        handler, {"root_id": "01HQ"}, dedupe=True, priority=WAITED_ON_PRIORITY
    )
    later = await job_queue.enqueue(handler, {"root_id": "01HQ"}, dedupe=True)

    assert later == pressed
    page = await job_queue.list(job_type=handler)
    assert [row.priority for row in page.jobs] == [WAITED_ON_PRIORITY]


@pytest.mark.integration
async def test_only_one_of_a_declared_alone_type_runs_at_a_time(job_queue: JobQueue) -> None:
    """A whole-library pass reads everything and writes one answer, so a second is the first's work.

    `enqueue_when_settled` collapses onto a WAITING row and deliberately not onto a running one, so
    without `alone` the waiting row would be claimed within seconds, freeing the collapse and
    letting the next settle queue another: several copies of one pass holding the pool while file
    reads wait.

    The shape this leaves is the one a settle-driven pass wants: one running, one waiting behind
    it, and the run after covers whatever arrived during this one.
    """

    async def handler(_context: JobContext) -> None:
        return None

    register_handler("sweeping", handler, name="Sweeping", alone=True)
    first = await job_queue.enqueue("sweeping")
    await job_queue.enqueue("sweeping")

    claimed = await job_queue.claim(WORKER)
    assert claimed is not None and claimed.id == first
    assert await job_queue.claim(OTHER_WORKER) is None, "a second copy of the pass was started"

    # And the one waiting runs as soon as the first is done, rather than being lost.
    assert await job_queue.complete(first, WORKER)
    second = await job_queue.claim(OTHER_WORKER)
    assert second is not None and second.type == "sweeping"


@pytest.mark.integration
async def test_a_type_not_declared_alone_still_runs_beside_itself(job_queue: JobQueue) -> None:
    """The known positive for the rule above: without it, "nothing was claimed" is also what an
    empty queue looks like, and the cap would be indistinguishable from a broken claim."""
    handler = noop_handler()
    await job_queue.enqueue(handler)
    await job_queue.enqueue(handler)

    assert await job_queue.claim(WORKER) is not None
    assert await job_queue.claim(OTHER_WORKER) is not None


@pytest.mark.integration
async def test_where_a_waiting_job_is_in_the_line(temp_db: Database) -> None:
    """A waiting job's place in the line, and the order it is counted in.

    A tally by state is not a position: "580 jobs are waiting" is not "580 jobs are ahead of
    you", and a download waiting its turn could say it was waiting and nothing more. This is the
    answer, and it is counted in the order the queue is ACTUALLY claimed in: priority, then age,
    then id. Ordering it the way a listing happens to be ordered would produce a number that was
    wrong and plausible, which is worse than none.

    The claim order is asserted alongside rather than written down, so the position and the thing
    it is a position in cannot drift apart.
    """
    clock = FakeClock()
    queue = JobQueue(temp_db, clock=clock.now)
    await temp_db.initialize_schema()
    noop_handler()

    low = await queue.enqueue("probe", {"n": 1}, priority=200)
    clock.advance(1)
    urgent_older = await queue.enqueue("probe", {"n": 2}, priority=10)
    clock.advance(1)
    urgent_newer = await queue.enqueue("probe", {"n": 3}, priority=10)

    assert await queue.positions_of([low, urgent_older, urgent_newer]) == {
        urgent_older: 1,
        urgent_newer: 2,
        low: 3,
    }

    taken = await queue.claim(WORKER)
    assert taken is not None and taken.id == urgent_older, "the line is not the claim order"
    # The one that was taken is no longer IN the line, and everybody behind it moves up.
    assert await queue.positions_of([low, urgent_older, urgent_newer]) == {
        urgent_newer: 1,
        low: 2,
    }


@pytest.mark.integration
async def test_a_job_waiting_for_its_moment_is_not_in_the_line(temp_db: Database) -> None:
    """`run_after` is not a place in the queue.

    A settled batch waits a minute before it may be taken. Counting those rows would put somebody
    behind jobs that are not going to be claimed in front of them, which is a number that is wrong
    in the direction that matters: it says the wait is longer than it is. The condition is the
    claim's own, because any other condition is a second idea about what waiting means.
    """
    clock = FakeClock()
    queue = JobQueue(temp_db, clock=clock.now)
    await temp_db.initialize_schema()
    noop_handler()

    later = await queue.enqueue("probe", {"n": 1}, run_after=int(clock.now()) + 600)
    mine = await queue.enqueue("probe", {"n": 2})

    places = await queue.positions_of([later, mine])

    assert places == {mine: 1}, "a job waiting for its moment was counted as being in front"


@pytest.mark.integration
async def test_nothing_is_asked_for_an_empty_list(temp_db: Database) -> None:
    """A page with nothing waiting on it costs no statement at all. `IN ()` is a syntax error, so
    this is the guard as well as the saving."""
    queue = JobQueue(temp_db, clock=FakeClock().now)
    await temp_db.initialize_schema()

    assert await queue.positions_of([]) == {}


# --- the order a file's work is handed out in ---------------------------------------------------
#
# Declared at each handler and read by the fan-out, rather than the order of a tuple in one module
# that would hold only because the children of one probe share a priority and a second and ids go
# up. The three properties below are what the declaration is worth: the order is the DECLARED one,
# another job landing in the same second cannot break it, and a type that declared nothing keeps
# the place the list that named it gave it.


@pytest.mark.unit
def test_the_declared_order_is_what_is_handed_out_not_the_order_asked_for() -> None:
    """The point of declaring it: the caller's list order stops deciding anything."""
    register_handler("probe", noop, name="Probing")
    register_handler("thumbnail", noop, name="Thumbnail", follows="probe")
    register_handler("preview", noop, name="Preview", follows="thumbnail")
    register_handler("sprite", noop, name="Sprite", follows="preview")

    assert in_claim_order(["sprite", "preview", "thumbnail"]) == [
        "thumbnail",
        "preview",
        "sprite",
    ]


@pytest.mark.unit
def test_a_type_that_declared_nothing_goes_after_every_type_that_did() -> None:
    """Last, never first.

    Zero would put every undeclared type at the FRONT, so declaring three of a file's products
    would move the other five in front of them: a reordering nobody asked for, arriving as a side
    effect of writing down the order that was already right.
    """
    register_handler("probe", noop, name="Probing")
    register_handler("thumbnail", noop, name="Thumbnail", follows="probe")
    register_handler("face_scan", noop, name="Faces")

    assert in_claim_order(["face_scan", "thumbnail"]) == ["thumbnail", "face_scan"]


@pytest.mark.unit
def test_two_types_that_declared_nothing_keep_the_order_they_were_given_in() -> None:
    """A stable sort, so a list the composition root names is still a declaration of its own."""
    register_handler("face_scan", noop, name="Faces")
    register_handler("semantic_describe", noop, name="Meaning")
    register_handler("watermark_read", noop, name="Marks")

    named = ["face_scan", "semantic_describe", "watermark_read"]
    assert in_claim_order(named) == named


@pytest.mark.unit
def test_a_type_that_trails_goes_after_even_the_types_that_declared_nothing() -> None:
    """The repaired copy's case: last of all, not last of its own feature's chain.

    Following the last picture would put it before every face pass and description, because those
    are other features' and declare nothing, and an undeclared type sorts after every declared one.
    """
    register_handler("probe", noop, name="Probing")
    register_handler("thumbnail", noop, name="Thumbnail", follows="probe")
    register_handler("remux", noop, name="Repair", trails=True)
    register_handler("face_scan", noop, name="Faces")
    register_handler("semantic_describe", noop, name="Meaning")

    assert in_claim_order(["remux", "face_scan", "thumbnail", "semantic_describe"]) == [
        "thumbnail",
        "face_scan",
        "semantic_describe",
        "remux",
    ]


@pytest.mark.unit
def test_a_type_cannot_both_trail_and_follow_one_in_particular() -> None:
    with pytest.raises(ValueError, match="trails everything"):
        register_handler("remux", noop, name="Repair", follows="sprite", trails=True)


@pytest.mark.unit
def test_a_circle_of_follows_is_refused_rather_than_walked() -> None:
    """Two features that each declared the other. The symptom would otherwise be a hang."""
    register_handler("one", noop, name="One", follows="two")
    register_handler("two", noop, name="Two", follows="one")

    with pytest.raises(ValueError, match="run in a circle"):
        in_claim_order(["one", "two"])


@pytest.mark.integration
async def test_a_probes_children_are_claimed_in_the_declared_order_inside_one_second(
    job_queue: JobQueue,
) -> None:
    """The property the declaration exists for, end to end and against the real claim.

    Everything is enqueued inside one second at one priority, the case where arrival order alone
    would look right, AND a job of another kind lands in the middle of them, so this fails
    for a fan-out that hands its children out in the order its own list happens to be in.
    """
    register_handler("probe", noop, name="Probing")
    register_handler("thumbnail", noop, name="Thumbnail", follows="probe")
    register_handler("preview", noop, name="Preview", follows="thumbnail")
    register_handler("sprite", noop, name="Sprite", follows="preview")
    register_handler("download", noop, name="Downloading")

    asked = ["sprite", "preview", "thumbnail"]
    for index, job_type in enumerate(in_claim_order(asked)):
        await job_queue.enqueue(job_type)
        if index == 1:
            await job_queue.enqueue("download")

    order: list[str] = []
    while (claimed := await job_queue.claim(f"worker-{len(order)}")) is not None:
        order.append(claimed.type)

    assert order[:2] == ["thumbnail", "preview"]
    assert order.index("sprite") > order.index("preview")


# --- one urgency per kind of work ---------------------------------------------------------------


@pytest.mark.integration
async def test_a_caller_cannot_ask_for_a_pass_more_urgently_than_it_declared(
    job_queue: JobQueue,
) -> None:
    """The duplicate sweep's button and its settle ask at one urgency: at two, a pass that may only
    have one running would have two rows and sweep the library twice."""
    register_handler("dedup_scan", noop, name="Duplicates", urgency=BACKGROUND_PRIORITY)

    job_id = await job_queue.enqueue("dedup_scan", priority=WAITED_ON_PRIORITY)

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.priority == BACKGROUND_PRIORITY


@pytest.mark.integration
async def test_a_declared_urgency_does_not_make_a_lazier_caller_more_urgent(
    job_queue: JobQueue,
) -> None:
    """A ceiling on urgency, never a default: a caller may always ask for something less urgent."""
    register_handler("dedup_scan", noop, name="Duplicates", urgency=DEFAULT_PRIORITY)

    job_id = await job_queue.enqueue("dedup_scan", priority=BACKGROUND_PRIORITY)

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.priority == BACKGROUND_PRIORITY


@pytest.mark.integration
async def test_a_type_that_declared_no_urgency_takes_the_priority_it_was_asked_for(
    job_queue: JobQueue,
) -> None:
    """The ordinary case, which the clamp must leave exactly as it was."""
    register_handler("probe", noop, name="Probing")

    job_id = await job_queue.enqueue("probe", priority=WAITED_ON_PRIORITY)

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.priority == WAITED_ON_PRIORITY


# --- the queue asks whether a family can run here ------------------------------------------------
#
# The board knows that recognition needs its weights, and the pool asks it rather than claiming
# those rows anyway, where every one of them would reach its handler to find out what the board
# already knew: a park per file, kept for thirty days, on a library with a hundred thousand of
# them.


@pytest.mark.integration
async def test_the_queue_does_not_claim_work_its_family_cannot_do_here(temp_db: Database) -> None:
    """It waits where it is, exactly as a type at its cap does, rather than being handed out."""
    await temp_db.initialize_schema()
    board = Switchboard()
    board.declare_ready(Family.IDENTIFY, _not_ready("The models are not installed."))
    queue = JobQueue(temp_db, switchboard=board)
    register_handler("face_scan", noop, name="Faces", family=Family.IDENTIFY)
    register_handler("thumbnail", noop, name="Thumbnail", family=Family.GENERATE)
    await queue.enqueue("face_scan")
    picture = await queue.enqueue("thumbnail")

    claimed = await queue.claim(WORKER)

    assert claimed is not None
    assert claimed.id == picture
    assert await queue.claim(WORKER) is None


@pytest.mark.integration
async def test_work_that_only_coordinates_is_claimed_whatever_the_family_can_do(
    temp_db: Database,
) -> None:
    """An Identify run asked for the MEANING of a library, on a machine with no face weights, is
    work that can be done, so the Build's own types are not held back over a capability only one
    of their products wants."""
    await temp_db.initialize_schema()
    board = Switchboard()
    board.declare_ready(Family.IDENTIFY, _not_ready("The models are not installed."))
    queue = JobQueue(temp_db, switchboard=board)
    register_handler(
        "identify", noop, name="Going over the library", family=Family.IDENTIFY, needs_ready=False
    )
    run = await queue.enqueue("identify")

    claimed = await queue.claim(WORKER)

    assert claimed is not None
    assert claimed.id == run


@pytest.mark.integration
async def test_a_family_nobody_declared_an_answer_for_is_ready(temp_db: Database) -> None:
    """The board's own rule, and what keeps this from becoming a list every feature must join
    before its work will run at all."""
    await temp_db.initialize_schema()
    queue = JobQueue(temp_db, switchboard=Switchboard())
    register_handler("face_scan", noop, name="Faces", family=Family.IDENTIFY)
    job_id = await queue.enqueue("face_scan")

    claimed = await queue.claim(WORKER)

    assert claimed is not None
    assert claimed.id == job_id


@pytest.mark.integration
async def test_a_readiness_that_cannot_be_read_does_not_hold_work_back(temp_db: Database) -> None:
    """ "Not known" is the honest answer to a question that did not come back, and holding work over
    one would be inventing a fault the machine does not have."""
    await temp_db.initialize_schema()

    async def explodes() -> Readiness:
        raise RuntimeError("the feature could not be asked")

    board = Switchboard()
    board.declare_ready(Family.IDENTIFY, explodes)
    queue = JobQueue(temp_db, switchboard=board)
    register_handler("face_scan", noop, name="Faces", family=Family.IDENTIFY)
    job_id = await queue.enqueue("face_scan")

    claimed = await queue.claim(WORKER)

    assert claimed is not None
    assert claimed.id == job_id


async def noop(context: JobContext) -> None:
    """A handler for a test that is about the declaration rather than about the work."""
    return None


def _not_ready(problem: str) -> Callable[[], Awaitable[Readiness]]:
    async def ask() -> Readiness:
        return Readiness(ready=False, problem=problem)

    return ask


def test_asking_how_much_a_job_kept_leaves_no_folder_behind(tmp_path: Path) -> None:
    """`size_of` is a READ. A job that never wrote anything has no folder, answers None, and still
    has no folder afterwards: `of` makes one, and a size read that went through it would leave an
    empty folder for every paused download asked about after a restart. Asserted on the listing of
    the root, which is what would show one."""
    root = tmp_path / "workspaces"
    root.mkdir()
    workspaces = Workspaces(root)

    assert workspaces.size_of("01HX00000000000000000JOB01") is None
    assert list(root.iterdir()) == [], "a folder was made by asking about it"

    (workspaces.of("01HX00000000000000000JOB02") / "part").write_bytes(b"12345")
    assert workspaces.size_of("01HX00000000000000000JOB02") == 5
    assert [one.name for one in root.iterdir()] == ["01HX00000000000000000JOB02"]


@pytest.mark.unit
def test_asking_how_much_a_name_off_the_root_kept_answers_nothing(tmp_path: Path) -> None:
    """The size read is confined like every other door into the root: a name that would leave it
    has kept nothing, and asking makes nothing."""
    root = tmp_path / "workspaces"
    root.mkdir()
    workspaces = Workspaces(root)

    assert workspaces.size_of("../elsewhere") is None
    assert list(tmp_path.iterdir()) == [root]


@pytest.mark.unit
def test_a_file_that_goes_while_its_job_is_measured_is_not_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A download finishing a piece while a paused job's size is read: the piece listed and then
    gone is simply not in the figure, rather than an error on the screen that asked."""
    import os

    workspaces = Workspaces(tmp_path / "workspaces")
    folder = workspaces.of("01HX00000000000000000JOB03")
    (folder / "kept").write_bytes(b"123")
    (folder / "going").write_bytes(b"4567")
    real = os.lstat

    def lstat(path: str) -> os.stat_result:
        if str(path).endswith("going"):
            raise FileNotFoundError(path)
        return real(path)

    monkeypatch.setattr(os, "lstat", lstat)

    assert workspaces.size_of("01HX00000000000000000JOB03") == 3


@pytest.mark.unit
def test_sweeping_a_job_that_kept_nothing_is_quiet(tmp_path: Path) -> None:
    workspaces = Workspaces(tmp_path / "workspaces")

    workspaces.sweep("01HX00000000000000000JOB04")

    assert not (tmp_path / "workspaces").exists()


@pytest.mark.unit
def test_a_boot_sweep_with_every_workspace_still_wanted_removes_nothing(tmp_path: Path) -> None:
    workspaces = Workspaces(tmp_path / "workspaces")
    workspaces.of("01HX00000000000000000JOB05")

    assert workspaces.sweep_all_but(["01HX00000000000000000JOB05"]) == 0
    assert (workspaces.root / "01HX00000000000000000JOB05").is_dir()


# --- the pool's smaller doors -------------------------------------------------------------------


async def test_a_context_with_nowhere_to_keep_work_refuses_a_workspace(
    job_queue: JobQueue, content_store: ContentStore, library_store: LibraryStore
) -> None:
    """A pool built without workspaces hands out none: an error rather than a temporary folder
    nobody would ever sweep."""
    bare = JobContext(job=_a_job(), worker_id=WORKER, queue=job_queue)
    without = JobContext(
        job=_a_job(),
        worker_id=WORKER,
        queue=job_queue,
        capabilities=SystemCapabilities(content=content_store, library=library_store),
    )

    for context in (bare, without):
        with pytest.raises(RuntimeError):
            _ = context.workspace


@pytest.mark.integration
async def test_a_handler_whose_claim_is_gone_is_told_to_stop_as_a_cancel(
    job_queue: JobQueue,
) -> None:
    from sift.kernel.jobs import JobCanceled
    from sift.kernel.jobs.queue import STOP_TO_CANCEL

    noop_handler("scan")
    await job_queue.enqueue("scan")
    job = await job_queue.claim(WORKER)
    assert job is not None
    context = JobContext(job=job, worker_id=WORKER, queue=job_queue)
    await job_queue.cancel(job.id)

    with pytest.raises(JobCanceled):
        await context.raise_if_canceled()

    assert context.stopping() == STOP_TO_CANCEL


@pytest.mark.unit
def test_a_type_cannot_follow_itself() -> None:
    with pytest.raises(ValueError, match="cannot follow itself"):
        register_handler("sprite", noop, name="Sprites", follows="sprite")


@pytest.mark.unit
def test_one_file_of_the_work_is_called_by_its_declared_words_or_else_its_name() -> None:
    from sift.kernel.jobs.worker_pool import counted_as

    register_handler("thumbnail", noop, name="Generating thumbnail", counts="thumbnails")
    register_handler("remux", noop, name="Repairing")

    assert counted_as("thumbnail") == "thumbnails"
    assert counted_as("remux") == "Repairing"
    assert counted_as("never_registered") == "never_registered"


@pytest.mark.unit
def test_the_carriers_and_the_order_declared_are_read_back_as_declared() -> None:
    from sift.kernel.jobs.worker_pool import registered_follows, registered_product_carriers

    register_handler("identify_file", noop, name="Identifying", carries_products=True)
    register_handler("sprite", noop, name="Sprites", follows="thumbnail")

    assert "identify_file" in registered_product_carriers()
    assert "sprite" not in registered_product_carriers()
    assert registered_follows()["sprite"] == "thumbnail"


@pytest.mark.unit
def test_a_declared_failure_holds_its_job_and_an_ordinary_one_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A graphics card that stopped answering is the machine, not the file: a failure declared a
    hold is waited out, `JobHeld` says its own wait, and anything else is an ordinary failure."""
    from sift.kernel.jobs import JobHeld
    from sift.kernel.jobs.worker_pool import held_for, hold_on

    class CardGone(RuntimeError):
        pass

    monkeypatch.setattr(worker_pool, "_HOLDS", {})
    with pytest.raises(ValueError, match="positive number of seconds"):
        hold_on(CardGone, seconds=0)
    hold_on(CardGone, seconds=90)

    assert held_for(CardGone("no answer")) == 90
    assert held_for(JobHeld("busy", retry_in=15)) == 15
    assert held_for(RuntimeError("a damaged file")) is None


async def test_the_pool_says_where_its_jobs_keep_their_work(
    job_queue: JobQueue, content_store: ContentStore, library_store: LibraryStore, tmp_path: Path
) -> None:
    workspaces = Workspaces(tmp_path / "workspaces")
    kept = WorkerPool(
        job_queue,
        concurrency=1,
        capabilities=SystemCapabilities(
            content=content_store, library=library_store, workspaces=workspaces
        ),
    )

    assert kept.workspaces is workspaces
    assert WorkerPool(job_queue, concurrency=1).workspaces is None


async def test_the_boot_sweep_leaves_an_empty_root_alone_and_survives_one_it_cannot_read(
    job_queue: JobQueue,
    content_store: ContentStore,
    library_store: LibraryStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pool has to start whatever the state of a cache directory: a root with nothing in it
    asks the queue nothing, and one that cannot be listed is a line in the log."""
    workspaces = Workspaces(tmp_path / "workspaces")
    workspaces.root.mkdir()
    pool = WorkerPool(
        job_queue,
        concurrency=1,
        capabilities=SystemCapabilities(
            content=content_store, library=library_store, workspaces=workspaces
        ),
    )
    asked: list[object] = []

    async def unfinished_among(ids: object) -> list[str]:
        asked.append(ids)
        return []

    monkeypatch.setattr(job_queue, "unfinished_among", unfinished_among)
    await pool._sweep_stale_workspaces()
    assert asked == []

    def refuse(_root: Path) -> list[str]:
        raise PermissionError("the cache folder is not ours to read")

    monkeypatch.setattr(worker_pool, "_directories_under", refuse)
    await pool._sweep_stale_workspaces()
    assert workspaces.root.is_dir()


@pytest.mark.integration
async def test_a_handler_that_stopped_because_it_was_cancelled_leaves_the_row_as_it_is(
    job_queue: JobQueue,
) -> None:
    """It asked, found the claim gone, and stopped: the row already says canceled, and nothing the
    pool writes may say anything else."""
    from sift.kernel.jobs import JobCanceled

    async def cancelled_meanwhile(context: JobContext) -> None:
        await job_queue.cancel(context.job.id)
        raise JobCanceled("no longer this worker's")

    register_handler("scan", cancelled_meanwhile, name="Test job")
    job_id = await job_queue.enqueue("scan", max_attempts=3)

    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.CANCELED
    assert job.error is None


@pytest.mark.integration
async def test_a_handler_the_machine_cannot_serve_just_now_is_held_with_its_attempt_back(
    job_queue: JobQueue,
) -> None:
    """Waiting on the machine, not failing: back in the line for the wait it named, the attempt
    handed back, and the reason on the row."""
    from sift.kernel.jobs import JobHeld

    async def card_gone(context: JobContext) -> None:
        raise JobHeld("The graphics card stopped answering.", retry_in=3600)

    register_handler("face_scan", card_gone, name="Test job")
    job_id = await job_queue.enqueue("face_scan", max_attempts=3)

    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            job = await job_queue.get(job_id)
            assert job is not None
            if job.error is not None:
                break
            await asyncio.sleep(0.01)
    finally:
        await pool.stop()

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.QUEUED
    assert job.attempts == 0
    assert job.error == "The graphics card stopped answering."
    assert job.run_after is not None and job.run_after > int(time.time()) + 3000


# --- a job that has the queue to itself, and what the registry says of a type --------------------


@pytest.mark.integration
async def test_an_exclusive_job_waiting_is_the_only_thing_claimed_and_runs_alone(
    job_queue: JobQueue,
) -> None:
    """The device's benchmark measures the machine, not other work: while one waits it is the
    next claimed whatever is older, and while it runs nothing else is claimed at all."""
    probe = noop_handler()

    async def measure(_context: JobContext) -> None:
        return None

    register_handler("measuring", measure, name="Measuring", exclusive=True)
    older = await job_queue.enqueue(probe)
    alone = await job_queue.enqueue("measuring")

    claimed = await job_queue.claim(WORKER)
    assert claimed is not None and claimed.id == alone, "the older job went first"
    assert await job_queue.claim(OTHER_WORKER) is None, "work was claimed beside it"

    assert await job_queue.complete(alone, WORKER)
    after = await job_queue.claim(OTHER_WORKER)
    assert after is not None and after.id == older


@pytest.mark.integration
async def test_an_exclusive_job_its_switch_holds_back_holds_everything_back_too(
    job_queue: JobQueue,
) -> None:
    """Waiting and not allowed to start, it still has the queue to itself: running the work
    behind it would be the other work it must not be measured beside."""

    async def measure(_context: JobContext) -> None:
        return None

    register_handler("measuring", measure, name="Measuring", exclusive=True)
    await job_queue.enqueue(noop_handler())
    await job_queue.enqueue("measuring")

    async def nothing_ready() -> set[str]:
        return {"measuring"}

    job_queue._not_ready_types = nothing_ready  # type: ignore[method-assign]

    assert await job_queue.claim(WORKER) is None


def test_what_the_registry_says_of_a_type_follows_its_latest_registration() -> None:
    """Upkeep left off Activity and a type with the queue to itself are each one set, so a type
    registered again without the word is taken out of it, not left in from before."""

    async def nothing(_context: JobContext) -> None:
        return None

    register_handler("pruning", nothing, name="Pruning", unlisted=True, exclusive=True)
    assert "pruning" in worker_pool.unlisted_job_types()
    assert "pruning" in worker_pool.exclusive_job_types()

    del worker_pool._HANDLERS["pruning"]
    register_handler("pruning", nothing, name="Pruning")
    assert "pruning" not in worker_pool.unlisted_job_types()
    assert "pruning" not in worker_pool.exclusive_job_types()


def test_a_job_says_only_the_files_it_brought_in_and_never_a_negative() -> None:
    """The count on a scan's History line: new files only, so a caller's arithmetic that went
    below nothing adds nothing."""
    context = JobContext(job=_a_job("scan"), worker_id=WORKER, queue=None)  # type: ignore[arg-type]
    context.arrived(3)
    context.arrived(-2)
    context.arrived(1)
    assert context.files_arrived == 4


@pytest.mark.integration
async def test_a_job_whose_family_was_cleared_while_it_ran_is_nobodys_press(
    job_queue: JobQueue,
) -> None:
    """A press reaches a job through the top of its tree; with the tree gone there is no press
    to name, and the job's History line is Sift's rather than a guess."""
    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    orphan = dataclasses.replace(_a_job("thumbnail"), id=new_id(), timing="press")

    assert await pool._pressed_by(orphan) is None
    assert await pool._pressed_by(dataclasses.replace(orphan, requested_by="u1")) == "u1"


async def test_work_arriving_wakes_the_supervisor_immediately(job_queue: JobQueue) -> None:
    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    waited_on = pool._waking.arrived
    pool.work_arrived()
    assert waited_on.is_set(), "every idle worker claims now"
    assert pool._waking.arrived is not waited_on, "the next wait is a fresh event"
    assert not pool._waking.reconfigure.is_set()
    pool._waking.wake()
    assert pool._waking.reconfigure.is_set(), "a press for turbo or eco is taken within a moment"
