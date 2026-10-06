# SPDX-License-Identifier: AGPL-3.0-or-later
"""The off switch over a kind of background work, and whether a family's work can run at all.

Every kind of whole-library pass can be switched off: the walk, the duplicate sweep and the folder
pass as well as the pictures, the faces, the meaning and the marks.

The property worth holding is that a switch stops the work being WRITTEN DOWN. A handler that opens
by reading its own switch and returning does the right thing and costs the wrong amount: the job is
still written, claimed, run and recorded, once per file, for as long as the switch stays off, so a
library would run a face scan per file with recognition off. That is why the import gates sit in
front of the enqueue.
"""

from __future__ import annotations

import asyncio

import pytest

from sift.kernel.jobs import (
    JobContext,
    JobQueue,
    JobState,
    JobSwitchedOff,
    Readiness,
    Switch,
    Switchboard,
    WorkerPool,
    register_handler,
)
from sift.kernel.jobs.families import Family

pytestmark = pytest.mark.usefixtures("clean_handlers")


def _switch(on: bool, *, key: str = "importing.scan", refusal: str = "Scanning is off.") -> Switch:
    async def answer() -> bool:
        return on

    return Switch(key=key, refusal=refusal, on=answer)


def _noop(job_type: str) -> None:
    async def handler(context: JobContext) -> None:
        return None

    register_handler(job_type, handler, name="Test job")


# --- the registry itself ----------------------------------------------------------------------


@pytest.mark.unit
async def test_an_empty_board_allows_everything() -> None:
    """The rule that keeps this from becoming a list every new job must be added to before it runs.

    Work nobody has declared a switch for is on, and a family nobody was asked about is ready.
    """
    board = Switchboard()

    assert await board.refusal("scan") is None
    assert board.switch_of("scan") is None
    assert await board.readiness() == {}


@pytest.mark.unit
async def test_several_job_types_share_one_switch() -> None:
    """One thing to a person, three rows to the queue: the walk, the whole-library pass, the
    catch-up."""
    board = Switchboard()
    board.declare(_switch(False), "scan", "library_scan", "library_reconcile")

    for job_type in ("scan", "library_scan", "library_reconcile"):
        assert await board.refusal(job_type) == "Scanning is off."
    assert await board.refusal("probe") is None, "a type outside the switch was caught by it"


@pytest.mark.unit
async def test_a_switch_that_cannot_be_read_is_treated_as_on() -> None:
    """The one wrong answer that looks like nothing being wrong.

    A broken settings read would otherwise silence every background pass in the application at
    once, with nothing anywhere saying why the library stopped filling.
    """

    async def explode() -> bool:
        raise RuntimeError("the settings table is not there")

    board = Switchboard()
    board.declare(Switch(key="importing.scan", refusal="off", on=explode), "scan")

    assert await board.refusal("scan") is None


@pytest.mark.unit
async def test_a_readiness_that_cannot_be_read_is_left_out_rather_than_called_not_ready() -> None:
    """ "Not known" is the honest answer to a read that did not come back, and a screen that said
    "waiting for the runtime" over it would be inventing a fault."""

    async def fine() -> Readiness:
        return Readiness(ready=True)

    async def explode() -> Readiness:
        raise RuntimeError("no")

    board = Switchboard()
    board.declare_ready(Family.SEMANTIC, fine)
    board.declare_ready(Family.IDENTIFY, explode)

    answers = await board.readiness()

    assert answers == {Family.SEMANTIC: Readiness(ready=True)}
    assert Family.IDENTIFY not in answers


@pytest.mark.unit
async def test_callers_arriving_together_share_one_ask_and_a_later_caller_asks_again() -> None:
    asked = 0
    gate = asyncio.Event()

    async def slow() -> Readiness:
        nonlocal asked
        asked += 1
        await gate.wait()
        return Readiness(ready=True)

    board = Switchboard()
    board.declare_ready(Family.SEMANTIC, slow)
    callers = [asyncio.ensure_future(board.readiness()) for _ in range(8)]
    await asyncio.sleep(0)
    gate.set()
    answers = await asyncio.gather(*callers)

    assert asked == 1
    assert all(one == {Family.SEMANTIC: Readiness(ready=True)} for one in answers)
    answers[0].clear()
    assert await board.readiness() == {Family.SEMANTIC: Readiness(ready=True)}
    assert asked == 2


# --- the queue refuses to write the work down ---------------------------------------------------


async def test_enqueue_refuses_work_that_is_switched_off(job_queue: JobQueue) -> None:
    """Refused where the request is, rather than run and thrown away."""
    _noop("scan")
    job_queue.switchboard.declare(_switch(False), "scan")

    with pytest.raises(JobSwitchedOff, match="Scanning is off"):
        await job_queue.enqueue("scan", {"root_id": "r1"})

    assert (await job_queue.list(job_type="scan")).total == 0, "the job was written down anyway"


async def test_enqueue_allows_the_same_work_once_the_switch_is_on(job_queue: JobQueue) -> None:
    """Read per request and never held, so a change takes effect on the next job rather than the
    next restart. The known positive for the test above."""
    _noop("scan")
    job_queue.switchboard.declare(_switch(True), "scan")

    job_id = await job_queue.enqueue("scan", {"root_id": "r1"})

    assert job_id
    assert (await job_queue.list(job_type="scan")).total == 1


async def test_a_queued_job_is_cancelled_rather_than_run_when_the_switch_moves(
    job_queue: JobQueue,
) -> None:
    """A switch moves while a queue is full, and the enqueue cannot speak for a row already there.

    A pass over a library hands out thousands of jobs, so switching it off has to stop the ones
    already queued or the switch means "stop in an hour". Cancelled and not failed: nothing went
    wrong, and a stopped job belongs in the pile one press clears.
    """
    ran: list[str] = []

    async def handler(context: JobContext) -> None:
        ran.append(context.job.id)

    register_handler("scan", handler, name="Test job")
    job_id = await job_queue.enqueue("scan", {"root_id": "r1"})

    # Queued while it was allowed; switched off before a worker got to it.
    job_queue.switchboard.declare(_switch(False), "scan")
    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    try:
        for _ in range(200):
            job = await job_queue.get(job_id)
            if job is not None and job.state is JobState.CANCELED:
                break
            await _tick()
    finally:
        await pool.stop()

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.state is JobState.CANCELED, f"a switched-off job ended {job.state}"
    assert ran == [], "the handler ran for work that was switched off"


async def _tick() -> None:
    await asyncio.sleep(0.01)


# --- what a screen reads off the board ---------------------------------------------------------


@pytest.mark.unit
async def test_the_switches_are_listed_by_type_and_the_list_is_a_copy() -> None:
    board = Switchboard()
    scan = _switch(True)
    board.declare(scan, "scan_walk", "scan_pass")

    listed = board.switches()
    listed.pop("scan_walk")

    assert board.switches() == {"scan_walk": scan, "scan_pass": scan}


@pytest.mark.unit
async def test_a_window_says_the_hour_it_opens_and_nothing_it_cannot_read() -> None:
    """The hour a held pass says it waits for. Never declared, blank, or unreadable is no hour:
    the sentence then says there is a window, which is true, rather than inventing one."""
    board = Switchboard()
    assert await board.window_opens(Family.IDENTIFY) is None

    async def at_ten() -> str:
        return " 22:00 "

    board.declare_window(Family.IDENTIFY, at_ten)
    assert await board.window_opens(Family.IDENTIFY) == "22:00"

    async def blank() -> str:
        return "  "

    board.declare_window(Family.IDENTIFY, blank)
    assert await board.window_opens(Family.IDENTIFY) is None

    async def unreadable() -> str:
        raise OSError("the settings database is locked")

    board.declare_window(Family.IDENTIFY, unreadable)
    assert await board.window_opens(Family.IDENTIFY) is None


@pytest.mark.unit
async def test_quiet_hours_that_cannot_be_read_hold_nothing_back() -> None:
    """A broken settings read must not silently stop every quiet-hours task in the install."""
    from sift.kernel.jobs.switchboard import NOTHING_HELD

    board = Switchboard()

    async def unreadable() -> object:
        raise OSError("the settings database is locked")

    board.declare_quiet_hours(unreadable)  # type: ignore[arg-type]

    assert await board.quiet_hold() == NOTHING_HELD
