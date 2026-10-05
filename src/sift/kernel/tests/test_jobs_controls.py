# SPDX-License-Identifier: AGPL-3.0-or-later
"""The operator's controls and housekeeping: retention, clearing, stopping everything, pausing."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sift.kernel.content import ContentStore, LibraryStore
from sift.kernel.jobs import (
    STOP_TO_PAUSE,
    JobContext,
    JobPaused,
    JobQueue,
    JobState,
    SystemCapabilities,
    WorkerPool,
    Workspaces,
    folded_state,
    recover,
    register_handler,
    sweep,
)
from sift.kernel.jobs.tuning import (
    BACKGROUND_PRIORITY,
    PARKED_RETENTION_SECONDS,
    SETTLED_RETENTION_SECONDS,
    WAITED_ON_PRIORITY,
)
from sift.kernel.tests.jobs_helpers import (
    OTHER_WORKER,
    WORKER,
    _state,
    noop_handler,
    wait_for_state,
)
from sift.testing.fixtures import FakeClock

pytestmark = pytest.mark.usefixtures("clean_handlers")


# --- retention -----------------------------------------------------------------------------
#
# The table is the queue's whole memory, and without pruning it grows by thousands of rows a day
# with no ceiling. These say what may be forgotten, what may not, and (the one that matters most)
# that forgetting a parent can never take a live child with it.


async def _aged(queue: JobQueue, job_id: str, *, seconds_ago: int) -> None:
    """Backdate a job so a retention window can be tested without waiting a week.

    Both stamps: a job queued and settled that long ago. The prune counts from the settling
    (`updated_at`); see the test below for a job queued long ago that settled just now.
    """
    async with queue._db.write() as connection:
        await connection.execute(
            "UPDATE jobs SET created_at = ?, updated_at = ? WHERE id = ?",
            (int(queue._now()) - seconds_ago, int(queue._now()) - seconds_ago, job_id),
        )


@pytest.mark.integration
async def test_a_finished_job_older_than_the_window_is_forgotten(job_queue: JobQueue) -> None:
    old = await job_queue.enqueue(noop_handler(), {"asset_id": "A"})
    await _state(job_queue, old, "done")
    await _aged(job_queue, old, seconds_ago=SETTLED_RETENTION_SECONDS + 60)

    assert await job_queue.prune_settled() == 1
    assert await job_queue.get(old) is None


@pytest.mark.integration
@pytest.mark.parametrize("state", ["done", "blocked"])
async def test_a_job_queued_long_ago_that_settled_just_now_is_kept(
    job_queue: JobQueue, state: str
) -> None:
    """The window counts from the settling, not from the queueing.

    A job that waited or ran for longer than the window before it finished must still be on the
    task screen for the whole window after it finished; counted from `created_at` it would go at
    the next pass, and a job parked a month after it was queued would go the moment it parked.
    """
    job_id = await job_queue.enqueue(noop_handler(), {"asset_id": "A"})
    await _state(job_queue, job_id, state)
    async with job_queue._db.write() as connection:
        await connection.execute(
            "UPDATE jobs SET created_at = ?, updated_at = ? WHERE id = ?",
            (int(job_queue._now()) - PARKED_RETENTION_SECONDS * 2, int(job_queue._now()), job_id),
        )

    assert await job_queue.prune_settled() == 0
    assert await job_queue.get(job_id) is not None


@pytest.mark.integration
async def test_a_finished_job_inside_the_window_is_kept(job_queue: JobQueue) -> None:
    """The window is the whole feature. A prune that ignored it would empty the screen."""
    recent = await job_queue.enqueue(noop_handler(), {"asset_id": "A"})
    await _state(job_queue, recent, "done")
    await _aged(job_queue, recent, seconds_ago=SETTLED_RETENTION_SECONDS - 60)

    assert await job_queue.prune_settled() == 0
    assert await job_queue.get(recent) is not None


@pytest.mark.integration
@pytest.mark.parametrize("state", ["failed", "queued", "running"])
async def test_only_settled_and_uninteresting_states_are_forgotten(
    job_queue: JobQueue, state: str
) -> None:
    """`failed` is the one that would be easy to sweep with the rest and must not be.

    It is the only record that something did not happen, the reclaim screen counts it, and clearing
    it is already something somebody chooses. The two live states are here for the obvious reason:
    age is not a reason to forget work that has not happened yet.

    **`blocked` is not in this list, though that reasoning sounds as if it should be:** *age is not
    a reason to forget work that has not happened yet* is true of a job parked for an afternoon,
    and assumes a park is always a short wait for a thing that arrives. A scan parked on a
    recognition family with no weights is one row per file and only the weights arriving clears it,
    so the pile has no ceiling at all (see the two tests below and `PARKED_RETENTION_SECONDS`).
    """
    job_id = await job_queue.enqueue(noop_handler(), {"asset_id": "A"})
    await _state(job_queue, job_id, state)
    await _aged(job_queue, job_id, seconds_ago=SETTLED_RETENTION_SECONDS * 10)

    assert await job_queue.prune_settled() == 0
    assert await job_queue.get(job_id) is not None


@pytest.mark.integration
async def test_a_parked_job_is_kept_far_longer_than_a_finished_one(job_queue: JobQueue) -> None:
    """Long enough that nothing which was going to be released is ever reached by the sweep.

    A park is work waiting for one thing (a login, a set of weights), and the thing arriving
    releases it. Two weeks is an ordinary wait for somebody who has not signed in; a finished job
    the same age has been gone for a week.
    """
    parked = await job_queue.enqueue(noop_handler(), {"asset_id": "A"})
    await _state(job_queue, parked, "blocked")
    await _aged(job_queue, parked, seconds_ago=SETTLED_RETENTION_SECONDS * 2)

    assert await job_queue.prune_settled() == 0
    assert await job_queue.get(parked) is not None


@pytest.mark.integration
async def test_a_job_parked_past_its_own_window_is_forgotten(job_queue: JobQueue) -> None:
    """The ceiling on a pile only one act can clear.

    A scan parked on a family whose weights are not on disk parks a row per file, so a library
    scanned that way leaves as many parked rows as it has files and every list of the queue reads
    past all of them for as long as the family stays that way. This discards work rather than
    forgetting a record of it (see `PARKED_RETENTION_SECONDS`), which is why the window is a
    month and not a week.
    """
    parked = await job_queue.enqueue(noop_handler(), {"asset_id": "A"})
    await _state(job_queue, parked, "blocked")
    await _aged(job_queue, parked, seconds_ago=PARKED_RETENTION_SECONDS + 60)

    assert await job_queue.prune_settled() == 1
    assert await job_queue.get(parked) is None


@pytest.mark.integration
async def test_forgetting_a_parent_can_never_take_a_live_child_with_it(
    job_queue: JobQueue,
) -> None:
    """The cascade is the danger, and this is the test that says it cannot fire.

    `parent_id` carries ON DELETE CASCADE, so deleting an old finished parent would delete whatever
    hangs off it, including a job that is running right now. The prune only takes childless rows,
    so a tree is peeled from the leaves and a parent becomes eligible only once nothing is under it.
    """
    job_type = noop_handler()
    parent = await job_queue.enqueue(job_type, {"asset_id": "P"})
    child = await job_queue.enqueue(job_type, {"asset_id": "C"}, parent_id=parent)
    await _state(job_queue, parent, "done")
    await _state(job_queue, child, "running")
    await _aged(job_queue, parent, seconds_ago=SETTLED_RETENTION_SECONDS * 10)
    await _aged(job_queue, child, seconds_ago=SETTLED_RETENTION_SECONDS * 10)

    assert await job_queue.prune_settled() == 0
    assert await job_queue.get(parent) is not None
    assert await job_queue.get(child) is not None


@pytest.mark.integration
async def test_a_settled_tree_is_peeled_leaf_first_over_successive_passes(
    job_queue: JobQueue,
) -> None:
    """The other half of the rule above: once nothing is live, the whole tree does go.

    Two passes rather than one, and that is the design rather than a shortcoming: the parent is
    not childless until the child has gone. The watchdog runs every thirty seconds, so catching up
    costs time nobody is waiting on.
    """
    job_type = noop_handler()
    parent = await job_queue.enqueue(job_type, {"asset_id": "P"})
    child = await job_queue.enqueue(job_type, {"asset_id": "C"}, parent_id=parent)
    for job_id in (parent, child):
        await _state(job_queue, job_id, "done")
        await _aged(job_queue, job_id, seconds_ago=SETTLED_RETENTION_SECONDS * 10)

    assert await job_queue.prune_settled() == 1
    assert await job_queue.get(child) is None
    assert await job_queue.get(parent) is not None

    assert await job_queue.prune_settled() == 1
    assert await job_queue.get(parent) is None


@pytest.mark.integration
async def test_one_pass_removes_no_more_than_its_batch(job_queue: JobQueue) -> None:
    """The bound is what keeps housekeeping off the single writer for long stretches."""
    job_type = noop_handler()
    for index in range(5):
        job_id = await job_queue.enqueue(job_type, {"asset_id": str(index)})
        await _state(job_queue, job_id, "done")
        await _aged(job_queue, job_id, seconds_ago=SETTLED_RETENTION_SECONDS * 10)

    assert await job_queue.prune_settled(batch=2) == 2
    assert await job_queue.prune_settled(batch=2) == 2
    assert await job_queue.prune_settled(batch=2) == 1
    assert await job_queue.prune_settled(batch=2) == 0


@pytest.mark.integration
async def test_the_watchdog_sweep_forgets_what_is_settled_and_old(job_queue: JobQueue) -> None:
    """The second half of the sweep, which nothing else reaches through the sweep itself.

    Reclaiming is what recovers a stuck queue and every test above it is about that. This is the
    housekeeping pass on the same timer, and the only thing that runs it in production is this
    function, so a sweep that stopped calling it would leave the table growing for ever with a
    green suite, because `prune_settled` is tested on its own and would go on passing.
    """
    old = await job_queue.enqueue(noop_handler(), {"asset_id": "A"})
    await _state(job_queue, old, "done")
    await _aged(job_queue, old, seconds_ago=SETTLED_RETENTION_SECONDS * 10)

    await sweep(job_queue)

    assert await job_queue.get(old) is None


async def _ran(queue: JobQueue, job_id: str, *, state: str = "done") -> None:
    """Settle a job as a RUN: picked up by a worker, then ended. What the prune keeps one of."""
    async with queue._db.write() as connection:
        await connection.execute(
            "UPDATE jobs SET state = ?, started_at = ? WHERE id = ?",
            (state, int(queue._now()), job_id),
        )


@pytest.mark.integration
async def test_a_types_only_run_is_kept_however_old_it_is(job_queue: JobQueue) -> None:
    """A weekly backup's row is a week old at the moment the next one falls due. Taken then, the
    queue would hold no run of the backup at all until the next one ended, and the scheduler and
    Activity's last run read it from here."""
    only = await job_queue.enqueue(noop_handler("weekly_chore"))
    await _ran(job_queue, only)
    await _aged(job_queue, only, seconds_ago=SETTLED_RETENTION_SECONDS * 10)

    assert await job_queue.prune_settled() == 0
    assert await job_queue.get(only) is not None


@pytest.mark.integration
async def test_a_newer_run_lets_the_old_one_go(job_queue: JobQueue) -> None:
    """One run is kept per type, the NEWEST, so the pile is still bounded."""
    kind = noop_handler("weekly_chore")
    old = await job_queue.enqueue(kind)
    new = await job_queue.enqueue(kind)
    for job_id, age in ((old, 20), (new, 10)):
        await _ran(job_queue, job_id)
        await _aged(job_queue, job_id, seconds_ago=SETTLED_RETENTION_SECONDS * age)

    assert await job_queue.prune_settled() == 1
    assert await job_queue.get(old) is None
    assert await job_queue.get(new) is not None
    assert await job_queue.prune_settled() == 0


@pytest.mark.integration
async def test_a_waiting_row_taken_back_is_not_a_run_and_does_not_displace_one(
    job_queue: JobQueue,
) -> None:
    """A schedule change cancels the waiting row before it ever starts. That is not a run of
    anything, so it neither counts as the newest run nor is kept as one."""
    kind = noop_handler("weekly_chore")
    ran = await job_queue.enqueue(kind)
    await _ran(job_queue, ran)
    await _aged(job_queue, ran, seconds_ago=SETTLED_RETENTION_SECONDS * 20)
    withdrawn = await job_queue.enqueue(kind)
    await _state(job_queue, withdrawn, "canceled")
    await _aged(job_queue, withdrawn, seconds_ago=SETTLED_RETENTION_SECONDS * 10)

    assert await job_queue.prune_settled() == 1
    assert await job_queue.get(withdrawn) is None
    assert await job_queue.get(ran) is not None


# --- throwing failures away, which running them again cannot replace ------------------------------


@pytest.mark.integration
async def test_clearing_the_failures_removes_them(job_queue: JobQueue) -> None:
    """A failure that cannot succeed is offered again, fails again, and is still there. Without
    this the only way to empty a screen full of them would be to retry every one and watch it."""
    handler = noop_handler()
    failed = []
    for _ in range(3):
        job_id = await job_queue.enqueue(handler, max_attempts=1)
        await job_queue.claim(WORKER)
        await job_queue.fail(job_id, WORKER, "it broke")
        failed.append(job_id)

    assert await job_queue.clear_failed() == 3

    for job_id in failed:
        assert await job_queue.get(job_id) is None


@pytest.mark.integration
async def test_clearing_the_failures_leaves_everything_else_alone(job_queue: JobQueue) -> None:
    """Cancelled work is somebody's decision and finished work ages out by itself. Neither is this
    button's business, and a delete that took either would be taking something nobody offered."""
    handler = noop_handler()
    stopped = await job_queue.enqueue(handler)
    await job_queue.cancel(stopped)

    running = await job_queue.enqueue(handler)
    await job_queue.claim(WORKER)

    broken = await job_queue.enqueue(handler, max_attempts=1)
    await job_queue.claim(WORKER)
    await job_queue.fail(broken, WORKER, "it broke")

    assert await job_queue.clear_failed() == 1

    assert await job_queue.get(broken) is None
    assert (await job_queue.get(stopped)) is not None
    assert (await job_queue.get(running)) is not None


@pytest.mark.integration
async def test_a_failure_with_work_still_under_it_is_left_where_it_is(job_queue: JobQueue) -> None:
    """`parent_id` cascades on delete, so removing a parent removes every child, including one
    that is running this second. Taking only rows nothing hangs off means the cascade can never
    fire, and the tree comes apart leaf by leaf over successive presses instead."""
    handler = noop_handler()
    parent = await job_queue.enqueue(handler, max_attempts=1)
    await job_queue.claim(WORKER)
    child = await job_queue.enqueue(handler, parent_id=parent)
    await job_queue.fail(parent, WORKER, "it broke")

    assert await job_queue.clear_failed() == 0

    assert (await job_queue.get(parent)) is not None
    assert (await job_queue.get(child)) is not None


@pytest.mark.integration
async def test_nothing_to_clear_is_a_zero_rather_than_a_fuss(job_queue: JobQueue) -> None:
    assert await job_queue.clear_failed() == 0


# --- stopping the whole queue --------------------------------------------------------------------


@pytest.mark.integration
async def test_stopping_everything_takes_running_work_as_well(job_queue: JobQueue) -> None:
    """The one that makes the button mean anything.

    Work here is produced by work: a scan hands out a probe per file it walks. Taking only what is
    waiting would empty the queue and leave the scan that filled it still walking, so the rows
    would be back within a second and the press would look like it did nothing.
    """
    handler = noop_handler()
    waiting = await job_queue.enqueue(handler)
    running = await job_queue.enqueue(handler)
    await job_queue.claim(WORKER)
    blocked = await job_queue.enqueue(handler)
    await job_queue.claim(WORKER)
    await job_queue.block(blocked, WORKER, "nobody is logged in")

    assert await job_queue.cancel_everything() == 3

    for job_id in (waiting, running, blocked):
        job = await job_queue.get(job_id)
        assert job is not None
        assert job.state is JobState.CANCELED


@pytest.mark.integration
async def test_stopping_everything_calls_off_a_pass_whose_head_had_finished(
    job_queue: JobQueue,
) -> None:
    """The head of a pass hands its work out and ends, so its own row says done while the pass is
    still going. Stopped, the pass is called off, head and all; a pass that had finished, and a
    head with nothing under it, stay done."""
    noop_handler("scan")
    noop_handler("probe")
    over = await job_queue.enqueue("scan")
    await job_queue.claim(WORKER)
    await job_queue.complete(over, WORKER)

    going = await job_queue.enqueue("scan")
    await job_queue.claim(WORKER)
    await job_queue.enqueue("probe", parent_id=going)
    await job_queue.complete(going, WORKER)

    assert await job_queue.cancel_everything() == 1

    stopped = await job_queue.get(going)
    untouched = await job_queue.get(over)
    assert stopped is not None and stopped.state is JobState.CANCELED
    assert untouched is not None and untouched.state is JobState.DONE


@pytest.mark.integration
async def test_stopping_everything_leaves_what_is_already_over_alone(job_queue: JobQueue) -> None:
    """Finished and failed work is not this button's business. A failure is what `clear_failed`
    is for, and rewriting one as cancelled would lose the fact that it was tried and broke."""
    handler = noop_handler()
    done = await job_queue.enqueue(handler)
    await job_queue.claim(WORKER)
    await job_queue.complete(done, WORKER)

    broken = await job_queue.enqueue(handler, max_attempts=1)
    await job_queue.claim(WORKER)
    await job_queue.fail(broken, WORKER, "it broke")

    assert await job_queue.cancel_everything() == 0

    finished = await job_queue.get(done)
    failure = await job_queue.get(broken)
    assert finished is not None and finished.state is JobState.DONE
    assert failure is not None and failure.state is JobState.FAILED


@pytest.mark.integration
async def test_stopping_everything_moves_a_parent_off_its_stalled_fraction(
    job_queue: JobQueue,
) -> None:
    """The roll-up has to actually run, and it is written as a set rather than as a list of ids.

     A parent whose children were all called off should read as over rather than sit for ever at the
     fraction the cancel left it on. This is the assertion that fails if the statement stops
     matching:
    the ids are never named, so nothing else would notice.
    """
    handler = noop_handler()
    parent = await job_queue.enqueue(handler)
    await job_queue.claim(WORKER)
    for _ in range(4):
        await job_queue.enqueue(handler, parent_id=parent)
    await job_queue.complete(parent, WORKER)

    before = await job_queue.get(parent)
    assert before is not None
    assert before.progress < 1.0

    assert await job_queue.cancel_everything() == 4

    after = await job_queue.get(parent)
    assert after is not None
    assert after.progress == 1.0


@pytest.mark.integration
async def test_nothing_to_stop_is_a_zero_rather_than_a_fuss(job_queue: JobQueue) -> None:
    assert await job_queue.cancel_everything() == 0


# --- pausing, and starting again ----------------------------------------------------------
#
# A pause is not a cancel with a nicer word, and the difference is entirely in what is kept: the
# row, its attempts, its place in the world, and whatever the handler had already written into its
# workspace. These hold each of those, and the two ways a pause can be got wrong: a paused job
# that something claims anyway, and a paused job that something sweeps up as an orphan.


async def _paused_workspaces(tmp_path: Path) -> Workspaces:
    return Workspaces(tmp_path / "workspaces")


@pytest.mark.integration
async def test_a_waiting_job_is_paused_at_once_and_nobody_claims_it(job_queue: JobQueue) -> None:
    """Nothing is running it, so there is nobody to ask and nothing half-written to look after."""
    noop_handler("probe")
    job_id = await job_queue.enqueue("probe")

    assert await job_queue.pause(job_id) is True

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.PAUSED
    assert await job_queue.claim(WORKER) is None, "a paused job was handed to a worker"


@pytest.mark.integration
async def test_a_paused_job_is_counted_as_itself_and_as_work_that_is_not_finished(
    job_queue: JobQueue,
) -> None:
    noop_handler("probe")
    job_id = await job_queue.enqueue("probe")
    await job_queue.pause(job_id)

    assert (await job_queue.counts()).get("paused") == 1
    assert await job_queue.outstanding("probe") == 1
    assert folded_state(JobState.PAUSED, {}) is JobState.PAUSED


@pytest.mark.integration
async def test_starting_a_paused_job_again_keeps_everything_it_had(job_queue: JobQueue) -> None:
    """The same job going back into the line: same payload, same priority, same attempts spent."""
    noop_handler("probe")
    job_id = await job_queue.enqueue("probe", {"asset_id": "a1"}, priority=20, run_after=1 << 40)
    await job_queue.pause(job_id)

    assert await job_queue.resume(job_id) is True

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.QUEUED
    assert job.payload == {"asset_id": "a1"}
    assert job.priority == 20
    assert job.attempts == 0
    assert job.run_after is None, "a resumed job still waiting for a time is a control that did not"


@pytest.mark.integration
async def test_a_job_that_is_not_pausable_says_so_rather_than_pretending(
    job_queue: JobQueue,
) -> None:
    """The answer the route turns into a 409. A finished job is not something to stop."""
    noop_handler("probe")
    job_id = await job_queue.enqueue("probe")
    claimed = await job_queue.claim(WORKER)
    assert claimed is not None
    await job_queue.complete(job_id, WORKER)

    assert await job_queue.pause(job_id) is False
    assert await job_queue.resume(job_id) is False


@pytest.mark.integration
async def test_the_watchdog_leaves_a_paused_job_where_it_is(job_queue: JobQueue) -> None:
    """The reaper takes RUNNING rows whose heartbeat has gone quiet. A paused row has no worker by
    design, and reclaiming it would be the queue quietly undoing somebody's decision."""
    noop_handler("probe")
    job_id = await job_queue.enqueue("probe")
    await job_queue.pause(job_id)

    await sweep(job_queue, stale_after=0)

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.PAUSED


@pytest.mark.integration
async def test_a_paused_job_can_still_be_cancelled(job_queue: JobQueue) -> None:
    """Stopping something for now and stopping it for good are different presses, and the second
    has to reach the first: a Stop that left a pile of paused rows behind would not be one."""
    noop_handler("probe")
    job_id = await job_queue.enqueue("probe")
    await job_queue.pause(job_id)

    assert await job_queue.cancel(job_id) == [job_id]

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.CANCELED
    assert await job_queue.resume(job_id) is False


@pytest.mark.integration
async def test_a_running_handler_is_asked_to_stop_and_keeps_what_it_wrote(
    job_queue: JobQueue,
    content_store: ContentStore,
    library_store: LibraryStore,
    tmp_path: Path,
) -> None:
    """The whole point of the state, in one test.

    The handler writes a part-file, watches `stopping()`, and raises `JobPaused` when it is asked.
    The row lands in `paused` with the attempt handed back, and the part-file is still there,
    which is the difference between pausing a download at forty per cent and starting it again.
    """
    workspaces = await _paused_workspaces(tmp_path)
    asked = asyncio.Event()
    stopped = asyncio.Event()

    async def handler(context: JobContext) -> None:
        (context.workspace / "part").write_bytes(b"148 of 240")
        asked.set()
        while True:
            await context.raise_if_canceled()
            if context.stopping() == STOP_TO_PAUSE:
                stopped.set()
                raise JobPaused("stopped part way through")
            await asyncio.sleep(0.01)

    register_handler("download", handler, name="Test job")
    job_id = await job_queue.enqueue("download")

    pool = WorkerPool(
        job_queue,
        concurrency=1,
        poll_interval=0.01,
        heartbeat_interval=0.01,
        capabilities=SystemCapabilities(
            content=content_store, library=library_store, workspaces=workspaces
        ),
    )
    await pool.start()
    try:
        await asyncio.wait_for(asked.wait(), timeout=10)
        assert await job_queue.pause(job_id) is True
        await asyncio.wait_for(stopped.wait(), timeout=10)
        job = await wait_for_state(job_queue, job_id, JobState.PAUSED)
    finally:
        await pool.stop()

    assert job.attempts == 0, "a job somebody stopped has not tried and failed"
    assert (workspaces.of(job_id) / "part").read_bytes() == b"148 of 240"


#: How often the download's fetch watcher reads `stopping()`: the check interval a pause must be
#: seen inside. The download slice's own `_DISK_CHECK_INTERVAL_SECONDS`, written out here because
#: the kernel's tests do not import a slice.
_A_HANDLERS_CHECK_SECONDS = 2.0

#: A heartbeat interval nothing in a test will ever reach, so the only way the handler can hear a
#: request is the queue waking its worker.
_NEVER_BEATS = 3600.0


@pytest.mark.integration
async def test_a_pause_reaches_a_handler_that_only_reads_stopping_without_waiting_for_a_beat(
    job_queue: JobQueue,
    content_store: ContentStore,
    library_store: LibraryStore,
    tmp_path: Path,
) -> None:
    """The press is heard within one of the handler's own checks, not at the next heartbeat.

    Refreshed only by the fifteen-second beat, a paused download would go on for seconds and
    hundreds of megabytes. The heartbeat here never comes round at all, and the handler never beats
    of its own accord (it reads `stopping()` and nothing else, as the download's fetch watcher
    does), so the only thing that can carry the pause to it is the queue waking the worker.
    """
    workspaces = await _paused_workspaces(tmp_path)
    running = asyncio.Event()
    heard_after: list[float] = []
    loop = asyncio.get_running_loop()
    pressed = 0.0

    async def handler(context: JobContext) -> None:
        running.set()
        while True:  # polls on purpose: this is the shape of the download's fetch watcher
            if context.stopping() == STOP_TO_PAUSE:
                break
            await asyncio.sleep(0.01)
        heard_after.append(loop.time() - pressed)
        raise JobPaused("stopped part way through")

    register_handler("download", handler, name="Test job")
    job_id = await job_queue.enqueue("download")

    pool = WorkerPool(
        job_queue,
        concurrency=1,
        poll_interval=0.01,
        heartbeat_interval=_NEVER_BEATS,
        capabilities=SystemCapabilities(
            content=content_store, library=library_store, workspaces=workspaces
        ),
    )
    await pool.start()
    try:
        await asyncio.wait_for(running.wait(), timeout=10)
        pressed = loop.time()
        assert await job_queue.pause(job_id) is True
        await wait_for_state(job_queue, job_id, JobState.PAUSED)
    finally:
        await pool.stop()

    assert len(heard_after) == 1
    assert heard_after[0] < _A_HANDLERS_CHECK_SECONDS, heard_after


@pytest.mark.integration
async def test_a_cancel_drops_a_running_handler_without_waiting_for_a_beat(
    job_queue: JobQueue,
    content_store: ContentStore,
    library_store: LibraryStore,
    tmp_path: Path,
) -> None:
    """The same wake for a cancel: the worker beats at once, finds the claim gone, and drops the
    handler, rather than waiting for the next heartbeat, fetching all the while."""
    running = asyncio.Event()
    dropped = asyncio.Event()

    async def handler(context: JobContext) -> None:
        running.set()
        try:
            await asyncio.sleep(_NEVER_BEATS)
        except asyncio.CancelledError:
            dropped.set()
            raise

    register_handler("download", handler, name="Test job")
    job_id = await job_queue.enqueue("download")

    pool = WorkerPool(
        job_queue,
        concurrency=1,
        poll_interval=0.01,
        heartbeat_interval=_NEVER_BEATS,
        capabilities=SystemCapabilities(
            content=content_store,
            library=library_store,
            workspaces=await _paused_workspaces(tmp_path),
        ),
    )
    await pool.start()
    try:
        await asyncio.wait_for(running.wait(), timeout=10)
        assert await job_queue.cancel(job_id) == [job_id]
        await asyncio.wait_for(dropped.wait(), timeout=_A_HANDLERS_CHECK_SECONDS)
    finally:
        await pool.stop()


@pytest.mark.integration
async def test_a_listener_that_raises_does_not_stop_the_pause_or_the_other_listeners(
    job_queue: JobQueue,
) -> None:
    """The request is the write; telling a listener is a courtesy that must not undo it."""
    heard: list[list[str]] = []

    def broken(job_ids: object) -> None:
        raise RuntimeError("a listener with a fault of its own")

    job_queue.listen_for_stops(broken)
    unlisten = job_queue.listen_for_stops(lambda job_ids: heard.append(list(job_ids)))
    job_id = await job_queue.enqueue("download", require_handler=False)
    assert await job_queue.cancel(job_id) == [job_id]
    unlisten()
    other = await job_queue.enqueue("download", require_handler=False)
    assert await job_queue.cancel(other) == [other]

    assert heard == [[job_id]], "heard the first, and nothing once it stopped listening"


@pytest.mark.integration
async def test_a_resume_while_the_handler_is_still_stopping_withdraws_the_pause(
    job_queue: JobQueue,
    content_store: ContentStore,
    library_store: LibraryStore,
    tmp_path: Path,
) -> None:
    """Pause, then Resume before the handler has finished winding down.

    A handler slower to stop than the gap between the two presses would otherwise land `paused` over
    the resume and leave the row paused for good. So a resume that finds no paused row withdraws the
    request instead, and a handler that lands after that goes back into the line rather than into a
    pause nobody wants any more.
    """
    workspaces = await _paused_workspaces(tmp_path)
    asked = asyncio.Event()
    noticed = asyncio.Event()
    let_go = asyncio.Event()
    runs = 0

    async def handler(context: JobContext) -> None:
        nonlocal runs
        runs += 1
        if runs > 1:
            return
        asked.set()
        while True:
            await context.raise_if_canceled()
            if context.stopping() == STOP_TO_PAUSE:
                noticed.set()
                # Still winding down (the tool being killed, the part-file flushed) while
                # somebody presses Resume.
                await asyncio.wait_for(let_go.wait(), timeout=10)
                raise JobPaused("stopped part way through")
            await asyncio.sleep(0.01)

    register_handler("download", handler, name="Test job")
    job_id = await job_queue.enqueue("download")

    pool = WorkerPool(
        job_queue,
        concurrency=1,
        poll_interval=0.01,
        heartbeat_interval=0.01,
        capabilities=SystemCapabilities(
            content=content_store, library=library_store, workspaces=workspaces
        ),
    )
    await pool.start()
    try:
        await asyncio.wait_for(asked.wait(), timeout=10)
        assert await job_queue.pause(job_id) is True
        await asyncio.wait_for(noticed.wait(), timeout=10)
        assert await job_queue.resume(job_id) is True, "the request is withdrawn, not refused"
        let_go.set()
        job = await wait_for_state(job_queue, job_id, JobState.DONE)
    finally:
        await pool.stop()

    assert runs == 2, "landed as queued and claimed again, never as paused"
    assert job.attempts == 1, "the interrupted attempt was handed back"


@pytest.mark.integration
async def test_a_resumed_job_is_claimed_again_and_finds_the_same_workspace(
    job_queue: JobQueue,
    content_store: ContentStore,
    library_store: LibraryStore,
    tmp_path: Path,
) -> None:
    """The other half: the bytes are not merely kept, the next attempt is handed them."""
    workspaces = await _paused_workspaces(tmp_path)
    (workspaces.root / "placeholder").mkdir(parents=True)
    seen: list[bytes] = []

    async def handler(context: JobContext) -> None:
        part = context.workspace / "part"
        if not part.exists():
            part.write_bytes(b"148 of 240")
            raise JobPaused("stopped part way through")
        seen.append(part.read_bytes())

    register_handler("download", handler, name="Test job")
    job_id = await job_queue.enqueue("download")

    pool = WorkerPool(
        job_queue,
        concurrency=1,
        poll_interval=0.01,
        heartbeat_interval=0.01,
        capabilities=SystemCapabilities(
            content=content_store, library=library_store, workspaces=workspaces
        ),
    )
    await pool.start()
    try:
        await wait_for_state(job_queue, job_id, JobState.PAUSED)
        assert await job_queue.resume(job_id) is True
        await wait_for_state(job_queue, job_id, JobState.DONE)
    finally:
        await pool.stop()

    assert seen == [b"148 of 240"]
    assert not workspaces.of(job_id).joinpath("part").exists(), "a finished job kept its workspace"


@pytest.mark.integration
async def test_a_handler_that_ignores_the_ask_still_ends_paused_when_it_stops(
    job_queue: JobQueue,
    content_store: ContentStore,
    library_store: LibraryStore,
    tmp_path: Path,
) -> None:
    """A handler is allowed to ignore `stopping()`. What it may not do is leave the row running.

    This one never looks, and raises for a reason of its own after the pause was asked for. The
    interruption was ours, so the row lands in `paused` rather than being charged a failure, and
    the workspace it was part way through is kept with it.
    """
    workspaces = await _paused_workspaces(tmp_path)
    asked = asyncio.Event()
    let_go = asyncio.Event()

    async def handler(context: JobContext) -> None:
        (context.workspace / "part").write_bytes(b"half")
        asked.set()
        await asyncio.wait_for(let_go.wait(), timeout=10)
        raise RuntimeError("the socket went away")

    register_handler("download", handler, name="Test job")
    job_id = await job_queue.enqueue("download")

    pool = WorkerPool(
        job_queue,
        concurrency=1,
        poll_interval=0.01,
        heartbeat_interval=0.01,
        capabilities=SystemCapabilities(
            content=content_store, library=library_store, workspaces=workspaces
        ),
    )
    await pool.start()
    try:
        await asyncio.wait_for(asked.wait(), timeout=10)
        await job_queue.pause(job_id)
        let_go.set()
        job = await wait_for_state(job_queue, job_id, JobState.PAUSED)
    finally:
        await pool.stop()

    assert job.error is None, "an interruption we asked for is not the job's failure"
    assert (workspaces.of(job_id) / "part").exists()


@pytest.mark.integration
async def test_a_job_that_finishes_before_it_notices_the_ask_is_done(
    job_queue: JobQueue,
    content_store: ContentStore,
    library_store: LibraryStore,
    tmp_path: Path,
) -> None:
    """Finishing is not something a pause undoes, and the workspace goes with the job."""
    workspaces = await _paused_workspaces(tmp_path)
    noop_handler("download")
    job_id = await job_queue.enqueue("download")

    claimed = await job_queue.claim(WORKER)
    assert claimed is not None
    assert await job_queue.pause(job_id) is True  # asked while it runs
    assert await job_queue.complete(job_id, WORKER) is True

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.DONE
    assert workspaces.sweep_all_but(await job_queue.unfinished_among([job_id])) == 0


@pytest.mark.integration
async def test_a_restart_leaves_a_job_that_was_asked_to_pause_paused(job_queue: JobQueue) -> None:
    """Pause it, then quit Sift. The request was written on the row, so the shutdown that hands
    every running job back honours it instead of putting it straight back in the line."""
    noop_handler("download")
    job_id = await job_queue.enqueue("download")
    assert await job_queue.claim(WORKER) is not None
    await job_queue.pause(job_id)

    assert await job_queue.release_running() == [job_id]

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.PAUSED
    assert job.attempts == 0


@pytest.mark.integration
async def test_a_workspace_nothing_is_coming_back_to_is_swept_at_boot(
    job_queue: JobQueue, tmp_path: Path
) -> None:
    """The case no worker can clean up after: a job that ended with nobody running it."""
    workspaces = await _paused_workspaces(tmp_path)
    noop_handler("download")
    living = await job_queue.enqueue("download")
    settled = await job_queue.enqueue("download")
    await job_queue.pause(living)
    await job_queue.cancel(settled)
    workspaces.of(living).joinpath("part").write_bytes(b"kept")
    workspaces.of(settled).joinpath("part").write_bytes(b"gone")

    removed = workspaces.sweep_all_but(await job_queue.unfinished_among([living, settled]))

    assert removed == 1
    assert workspaces.of(living).joinpath("part").exists()
    assert not (workspaces.root / settled / "part").exists()


@pytest.mark.unit
def test_a_workspace_is_refused_a_name_that_would_leave_the_root(tmp_path: Path) -> None:
    """This module deletes directories, so the name it is handed is checked rather than trusted."""
    workspaces = Workspaces(tmp_path / "workspaces")

    for name in ("..", "", "../elsewhere", "nested/below"):
        with pytest.raises(ValueError, match="not a job id"):
            workspaces.of(name)


# --- the queue's smaller doors, each asked on its own -------------------------------------------


@pytest.mark.integration
async def test_stopping_listening_twice_is_stopping_once(job_queue: JobQueue) -> None:
    """A worker that unhooks on its way out and again in its `finally` must not raise."""
    told: list[list[str]] = []
    unlisten = job_queue.listen_for_stops(lambda ids: told.append(list(ids)))
    unlisten()
    unlisten()
    noop_handler("scan")
    job_id = await job_queue.enqueue("scan")

    await job_queue.cancel(job_id)

    assert told == []


@pytest.mark.integration
async def test_a_settled_listener_that_fails_does_not_stop_the_others_or_the_write(
    job_queue: JobQueue,
) -> None:
    heard: list[str] = []

    async def broken(_job_id: str) -> None:
        raise RuntimeError("a scheduler that could not read its settings")

    async def listening(job_id: str) -> None:
        heard.append(job_id)

    job_queue.listen_for_settled("scan", broken)
    job_queue.listen_for_settled("scan", listening)
    noop_handler("scan")
    job_id = await job_queue.enqueue("scan")

    assert await job_queue.cancel(job_id) == [job_id]

    assert heard == [job_id]
    stored = await job_queue.get(job_id)
    assert stored is not None and stored.state is JobState.CANCELED


@pytest.mark.integration
async def test_a_cancel_sift_made_keeps_its_reason_on_the_row_it_named(job_queue: JobQueue) -> None:
    noop_handler("scan")
    noop_handler("thumbnail")
    parent = await job_queue.enqueue("scan")
    child = await job_queue.enqueue("thumbnail", parent_id=parent)
    by_hand = await job_queue.enqueue("scan")

    await job_queue.cancel(parent, why="Sift stopped it so your folder's files can arrive.")
    await job_queue.cancel(by_hand)

    said = [(await job_queue.get(one)) for one in (parent, child, by_hand)]
    assert [one.state.value for one in said if one] == ["canceled"] * 3
    assert [one.error for one in said if one] == [
        "Sift stopped it so your folder's files can arrive.",
        None,
        None,
    ]


@pytest.mark.integration
async def test_a_cancel_says_which_kinds_of_work_it_stopped_before_it_lands(
    job_queue: JobQueue,
) -> None:
    noop_handler("scan")
    noop_handler("thumbnail")
    parent = await job_queue.enqueue("scan")
    await job_queue.enqueue("thumbnail", parent_id=parent)
    stopped: list[set[str]] = []

    await job_queue.cancel(parent, on_canceled=stopped.append)

    assert stopped == [{"scan", "thumbnail"}]


@pytest.mark.unit
async def test_enqueueing_nothing_writes_nothing(job_queue: JobQueue) -> None:
    noop_handler("scan")

    assert await job_queue.enqueue_many("scan", []) == []
    assert (await job_queue.counts()).get("queued", 0) == 0


@pytest.mark.unit
async def test_a_timing_word_the_queue_does_not_know_is_refused(job_queue: JobQueue) -> None:
    noop_handler("scan")

    with pytest.raises(ValueError, match="at must be one of"):
        await job_queue.enqueue("scan", at="whenever")


@pytest.mark.integration
async def test_somebody_is_waiting_only_for_urgent_work_that_is_due(
    job_queue: JobQueue, fake_clock: FakeClock
) -> None:
    """What decides whether background work gives way: urgent work, claimable now. Background
    work, or urgent work held for later, is nobody waiting."""
    queue = JobQueue(job_queue._db, clock=fake_clock.now)
    noop_handler("scan")
    await queue.enqueue("scan", priority=BACKGROUND_PRIORITY)
    later = await queue.enqueue(
        "scan", priority=WAITED_ON_PRIORITY, run_after=int(fake_clock.now()) + 600
    )
    assert await queue.somebody_waiting() is False

    fake_clock.advance(601)
    assert await queue.somebody_waiting() is True
    assert await queue.get(later) is not None


@pytest.mark.integration
async def test_quiet_hours_work_is_counted_for_the_types_asked_about(job_queue: JobQueue) -> None:
    noop_handler("quiet_work")
    noop_handler("other_work")
    await job_queue.enqueue("quiet_work", {})
    await job_queue.enqueue("other_work", {})

    held = await job_queue.held_by_type(frozenset({"quiet_work"}))

    assert held == {"quiet_work": 1}


@pytest.mark.integration
async def test_quiet_hours_work_waiting_is_counted_apart_from_work_already_running(
    job_queue: JobQueue,
) -> None:
    """What has not started is what quiet hours still hold back; a run already going is not."""
    noop_handler("quiet_work")
    await job_queue.enqueue("quiet_work", {"n": 1})
    await job_queue.enqueue("quiet_work", {"n": 2})
    running = await job_queue.claim(WORKER)
    assert running is not None

    asked = frozenset({"quiet_work"})
    assert await job_queue.held_by_type(asked) == {"quiet_work": 2}
    assert await job_queue.held_by_type(asked, waiting_only=True) == {"quiet_work": 1}


@pytest.mark.integration
async def test_withdrawing_when_nothing_is_waiting_takes_back_nothing(job_queue: JobQueue) -> None:
    noop_handler("tidy_up")
    pressed = await job_queue.enqueue("tidy_up", {}, requested_by="someone")

    assert await job_queue.withdraw_waiting("tidy_up") == []
    stored = await job_queue.get(pressed)
    assert stored is not None and stored.state is JobState.QUEUED


@pytest.mark.integration
async def test_settling_into_a_pass_that_is_switched_off_asks_for_nothing(
    job_queue: JobQueue,
) -> None:
    """A file's work asks for the passes that read what it made; one switched off is skipped, and
    the file's work does not fail for having asked."""
    from sift.kernel.jobs import Switch

    noop_handler("dedup_sweep")
    noop_handler("tag_sweep")

    async def off() -> bool:
        return False

    job_queue.switchboard.declare(Switch(key="k", refusal="Off.", on=off), "dedup_sweep")

    await job_queue.settle_into(["dedup_sweep", "tag_sweep"])

    assert (await job_queue.list(job_type="dedup_sweep")).total == 0
    assert (await job_queue.list(job_type="tag_sweep")).total == 1


@pytest.mark.integration
async def test_a_pause_that_lands_after_the_claim_was_lost_is_not_ours(
    job_queue: JobQueue,
) -> None:
    noop_handler("scan")
    await job_queue.enqueue("scan")
    job = await job_queue.claim(WORKER)
    assert job is not None

    assert await job_queue.pause_running(job.id, OTHER_WORKER, "asked to pause") is False
    stored = await job_queue.get(job.id)
    assert stored is not None and stored.state is JobState.RUNNING


@pytest.mark.integration
async def test_a_hold_that_lands_after_the_claim_was_lost_is_not_ours(job_queue: JobQueue) -> None:
    """The same fence every write a worker makes carries: a worker whose claim went cannot put the
    job back in the line under somebody else's feet."""
    noop_handler("face_scan")
    await job_queue.enqueue("face_scan")
    job = await job_queue.claim(WORKER)
    assert job is not None

    assert await job_queue.hold(job.id, OTHER_WORKER, "card gone", retry_in=60) is False
    stored = await job_queue.get(job.id)
    assert stored is not None and stored.state is JobState.RUNNING


@pytest.mark.integration
async def test_the_payloads_still_waiting_are_the_ones_a_press_can_pull_forward(
    job_queue: JobQueue,
) -> None:
    noop_handler("scan")
    await job_queue.enqueue("scan", {"folder": "a"})
    await job_queue.enqueue("scan", {"folder": "b"})
    await job_queue.claim(WORKER)

    waiting = await job_queue.queued_payloads("scan")

    assert len(waiting) == 1 and waiting[0]["folder"] in {"a", "b"}


@pytest.mark.integration
async def test_the_next_scheduled_run_is_the_earliest_waiting_and_none_without_one(
    job_queue: JobQueue,
) -> None:
    noop_handler("tidy_up")
    assert await job_queue.next_scheduled("tidy_up") is None

    await job_queue.enqueue("tidy_up", {}, run_after=2_000_000_000)
    await job_queue.enqueue("tidy_up", {}, run_after=1_900_000_000)

    assert await job_queue.next_scheduled("tidy_up") == 1_900_000_000


@pytest.mark.integration
async def test_the_last_finished_run_is_the_one_before_a_run_still_going(
    job_queue: JobQueue,
) -> None:
    noop_handler("tidy_up")
    assert await job_queue.last_finished_runs([]) == {}
    assert await job_queue.last_finished_runs(["tidy_up"]) == {}

    await job_queue.enqueue("tidy_up", {})
    first = await job_queue.claim(WORKER)
    assert first is not None
    await job_queue.complete(first.id, WORKER)
    await job_queue.enqueue("tidy_up", {})
    assert await job_queue.claim(WORKER) is not None

    last = await job_queue.last_finished_runs(["tidy_up"])

    assert [one.id for one in last.values()] == [first.id]
    assert last["tidy_up"].error is None, "a run that did not fail carries no failure"


@pytest.mark.integration
async def test_a_failed_last_run_carries_why_it_failed(job_queue: JobQueue) -> None:
    """Activity says why the last run of a chore failed; the row's own words are the answer."""
    noop_handler("tidy_up")
    await job_queue.enqueue("tidy_up", {})
    job = await job_queue.claim(WORKER)
    assert job is not None
    await job_queue.fail(job.id, WORKER, "the picture has no frames\nmore detail", permanent=True)

    last = (await job_queue.last_finished_runs(["tidy_up"]))["tidy_up"]

    assert last.state.value == "failed"
    assert last.error is not None and last.error.startswith("the picture has no frames")


@pytest.mark.integration
async def test_the_last_done_run_is_found_past_a_newer_failure(job_queue: JobQueue) -> None:
    """A page that says what its run did reads the last run that finished, not the last attempt."""
    noop_handler("tidy_up")
    await job_queue.enqueue("tidy_up", {})
    done = await job_queue.claim(WORKER)
    assert done is not None
    await job_queue.complete(done.id, WORKER)
    await job_queue.enqueue("tidy_up", {})
    failed = await job_queue.claim(WORKER)
    assert failed is not None
    await job_queue.fail(failed.id, WORKER, "no frames", permanent=True)

    assert (await job_queue.last_finished_runs(["tidy_up"]))["tidy_up"].id == failed.id
    finished = await job_queue.last_finished_runs(["tidy_up"], ended_in=(JobState.DONE,))
    assert finished["tidy_up"].id == done.id
    with pytest.raises(ValueError):
        await job_queue.last_finished_runs(["tidy_up"], ended_in=(JobState.RUNNING,))


@pytest.mark.integration
async def test_a_boot_resumes_what_a_benchmark_paused_and_never_a_persons_pause(
    job_queue: JobQueue,
) -> None:
    noop_handler("download")
    landed, asked, persons, both, waiting = [
        await job_queue.enqueue("download", {"n": n}) for n in range(5)
    ]
    for _ in (landed, asked, persons, both):
        assert await job_queue.claim(WORKER) is not None
    assert await job_queue.pause(landed, for_benchmark=True)
    assert await job_queue.pause_running(landed, WORKER, "stopped")
    assert await job_queue.pause(asked, for_benchmark=True)
    assert await job_queue.pause(waiting, for_benchmark=True)
    assert await job_queue.pause(persons)
    assert await job_queue.pause(both)
    assert not await job_queue.pause(both, for_benchmark=True)

    await recover(job_queue)

    states = [await job_queue.get(one) for one in (landed, asked, waiting, persons, both)]
    assert [None if one is None else one.state for one in states] == [
        JobState.QUEUED,
        JobState.QUEUED,
        JobState.QUEUED,
        JobState.PAUSED,
        JobState.PAUSED,
    ]
    assert await job_queue.resume_after_benchmark() == []


@pytest.mark.integration
async def test_a_handler_hears_a_benchmarks_pause_as_a_pause(job_queue: JobQueue) -> None:
    noop_handler("download")
    job_id = await job_queue.enqueue("download")
    assert await job_queue.claim(WORKER) is not None
    await job_queue.pause(job_id, for_benchmark=True)

    beat = await job_queue.beat(job_id, WORKER)

    assert beat is not None and beat.stop == STOP_TO_PAUSE


@pytest.mark.integration
async def test_a_benchmark_s_hold_pauses_what_the_claim_would_take_a_chunk_at_a_time(
    job_queue: JobQueue,
) -> None:
    probe = noop_handler()
    later = await job_queue.enqueue(probe, {"n": "later"}, run_after=2_000_000_000)
    waiting = [await job_queue.enqueue(probe, {"n": n}) for n in range(5)]
    assert await job_queue.held_by_exclusive() is False

    async def measuring(_context: JobContext) -> None:
        return None

    register_handler("measuring", measuring, name="Measuring this device", exclusive=True)
    await job_queue.enqueue("measuring", {})

    assert await job_queue.held_by_exclusive() is True
    assert await job_queue.due_by_type() == {"measuring": 1}, "what it holds back is not due"
    assert await job_queue.claim(WORKER) is not None
    assert await job_queue.pause_waiting_for_benchmark(chunk=2) == 5
    still = await job_queue.get(later)
    assert still is not None and still.state is JobState.QUEUED, "work due later is not held"
    assert sorted(await job_queue.resume_after_benchmark(chunk=2)) == sorted(waiting)
