# SPDX-License-Identifier: AGPL-3.0-or-later
"""The workers: a handler run, retried, stopped, and the claim's exactly-once under real processes."""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from sift.kernel.content import ContentStore, LibraryStore
from sift.kernel.db import Database
from sift.kernel.jobs import (
    HEARTBEAT_SECONDS,
    SHUTDOWN_GRACE_SECONDS,
    STALE_AFTER_SECONDS,
    Beat,
    Job,
    JobBlocked,
    JobContext,
    JobQueue,
    JobState,
    SystemCapabilities,
    SystemSecrets,
    WorkerPool,
    recover,
    register_handler,
    worker_pool,
)
from sift.kernel.tests.jobs_helpers import (
    OTHER_WORKER,
    WORKER,
    Recorder,
    _a_job,
    drain,
    noop_handler,
    wait_for_state,
)

pytestmark = pytest.mark.usefixtures("clean_handlers")


# --- the workers ------------------------------------------------------------------------


@pytest.mark.integration
async def test_a_worker_runs_a_job_and_marks_it_done(job_queue: JobQueue) -> None:
    recorder = Recorder()
    register_handler("probe", recorder.handler, name="Test job")
    job_id = await job_queue.enqueue("probe", {"asset_id": "01HQ"})

    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    assert recorder.seen == [job_id]
    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.DONE


@pytest.mark.integration
async def test_a_handler_that_raises_fails_its_job_with_the_reason(job_queue: JobQueue) -> None:
    async def explode(context: JobContext) -> None:
        raise RuntimeError("the codec is not supported")

    register_handler("probe", explode, name="Test job")
    job_id = await job_queue.enqueue("probe", max_attempts=1)

    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.FAILED
    assert job.error == "RuntimeError: the codec is not supported"


@pytest.mark.integration
async def test_a_handler_that_raises_is_retried_until_its_attempts_run_out(
    job_queue: JobQueue,
) -> None:
    attempts: list[int] = []

    async def explode(context: JobContext) -> None:
        attempts.append(context.attempt)
        raise RuntimeError("nope")

    register_handler("probe", explode, name="Test job")
    await job_queue.enqueue("probe", max_attempts=3)

    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    assert attempts == [1, 2, 3]


@pytest.mark.integration
async def test_a_handler_that_needs_a_login_parks_its_job(job_queue: JobQueue) -> None:
    async def needs_cookies(context: JobContext) -> None:
        raise JobBlocked("this site needs a login")

    register_handler("download", needs_cookies, name="Test job")
    job_id = await job_queue.enqueue("download", max_attempts=3)

    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.BLOCKED
    assert job.attempts == 0
    assert job.error == "this site needs a login"


@pytest.mark.integration
async def test_a_job_whose_type_no_longer_exists_fails_without_retrying(
    job_queue: JobQueue,
) -> None:
    """An upgrade dropped the feature. The row is still there, and no retry will find its code."""
    job_id = await job_queue.enqueue("removed-feature", require_handler=False, max_attempts=5)

    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.FAILED
    assert job.attempts == 1
    assert job.error is not None
    assert "no longer exists" in job.error


@pytest.mark.integration
async def test_a_handler_can_say_what_it_did(job_queue: JobQueue) -> None:
    """Through the context a handler is given, rather than by reaching for the queue itself.

    The whole of what a handler may do to its own job is on that object, and a note is part of
    it, so a control that finished instantly having found no work can say so instead of looking
    broken.
    """

    async def scan(context: JobContext) -> None:
        await context.set_note("Nothing to do.")

    register_handler("scan", scan, name="Test job")
    job_id = await job_queue.enqueue("scan")

    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.note == "Nothing to do."


@pytest.mark.integration
async def test_a_handler_can_report_progress_and_fan_out(job_queue: JobQueue) -> None:
    async def scan(context: JobContext) -> None:
        for index in range(2):
            await context.enqueue_child("probe", {"n": index})
        await context.set_progress(0.5)

    recorder = Recorder()
    register_handler("scan", scan, name="Test job")
    register_handler("probe", recorder.handler, name="Test job")
    parent = await job_queue.enqueue("scan", {"root_id": "01HQ"})

    pool = WorkerPool(job_queue, concurrency=2, poll_interval=0.01)
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    assert len(recorder.seen) == 2
    parent_job = await job_queue.get(parent)
    assert parent_job is not None
    assert parent_job.state is JobState.DONE
    assert parent_job.progress == 1.0  # both children finished


@pytest.mark.integration
async def test_a_handler_that_checks_for_cancellation_stops(job_queue: JobQueue) -> None:
    """A long handler can ask, between steps, whether it is still wanted."""
    running = asyncio.Event()
    carry_on = asyncio.Event()
    stopped = asyncio.Event()

    async def slow(context: JobContext) -> None:
        running.set()
        await carry_on.wait()
        try:
            await context.raise_if_canceled()
        finally:
            stopped.set()

    register_handler("scan", slow, name="Test job")
    job_id = await job_queue.enqueue("scan")

    # The beat is turned right down, so nothing but the handler's own check can notice.
    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01, heartbeat_interval=60)
    await pool.start()
    try:
        await asyncio.wait_for(running.wait(), timeout=5)
        await job_queue.cancel(job_id)
        carry_on.set()
        await asyncio.wait_for(stopped.wait(), timeout=5)
        await drain(job_queue)
    finally:
        await pool.stop()

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.CANCELED


@pytest.mark.integration
@pytest.mark.regression
async def test_a_worker_whose_job_is_taken_away_is_stopped_mid_flight(
    job_queue: JobQueue,
) -> None:
    """The heartbeat is a fence, not just a liveness signal.

    A worker that hangs long enough for the watchdog to give up on it must not carry on running
    the job that has since been handed to somebody else, so when its heartbeat stops matching,
    the handler is cancelled where it stands.
    """
    running = asyncio.Event()
    cancelled = asyncio.Event()

    async def forever(context: JobContext) -> None:
        running.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    register_handler("scan", forever, name="Test job")
    job_id = await job_queue.enqueue("scan")

    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01, heartbeat_interval=0.02)
    await pool.start()
    try:
        await asyncio.wait_for(running.wait(), timeout=5)
        await job_queue.cancel(job_id)  # the job stops being this worker's
        await asyncio.wait_for(cancelled.wait(), timeout=5)
    finally:
        await pool.stop()

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.CANCELED  # the worker did not write over it


@pytest.mark.integration
@pytest.mark.regression
async def test_a_worker_that_loses_a_job_goes_on_to_the_next_one(job_queue: JobQueue) -> None:
    """Losing a job must not lose the worker with it.

    The handler is cancelled where it stands when its job is taken away, and a cancelled task does
    not politely hand back a result: ask it for one and it raises. Left unhandled that kills the
    worker loop, silently, and the process runs one worker short for the rest of its life while
    every row it never claimed sits in the queue looking fine. Nothing else notices: the fence in
    the database still stops the damage, so only this test sees the worker go.
    """
    running = asyncio.Event()
    recorder = Recorder()

    async def forever(context: JobContext) -> None:
        running.set()
        await asyncio.sleep(60)

    register_handler("scan", forever, name="Test job")
    register_handler("probe", recorder.handler, name="Test job")

    doomed = await job_queue.enqueue("scan")
    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01, heartbeat_interval=0.02)
    await pool.start()
    try:
        await asyncio.wait_for(running.wait(), timeout=5)
        await job_queue.cancel(doomed)

        next_job = await job_queue.enqueue("probe", {"asset_id": "01HQ"})
        await drain(job_queue)
    finally:
        await pool.stop()

    assert recorder.seen == [next_job]  # the one worker there is picked it up


@pytest.mark.unit
def test_a_live_job_beats_several_times_before_the_watchdog_gives_up_on_it() -> None:
    """The two intervals are a pair, and nothing else checks that they agree.

    Beat too rarely, or give up too soon, and the watchdog reclaims perfectly healthy jobs and
    hands them to a second worker: the exact failure this module exists to prevent, arriving
    through the front door. Lower either number carelessly and every long job runs twice.

    This is why the numbers are constants and not settings: a test can assert a relation between
    two of them, and nothing can assert it about two values somebody typed into a file.
    """
    assert HEARTBEAT_SECONDS * 4 <= STALE_AFTER_SECONDS


@pytest.mark.unit
@pytest.mark.regression
def test_the_desktop_app_gives_the_backend_longer_to_stop_than_the_pool_takes_to_drain(
    repo_root: Path,
) -> None:
    """Shutdown waits for the jobs already running, so whatever stops the backend has to allow for
    that. The desktop app asks it to stop and takes the process after `STOP_TIMEOUT_MS`; shorter
    than the drain, the process is taken mid-drain and the wait never happens. Nothing is lost (an
    unfinished job is still in the database and re-runs at the next boot), but the work is redone
    for no reason.
    """
    shell = (repo_root / "desktop" / "src" / "backend.ts").read_text(encoding="utf-8")

    declared = re.search(r"^const STOP_TIMEOUT_MS = ([\d_]+);", shell, re.MULTILINE)
    assert declared is not None, "desktop/src/backend.ts no longer declares STOP_TIMEOUT_MS"
    assert int(declared.group(1).replace("_", "")) / 1000 > SHUTDOWN_GRACE_SECONDS


@pytest.mark.integration
@pytest.mark.regression
async def test_a_handler_that_finishes_a_job_it_has_lost_does_not_write_over_it(
    job_queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The narrow window: the job is cancelled while the handler is on its last line.

    Too late for the heartbeat to stop it, so the handler returns normally and the worker goes to
    mark it done. The fence refuses the write, and the worker must *say so*. Silently dropping
    the outcome is how "the job ran, but the row says otherwise" becomes a mystery instead of a
    log line, so the log line is what this asserts; the row is guarded by the fence either way.
    """
    events: list[str] = []
    monkeypatch.setattr(worker_pool.log, "info", lambda event, **fields: events.append(event))

    finishing = asyncio.Event()
    may_finish = asyncio.Event()

    async def slow(context: JobContext) -> None:
        finishing.set()
        await may_finish.wait()  # cancelled while sitting here, and then returns normally

    register_handler("scan", slow, name="Test job")
    job_id = await job_queue.enqueue("scan")

    # No beat will fire in time, so the handler never learns; only the fence stops it.
    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01, heartbeat_interval=60)
    await pool.start()
    try:
        await asyncio.wait_for(finishing.wait(), timeout=5)
        await job_queue.cancel(job_id)
        may_finish.set()
        await drain(job_queue)
    finally:
        await pool.stop()

    assert "job.lost" in events  # the worker noticed, and said so

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.CANCELED  # not done
    assert job.progress == 0.0


@pytest.mark.integration
async def test_a_pool_cannot_be_started_twice(job_queue: JobQueue) -> None:
    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            await pool.start()
    finally:
        await pool.stop()


@pytest.mark.integration
async def test_stopping_a_pool_that_never_started_is_fine(job_queue: JobQueue) -> None:
    await WorkerPool(job_queue, concurrency=1).stop()


@pytest.mark.integration
async def test_a_worker_survives_the_database_going_away_under_it(job_queue: JobQueue) -> None:
    """One transient error must not silently cost the process a worker for the rest of its life."""
    failures = 0
    real_claim = job_queue.claim

    async def flaky(*args: object, **kwargs: object) -> Job | None:
        nonlocal failures
        failures += 1
        if failures == 1:
            raise RuntimeError("database is locked")
        return await real_claim(*args, **kwargs)  # type: ignore[arg-type]

    job_queue.claim = flaky  # type: ignore[method-assign]
    recorder = Recorder()
    register_handler("probe", recorder.handler, name="Test job")
    job_id = await job_queue.enqueue("probe")

    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    assert recorder.seen == [job_id]


@pytest.mark.integration
@pytest.mark.regression
async def test_a_worker_survives_a_failure_to_write_down_what_happened(
    job_queue: JobQueue,
) -> None:
    """Recording the outcome is a database write like any other, and can fail like any other.

    The settle is guarded as the claim is: unguarded, one transient error while marking a job done
    would kill the worker task outright, the pool would run a worker short for the rest of the
    process's life, the queue would quietly stop draining, and nothing anywhere would say why. The
    same shape of bug as a worker asking a cancelled task for its result: both are a worker dying
    in silence while the data stays perfectly consistent.
    """
    completions = 0
    real_complete = job_queue.complete

    async def flaky(*args: object, **kwargs: object) -> bool:
        nonlocal completions
        completions += 1
        if completions == 1:
            raise RuntimeError("database is locked")
        return await real_complete(*args, **kwargs)  # type: ignore[arg-type]

    job_queue.complete = flaky  # type: ignore[method-assign]
    recorder = Recorder()
    register_handler("probe", recorder.handler, name="Test job")

    first = await job_queue.enqueue("probe", {"n": 1}, max_attempts=1)
    second = await job_queue.enqueue("probe", {"n": 2})

    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    try:
        # The first job's completion blows up. The worker must still be there for the second.
        await wait_for_state(job_queue, second, JobState.DONE)
    finally:
        await pool.stop()

    assert second in recorder.seen
    assert first in recorder.seen  # it ran; only writing down that it ran failed


@pytest.mark.integration
@pytest.mark.regression
async def test_a_child_settling_leaves_its_running_parents_progress_alone(
    job_queue: JobQueue,
) -> None:
    """A parent's progress is its own handler's: a walk says files read of files to read, and
    the files it handed out settling must not write their own share over it."""
    noop_handler("probe")
    parent = await job_queue.enqueue(noop_handler("scan"), {"root_id": "01HQ"})
    assert await job_queue.claim(WORKER) is not None
    child = await job_queue.enqueue("probe", {"n": 1}, parent_id=parent)
    await job_queue.set_progress(parent, WORKER, 0.25)

    claimed = await job_queue.claim(OTHER_WORKER)
    assert claimed is not None and claimed.id == child
    assert await job_queue.complete(child, OTHER_WORKER)

    job = await job_queue.get(parent)
    assert job is not None
    assert job.progress == 0.25


@pytest.mark.integration
async def test_a_worker_keeps_going_when_a_heartbeat_fails_to_write(job_queue: JobQueue) -> None:
    """A database hiccup says nothing about who owns the job. Killing a live job over one would be
    an outage of its own, and if it is not a hiccup, the watchdog is what reclaims the row."""
    beats = 0
    recovered = asyncio.Event()
    real_beat = job_queue.beat

    async def flaky(*args: object, **kwargs: object) -> Beat | None:
        nonlocal beats
        beats += 1
        if beats == 1:
            raise RuntimeError("database is locked")
        answer = await real_beat(*args, **kwargs)  # type: ignore[arg-type]
        if beats >= 3:
            recovered.set()  # it failed once, and has since beaten twice
        return answer

    job_queue.beat = flaky  # type: ignore[method-assign]

    async def slow(context: JobContext) -> None:
        # Held open until the beat has failed and then recovered, so the assertion below is not a
        # race against how long a stack trace takes to format.
        await recovered.wait()

    register_handler("probe", slow, name="Test job")
    job_id = await job_queue.enqueue("probe")

    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01, heartbeat_interval=0.01)
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    assert beats >= 3  # it failed, and it carried on
    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.DONE


@pytest.mark.integration
async def test_the_watchdog_survives_a_sweep_that_fails(job_queue: JobQueue) -> None:
    """The thing that recovers from failure does not get to die of one."""
    sweeps = 0
    recovered = asyncio.Event()
    real_reclaim = job_queue.reclaim

    async def flaky(*args: object, **kwargs: object) -> tuple[list[str], list[str]]:
        nonlocal sweeps
        sweeps += 1
        if sweeps == 1:
            raise RuntimeError("database is locked")
        recovered.set()
        return await real_reclaim(*args, **kwargs)  # type: ignore[arg-type]

    job_queue.reclaim = flaky  # type: ignore[method-assign]

    pool = WorkerPool(
        job_queue, concurrency=1, poll_interval=0.01, watchdog_interval=0.01, stale_after=0
    )
    await pool.start()
    try:
        await asyncio.wait_for(recovered.wait(), timeout=5)
    finally:
        await pool.stop()

    assert sweeps >= 2


@pytest.mark.integration
async def test_a_job_that_will_not_stop_is_handed_back_rather_than_left_running(
    job_queue: JobQueue,
) -> None:
    """Shutdown does not wait forever, and it does not walk away either.

    Leaving the row `running` for boot recovery to find would work, and would charge the job an
    attempt for a restart it had no part in, which is how a pass over a large library would end up
    with files permanently given up on after a few deploys. A shutdown is the one interruption the
    process knows it caused, so it hands the work back and hands the attempt back with it.
    """
    running = asyncio.Event()

    async def forever(context: JobContext) -> None:
        running.set()
        await asyncio.sleep(60)

    register_handler("scan", forever, name="Test job")
    job_id = await job_queue.enqueue("scan")

    pool = WorkerPool(
        job_queue,
        concurrency=1,
        poll_interval=0.01,
        heartbeat_interval=60,
        watchdog=False,
        shutdown_grace=0.05,
    )
    await pool.start()
    await asyncio.wait_for(running.wait(), timeout=5)
    await pool.stop()

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.QUEUED
    assert job.attempts == 0

    # And there is nothing left for boot recovery to find, because nothing was left behind.
    assert await recover(job_queue) == ([], [])


@pytest.mark.integration
async def test_a_pool_with_a_type_limit_runs_the_capped_type(job_queue: JobQueue) -> None:
    recorder = Recorder()
    register_handler("transcode", recorder.handler, name="Test job")
    register_handler("thumbnail", recorder.handler, name="Test job")
    for index in range(4):
        await job_queue.enqueue("transcode", {"n": index})
        await job_queue.enqueue("thumbnail", {"n": index})

    pool = WorkerPool(job_queue, concurrency=4, poll_interval=0.01, limits={"transcode": 1})
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    assert len(recorder.seen) == 8


@pytest.mark.integration
async def test_a_handler_sees_its_payload_and_which_attempt_it_is(job_queue: JobQueue) -> None:
    seen: list[tuple[dict[str, object], int]] = []

    async def inspect(context: JobContext) -> None:
        seen.append((context.payload, context.attempt))
        await context.raise_if_canceled()  # still ours: this returns, it does not raise

    register_handler("probe", inspect, name="Test job")
    await job_queue.enqueue("probe", {"asset_id": "01HQ"})

    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    assert seen == [({"asset_id": "01HQ"}, 1)]


@pytest.mark.integration
async def test_the_watchdog_runs_inside_the_pool(job_queue: JobQueue) -> None:
    """A hung worker is not a crashed one: nothing announces it, and only the sweep notices."""
    hung = asyncio.Event()

    async def hang(context: JobContext) -> None:
        hung.set()
        await asyncio.sleep(30)

    register_handler("scan", hang, name="Test job")
    job_id = await job_queue.enqueue("scan", max_attempts=1)

    pool = WorkerPool(
        job_queue,
        concurrency=1,
        poll_interval=0.01,
        heartbeat_interval=60,  # it never beats, so its heartbeat goes quiet immediately
        watchdog_interval=0.05,
        stale_after=0,
    )
    await pool.start()
    try:
        await asyncio.wait_for(hung.wait(), timeout=5)
        # One attempt, and it was spent on the run that hung, so the sweep gives up rather than
        # handing a job that hangs workers to the next worker as well.
        job = await wait_for_state(job_queue, job_id, JobState.FAILED)
    finally:
        await pool.stop()

    assert job.error == "the worker running this job stopped responding"


@pytest.mark.integration
async def test_a_pool_with_no_watchdog_leaves_stale_jobs_alone(job_queue: JobQueue) -> None:
    """Read while the pool is still up, deliberately.

    Stopping the pool now hands back everything in flight, so a check made after the stop would be
    reading the shutdown rather than the watchdog. What this is about is that a pool running WITHOUT
    a watchdog reclaims nothing on its own.
    """
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.claim(WORKER)

    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01, watchdog=False)
    await pool.start()
    await asyncio.sleep(0.05)

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.RUNNING

    await pool.stop()


# --- the property the whole design rests on ---------------------------------------------


@pytest.mark.integration
async def test_many_workers_racing_for_many_jobs_claim_each_one_exactly_once(
    job_queue: JobQueue,
) -> None:
    """No job claimed twice, no job left behind.

    Everything else in this module is a detail. If this does not hold, the queue is not a queue.
    """
    job_count = 60
    worker_count = 12
    for index in range(job_count):
        await job_queue.enqueue("probe", {"n": index}, require_handler=False)

    claimed: list[str] = []

    async def worker(worker_id: str) -> None:
        while (job := await job_queue.claim(worker_id)) is not None:
            claimed.append(job.id)
            await job_queue.complete(job.id, worker_id)

    await asyncio.gather(*(worker(f"worker-{index}") for index in range(worker_count)))

    assert len(claimed) == job_count
    assert len(set(claimed)) == job_count
    assert await job_queue.counts() == {"done": job_count}


@pytest.mark.integration
async def test_a_full_pool_runs_every_job_exactly_once(job_queue: JobQueue) -> None:
    recorder = Recorder()
    register_handler("probe", recorder.handler, name="Test job")
    for index in range(40):
        await job_queue.enqueue("probe", {"n": index})

    pool = WorkerPool(job_queue, concurrency=8, poll_interval=0.01)
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    assert len(recorder.seen) == 40
    assert len(set(recorder.seen)) == 40


# --- kill the process, and mean it -------------------------------------------------------

# Claims the job, says so, and then waits to be killed. The id goes to stderr because the logger
# has stdout, and a log line is not the answer to "did it claim it".
_CLAIM_THEN_HANG = """
import asyncio, sys
from pathlib import Path

import sift.kernel.jobs  # registers the schema
from sift.kernel.db import Database
from sift.kernel.jobs import JobQueue


async def main() -> None:
    database = Database(Path(sys.argv[1]))
    await database.connect()
    await database.initialize_schema()
    queue = JobQueue(database)
    job = await queue.claim("doomed-worker")
    assert job is not None
    sys.stderr.write(job.id + chr(10))
    sys.stderr.flush()
    await asyncio.sleep(300)


asyncio.run(main())
"""


async def _enqueue_one(path: Path) -> str:
    database = Database(path)
    await database.connect()
    try:
        await database.initialize_schema()
        return await JobQueue(database).enqueue(
            "probe", {"asset_id": "01HQ"}, require_handler=False
        )
    finally:
        await database.close()


async def _restart_and_finish(path: Path, job_id: str) -> tuple[list[str], Job]:
    """What Sift does at boot: reclaim the orphan, then run it."""
    database = Database(path)
    await database.connect()
    try:
        queue = JobQueue(database)

        orphaned = await queue.get(job_id)
        assert orphaned is not None
        assert orphaned.state is JobState.RUNNING  # still claimed, and nobody is running it
        assert orphaned.claimed_by == "doomed-worker"

        requeued, _ = await recover(queue)
        assert requeued == [job_id]

        recorder = Recorder()
        register_handler("probe", recorder.handler, name="Test job")
        pool = WorkerPool(queue, concurrency=4, poll_interval=0.01)
        await pool.start()
        try:
            await drain(queue)
        finally:
            await pool.stop()

        job = await queue.get(job_id)
        assert job is not None
        return recorder.seen, job
    finally:
        await database.close()


@pytest.mark.integration
def test_a_job_whose_process_is_killed_runs_again_exactly_once(tmp_path: Path) -> None:
    """The one that matters: pull the plug mid-job and Sift resumes, without redoing it twice.

    A real process, really killed. A crash simulated in-process is a test of the simulation:
    SIGKILL gives the process no chance to tidy up, roll back, or write anything, which is exactly
    the case the design is for. The claim was one statement, and SQLite is what undoes it.
    """
    path = tmp_path / "jobs.sqlite3"
    job_id = asyncio.run(_enqueue_one(path))

    doomed = subprocess.Popen(
        [sys.executable, "-c", _CLAIM_THEN_HANG, str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[3])},
    )
    try:
        assert doomed.stderr is not None
        assert doomed.stderr.readline().strip() == job_id  # it really did claim it first
        # `kill()`, not `send_signal(SIGKILL)`: on POSIX it sends exactly that signal, and on
        # Windows it calls TerminateProcess, which is the same unconditional, uncatchable
        # stop. Written this way the recovery is proved on both sites rather than skipped
        # on the one Sift ships on.
        doomed.kill()
        doomed.wait(timeout=30)
    finally:
        doomed.kill()

    # Windows lets go of a killed process's files in its own time, and until it has the database
    # answers a disk error to whoever opens it: the restart is tried again for a few seconds.
    for attempt in range(20):
        try:
            seen, job = asyncio.run(_restart_and_finish(path, job_id))
            break
        except sqlite3.OperationalError as error:
            if "disk I/O error" not in str(error) or attempt == 19:
                raise
            time.sleep(0.5)

    assert seen == [job_id]  # exactly once: not zero, not twice
    assert job.state is JobState.DONE
    assert job.attempts == 2  # the killed run cost one of them


# --- The system-capability seam ---------------------------------------------------------------
#
# What a handler may do as the system, and (more to the point) what it may not. A job has no
# request and no viewer, so the kernel hands it the unscoped content store and, when a slice
# supplies one, the master key. These cover the wiring reaching a real handler, and the two
# answers a handler must be able to tell apart: "no key, wait for a login" and "no capabilities at
# all, which is a wiring bug and not something to wait for".


class KeyStore:
    """A stand-in for the auth slice's store, which the kernel may not import."""

    def __init__(self, key: bytes | None) -> None:
        self.key = key
        self.asked = 0

    async def master_key(self) -> bytes | None:
        self.asked += 1
        return self.key


@pytest.mark.integration
async def test_a_handler_is_handed_the_content_store_the_pool_was_built_with(
    job_queue: JobQueue, content_store: ContentStore, library_store: LibraryStore
) -> None:
    seen: list[ContentStore] = []

    async def handler(context: JobContext) -> None:
        seen.append(context.content)

    register_handler("probe", handler, name="Test job")
    await job_queue.enqueue("probe")

    pool = WorkerPool(
        job_queue,
        concurrency=1,
        poll_interval=0.01,
        capabilities=SystemCapabilities(content=content_store, library=library_store),
    )
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    assert seen == [content_store]  # the same instance, not a second store over the same database


@pytest.mark.integration
async def test_a_handler_reaches_the_master_key_through_the_seam(
    job_queue: JobQueue, content_store: ContentStore, library_store: LibraryStore
) -> None:
    keys = KeyStore(b"k" * 32)
    seen: list[bytes | None] = []

    async def handler(context: JobContext) -> None:
        seen.append(await context.master_key())

    register_handler("download", handler, name="Test job")
    await job_queue.enqueue("download")

    pool = WorkerPool(
        job_queue,
        concurrency=1,
        poll_interval=0.01,
        capabilities=SystemCapabilities(content=content_store, library=library_store, secrets=keys),
    )
    await pool.start()
    try:
        await drain(job_queue)
    finally:
        await pool.stop()

    assert seen == [b"k" * 32]
    assert keys.asked == 1


@pytest.mark.integration
async def test_a_job_blocks_rather_than_fails_when_nobody_has_logged_in(
    job_queue: JobQueue, content_store: ContentStore, library_store: LibraryStore
) -> None:
    """The path a download lives on: no key is a wait, never a failure.

    A key exists in this process only once a password has unwrapped it, so `None` is the ordinary
    state after a restart. Failing here would burn an attempt on something that was never going to
    succeed without a human, and a fortnight logged out would exhaust `max_attempts` and kill a
    download for good.
    """

    async def handler(context: JobContext) -> None:
        if await context.master_key() is None:
            raise JobBlocked("this site needs a login")

    register_handler("download", handler, name="Test job")
    job_id = await job_queue.enqueue("download")

    pool = WorkerPool(
        job_queue,
        concurrency=1,
        poll_interval=0.01,
        capabilities=SystemCapabilities(
            content=content_store, library=library_store, secrets=KeyStore(None)
        ),
    )
    await pool.start()
    try:
        await wait_for_state(job_queue, job_id, JobState.BLOCKED)
    finally:
        await pool.stop()

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.BLOCKED
    assert job.error == "this site needs a login"
    assert job.attempts == 0  # blocking hands the attempt back; it cost nothing


@pytest.mark.integration
async def test_an_unwired_secret_store_reads_as_no_key_rather_than_an_error(
    job_queue: JobQueue, content_store: ContentStore, library_store: LibraryStore
) -> None:
    """No backend behind the seam answers the same as a backend with nobody logged in.

    This is what lets a handler's blocked path be written and tested before the slice that stores
    a secret exists, and what stops the wiring gap from surfacing as a crash instead.
    """
    context = JobContext(
        job=_a_job(),
        worker_id=WORKER,
        queue=job_queue,
        capabilities=SystemCapabilities(content=content_store, library=library_store),
    )

    assert await context.master_key() is None


async def test_a_context_with_no_capabilities_says_so_instead_of_handing_out_none(
    job_queue: JobQueue,
) -> None:
    """A wiring bug must not look like "no key": that would block forever, waiting for a login
    that would never help. It is a programming error, and it is raised as one."""
    context = JobContext(job=_a_job(), worker_id=WORKER, queue=job_queue)

    with pytest.raises(RuntimeError, match="without system capabilities"):
        _ = context.content
    with pytest.raises(RuntimeError, match="without system capabilities"):
        _ = context.library
    with pytest.raises(RuntimeError, match="without system capabilities"):
        await context.master_key()


async def test_a_context_hands_a_handler_the_library_store(
    job_queue: JobQueue, content_store: ContentStore, library_store: LibraryStore
) -> None:
    """The roots and the tree, unscoped. The scan and watch handlers act for nobody, so they cannot
    reach a root's path through the access layer and are handed the store that keeps it."""
    context = JobContext(
        job=_a_job(),
        worker_id=WORKER,
        queue=job_queue,
        capabilities=SystemCapabilities(content=content_store, library=library_store),
    )

    assert context.library is library_store


def test_the_secret_store_shape_is_checkable() -> None:
    assert isinstance(KeyStore(None), SystemSecrets)
    assert not isinstance(object(), SystemSecrets)


def test_require_str_returns_a_present_field_and_refuses_a_missing_one(job_queue: JobQueue) -> None:
    """A handler asks its context for the one field it needs. A present, non-empty string comes
    back; a missing, empty, or non-string one is a ValueError: the enqueue was wrong, and the job
    fails with a reason rather than the handler tripping over a None later."""
    present = JobContext(
        job=replace(_a_job(), payload={"asset_id": "abc"}), worker_id=WORKER, queue=job_queue
    )
    assert present.require_str("asset_id", "needs an asset_id") == "abc"

    for bad in ({}, {"asset_id": ""}, {"asset_id": 5}):
        context = JobContext(job=replace(_a_job(), payload=bad), worker_id=WORKER, queue=job_queue)
        with pytest.raises(ValueError, match="needs an asset_id"):
            context.require_str("asset_id", "needs an asset_id")
