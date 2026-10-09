# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a job's row lands: done, failed, parked, handed back, its note, its family's progress."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from sift.kernel.db import Database
from sift.kernel.jobs import (
    MAX_ERROR_CHARACTERS,
    JobQueue,
    JobState,
    folded_state,
    recover,
    sweep,
)
from sift.kernel.tests.jobs_helpers import (
    OTHER_WORKER,
    WORKER,
    noop_handler,
)
from sift.testing.fixtures import FakeClock

pytestmark = pytest.mark.usefixtures("clean_handlers")


# --- the state machine ------------------------------------------------------------------


@pytest.mark.integration
async def test_a_completed_job_is_done_and_finished(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)

    assert await job_queue.complete(job_id, WORKER) is True

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.DONE
    assert job.progress == 1.0
    assert job.claimed_by is None


@pytest.mark.integration
async def test_a_failure_with_attempts_left_goes_back_in_the_queue(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler(), max_attempts=3)
    await job_queue.claim(WORKER)

    assert await job_queue.fail(job_id, WORKER, "it broke") is JobState.QUEUED

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.QUEUED
    assert job.attempts == 1
    assert job.error == "it broke"


@pytest.mark.integration
async def test_a_failure_that_waits_lands_queued_for_later_and_a_last_one_does_not(
    job_queue: JobQueue,
) -> None:
    kind = noop_handler()
    job_id = await job_queue.enqueue(kind, max_attempts=2)
    await job_queue.claim(WORKER)
    assert await job_queue.fail(job_id, WORKER, "dropped", retry_in=30) is JobState.QUEUED
    job = await job_queue.get(job_id)
    assert job is not None and job.run_after == job.updated_at + 30
    plain = await job_queue.enqueue(kind, max_attempts=1)
    await job_queue.claim(WORKER)
    assert await job_queue.fail(plain, WORKER, "gone", retry_in=30) is JobState.FAILED
    last = await job_queue.get(plain)
    assert last is not None and last.run_after is None, "a failed job waits for nothing"


@pytest.mark.integration
async def test_a_job_that_runs_out_of_attempts_fails(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler(), max_attempts=2)

    await job_queue.claim(WORKER)
    assert await job_queue.fail(job_id, WORKER, "once") is JobState.QUEUED
    await job_queue.claim(WORKER)
    assert await job_queue.fail(job_id, WORKER, "twice") is JobState.FAILED

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.attempts == 2
    assert job.state is JobState.FAILED


@pytest.mark.integration
async def test_a_failure_no_retry_can_fix_does_not_get_retried(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler(), max_attempts=5)
    await job_queue.claim(WORKER)

    assert await job_queue.fail(job_id, WORKER, "gone", permanent=True) is JobState.FAILED

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.FAILED
    assert job.attempts == 1


@pytest.mark.integration
async def test_the_claim_clears_the_previous_run_s_error(job_queue: JobQueue) -> None:
    """A retrying job that still shows last time's error looks permanently broken."""
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)
    await job_queue.fail(job_id, WORKER, "the first time")

    claimed = await job_queue.claim(WORKER)

    assert claimed is not None
    assert claimed.error is None


@pytest.mark.integration
async def test_a_job_can_be_cancelled_while_queued(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler())

    assert await job_queue.cancel(job_id) == [job_id]

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.CANCELED
    assert await job_queue.claim(WORKER) is None


@pytest.mark.integration
async def test_a_job_can_be_cancelled_while_running(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)

    assert await job_queue.cancel(job_id) == [job_id]

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.CANCELED


@pytest.mark.integration
async def test_a_finished_job_cannot_be_cancelled(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)
    await job_queue.complete(job_id, WORKER)

    assert await job_queue.cancel(job_id) == []

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.DONE


@pytest.mark.integration
async def test_cancelling_a_job_that_does_not_exist_does_nothing(job_queue: JobQueue) -> None:
    assert await job_queue.cancel("01HQNOSUCHJOB0000000000000") == []


@pytest.mark.integration
async def test_a_failed_job_can_be_retried_with_a_fresh_set_of_attempts(
    job_queue: JobQueue,
) -> None:
    job_id = await job_queue.enqueue(noop_handler(), max_attempts=1)
    await job_queue.claim(WORKER)
    await job_queue.fail(job_id, WORKER, "it broke")

    assert await job_queue.retry(job_id) is True

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.QUEUED
    assert job.attempts == 0
    assert job.error is None
    assert await job_queue.claim(WORKER) is not None


@pytest.mark.integration
async def test_a_cancelled_job_can_be_retried(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.cancel(job_id)

    assert await job_queue.retry(job_id) is True

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.QUEUED


@pytest.mark.integration
async def test_a_running_job_cannot_be_retried_out_from_under_its_worker(
    job_queue: JobQueue,
) -> None:
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)

    assert await job_queue.retry(job_id) is False

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.RUNNING


@pytest.mark.integration
async def test_everything_that_failed_can_be_put_back_in_one_go(job_queue: JobQueue) -> None:
    """Failures arrive in batches, so the way out of them is one press rather than forty."""
    handler = noop_handler()
    failed = []
    for _ in range(3):
        job_id = await job_queue.enqueue(handler, max_attempts=1)
        await job_queue.claim(WORKER)
        await job_queue.fail(job_id, WORKER, "it broke")
        failed.append(job_id)

    assert await job_queue.retry_failed() == 3

    for job_id in failed:
        job = await job_queue.get(job_id)
        assert job is not None
        assert job.state is JobState.QUEUED
        assert job.attempts == 0
        assert job.error is None


@pytest.mark.integration
async def test_putting_the_failures_back_leaves_everything_else_alone(job_queue: JobQueue) -> None:
    """Cancelled is not failed. Somebody stopped that job deliberately, and a sweep that undid the
    decision would be a button that does something nobody asked for, and a running job taken out
    from under its worker is worse than either."""
    handler = noop_handler()
    stopped = await job_queue.enqueue(handler)
    await job_queue.cancel(stopped)

    running = await job_queue.enqueue(handler)
    await job_queue.claim(WORKER)

    broken = await job_queue.enqueue(handler, max_attempts=1)
    await job_queue.claim(WORKER)
    await job_queue.fail(broken, WORKER, "it broke")

    assert await job_queue.retry_failed() == 1

    async def state_of(job_id: str) -> JobState:
        job = await job_queue.get(job_id)
        assert job is not None
        return job.state

    assert await state_of(stopped) is JobState.CANCELED
    assert await state_of(running) is JobState.RUNNING
    assert await state_of(broken) is JobState.QUEUED


@pytest.mark.integration
async def test_everything_that_was_stopped_can_be_started_again(job_queue: JobQueue) -> None:
    """The other half of stopping everything.

    A stop leaves the rows in the table with their payloads intact, so the work can simply be
    offered again. Without this the only route back to the same work would be to scan the folders:
    walking the whole library to rediscover files it already knew about.
    """
    handler = noop_handler()
    stopped = [await job_queue.enqueue(handler) for _ in range(3)]
    assert await job_queue.cancel_everything() == 3

    assert await job_queue.retry_canceled() == 3

    for job_id in stopped:
        job = await job_queue.get(job_id)
        assert job is not None
        assert job.state is JobState.QUEUED
        assert job.attempts == 0
    assert await job_queue.claim(WORKER) is not None


@pytest.mark.integration
async def test_starting_what_was_stopped_leaves_the_failures_where_they_are(
    job_queue: JobQueue,
) -> None:
    """Stopped and failed wear the same "unfinished" label and are not the same thing.

     One is a decision being taken back; the other is work that broke and will likely break again,
     more expensively. `retry_failed` is the button for the second, and the two must not become one:
    a sweep that took both would make every failure a cost of pressing start.
    """
    handler = noop_handler()
    stopped = await job_queue.enqueue(handler)
    await job_queue.cancel(stopped)

    broken = await job_queue.enqueue(handler, max_attempts=1)
    await job_queue.claim(WORKER)
    await job_queue.fail(broken, WORKER, "it broke")

    assert await job_queue.retry_canceled() == 1

    async def state_of(job_id: str) -> JobState:
        job = await job_queue.get(job_id)
        assert job is not None
        return job.state

    assert await state_of(stopped) is JobState.QUEUED
    assert await state_of(broken) is JobState.FAILED


@pytest.mark.integration
async def test_starting_what_was_stopped_when_nothing_was_is_not_an_error(
    job_queue: JobQueue,
) -> None:
    """Nothing is stopped, which is what the caller wanted. Zero, not a refusal."""
    assert await job_queue.retry_canceled() == 0


@pytest.mark.integration
async def test_the_run_is_not_pinned_by_work_that_is_waiting_for_a_time(
    temp_db: Database,
) -> None:
    """A job due tomorrow is not what the current batch of work started with.

    The obvious reading of "this run" (everything since the oldest unfinished job) is wrong here
    and the fault is silent. The daily backup and the quarantine prune sit queued with `run_after`
    a day out, so within a day they are the
    oldest unfinished rows in the table and every bar on the dashboard would quietly start measuring
    "the last two days" instead of the import in front of somebody.

    Built with the holding turned OFF, because what this asks about is the STATEMENT and it asks
    twice in the same instant. The shipped queue holds its answer for a few seconds (see
    `work_summary` for what that costs when it does not), so a second read a millisecond later is
    the first read again, and this would be asserting on a cache rather than on a query. Said with
    a number rather than by reaching into the object, so the two halves cannot come apart.
    """
    await temp_db.initialize_schema()
    queue = JobQueue(temp_db, summary_fresh_for=0)
    handler = noop_handler()
    tomorrow = await queue.enqueue(handler, run_after=int(time.time()) + 86_400)
    assert tomorrow is not None

    summary = await queue.work_summary()

    assert summary.since is None, "a job waiting for a time started a run on its own"

    now_job = await queue.enqueue(handler)
    assert now_job is not None
    started = await queue.work_summary()
    assert started.since is not None


@pytest.mark.integration
async def test_the_whole_table_is_not_scanned_once_per_read(temp_db: Database) -> None:
    """The summary is held for a few seconds, and this is the reason the screen is affordable.

    The Jobs screen re-reads whenever the queue says anything, so during an import, scanned once
    per request, it would scan once per finished job and slow the import to a crawl. A screen that
    reports on work must not be the reason the work is slow.

    Asserted through the CLOCK rather than by counting queries, because what is promised is a
    freshness window and not a call count: an answer inside the window is the one already taken,
    and the moment the window passes the table is read again. Counting statements would pass just
    as well against a cache that never expired.
    """
    clock = FakeClock(1000)
    await temp_db.initialize_schema()
    queue = JobQueue(temp_db, clock=clock.now, summary_fresh_for=5)
    handler = noop_handler()

    await queue.enqueue(handler)
    first = await queue.work_summary()
    assert first.states[handler]["queued"] == 1

    # A second job, and a read from inside the window: the answer is the one already taken.
    await queue.enqueue(handler)
    held = await queue.work_summary()
    assert held.states[handler]["queued"] == 1, "the summary was recomputed on every read"

    # And it is a window, not a freeze. This is the half a cache that never expired would fail.
    clock.advance(5)
    fresh = await queue.work_summary()
    assert fresh.states[handler]["queued"] == 2, "the summary never went stale, so it never updates"


@pytest.mark.integration
async def test_the_run_counts_this_batch_and_not_the_whole_table(temp_db: Database) -> None:
    """The bars describe the import being watched, not every row the queue still remembers.

    A stage with nothing left would sit at a full bar for ever ("20,000 of 20,000"), which is a true
    statement about the retention window and says nothing about the work in front of somebody.

    One queue and one clock throughout, and the clock is what makes this a real test: `created_at`
    is whole SECONDS, so a batch written in the same second as the one before it cannot be told
    from it. Moved by hand here, the two batches are hours apart, which is what they are on a
    machine somebody is watching.
    """
    await temp_db.initialize_schema()
    clock = FakeClock(1000)
    queue = JobQueue(temp_db, clock=clock.now)
    handler = noop_handler()

    # An older batch, finished. The queue is empty after it, so that run ENDED, which is the
    # boundary the next one starts from.
    for _ in range(2):
        old_job = await queue.enqueue(handler)
        await queue.claim(WORKER)
        await queue.complete(old_job, WORKER)

    quiet = await queue.work_summary()
    assert quiet.since is None
    assert quiet.run[handler].done == 0, "a finished batch is still being counted as a run"
    # The whole table is reported beside it: that is what the state tallies are made of.
    assert quiet.states[handler]["done"] == 2

    clock.advance(3600)
    fresh = await queue.enqueue(handler)
    await queue.claim(WORKER)
    await queue.complete(fresh, WORKER)
    await queue.enqueue(handler)

    summary = await queue.work_summary()

    assert summary.run[handler].done == 1
    assert summary.run[handler].outstanding == 1
    assert summary.states[handler]["done"] == 3


@pytest.mark.integration
async def test_clearing_the_stopped_ones_takes_a_whole_tree(job_queue: JobQueue) -> None:
    """One press, and the tree goes, which needs the loop, because a tree comes apart leaf first.

    A row is only removed when nothing hangs off it: `parent_id` cascades, so deleting a parent
    would take its children with it including any that are running. So a single statement removes
    only the deepest leaves and leaves the parents standing, which on a stopped import is a screen
    that is still full after the button said it cleared everything.
    """
    noop_handler("scan")
    noop_handler("probe")
    parent = await job_queue.enqueue("scan")
    child = await job_queue.enqueue("probe", parent_id=parent)
    await job_queue.enqueue("probe", parent_id=child)
    await job_queue.cancel(parent)

    assert await job_queue.clear_canceled() == 3

    assert await job_queue.counts() == {}
    assert await job_queue.get(parent) is None


@pytest.mark.integration
async def test_clearing_the_stopped_ones_leaves_the_other_piles_alone(job_queue: JobQueue) -> None:
    """Failures and finished work are not stopped work, and the cascade is why it matters.

    A stopped parent whose child FINISHED stays where it is: the child is a real record of what the
    library has, and removing the parent would take it with it. So the pass stops rather than
    reaching through, which is the same rule that makes it need a loop at all.
    """
    handler = noop_handler()
    stopped = await job_queue.enqueue(handler)
    await job_queue.cancel(stopped)

    broken = await job_queue.enqueue(handler, max_attempts=1)
    await job_queue.claim(WORKER)
    await job_queue.fail(broken, WORKER, "it broke")

    noop_handler("scan")
    kept = await job_queue.enqueue("scan")
    # Claimed one at a time and CHECKED, because `claim` takes the oldest queued job rather than a
    # named one: an unchecked claim here could take the parent and complete the wrong row.
    taken = await job_queue.claim(WORKER)
    assert taken is not None and taken.id == kept
    finished = await job_queue.enqueue(handler, parent_id=kept)
    taken = await job_queue.claim(WORKER)
    assert taken is not None and taken.id == finished
    await job_queue.complete(finished, WORKER)
    await job_queue.cancel(kept)

    assert await job_queue.clear_canceled() == 1

    assert await job_queue.get(stopped) is None
    assert await job_queue.get(broken) is not None
    assert await job_queue.get(finished) is not None
    assert await job_queue.get(kept) is not None


@pytest.mark.integration
async def test_clearing_the_stopped_ones_when_there_are_none_is_not_an_error(
    job_queue: JobQueue,
) -> None:
    """Nothing is stopped, which is what the caller wanted. Zero, not a refusal."""
    assert await job_queue.clear_canceled() == 0


@pytest.mark.integration
async def test_putting_the_failures_back_when_there_are_none_is_not_an_error(
    job_queue: JobQueue,
) -> None:
    """The queue holds no failures, which is what the caller wanted. Zero, not a refusal."""
    assert await job_queue.retry_failed() == 0


# --- the fence --------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.regression
async def test_a_worker_cannot_finish_a_job_that_was_taken_from_it(job_queue: JobQueue) -> None:
    """The double-run bug, in its simplest form.

     Worker one hangs. The watchdog gives up on it and puts the job back. Worker two claims it and
     redoes the work. Worker one then wakes up and reports success on the job worker two is holding,
    and unless every write it makes carries the claim it no longer has, it lands.
    """
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)

    await job_queue.reclaim(stale_after=None, error="hung")
    await job_queue.claim(OTHER_WORKER)

    assert await job_queue.complete(job_id, WORKER) is False
    assert await job_queue.fail(job_id, WORKER, "late") is None
    assert await job_queue.block(job_id, WORKER, "late") is False
    assert await job_queue.heartbeat(job_id, WORKER) is False
    assert await job_queue.set_progress(job_id, WORKER, 0.5) is False

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.RUNNING
    assert job.claimed_by == OTHER_WORKER


@pytest.mark.integration
async def test_a_worker_cannot_finish_a_job_it_never_claimed(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler())

    assert await job_queue.complete(job_id, WORKER) is False


@pytest.mark.integration
async def test_the_owning_worker_heartbeats_and_reports_progress(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)

    assert await job_queue.heartbeat(job_id, WORKER) is True
    assert await job_queue.set_progress(job_id, WORKER, 0.25) is True

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.progress == 0.25


@pytest.mark.integration
async def test_a_job_says_how_many_files_it_is_about_and_is_weighed_by_them(
    job_queue: JobQueue,
) -> None:
    """A scan of eight thousand files counted as one row in every estimate would say "under a
    minute" for twenty minutes. A job says its units once it knows, and what is left of a kind is
    units times the fraction not yet done."""
    job_type = noop_handler("scan")
    job_id = await job_queue.enqueue(job_type)
    await job_queue.claim(WORKER)

    job = await job_queue.get(job_id)
    assert job is not None and job.units == 1, "an ordinary job is about one file"
    assert await job_queue.set_units(job_id, WORKER, 174) is True
    await job_queue.set_progress(job_id, WORKER, 0.5)

    job = await job_queue.get(job_id)
    assert job is not None and job.units == 174
    summary = await job_queue.work_summary()
    assert summary.run[job_type].left_units == pytest.approx(87.0)


@pytest.mark.integration
async def test_only_the_owning_worker_may_say_how_many_files(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)
    assert await job_queue.set_units(job_id, "somebody-else", 5) is False


@pytest.mark.integration
async def test_a_waiting_job_is_told_its_files_and_a_claimed_one_is_not(
    job_queue: JobQueue,
) -> None:
    """A count ahead of a scan writes onto the scan while it waits; once claimed it counts itself."""
    job_type = noop_handler("scan")
    job_id = await job_queue.enqueue(job_type)

    assert await job_queue.set_waiting_units(job_id, 120) is True
    summary = await job_queue.work_summary()
    assert summary.run[job_type].left_units == pytest.approx(120.0)

    await job_queue.claim(WORKER)
    assert await job_queue.set_waiting_units(job_id, 7) is False
    job = await job_queue.get(job_id)
    assert job is not None and job.units == 120


@pytest.mark.integration
@pytest.mark.parametrize(("given_", "stored"), [(-1.0, 0.0), (2.0, 1.0), (0.5, 0.5)])
async def test_progress_stays_between_nothing_and_everything(
    job_queue: JobQueue, given_: float, stored: float
) -> None:
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)

    await job_queue.set_progress(job_id, WORKER, given_)

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.progress == stored


# --- blocked: a wait, not a failure -----------------------------------------------------


@pytest.mark.integration
async def test_a_job_that_needs_a_login_waits_instead_of_failing(job_queue: JobQueue) -> None:
    """The site logins are encrypted with a key derived from an admin's password.

    Logged out, that key does not exist, and no amount of retrying will conjure it. So the job
    waits, and crucially it hands back the attempt it took, because a fortnight away must not
    quietly burn three retries and kill a download that was never going to fail.
    """
    job_id = await job_queue.enqueue(noop_handler("download"), max_attempts=3)
    await job_queue.claim(WORKER)

    assert await job_queue.block(job_id, WORKER, "nobody is logged in") is True

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.BLOCKED
    assert job.attempts == 0


@pytest.mark.integration
async def test_a_blocked_job_is_not_claimable(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler("download"))
    await job_queue.claim(WORKER)
    await job_queue.block(job_id, WORKER, "nobody is logged in")

    assert await job_queue.claim(OTHER_WORKER) is None


@pytest.mark.integration
async def test_logging_in_returns_blocked_jobs_to_the_queue(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler("download"))
    await job_queue.claim(WORKER)
    await job_queue.block(job_id, WORKER, "nobody is logged in")

    assert await job_queue.unblock() == [job_id]

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.QUEUED
    assert job.error is None
    assert await job_queue.claim(WORKER) is not None


@pytest.mark.integration
async def test_blocking_and_unblocking_forever_never_exhausts_the_attempts(
    job_queue: JobQueue,
) -> None:
    """The whole point of not counting a wait. Ten logouts must not kill a job with three retries."""
    job_id = await job_queue.enqueue(noop_handler("download"), max_attempts=3)

    for _ in range(10):
        await job_queue.claim(WORKER)
        await job_queue.block(job_id, WORKER, "nobody is logged in")
        await job_queue.unblock()

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.QUEUED
    assert job.attempts == 0


@pytest.mark.integration
async def test_unblocking_one_kind_of_work_leaves_the_rest_parked(job_queue: JobQueue) -> None:
    """A login is what releases everything; anything else releases only what it supplied.

    Recognition models arriving unpark the face scans that were waiting for them and must not
    touch a job parked on a stored login, which is still exactly as unavailable as it was. The
    park costs no attempt, so the cost of getting this wrong is not a dead job: it is every
    parked row in the queue being claimed, run and parked again on every unrelated event.
    """
    scan = await job_queue.enqueue(noop_handler("face_scan"))
    login = await job_queue.enqueue(noop_handler("download"))
    for job_id in (scan, login):
        await job_queue.claim(WORKER)
        await job_queue.block(job_id, WORKER, "waiting")

    assert await job_queue.unblock(job_type="face_scan") == [scan]

    released = await job_queue.get(scan)
    parked = await job_queue.get(login)
    assert released is not None and released.state is JobState.QUEUED
    assert parked is not None and parked.state is JobState.BLOCKED
    assert parked.error == "waiting"


@pytest.mark.integration
async def test_unblocking_with_nothing_blocked_does_nothing(job_queue: JobQueue) -> None:
    assert await job_queue.unblock() == []


@pytest.mark.integration
async def test_a_blocked_job_can_be_cancelled(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler("download"))
    await job_queue.claim(WORKER)
    await job_queue.block(job_id, WORKER, "nobody is logged in")

    assert await job_queue.cancel(job_id) == [job_id]


# --- recovery and the watchdog ----------------------------------------------------------


@pytest.mark.integration
async def test_boot_recovery_puts_a_running_job_back(job_queue: JobQueue) -> None:
    """At boot this process is the only one, so a running row is nobody's."""
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)

    requeued, failed = await recover(job_queue)

    assert requeued == [job_id]
    assert failed == []

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.QUEUED
    assert job.claimed_by is None
    assert job.heartbeat_at is None
    assert job.attempts == 1  # the interrupted run still cost an attempt


@pytest.mark.integration
async def test_boot_recovery_leaves_finished_and_waiting_jobs_alone(job_queue: JobQueue) -> None:
    done = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)
    await job_queue.complete(done, WORKER)

    blocked = await job_queue.enqueue(noop_handler("download"))
    await job_queue.claim(WORKER)
    await job_queue.block(blocked, WORKER, "nobody is logged in")

    await recover(job_queue)

    assert await job_queue.counts() == {"done": 1, "blocked": 1}


@pytest.mark.integration
async def test_boot_recovery_with_nothing_running_is_a_no_op(job_queue: JobQueue) -> None:
    assert await recover(job_queue) == ([], [])


# --- handing work back on the way out ----------------------------------------------------------
#
# An attempt is charged at the claim, which is what stops a handler that kills the process being
# retried forever. The cost is that a restart charges a job for something that was not its fault,
# so a long pass across a few deploys ends with files given up on permanently, having never
# misbehaved. An ORDERLY shutdown is the one interruption this process knows it caused, so it is the
# one that can hand the attempt back. A crash still goes through recovery above and still pays.


@pytest.mark.integration
async def test_an_orderly_shutdown_hands_a_running_job_back_without_charging_it(
    job_queue: JobQueue,
) -> None:
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)

    released = await job_queue.release_running()

    assert released == [job_id]
    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.QUEUED
    assert job.claimed_by is None
    assert job.heartbeat_at is None
    assert job.attempts == 0, "a restart is not an attempt"
    assert job.note is not None


@pytest.mark.integration
async def test_a_job_survives_more_restarts_than_it_has_attempts(job_queue: JobQueue) -> None:
    """The whole point: a restart does not spend an attempt.

    If it did, three restarts during one long pass would fail a file permanently. The scan running
    at each of those moments would take an attempt for each, hit its limit and never be queued
    again, with nothing wrong with the file and nothing wrong with the handler.
    """
    job_id = await job_queue.enqueue(noop_handler(), max_attempts=3)

    for _ in range(5):
        await job_queue.claim(WORKER)
        await job_queue.release_running()

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.QUEUED
    assert job.attempts == 0


@pytest.mark.integration
async def test_handing_back_leaves_finished_and_waiting_jobs_alone(job_queue: JobQueue) -> None:
    done = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)
    await job_queue.complete(done, WORKER)

    blocked = await job_queue.enqueue(noop_handler("download"))
    await job_queue.claim(WORKER)
    await job_queue.block(blocked, WORKER, "nobody is logged in")

    assert await job_queue.release_running() == []
    assert await job_queue.counts() == {"done": 1, "blocked": 1}


@pytest.mark.integration
async def test_a_worker_cannot_report_on_a_job_that_was_handed_back(job_queue: JobQueue) -> None:
    """The fence, checked. Clearing the claim is what makes a cancelled worker's late write land on
    nothing: otherwise a job requeued by the shutdown could be marked done by the very worker the
    shutdown took it from, and the work would silently never be redone."""
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)
    await job_queue.release_running()

    assert await job_queue.complete(job_id, WORKER) is False

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.QUEUED


# --- a job saying what it did ------------------------------------------------------------------


@pytest.mark.integration
async def test_a_job_can_say_what_it_did(job_queue: JobQueue) -> None:
    """`error` only ever explains a failure, so a job that succeeded and found nothing to do needs
    another way to say so: a control that finishes instantly having done nothing reads as broken."""
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)

    assert await job_queue.set_note(job_id, WORKER, "Nothing to do.") is True

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.note == "Nothing to do."


@pytest.mark.integration
async def test_a_note_from_a_worker_that_no_longer_holds_the_job_lands_on_nothing(
    job_queue: JobQueue,
) -> None:
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)

    assert await job_queue.set_note(job_id, "somebody-else", "I did this") is False

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.note is None


@pytest.mark.integration
async def test_a_note_is_scrubbed_the_way_an_error_is(job_queue: JobQueue) -> None:
    """A note is written to be read, so it is likelier than an error to name something, and it
    travels in the database, the backup and any diagnostics export exactly as an error does."""
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)

    await job_queue.set_note(job_id, WORKER, f"Swept {Path.home()}/Library and found nothing")

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.note is not None
    assert str(Path.home()) not in job.note


@pytest.mark.integration
async def test_a_job_that_takes_the_process_down_every_time_eventually_fails(
    job_queue: JobQueue,
) -> None:
    """Attempts are counted at the claim, not at the failure, and this is why.

    A handler that kills the process never reaches a failure path, so a counter that only moved on
    failure would let this job be claimed, crash, be recovered, and be claimed again, forever. It
    would take the whole queue with it: Sift would never finish starting up.
    """
    job_id = await job_queue.enqueue(noop_handler(), max_attempts=2)

    for _ in range(2):
        await job_queue.claim(WORKER)
        await recover(job_queue)  # the crash, and the restart after it

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.FAILED
    assert job.error == "Sift was restarted while this job was running"


@pytest.mark.integration
async def test_the_watchdog_takes_back_a_job_whose_worker_went_quiet(temp_db: Database) -> None:
    clock = FakeClock()
    queue = JobQueue(temp_db, clock=clock.now)
    await temp_db.initialize_schema()
    job_id = await queue.enqueue(noop_handler())
    await queue.claim(WORKER)

    clock.advance(300)
    await sweep(queue, stale_after=120)

    job = await queue.get(job_id)
    assert job is not None
    assert job.state is JobState.QUEUED


@pytest.mark.integration
async def test_the_watchdog_leaves_a_job_that_is_merely_slow(temp_db: Database) -> None:
    """Reclaiming a job that is only slow means running it twice, which is the bug it prevents."""
    clock = FakeClock()
    queue = JobQueue(temp_db, clock=clock.now)
    await temp_db.initialize_schema()
    job_id = await queue.enqueue(noop_handler())
    await queue.claim(WORKER)

    clock.advance(60)
    await queue.heartbeat(job_id, WORKER)  # still alive, just working
    clock.advance(60)
    await sweep(queue, stale_after=120)

    job = await queue.get(job_id)
    assert job is not None
    assert job.state is JobState.RUNNING
    assert job.claimed_by == WORKER


@pytest.mark.integration
async def test_a_hung_job_eventually_fails_rather_than_hanging_the_next_worker(
    temp_db: Database,
) -> None:
    clock = FakeClock()
    queue = JobQueue(temp_db, clock=clock.now)
    await temp_db.initialize_schema()
    job_id = await queue.enqueue(noop_handler(), max_attempts=2)

    for _ in range(2):
        await queue.claim(WORKER)
        clock.advance(300)
        await sweep(queue, stale_after=120)

    job = await queue.get(job_id)
    assert job is not None
    assert job.state is JobState.FAILED
    assert job.error == "the worker running this job stopped responding"


@pytest.mark.integration
async def test_a_watchdog_sweep_with_nothing_stale_is_a_no_op(job_queue: JobQueue) -> None:
    await job_queue.enqueue(noop_handler())

    await sweep(job_queue)

    assert await job_queue.counts() == {"queued": 1}


# --- fan-out ----------------------------------------------------------------------------


async def run_one(queue: JobQueue, job_id: str, *, outcome: str = "done") -> None:
    """Claim a specific job and take it to a terminal state, the way a worker would."""
    claimed = await queue.claim(WORKER)
    assert claimed is not None
    assert claimed.id == job_id  # the queue handed out the job this test meant to run
    if outcome == "done":
        await queue.complete(job_id, WORKER)
    else:
        await queue.fail(job_id, WORKER, outcome)


@pytest.mark.integration
async def test_a_parent_s_progress_is_its_children_s(job_queue: JobQueue) -> None:
    noop_handler("scan")
    noop_handler("probe")
    parent = await job_queue.enqueue("scan", {"root_id": "01HQ"})
    children = [
        await job_queue.enqueue("probe", {"n": index}, parent_id=parent) for index in range(4)
    ]

    await run_one(job_queue, parent)

    parent_job = await job_queue.get(parent)
    assert parent_job is not None
    assert parent_job.state is JobState.DONE
    assert parent_job.progress == 0.0  # it handed out the work; the work is not done

    for child in children[:2]:
        await run_one(job_queue, child)

    parent_job = await job_queue.get(parent)
    assert parent_job is not None
    assert parent_job.progress == 0.5


@pytest.mark.integration
async def test_a_child_that_fails_still_counts_towards_the_parent_s_progress(
    job_queue: JobQueue,
) -> None:
    """A parent whose bar stops at 90% because one file was corrupt is a support ticket."""
    noop_handler("scan")
    noop_handler("probe")
    parent = await job_queue.enqueue("scan")
    good = await job_queue.enqueue("probe", parent_id=parent, max_attempts=1)
    bad = await job_queue.enqueue("probe", parent_id=parent, max_attempts=1)

    await run_one(job_queue, parent)
    await run_one(job_queue, good)
    await run_one(job_queue, bad, outcome="corrupt")

    parent_job = await job_queue.get(parent)
    assert parent_job is not None
    assert parent_job.progress == 1.0


@pytest.mark.integration
async def test_a_child_that_is_retried_takes_the_parent_s_progress_back_down(
    job_queue: JobQueue,
) -> None:
    """Progress is recomputed, never accumulated. An accumulated count drifts on the first retry."""
    noop_handler("scan")
    noop_handler("probe")
    parent = await job_queue.enqueue("scan")
    child = await job_queue.enqueue("probe", parent_id=parent, max_attempts=1)

    await run_one(job_queue, parent)
    await run_one(job_queue, child, outcome="corrupt")

    parent_job = await job_queue.get(parent)
    assert parent_job is not None
    assert parent_job.progress == 1.0

    await job_queue.retry(child)

    parent_job = await job_queue.get(parent)
    assert parent_job is not None
    assert parent_job.progress == 0.0


@pytest.mark.integration
async def test_cancelling_a_parent_cancels_everything_under_it(job_queue: JobQueue) -> None:
    noop_handler("scan")
    noop_handler("probe")
    parent = await job_queue.enqueue("scan")
    child = await job_queue.enqueue("probe", parent_id=parent)
    grandchild = await job_queue.enqueue("probe", parent_id=child)

    canceled = await job_queue.cancel(parent)

    assert set(canceled) == {parent, child, grandchild}
    assert await job_queue.counts() == {"canceled": 3}


@pytest.mark.integration
async def test_stopping_a_pass_whose_head_had_finished_calls_the_head_off_too(
    job_queue: JobQueue,
) -> None:
    """A pass's first job hands the work out and ends. Stopped halfway, the family must not fold to
    done (its top was done, and a family reads Canceled only when its top was called off), or the
    pass would read as finished on Activity and as its task's last good run."""
    noop_handler("scan")
    noop_handler("probe")
    head = await job_queue.enqueue("scan")
    await job_queue.claim(WORKER)
    waiting = await job_queue.enqueue("probe", parent_id=head)
    await job_queue.complete(head, WORKER)

    assert await job_queue.cancel(head) == [waiting]

    stopped = await job_queue.get(head)
    assert stopped is not None and stopped.state is JobState.CANCELED
    assert folded_state(stopped.state, {"canceled": 1}) is JobState.CANCELED


@pytest.mark.integration
async def test_a_pass_that_had_finished_is_not_called_off_by_a_stop(job_queue: JobQueue) -> None:
    """Nothing under it was still to do, so the press stopped nothing and it stays done."""
    noop_handler("scan")
    noop_handler("probe")
    head = await job_queue.enqueue("scan")
    await job_queue.claim(WORKER)
    step = await job_queue.enqueue("probe", parent_id=head)
    await job_queue.complete(head, WORKER)
    await job_queue.claim(WORKER)
    await job_queue.complete(step, WORKER)

    assert await job_queue.cancel(head) == []

    finished = await job_queue.get(head)
    assert finished is not None and finished.state is JobState.DONE


@pytest.mark.integration
async def test_cancelling_a_child_leaves_its_parent_alone(job_queue: JobQueue) -> None:
    noop_handler("scan")
    noop_handler("probe")
    parent = await job_queue.enqueue("scan")
    child = await job_queue.enqueue("probe", parent_id=parent)

    assert await job_queue.cancel(child) == [child]

    parent_job = await job_queue.get(parent)
    assert parent_job is not None
    assert parent_job.state is JobState.QUEUED
    assert parent_job.progress == 1.0  # its one child is finished with, one way or another


@pytest.mark.integration
async def test_children_are_listed_oldest_first(temp_db: Database) -> None:
    clock = FakeClock()
    queue = JobQueue(temp_db, clock=clock.now)
    await temp_db.initialize_schema()
    noop_handler("scan")
    noop_handler("probe")
    parent = await queue.enqueue("scan")

    expected = []
    for index in range(3):
        clock.advance(1)
        expected.append(await queue.enqueue("probe", {"n": index}, parent_id=parent))

    assert [child.id for child in await queue.children(parent)] == expected


@pytest.mark.integration
async def test_a_job_with_no_children_has_none(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler())

    assert await job_queue.children(job_id) == []


# --- errors are scrubbed on the way in --------------------------------------------------


@pytest.mark.integration
@pytest.mark.regression
async def test_a_failure_message_keeps_the_facts_and_loses_the_name(job_queue: JobQueue) -> None:
    """This column is read on a dashboard, copied into a diagnostics export, and carried out of
    the machine in a backup. An exception's text is where a path leaks if it leaks anywhere.

    What goes is the account name. What stays is everything that says what actually happened:
    the filename, the folder layout, the error itself, because a failure nobody can diagnose is
    not a failure that has been made safe.
    """
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)

    await job_queue.fail(
        job_id, WORKER, "ffmpeg: /home/kate/Videos/holiday.mp4: Invalid data found"
    )

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.error is not None
    assert "kate" not in job.error
    assert "[redacted]" in job.error
    assert "holiday.mp4" in job.error
    assert "Invalid data found" in job.error


@pytest.mark.integration
@pytest.mark.regression
async def test_a_failure_message_is_not_allowed_to_be_enormous(job_queue: JobQueue) -> None:
    """Nothing bounds a tool's standard error, and this column ends up in a backup.

    ffmpeg will report every frame it did not like; a downloader will produce megabytes about one
    video. The useful part is at the front, and the rest is the same complaint again.
    """
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)

    await job_queue.fail(job_id, WORKER, ("noise " * 100_000) + "Error: the codec broke.")

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.error is not None
    # The END is kept: the reason a tool failed is its last lines, after everything it printed
    # on the way there: the front would be ffmpeg's opening banner with the error cut off.
    assert job.error.endswith("Error: the codec broke.")
    assert job.error.startswith("(truncated) ...")
    assert len(job.error) < MAX_ERROR_CHARACTERS + 100


@pytest.mark.integration
@pytest.mark.regression
async def test_a_failure_message_never_carries_a_credential(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)

    await job_queue.fail(job_id, WORKER, "rejected: Bearer abc.DEF-123_ghi and it stopped")

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.error is not None
    assert "abc.DEF-123_ghi" not in job.error
