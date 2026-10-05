# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the queue: pages, a family's steps, what is still to come, task runs and the live rows."""

from __future__ import annotations

import asyncio

import pytest

from sift.kernel.db import Database
from sift.kernel.ids import floor_at, new_id
from sift.kernel.jobs import (
    MAX_PAGE_SIZE,
    JobQueue,
    JobState,
    WorkAhead,
    families,
    folded_state,
    queue_controls,
    queue_pages,
    queue_rows,
)
from sift.kernel.jobs import schema as jobs_schema
from sift.kernel.tests.jobs_helpers import (
    OTHER_WORKER,
    WORKER,
    _state,
    noop_handler,
)
from sift.testing.fixtures import FakeClock

pytestmark = pytest.mark.usefixtures("clean_handlers")


# --- reading the queue a page at a time ------------------------------------------------------
#
# The one paged read of the queue there is. It lives here rather than in the screen that draws it
# because the table is the kernel's, and a feature reaching into it directly is how two ideas of
# what a job is start to exist.


async def test_a_page_is_newest_first(job_queue: JobQueue) -> None:
    """A queue is read to find out what is happening now, and that is at the end."""
    first = await job_queue.enqueue("probe", {"n": 1}, require_handler=False)
    second = await job_queue.enqueue("probe", {"n": 2}, require_handler=False)
    third = await job_queue.enqueue("probe", {"n": 3}, require_handler=False)

    page = await job_queue.list()

    assert [job.id for job in page.jobs] == [third, second, first]
    assert page.total == 3


async def test_the_queue_keeps_its_order_when_the_clock_steps_back(temp_db: Database) -> None:
    """Every order the queue has is by id, so a clock that steps backwards reorders nothing.

    The clock here goes back five seconds before each job, which is what a machine correcting its
    time does: each job is written after the one before it with a `created_at` before it. Ordered by
    that column, the list would put the newest last, a family's steps would come backwards and the
    claim would take the newest job first. The ids are minted under a floor that never goes down,
    so they hold.
    """
    clock = FakeClock()
    queue = JobQueue(temp_db, clock=clock.now)
    await temp_db.initialize_schema()
    kind = noop_handler()
    top = await queue.enqueue(kind)
    clock.advance(-5)
    first = await queue.enqueue(kind, parent_id=top)
    clock.advance(-5)
    second = await queue.enqueue(kind, parent_id=top)

    assert [job.id for job in (await queue.list()).jobs] == [second, first, top]
    assert [job.id for job in (await queue.steps(top)).jobs] == [first, second]
    assert [job.id for job in await queue.children(top)] == [first, second]
    claimed = await queue.claim(WORKER)
    assert claimed is not None
    assert claimed.id == top, "the claim took a job queued after the oldest one"
    assert await queue.positions_of([first, second]) == {first: 1, second: 2}


async def test_a_page_can_be_filtered(job_queue: JobQueue) -> None:
    queued = await job_queue.enqueue("probe", {}, require_handler=False)
    thumbnail = await job_queue.enqueue("thumbnail", {}, require_handler=False)
    await job_queue.claim(WORKER)  # the oldest: `queued` is now running

    assert [job.id for job in (await job_queue.list(job_type="thumbnail")).jobs] == [thumbnail]
    assert [job.id for job in (await job_queue.list(state=JobState.RUNNING)).jobs] == [queued]
    assert (await job_queue.list(state=JobState.DONE)).total == 0


async def test_an_omitted_filter_matches_everything(job_queue: JobQueue) -> None:
    """No filter is the ordinary request here, so a missing one is a wildcard on purpose.

    Worth stating out loud because the same shape on a single-object read is a bug: an absent
    request parameter arrives as None, and there it would match any row rather than none.
    """
    await job_queue.enqueue("probe", {}, require_handler=False)
    await job_queue.enqueue("thumbnail", {}, require_handler=False)

    assert (await job_queue.list()).total == 2
    assert (await job_queue.list(state=None, job_type=None, parent_id=None)).total == 2


async def test_a_page_can_be_filtered_to_one_parents_children(job_queue: JobQueue) -> None:
    parent = await job_queue.enqueue("probe", {}, require_handler=False)
    child = await job_queue.enqueue("thumbnail", {}, parent_id=parent, require_handler=False)
    await job_queue.enqueue("probe", {}, require_handler=False)

    page = await job_queue.list(parent_id=parent)

    assert [job.id for job in page.jobs] == [child]
    assert page.total == 1


async def test_the_total_describes_the_filter_and_not_the_page(job_queue: JobQueue) -> None:
    for _ in range(5):
        await job_queue.enqueue("probe", {}, require_handler=False)

    page = await job_queue.list(limit=2)

    assert len(page.jobs) == 2
    assert page.total == 5


async def test_a_page_past_the_end_still_knows_the_total(job_queue: JobQueue) -> None:
    """The count rides on a row, so an empty page would otherwise claim the queue held nothing,
    contradicting the pages that led someone to ask for this one."""
    for _ in range(3):
        await job_queue.enqueue("probe", {}, require_handler=False)

    page = await job_queue.list(limit=2, offset=99)

    assert page.jobs == []
    assert page.total == 3


async def test_an_empty_queue_is_empty_rather_than_a_mistake(job_queue: JobQueue) -> None:
    page = await job_queue.list(offset=10)
    assert page.jobs == []
    assert page.total == 0


async def test_a_page_is_capped_however_much_is_asked_for(job_queue: JobQueue) -> None:
    """The dashboard reads this over a socket every time anything moves, so an unbounded limit is
    a way to make the server do unbounded work per request."""
    for _ in range(5):
        await job_queue.enqueue("probe", {}, require_handler=False)

    page = await job_queue.list(limit=MAX_PAGE_SIZE + 5_000)

    assert len(page.jobs) == 5  # everything there is, and no error


async def test_a_page_that_could_never_hold_anything_is_a_bug(job_queue: JobQueue) -> None:
    with pytest.raises(ValueError, match="at least one row"):
        await job_queue.list(limit=0)
    with pytest.raises(ValueError, match="before the first row"):
        await job_queue.list(offset=-1)


@pytest.mark.integration
async def test_asking_whether_work_is_already_happening(job_queue: JobQueue) -> None:
    """`is_live` is the counterpart to `dedupe`, and it answers a different question.

    Dedupe is asked by somebody who has decided to queue something and does not want two of it, so
    it deliberately ignores a job already running: that one may have passed the change being
    reported. This is asked by somebody deciding whether to queue at all, and there a running job is
    the best possible answer: it is about to record exactly what the caller wants recorded.
    """
    handler = noop_handler()

    assert await job_queue.is_live(handler, {"asset_id": "a"}) is False

    queued = await job_queue.enqueue(handler, {"asset_id": "a"})
    assert await job_queue.is_live(handler, {"asset_id": "a"}) is True
    # A different payload is different work, and identity is the serialised payload rather than a
    # comparison of dictionaries read back, which would agree until two differed only in key
    # order.
    assert await job_queue.is_live(handler, {"asset_id": "b"}) is False

    await job_queue.claim(WORKER)
    assert await job_queue.is_live(handler, {"asset_id": "a"}) is True, (
        "a job under way is still work that is happening"
    )

    await job_queue.complete(queued, WORKER)
    assert await job_queue.is_live(handler, {"asset_id": "a"}) is False


# --- how much of a kind of work is still to happen -------------------------------------------


@pytest.mark.integration
async def test_outstanding_counts_only_the_work_that_has_not_happened(
    job_queue: JobQueue,
) -> None:
    """The question a progress bar has to ask, and the reason it exists.

    "There is work left" and "there is work left AND something is doing it" look identical from a
    count of unfinished FILES: a run stopped halfway leaves exactly as many undone as a run still
    going. Only the queue can tell them apart.
    """
    noop_handler("sweep")

    for number in range(1, 5):
        await job_queue.enqueue("sweep", {"n": number})

    assert await job_queue.outstanding("sweep") == 4

    # Claimed and still going: outstanding, which is the whole distinction this exists to make.
    running = await job_queue.claim(WORKER)
    assert running is not None
    assert await job_queue.outstanding("sweep") == 4

    finished = await job_queue.claim(WORKER)
    assert finished is not None
    assert await job_queue.complete(finished.id, WORKER)
    assert await job_queue.outstanding("sweep") == 3

    waiting = await job_queue.claim(OTHER_WORKER)
    assert waiting is not None
    assert await job_queue.cancel(waiting.id) == [waiting.id]

    # One running and one still queued. Neither has been done.
    assert await job_queue.outstanding("sweep") == 2


@pytest.mark.integration
async def test_outstanding_counts_a_blocked_job_as_work_that_will_happen(
    job_queue: JobQueue,
) -> None:
    """A job waiting on something is not a job that has been done. Counting it as finished is how a
    bar reaches the end while the work is still parked."""
    noop_handler("sweep")
    job_id = await job_queue.enqueue("sweep", {"n": 1})

    claimed = await job_queue.claim(WORKER)
    assert claimed is not None and claimed.id == job_id
    await job_queue.block(job_id, WORKER, "this site needs a login")

    job = await job_queue.get(job_id)
    assert job is not None and job.state == "blocked"
    assert await job_queue.outstanding("sweep") == 1


@pytest.mark.integration
async def test_outstanding_answers_across_several_types_at_once(job_queue: JobQueue) -> None:
    """A feature's progress is the whole of its own work, which is more than one kind of job: the
    pass that queues, and the pieces it queued. Asking per type and adding up would be two round
    trips and two moments, and the second could disagree with the first."""
    noop_handler("sweep")
    noop_handler("describe")
    noop_handler("somebody_else")

    await job_queue.enqueue("sweep", {"n": 1})
    await job_queue.enqueue("describe", {"n": 1})
    await job_queue.enqueue("describe", {"n": 2})
    await job_queue.enqueue("somebody_else", {"n": 1})

    assert await job_queue.outstanding("sweep", "describe") == 3
    # And another feature's queue is not this feature's progress.
    assert await job_queue.outstanding("sweep") == 1


@pytest.mark.integration
async def test_asking_about_no_types_at_all_is_nothing_rather_than_everything(
    job_queue: JobQueue,
) -> None:
    """The dangerous default. An empty list read as "no filter" would count the whole machine's
    queue as this feature's work, and every bar in the app would follow every import."""
    noop_handler("sweep")
    await job_queue.enqueue("sweep", {"n": 1})

    assert await job_queue.outstanding() == 0


@pytest.mark.integration
async def test_a_type_nothing_has_ever_queued_is_zero(job_queue: JobQueue) -> None:
    noop_handler("sweep")
    await job_queue.enqueue("sweep", {"n": 1})

    assert await job_queue.outstanding("never_queued") == 0


@pytest.mark.integration
async def test_unfinished_work_is_counted_per_type_in_one_question(job_queue: JobQueue) -> None:
    """What the machine's budget divides on, and it is read every few seconds.

    One query however many kinds of work exist: asking per type would be a round trip each and a
    different moment each, so the shares would be divided on a picture that never existed.
    """
    noop_handler("sweep")
    noop_handler("describe")

    await job_queue.enqueue("sweep", {"n": 1})
    await job_queue.enqueue("describe", {"n": 1})
    await job_queue.enqueue("describe", {"n": 2})

    assert await job_queue.unfinished_by_type() == {"sweep": 1, "describe": 2}


@pytest.mark.integration
async def test_work_that_has_happened_stops_being_counted(job_queue: JobQueue) -> None:
    """A type whose queue has drained is absent rather than zero, and a type still running counts.

    The distinction is the whole of what the budget divides on: a kind of work with nothing left
    wants none of the machine, and one whose only job is claimed and going wants all it can have.
    """
    noop_handler("sweep")
    await job_queue.enqueue("sweep", {"n": 1})
    await job_queue.enqueue("sweep", {"n": 2})

    running = await job_queue.claim(WORKER)
    assert running is not None
    assert await job_queue.unfinished_by_type() == {"sweep": 2}, "a claimed job stopped counting"

    finished = await job_queue.claim(OTHER_WORKER)
    assert finished is not None
    assert await job_queue.complete(finished.id, OTHER_WORKER)
    assert await job_queue.unfinished_by_type() == {"sweep": 1}

    assert await job_queue.complete(running.id, WORKER)
    assert await job_queue.unfinished_by_type() == {}


@pytest.mark.integration
async def test_a_blocked_job_still_wants_the_machine(job_queue: JobQueue) -> None:
    """Work waiting on something is work that will happen. Dropping it here would take a feature's
    share away the moment one of its jobs parked, and it would never get it back."""
    noop_handler("sweep")
    job_id = await job_queue.enqueue("sweep", {"n": 1})
    claimed = await job_queue.claim(WORKER)
    assert claimed is not None
    await job_queue.block(job_id, WORKER, "this site needs a login")

    assert await job_queue.unfinished_by_type() == {"sweep": 1}


@pytest.mark.integration
async def test_an_empty_queue_answers_nothing_rather_than_failing(job_queue: JobQueue) -> None:
    assert await job_queue.unfinished_by_type() == {}


# --- a family: a top row and everything it started, however deep ----------------------------------
#
# A download is one row that starts a probe that starts seven steps, and Activity folds that into
# one row. These say what the fold reads: which family a row is in, how its steps are counted, and
# the one state the folded row shows: above all, that folding can never hide a failure.


async def _family_of(queue: JobQueue, job_id: str) -> str | None:
    row = await queue._db.fetch_one("SELECT root_id FROM jobs WHERE id = ?", (job_id,))
    assert row is not None
    family = row["root_id"]
    return None if family is None else str(family)


@pytest.mark.integration
async def test_every_row_names_the_top_of_its_family_however_deep(job_queue: JobQueue) -> None:
    """Three levels, which is a download's real shape: the download, its probe, the probe's steps.

    A family read one level down would count a download as one step (its probe), while every
    thumbnail and Smart Search step under the probe was still to come.
    """
    kind = noop_handler()
    top = await job_queue.enqueue(kind, {"download_id": "D"})
    probe = await job_queue.enqueue(kind, {"asset_id": "A"}, parent_id=top)
    thumbnail = await job_queue.enqueue(kind, {"asset_id": "A"}, parent_id=probe)

    assert await _family_of(job_queue, top) == top
    assert await _family_of(job_queue, probe) == top
    assert await _family_of(job_queue, thumbnail) == top


@pytest.mark.integration
async def test_a_family_is_counted_by_state_and_the_top_is_not_a_step(job_queue: JobQueue) -> None:
    """The top is done the moment it has handed its work out, which is exactly why its own state
    says nothing about its steps: they are counted apart from it."""
    kind = noop_handler()
    top = await job_queue.enqueue(kind, {"download_id": "D"})
    probe = await job_queue.enqueue(kind, {"asset_id": "A"}, parent_id=top)
    await job_queue.enqueue(kind, {"asset_id": "A"}, parent_id=probe)
    await _state(job_queue, top, "done")
    await _state(job_queue, probe, "done")

    counted = (await job_queue.step_counts([top]))[top]

    assert counted.by_state == {"done": 1, "queued": 1}
    assert counted.steps == 2
    assert counted.at_least is False


@pytest.mark.integration
async def test_a_top_that_started_nothing_or_is_not_there_has_no_steps(job_queue: JobQueue) -> None:
    """Asked of a job that has been cleared, which is ordinary: an answer of nothing, not an error."""
    lone = await job_queue.enqueue(noop_handler(), {"asset_id": "A"})
    gone = new_id()

    counted = await job_queue.step_counts([lone, gone])

    assert counted[lone].by_state == {} and counted[lone].steps == 0
    assert counted[gone].by_state == {}


@pytest.mark.integration
async def test_a_count_stops_at_the_cap_and_says_so(
    job_queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A whole-library scan's family is the library several times over; counting it exactly on every
    refresh is a walk of all of it. The count stops, and says it is a floor."""
    monkeypatch.setattr(queue_pages, "STEP_COUNT_CAP", 2)
    kind = noop_handler()
    top = await job_queue.enqueue(kind, {"root_id": "R"})
    for index in range(3):
        await job_queue.enqueue(kind, {"asset_id": str(index)}, parent_id=top)

    counted = (await job_queue.step_counts([top]))[top]

    assert counted.by_state == {"queued": 2}
    assert counted.at_least is True


def test_a_failed_step_makes_the_folded_row_failed_even_while_another_runs() -> None:
    """The rule the fold rests on: a family folded to one row never hides a failure inside it."""
    assert folded_state(JobState.DONE, {"failed": 1, "running": 1, "done": 6}) is JobState.FAILED
    assert folded_state(JobState.FAILED, {}) is JobState.FAILED
    assert folded_state(JobState.DONE, {"running": 1, "queued": 3}) is JobState.RUNNING
    assert folded_state(JobState.DONE, {"queued": 1, "done": 7}) is JobState.QUEUED
    assert folded_state(JobState.RUNNING, {}) is JobState.RUNNING


def test_a_family_reads_canceled_only_when_its_top_was_called_off() -> None:
    """A finished download with one step somebody canceled is a finished download."""
    assert folded_state(JobState.DONE, {"canceled": 1, "done": 7}) is JobState.DONE
    assert folded_state(JobState.CANCELED, {"canceled": 8}) is JobState.CANCELED
    assert folded_state(JobState.DONE, {"done": 8}) is JobState.DONE


@pytest.mark.integration
async def test_a_family_names_its_file_only_when_its_steps_agree_on_one(
    job_queue: JobQueue,
) -> None:
    """A download's own payload names the download; its steps name the file it made. A scan's
    steps name hundreds, and naming the first of them would put a wrong name on the row."""
    kind = noop_handler()
    download = await job_queue.enqueue(kind, {"download_id": "D"})
    probe = await job_queue.enqueue(kind, {"asset_id": "A"}, parent_id=download)
    await job_queue.enqueue(kind, {"asset_id": "A"}, parent_id=probe)
    scan = await job_queue.enqueue(kind, {"root_id": "R"})
    await job_queue.enqueue(kind, {"asset_id": "A"}, parent_id=scan)
    await job_queue.enqueue(kind, {"asset_id": "B"}, parent_id=scan)

    assert await job_queue.family_files([download, scan]) == {download: "A"}


@pytest.mark.integration
async def test_a_folded_page_is_a_page_of_families(job_queue: JobQueue) -> None:
    """Fifty rows of downloads, not the six whose eight steps each fill a flat page."""
    kind = noop_handler()
    tops = [await job_queue.enqueue(kind, {"download_id": str(index)}) for index in range(2)]
    for top in tops:
        probe = await job_queue.enqueue(kind, {"asset_id": top}, parent_id=top)
        await job_queue.enqueue(kind, {"asset_id": top}, parent_id=probe)

    folded = await job_queue.list(tops_only=True)
    flat = await job_queue.list()

    assert [job.id for job in folded.jobs] == list(reversed(tops))
    assert folded.total == 2
    assert flat.total == 6


@pytest.mark.integration
async def test_a_page_of_families_counts_each_once_under_the_state_its_row_shows(
    job_queue: JobQueue,
) -> None:
    """Every pairing of a top's own state with a step's, the step two levels down: the tally and a
    state's page read `_FOLDED` in SQL, and each agrees with `folded_state` for every family. Each
    family is under exactly one state, so the states add up to the families listed."""
    kind = noop_handler()
    expected: dict[str, list[str]] = {}
    for own in JobState:
        for step in (None, *JobState):
            top = await job_queue.enqueue(kind, {"download_id": f"{own.value}-{step}"})
            await _state(job_queue, top, own.value)
            if step is not None:
                probe = await job_queue.enqueue(kind, {"asset_id": top}, parent_id=top)
                await _state(job_queue, probe, "done")
                below = await job_queue.enqueue(kind, {"asset_id": top}, parent_id=probe)
                await _state(job_queue, below, step.value)
            shown = folded_state(own, {} if step is None else {"done": 1, step.value: 1})
            expected.setdefault(shown.value, []).append(top)

    every = await job_queue.list(tops_only=True, limit=200)

    assert every.by_state == {state: len(tops) for state, tops in expected.items()}
    assert every.total == sum(every.by_state.values()) == len(JobState) * (len(JobState) + 1)
    for state, tops in expected.items():
        page = await job_queue.list(tops_only=True, folded=JobState(state), limit=200)
        assert sorted(job.id for job in page.jobs) == sorted(tops), state
        assert page.total == len(tops), state
        assert page.by_state == every.by_state, "a state's page narrowed its own tally"
    # A row's own state and a folded one are two questions, and a page of rows has no fold.
    with pytest.raises(ValueError):
        await job_queue.list(folded=JobState.FAILED)
    with pytest.raises(ValueError):
        await job_queue.list(tops_only=True, state=JobState.DONE, folded=JobState.FAILED)
    assert (await job_queue.list()).by_state == {}


@pytest.mark.integration
async def test_work_that_runs_by_itself_is_quiet_only_where_it_heads_its_own_row(
    job_queue: JobQueue,
) -> None:
    """A row of arrival work nobody pressed is left off the list while it waits, runs or is done.

    The other five are the cases that keep it honest: the same work as a step of somebody's run,
    pressed by somebody, failed, or canceled is listed, and a type not named quiet is untouched.
    The total and the tally by state say the rows drawn, and naming the type reads them all.
    """
    quiet = "fingerprint_stash_box"
    done = await job_queue.enqueue(quiet, {}, require_handler=False)
    await _state(job_queue, done, "done")
    waiting = await job_queue.enqueue(quiet, {}, require_handler=False)
    pressed = await job_queue.enqueue(quiet, {}, require_handler=False, requested_by="U1")
    await _state(job_queue, pressed, "done")
    failed = await job_queue.enqueue(quiet, {}, require_handler=False)
    await _state(job_queue, failed, "failed")
    canceled = await job_queue.enqueue(quiet, {}, require_handler=False)
    await _state(job_queue, canceled, "canceled")
    download = await job_queue.enqueue("download", {}, require_handler=False)
    step = await job_queue.enqueue(quiet, {}, parent_id=download, require_handler=False)
    await _state(job_queue, step, "done")

    flat = await job_queue.list(quiet=[quiet])
    listed = {job.id for job in flat.jobs}
    assert listed == {pressed, failed, canceled, download, step}, "the wrong rows are quiet"
    assert flat.total == len(flat.jobs), "the total counts a row the list does not draw"
    folded = await job_queue.list(tops_only=True, quiet=[quiet])
    assert {job.id for job in folded.jobs} == {pressed, failed, canceled, download}
    assert folded.total == 4
    assert (await job_queue.list(state=JobState.DONE, quiet=[quiet])).total == 2
    assert await job_queue.quiet_by_state([quiet]) == {"done": 1, "queued": 1}
    assert await job_queue.quiet_by_state([]) == {}
    # Not asked to be quiet, nothing is; and asked for by type, every row of it is read.
    assert (await job_queue.list()).total == 7
    assert (await job_queue.list(job_type=quiet)).total == 6
    assert waiting not in listed and done not in listed


@pytest.mark.integration
async def test_a_page_can_be_kept_to_the_types_named(job_queue: JobQueue) -> None:
    """The list's "Older tasks": only the rows of the types named, and none for an empty list."""
    gone = await job_queue.enqueue("face_asked_only", {}, require_handler=False)
    await job_queue.enqueue("thumbnail", {}, require_handler=False)

    assert [job.id for job in (await job_queue.list(among=["face_asked_only"])).jobs] == [gone]
    assert (await job_queue.list(among=[])).total == 0
    assert (await job_queue.list(among=None)).total == 2


@pytest.mark.integration
async def test_a_familys_steps_come_in_the_order_they_were_handed_out(job_queue: JobQueue) -> None:
    kind = noop_handler()
    top = await job_queue.enqueue(kind, {"download_id": "D"})
    probe = await job_queue.enqueue(kind, {"asset_id": "A"}, parent_id=top)
    thumbnail = await job_queue.enqueue(kind, {"asset_id": "A"}, parent_id=probe)
    preview = await job_queue.enqueue(kind, {"asset_id": "A"}, parent_id=probe)

    page = await job_queue.steps(top)

    assert [job.id for job in page.jobs] == [probe, thumbnail, preview]
    assert [job.parent_id for job in page.jobs] == [top, probe, probe]
    assert (page.total, page.at_least) == (3, False)
    assert (await job_queue.steps(probe)).jobs == [], "a step heads no family of its own"


@pytest.mark.integration
@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (1, -1)])
async def test_a_page_of_steps_refuses_a_window_that_is_not_one(
    job_queue: JobQueue, limit: int, offset: int
) -> None:
    with pytest.raises(ValueError, match="page"):
        await job_queue.steps(new_id(), limit=limit, offset=offset)


# --- what is still to come, per kind ------------------------------------------------------------
#
# `WorkAhead` is the denominator every progress bar on the Jobs screen divides by. It is registered
# from the composition root, so these ask it directly; each of the three properties below is a
# wrong answer a bar would otherwise draw.


async def test_a_counter_that_fails_is_left_out_rather_than_counted_as_nothing() -> None:
    """The one wrong answer that looks like good news.

    Zero means "nothing left to do". A feature switched off, mid-migration or simply broken would
    otherwise report the library finished, and a bar reading 100% is the last thing anybody
    investigates.
    """
    ahead = WorkAhead()

    async def counts() -> int:
        return 7

    async def refuses() -> int:
        raise RuntimeError("this feature is not built yet")

    ahead.register("thumbnail", counts)
    ahead.register("face_scan", refuses)

    assert await ahead.waiting() == {"thumbnail": 7}


async def test_an_answer_is_reused_rather_than_recounted_on_every_ask() -> None:
    """A count costs real time, and asked several times a second during an import it is spent
    watching a number that moves slowly. The dashboard re-reads whenever the queue moves."""
    asked = 0

    async def counts() -> int:
        nonlocal asked
        asked += 1
        return asked

    ahead = WorkAhead(fresh_for=60.0)
    ahead.register("thumbnail", counts)

    assert await ahead.waiting() == {"thumbnail": 1}
    assert await ahead.waiting() == {"thumbnail": 1}
    assert asked == 1, "the counter was asked again inside its own freshness window"

    # And an answer handed out is a copy: a caller that edits it cannot change what the next one is
    # told, which a shared dict would allow.
    answer = await ahead.waiting()
    answer["thumbnail"] = 999
    assert await ahead.waiting() == {"thumbnail": 1}


async def test_a_stale_answer_is_counted_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half of the window, so the reuse above cannot be a counter asked exactly once. The
    stale answer is handed out as it stands and counted again behind the read, so the new count is
    what the read after it is told."""
    from sift.kernel.jobs import work_ahead

    # A clock that stands still: a count then costs nothing, and a window of nought is always over.
    monkeypatch.setattr(work_ahead, "monotonic", lambda: 1000.0)
    asked = 0

    async def counts() -> int:
        nonlocal asked
        asked += 1
        return asked

    ahead = WorkAhead(fresh_for=0.0)
    ahead.register("thumbnail", counts)

    assert await ahead.waiting() == {"thumbnail": 1}
    assert await ahead.waiting() == {"thumbnail": 1}
    for _ in range(20):
        await asyncio.sleep(0)
    assert await ahead.waiting() == {"thumbnail": 2}


async def test_registering_a_kind_twice_replaces_it() -> None:
    """A composition root may build a feature more than once in a test, and two counters for one
    kind would be one of them answering at random."""
    ahead = WorkAhead(fresh_for=0.0)

    async def first() -> int:
        return 1

    async def second() -> int:
        return 2

    ahead.register("thumbnail", first)
    ahead.register("thumbnail", second)

    assert await ahead.waiting() == {"thumbnail": 2}


async def test_a_counter_that_answers_below_zero_is_read_as_nothing_left() -> None:
    """A count is an anti-join and cannot be negative, so a negative one is a broken counter rather
    than a fact. Floored instead of trusted: a negative denominator draws a bar past its end."""
    ahead = WorkAhead(fresh_for=0.0)

    async def wrong() -> int:
        return -5

    ahead.register("thumbnail", wrong)

    assert await ahead.waiting() == {"thumbnail": 0}


def test_a_run_is_fixed_when_it_begins_so_the_number_cannot_go_backwards() -> None:
    """A total counted in a moving window whose start moved forward as the oldest jobs finished
    would let finished work fall out of the far end and the numerator shrink while the machine was
    working: an "x of total" that keeps resetting."""
    ahead = WorkAhead()

    begun = ahead.run_of("thumbnail", left=100, done_already=0, busy=True)
    assert (begun.total, begun.done, begun.left) == (100, 0, 100)

    # Forty land. The size is the size it was, and `done` is derived by subtraction from a number
    # that only falls, so it cannot go backwards however the queue is counted.
    later = ahead.run_of("thumbnail", left=60, done_already=0, busy=True)
    assert (later.total, later.done, later.left) == (100, 40, 60)


def test_a_screen_opened_halfway_through_shows_the_run_rather_than_its_second_half() -> None:
    """The size is taken once, from what is left plus what the queue says is already done."""
    ahead = WorkAhead()

    run = ahead.run_of("thumbnail", left=30, done_already=70, busy=True)

    assert (run.total, run.done, run.left) == (100, 70, 30)


def test_files_arriving_mid_run_lengthen_it_rather_than_over_counting_what_is_done() -> None:
    """A total below what is left would report more done than there ever was."""
    ahead = WorkAhead()

    ahead.run_of("thumbnail", left=100, done_already=0, busy=True)
    grown = ahead.run_of("thumbnail", left=140, done_already=0, busy=True)

    assert (grown.total, grown.done, grown.left) == (140, 0, 140)


def test_a_kind_that_is_not_busy_has_no_run_and_is_measured_fresh_next_time() -> None:
    """Otherwise the next run is measured against a batch that finished hours ago."""
    ahead = WorkAhead()
    ahead.run_of("thumbnail", left=100, done_already=0, busy=True)

    idle = ahead.run_of("thumbnail", left=12, done_already=0, busy=False)
    assert (idle.total, idle.done, idle.left) == (12, 0, 12)

    # And the forgotten size is not remembered by the next run.
    again = ahead.run_of("thumbnail", left=12, done_already=0, busy=True)
    assert again.total == 12


# --- three reads on the queue, asked on their own -----------------------------------------------


async def test_asking_about_no_families_asks_nothing(job_queue: JobQueue) -> None:
    """An empty page folds to nothing, without a statement built over an empty list."""
    assert await job_queue.step_counts([]) == {}
    assert await job_queue.family_files([]) == {}


async def test_a_kind_with_failures_reports_them_beside_what_it_finished(
    job_queue: JobQueue,
) -> None:
    """The third arm of the tally. A kind whose failures were not counted would read as a kind
    with less work in it than it has."""
    kind = noop_handler("probe")
    job_id = await job_queue.enqueue(kind, {"n": 1})
    # A SECOND JOB, LEFT QUEUED, and it is what makes the run exist at all: `since` is None when
    # nothing is outstanding, and then every kind's run is empty on purpose: the screen says so
    # rather than drawing bars for work that finished days ago.
    #
    # Under a LATER-SORTING kind, so the tally has a row after the failed one whichever way the
    # grouping comes back. One kind's `failed` and `queued` would be two rows in an order nothing
    # promises, and a test that only passes when the failure is not last is a flaky test.
    await job_queue.enqueue(noop_handler("vacuum"), {"n": 2})
    worker = new_id()
    claimed = await job_queue.claim(worker)
    assert claimed is not None and claimed.id == job_id
    await job_queue.fail(job_id, worker, "it would not decode", permanent=True)

    summary = await job_queue.work_summary()

    assert summary.run[kind].failed == 1
    assert summary.states[kind][JobState.FAILED.value] == 1


async def test_a_cancelled_job_is_counted_in_the_tally_and_in_no_run(
    job_queue: JobQueue,
) -> None:
    """The state the run counters deliberately have no arm for.

    Cancelled is not outstanding, not done and not failed: it is work that was asked for and then
    unasked, and adding it to any of the three would make a bar report progress that never
    happened. It still belongs in the raw tally, because "how many rows of this kind are there" is
    a different question from "how is this run going", and a stop leaves a library-sized heap of
    them, which is exactly what the Jobs screen is being asked about at that moment.
    """
    kind = noop_handler("probe")
    stopped = await job_queue.enqueue(kind, {"n": 1})
    await job_queue.cancel(stopped)
    # Outstanding work under a later-sorting kind, so the run exists and the cancelled row is not
    # the last one the tally walks.
    await job_queue.enqueue(noop_handler("vacuum"), {"n": 2})

    summary = await job_queue.work_summary()

    assert summary.states[kind][JobState.CANCELED.value] == 1
    assert summary.run[kind].outstanding == 0
    assert summary.run[kind].done == 0
    assert summary.run[kind].failed == 0


async def test_clearing_cancelled_jobs_when_there_are_none_removes_nothing(
    job_queue: JobQueue,
) -> None:
    """The loop's own exit. It runs up to fifty thousand batches so a request cannot become one
    that never returns, and the first empty batch is what ends it."""
    assert await job_queue.clear_canceled() == 0


async def test_clearing_stops_at_its_ceiling_rather_than_becoming_a_request_that_never_returns(
    job_queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other way out of that loop, and the reason the ceiling is there.

    Fifty thousand batches is far past any real library, so reaching it needs the ceiling lowered
    rather than a library built: what is being pinned is that the loop ENDS on the count, not the
    count itself. Left uncovered it would be a guarantee nothing had ever taken.
    """
    kind = noop_handler()
    for n in range(3):
        job_id = await job_queue.enqueue(kind, {"n": n})
        await job_queue.cancel(job_id)
    monkeypatch.setattr(queue_controls, "_MOST_CLEAR_PASSES", 1)

    removed = await job_queue.clear_canceled(batch=1)

    # One pass, one row, and it returned rather than going round until the heap was gone.
    assert removed == 1
    assert await job_queue.clear_canceled() == 2


async def test_the_tally_by_kind_splits_the_same_counts_one_way_further(
    job_queue: JobQueue,
) -> None:
    """What anything summarising "how far through is Sift" divides by. One query over the whole
    table, because a count of the fifty rows on a screen is a fact about the screen."""
    kind = noop_handler()
    await job_queue.enqueue(kind, {"n": 1})
    await job_queue.enqueue(kind, {"n": 2})

    tally = await job_queue.counts_by_type()

    assert tally[kind][JobState.QUEUED.value] == 2


# --- the runs of a scheduled task ----------------------------------------------------------
#
# The Scheduled tasks screen draws the last few runs of each task, and the job row is the only
# record that says WHICH task ran: the work ledger keeps one run per FAMILY, and every scheduled
# task's job is in the same `other` family as every download and transcode. So these guard the
# two facts that record has to carry: when the work actually began, and whose it was.


@pytest.mark.integration
async def test_a_claim_writes_down_when_the_work_began(temp_db: Database) -> None:
    """`created_at` is when it was ASKED FOR, and for recurring work that is a cadence early.

    A backup's row is written when the previous backup finishes, so a weekly one is created seven
    days before it runs. Nothing else on the row moves at the claim (`updated_at` moves again on
    every progress report), so without this the table cannot say how long any job took.
    """
    clock = FakeClock()
    queue = JobQueue(temp_db, clock=clock.now)
    await temp_db.initialize_schema()
    # The type these tests enqueue, registered here rather than borrowed from another test's
    # registration.
    noop_handler("backup_run")

    await queue.enqueue("backup_run")
    clock.advance(7 * 24 * 60 * 60)
    claimed = await queue.claim(WORKER)

    assert claimed is not None
    assert claimed.started_at == int(clock.now())
    assert claimed.started_at != claimed.created_at


@pytest.mark.integration
async def test_one_tasks_runs_are_not_another_tasks(temp_db: Database) -> None:
    """The whole reason this reads job rows rather than the work ledger's runs."""
    clock = FakeClock()
    queue = JobQueue(temp_db, clock=clock.now)
    await temp_db.initialize_schema()
    # The type these tests enqueue, registered here rather than borrowed from another test's
    # registration.
    noop_handler("backup_run")
    noop_handler("quarantine_prune")

    for job_type in ("backup_run", "quarantine_prune", "quarantine_prune"):
        await queue.enqueue(job_type)
        clock.advance(1)
        job = await queue.claim(WORKER)
        assert job is not None
        clock.advance(1)
        await queue.set_note(job.id, WORKER, f"{job_type} did something")
        await queue.complete(job.id, WORKER)

    runs = await queue.task_runs(["backup_run", "quarantine_prune"])

    assert [run.runs_total for run in runs["backup_run"]] == [1]
    assert len(runs["quarantine_prune"]) == 2
    assert runs["backup_run"][0].note == "backup_run did something"
    assert runs["backup_run"][0].seconds == 1


@pytest.mark.integration
async def test_only_the_newest_few_runs_come_back_and_the_total_says_how_many(
    temp_db: Database,
) -> None:
    """Newest first, capped, with the true count beside it, so "and N more" is not a guess."""
    clock = FakeClock()
    queue = JobQueue(temp_db, clock=clock.now)
    await temp_db.initialize_schema()
    noop_handler("quarantine_prune")

    for which in range(7):
        await queue.enqueue("quarantine_prune")
        clock.advance(60)
        job = await queue.claim(WORKER)
        assert job is not None
        await queue.set_note(job.id, WORKER, f"run {which}")
        await queue.complete(job.id, WORKER)

    runs = (await queue.task_runs(["quarantine_prune"], most=5))["quarantine_prune"]

    assert [run.note for run in runs] == ["run 6", "run 5", "run 4", "run 3", "run 2"]
    assert {run.runs_total for run in runs} == {7}


@pytest.mark.integration
async def test_a_run_still_going_has_no_duration(temp_db: Database) -> None:
    """Null rather than "nought seconds", which on the screen would read as a run that did nothing."""
    clock = FakeClock()
    queue = JobQueue(temp_db, clock=clock.now)
    await temp_db.initialize_schema()
    # The type these tests enqueue, registered here rather than borrowed from another test's
    # registration.
    noop_handler("backup_run")

    await queue.enqueue("backup_run")
    clock.advance(5)
    assert await queue.claim(WORKER) is not None

    (run,) = (await queue.task_runs(["backup_run"]))["backup_run"]

    assert run.state is JobState.RUNNING
    assert run.finished_at is None
    assert run.seconds is None
    assert run.started_at == int(clock.now())


@pytest.mark.integration
async def test_a_waiting_row_taken_back_is_not_a_run(job_queue: JobQueue) -> None:
    """A weekly task switched off withdraws its waiting row; switched on again, the next run must
    not be placed from the withdrawal, nor Activity show it as the last run. A row that never
    started is not a run: the runs of a type are what ran, or is running, and nothing that was only
    waiting."""
    noop_handler("backup_run")

    await job_queue.enqueue("backup_run")
    await job_queue.withdraw_waiting("backup_run")

    assert await job_queue.task_runs(["backup_run"]) == {}


@pytest.mark.integration
async def test_a_task_nothing_has_run_is_absent_rather_than_empty(job_queue: JobQueue) -> None:
    assert await job_queue.task_runs(["backup_run"]) == {}
    assert await job_queue.task_runs([]) == {}


# --- the live rows, counted by the queue rather than read one payload at a time ------------------


@pytest.mark.integration
async def test_live_rows_are_counted_by_the_products_they_name_and_read_as_files(
    job_queue: JobQueue,
) -> None:
    """A run's thousands of tasks name a handful of product lists: the queue answers them grouped,
    and the files they are about as a column, with nothing parsed per row and nothing settled
    counted. A held row says so; a payload whose file is not a name is left out."""
    for asset, products in (("a-1", ["faces"]), ("a-2", ["faces"]), ("a-3", ["faces", "meaning"])):
        await job_queue.enqueue(
            "identify_file", {"asset_id": asset, "products": products}, require_handler=False
        )
    await job_queue.enqueue(
        "identify_file",
        {"asset_id": 7, "products": ["faces"]},
        require_handler=False,
        at="quiet",
    )
    finished = await job_queue.enqueue(
        "identify_file", {"asset_id": "a-9", "products": ["faces"]}, require_handler=False
    )
    async with job_queue._writing() as connection:
        await connection.execute("UPDATE jobs SET state = 'done' WHERE id = ?", (finished,))

    lines = await job_queue.live_products(["identify_file", "generate_file"])

    counted = {(line.products, line.quiet): line.count for line in lines}
    assert counted == {
        (("faces",), False): 2,
        (("faces", "meaning"), False): 1,
        (("faces",), True): 1,
    }
    assert {line.state for line in lines} == {JobState.QUEUED}
    assert sorted(await job_queue.live_asset_ids("identify_file")) == ["a-1", "a-2", "a-3"]
    await job_queue.enqueue("face_scan", {"asset_id": "a-1"}, require_handler=False)
    assert sorted(await job_queue.live_files()) == ["a-1", "a-2", "a-3"], "any type, each once"
    assert await job_queue.live_products([]) == []
    newest = await job_queue.newest_of("identify_file", limit=2)
    assert [one.id for one in newest] == sorted((one.id for one in newest), reverse=True)
    assert newest[0].id == finished, "every state, newest first"


@pytest.mark.integration
async def test_the_live_rows_say_which_are_a_press_over_some_files_and_a_press_reads_whole(
    job_queue: JobQueue,
) -> None:
    """Run task over two files is two rows named for the presser, each about a file, and what a
    pressed row hands out is the press's work too. A pass over the library is topped by a page
    about no file, and a file arriving names nobody: neither is a press. Every row of the presses
    since a moment reads back live or finished, so their bar is done of total; a cancelled one
    and an older press are not in it."""
    older = await job_queue.enqueue(
        "identify_file",
        {"asset_id": "a-0", "products": ["faces"]},
        require_handler=False,
        requested_by="u1",
    )
    async with job_queue._writing() as connection:
        # Made ten minutes before the others, as its id would say.
        await connection.execute(
            "UPDATE jobs SET state = 'done', id = ?, root_id = ? WHERE id = ?",
            (floor_at(1_000), floor_at(1_000), older),
        )
    pressed = [
        await job_queue.enqueue(
            "identify_file",
            {"asset_id": asset, "products": ["faces"], "again": True},
            require_handler=False,
            requested_by="u1",
        )
        for asset in ("a-1", "a-2", "a-3", "a-4")
    ]
    child = await job_queue.enqueue(
        "thumbnail", {"asset_id": "a-1"}, parent_id=pressed[0], require_handler=False
    )
    await job_queue.enqueue(
        "thumbnail", {"asset_id": "a-4"}, parent_id=pressed[3], require_handler=False
    )
    page = await job_queue.enqueue(
        "identify", {"products": ["faces"], "files": 9}, require_handler=False, requested_by="u1"
    )
    await job_queue.enqueue(
        "identify_file",
        {"asset_id": "b-1", "products": ["faces"]},
        parent_id=page,
        require_handler=False,
    )
    await job_queue.enqueue("face_scan", {"asset_id": "c-1"}, require_handler=False)
    # A task's Run now over one library folder: its page names the folder, and its tasks are its.
    narrowed = await job_queue.enqueue(
        "identify",
        {"products": ["faces"], "files": 2, "roots": ["r-1"]},
        require_handler=False,
        requested_by="u1",
    )
    made = await job_queue.enqueue(
        "identify_file",
        {"asset_id": "d-1", "products": ["faces"]},
        parent_id=narrowed,
        require_handler=False,
    )
    await job_queue.enqueue(
        "identify_file",
        {"asset_id": "d-2", "products": ["faces"]},
        parent_id=narrowed,
        require_handler=False,
    )
    async with job_queue._writing() as connection:
        await connection.execute("UPDATE jobs SET state = 'done' WHERE id = ?", (made,))
        await connection.execute("UPDATE jobs SET state = 'done' WHERE id = ?", (pressed[1],))
        await connection.execute("UPDATE jobs SET state = 'failed' WHERE id = ?", (pressed[2],))
        await connection.execute("UPDATE jobs SET state = 'canceled' WHERE id = ?", (child,))

    types = ["identify_file", "identify", "thumbnail", "face_scan"]
    lines = await job_queue.live_by_press(types)

    split = {
        (line.type, line.pressed, line.folders, line.products, line.again): line.count
        for line in lines
    }
    assert split == {
        ("identify_file", True, False, ("faces",), True): 2,
        ("thumbnail", True, False, (), False): 1,
        ("identify", False, False, ("faces",), False): 1,
        ("identify_file", False, False, ("faces",), False): 1,
        ("face_scan", False, False, (), False): 1,
        ("identify", False, True, ("faces",), False): 1,
        ("identify_file", False, True, ("faces",), False): 1,
    }
    since = min(line.since for line in lines if line.pressed)
    whole = await job_queue.pressed_since(["identify_file", "thumbnail", "identify"], since)
    read = {(row.type, row.state, row.folders): row.count for row in whole}
    assert read == {
        ("identify_file", JobState.QUEUED, False): 2,
        ("identify_file", JobState.DONE, False): 1,
        ("identify_file", JobState.FAILED, False): 1,
        ("thumbnail", JobState.QUEUED, False): 1,
        ("identify_file", JobState.QUEUED, True): 1,
        ("identify_file", JobState.DONE, True): 1,
    }, "the press and the run over a folder whole, no page, no cancelled step, no older press"
    assert all(
        row.again and row.products == ("faces",)
        for row in whole
        if row.type != "thumbnail" and not row.folders
    )
    assert await job_queue.live_by_press([]) == []
    assert await job_queue.pressed_since([], since) == []
    async with job_queue._writing() as connection:
        await connection.execute("UPDATE jobs SET state = 'done' WHERE id = ?", (narrowed,))
    tops = await job_queue.live_tops(["identify_file"])
    assert {"products": ["faces"], "files": 2, "roots": ["r-1"]} in tops, (
        "a run's first page answers for it while its tasks go, though it has finished"
    )
    assert await job_queue.live_tops([]) == []


#: The table as it was before version 15, with none of its indexes.
_BEFORE_THE_WALKS_KINDS = jobs_schema._CREATE_TABLE.replace(",\n  to_read      TEXT", "")


@pytest.mark.integration
async def test_a_queue_from_before_the_type_indexes_gets_them_at_boot(temp_db: Database) -> None:
    """Version 13: a library at 12 is given the two indexes the live questions read through, and
    keeps its rows; a new one is made with them."""
    async with temp_db.write() as connection:
        await connection.execute("DROP TABLE IF EXISTS jobs")
        await connection.execute(_BEFORE_THE_WALKS_KINDS)  # nosemgrep: sift-no-string-built-sql
        await connection.execute(
            "INSERT INTO jobs (id, type, payload, created_at, updated_at, root_id)"
            " VALUES ('J1', 'probe', '{}', 1, 1, 'J1')"
        )
        await jobs_schema.initialize(connection, on_disk=12)

    names = {
        row["name"]
        for row in await temp_db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'jobs'"
        )
    }
    assert {"ix_jobs_by_type", "ix_jobs_unpressed_tops"} <= names
    assert [row["id"] for row in await temp_db.fetch_all("SELECT id FROM jobs")] == ["J1"]


# --- what each kind of work has outstanding, for the budget and the Tasks rows -------------------


async def test_the_live_rows_of_each_kind_are_counted_by_state_and_finished_ones_are_not(
    job_queue: JobQueue,
) -> None:
    probe = noop_handler()
    scan = noop_handler("scan")
    running = await job_queue.enqueue(probe, {"n": 1})
    await job_queue.enqueue(probe, {"n": 2})
    blocked = await job_queue.enqueue(scan, {"n": 3})
    done = await job_queue.enqueue(scan, {"n": 4})
    await _state(job_queue, running, "running")
    await _state(job_queue, blocked, "blocked")
    await _state(job_queue, done, "done")

    assert await job_queue.live_by_type() == {
        probe: {"queued": 1, "running": 1},
        scan: {"blocked": 1},
    }


async def test_work_due_now_leaves_out_a_run_set_for_later(temp_db: Database) -> None:
    """The work ledger closes a family's run when nothing of it is due, so a timed task's next
    run, queued for tomorrow, must not keep today's run open."""
    clock = FakeClock(1_000)
    queue = JobQueue(temp_db, clock=clock.now)
    await temp_db.initialize_schema()
    probe = noop_handler()
    await queue.enqueue(probe, {"n": 1})
    await queue.enqueue(probe, {"n": 2}, run_after=5_000)
    later = noop_handler("tidy")
    await queue.enqueue(later, {}, run_after=5_000)

    assert await queue.due_by_type() == {probe: 1}
    clock.advance(4_001)
    assert await queue.due_by_type() == {probe: 2, later: 1}


async def test_a_page_leaving_out_the_upkeep_leaves_it_out_of_the_total_too(
    job_queue: JobQueue,
) -> None:
    """Activity does not list the background upkeep, and a total that still counted it would
    promise rows the list never draws."""
    probe = noop_handler()
    prune = noop_handler("prune")
    shown = await job_queue.enqueue(probe, {"n": 1})
    await job_queue.enqueue(prune, {"n": 2})
    await job_queue.enqueue(prune, {"n": 3})

    page = await job_queue.list(leaving_out=[prune])

    assert [job.id for job in page.jobs] == [shown]
    assert page.total == 1
    assert (await job_queue.list()).total == 3


async def test_the_top_of_a_job_that_is_gone_is_nothing(job_queue: JobQueue) -> None:
    top = await job_queue.enqueue(noop_handler(), {}, requested_by="someone")

    assert await job_queue.top_of(top) == ("probe", "someone")
    assert await job_queue.top_of(new_id()) is None


def test_a_kind_of_work_that_says_its_own_time_left_is_asked_and_one_that_does_not_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Work that is not priced (a swap waits on another person) says where it stands itself."""
    monkeypatch.setattr(families, "_OWN_ESTIMATES", {})
    families.estimate_itself("swapping", lambda: families.OwnEstimate(seconds=90))

    assert families.own_estimate("swapping") == families.OwnEstimate(seconds=90)
    assert families.own_estimate("probe") is None


@pytest.mark.parametrize(
    ("stored", "named"),
    [('["faces", "thumbnails"]', ("faces", "thumbnails")), ("not json", ()), (None, ())],
)
def test_the_products_a_grouped_row_names_are_read_and_anything_else_names_none(
    stored: object, named: tuple[str, ...]
) -> None:
    assert queue_rows._products_named(stored) == named


# --- what the walks still have to read --------------------------------------------------------


async def _walk(
    queue: JobQueue, payload: dict[str, object], *, units: int, to_read: str | None
) -> str:
    job_id = await queue.enqueue("walk", payload, require_handler=False)
    async with queue._db.write() as connection:
        await connection.execute(
            "UPDATE jobs SET units = ?, to_read = ? WHERE id = ?", (units, to_read, job_id)
        )
    return job_id


async def test_the_walks_files_to_read_are_shared_out_by_kind_and_the_uncounted_told(
    job_queue: JobQueue,
) -> None:
    await _walk(job_queue, {"root_id": "r-1"}, units=1000, to_read='{"video": 100, "image": 900}')
    half = await _walk(job_queue, {"root_id": "r-2"}, units=200, to_read='{"video": 200}')
    await _walk(job_queue, {"root_id": "r-3"}, units=1, to_read=None)
    await _walk(job_queue, {"root_id": "r-4", "paths": ["a.mp4"]}, units=1, to_read=None)
    later = await _walk(job_queue, {"root_id": "r-5"}, units=1, to_read=None)
    paused = await _walk(job_queue, {"root_id": "r-6"}, units=50, to_read='{"gif": 50}')
    await _walk(job_queue, {"root_id": "r-7", "asset_id": "A"}, units=1, to_read='{"gif": 9}')
    async with job_queue._db.write() as connection:
        await connection.execute(
            "UPDATE jobs SET state = 'running', progress = 0.5 WHERE id = ?", (half,)
        )
        await connection.execute(
            "UPDATE jobs SET run_after = ? WHERE id = ?", (int(job_queue._now()) + 3600, later)
        )
        await connection.execute("UPDATE jobs SET state = 'paused' WHERE id = ?", (paused,))

    unread = await job_queue.files_to_read(["walk"])

    assert unread.by_kind == {"video": 200.0, "image": 900.0}
    assert unread.uncounted == 1, "a named-path walk and one not due yet are not waited on"
    assert (await job_queue.files_to_read(["probe"])).by_kind == {}


async def test_a_counted_walk_still_waiting_has_all_it_counted_left(job_queue: JobQueue) -> None:
    job_id = await _walk(job_queue, {"root_id": "r-1"}, units=1, to_read=None)
    assert await job_queue.set_to_read(job_id, {"video": 100, "image": 200})

    unread = await job_queue.files_to_read(["walk"])

    assert unread.by_kind == {"video": 100.0, "image": 200.0}
    assert unread.uncounted == 0


async def test_a_walks_kinds_are_written_while_it_waits_or_by_the_worker_holding_it(
    job_queue: JobQueue,
) -> None:
    job_id = await _walk(job_queue, {"root_id": "r-1"}, units=3, to_read=None)

    assert await job_queue.set_to_read(job_id, {"video": 3})
    async with job_queue._db.write() as connection:
        await connection.execute(
            "UPDATE jobs SET state = 'running', claimed_by = ? WHERE id = ?", (OTHER_WORKER, job_id)
        )
    assert not await job_queue.set_to_read(job_id, {"video": 1}), "a waiting row's count only"
    assert not await job_queue.set_to_read(job_id, {"video": 1}, worker_id=WORKER)
    assert await job_queue.set_to_read(job_id, {"video": 2}, worker_id=OTHER_WORKER)
    (row,) = await job_queue._db.fetch_all("SELECT to_read FROM jobs WHERE id = ?", (job_id,))
    assert row["to_read"] == '{"video": 2}'


@pytest.mark.integration
async def test_a_queue_from_before_the_walks_kinds_gets_the_column_and_keeps_its_rows(
    temp_db: Database,
) -> None:
    async with temp_db.write() as connection:
        await connection.execute("DROP TABLE IF EXISTS jobs")
        await connection.execute(_BEFORE_THE_WALKS_KINDS)  # nosemgrep: sift-no-string-built-sql
        await connection.execute(
            "INSERT INTO jobs (id, type, payload, created_at, updated_at, root_id, units)"
            " VALUES ('J1', 'scan', '{\"root_id\": \"r\"}', 1, 1, 'J1', 40)"
        )
        await jobs_schema.initialize(connection, on_disk=14)

    rows = await temp_db.fetch_all("SELECT id, units, to_read FROM jobs")
    assert [(row["id"], row["units"], row["to_read"]) for row in rows] == [("J1", 40, None)]
    columns = await temp_db.fetch_all("SELECT name FROM pragma_table_info('jobs')")
    assert [row["name"] for row in columns].count("to_read") == 1
