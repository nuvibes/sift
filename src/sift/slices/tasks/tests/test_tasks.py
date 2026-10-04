# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tasks, read back and run, and the device kept awake for quiet hours, only then.

The power request is proved against a FAKE of the system call, which is the only honest way to
prove it off Windows and the right way on it: what matters is which flags were asked for and when,
and a real request would keep the test machine awake.
"""

from __future__ import annotations

from datetime import datetime

import pytest

import sift.main  # noqa: F401 (every task, and every schema, is declared by importing it)
from sift.kernel.access import Role, Viewer
from sift.kernel.db import Database
from sift.kernel.jobs import JobContext, JobQueue, JobState, register_handler, registered_handlers
from sift.kernel.jobs.failure_words import in_plain_words
from sift.kernel.jobs.quiet_hours import AT_NOW, AT_QUIET, WHEN_PRESS, WHEN_QUIET, WHEN_WORK
from sift.kernel.jobs.schedules import get_schedule, when_key
from sift.kernel.settings_registry import get_registered
from sift.slices.backup import EVERY_DAYS_KEY
from sift.slices.tasks import (
    EVERYTHING,
    FROM_KEY,
    KEEP_AWAKE_KEY,
    TASK_DRY_RUN,
    UNTIL_KEY,
    KeepAwake,
    NotAPart,
    Plan,
    PlanLine,
    Selection,
    TaskPart,
    TaskParts,
    TaskRefused,
    TasksService,
    UnknownTask,
    register_handlers,
)
from sift.slices.tasks.power import ES_CONTINUOUS, ES_SYSTEM_REQUIRED
from sift.slices.tasks.service import LastRun, TaskState
from sift.wiring.tasks import IDENTIFY_PRODUCTS, TASK_ORDER, rows_products

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("clean_handlers")]

#: Every task whose Run now is a counted pass, and so needs a starter handed in.
_STARTERS = (
    "generate",
    "identify",
    "faces",
    "smart-search",
    "watermarks",
    "music",
    "music-lookup",
    "enrichment",
)


class Calls:
    """SetThreadExecutionState, recorded rather than made."""

    def __init__(self) -> None:
        self.flags: list[int] = []

    def __call__(self, flags: int) -> int:
        self.flags.append(flags)
        return 1


class Settings:
    """The app settings the service reads, held by the test; defaults from the registry."""

    def __init__(self) -> None:
        self.stored: dict[str, object] = {}

    async def get(self, key: str) -> object:
        if key in self.stored:
            return self.stored[key]
        declared = get_registered(key)
        return None if declared is None else declared.default


def _clock_at(hour: int) -> float:
    return datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0).timestamp()


def _service(
    queue: JobQueue, database: Database, values: Settings, calls: Calls, hour: int
) -> TasksService:
    return TasksService(
        queue=queue,
        read=values.get,
        database=database,
        ledger=None,
        governs={"generate": ("thumbnail",)},
        starters={one: _nothing for one in _STARTERS},
        keep_awake=KeepAwake(calls),
        order=TASK_ORDER,
        now=lambda: _clock_at(hour),
    )


async def _nothing(at: str, viewer: Viewer, only: Selection = EVERYTHING) -> list[str]:
    return []


@pytest.fixture
async def queue(temp_db: Database) -> JobQueue:
    await temp_db.initialize_schema()
    return JobQueue(temp_db)


async def test_the_request_is_held_only_while_quiet_hours_have_work(
    queue: JobQueue, temp_db: Database
) -> None:
    """Taken with the system flag and never the display's; withdrawn when the work is gone, when
    the range closes, and when the person says no."""
    values = Settings()
    values.stored[when_key("backup")] = WHEN_QUIET
    calls = Calls()
    night = _service(queue, temp_db, values, calls, hour=1)

    assert await night.keep_awake_once() is False, "no quiet-hours work, no request"
    assert calls.flags == []

    await queue.enqueue("backup_run", {}, require_handler=False)
    assert await night.keep_awake_once() is True
    assert calls.flags == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED], "system required, never display"
    assert await night.keep_awake_once() is True
    assert len(calls.flags) == 1, "taken once, not again every look"

    values.stored[KEEP_AWAKE_KEY] = False
    assert await night.keep_awake_once() is False
    assert calls.flags[-1] == ES_CONTINUOUS, "withdrawn with the continuous flag alone"

    values.stored[KEEP_AWAKE_KEY] = True
    day = _service(queue, temp_db, values, calls, hour=13)
    assert await day.keep_awake_once() is False, "and never outside the range"


async def test_tomorrows_run_waiting_in_the_queue_keeps_nothing_awake(
    queue: JobQueue, temp_db: Database
) -> None:
    """A timed task's next run is queued the moment its last one ends, to wait for TOMORROW's
    range. Counted as quiet-hours work, it would hold the device awake all night after the backup
    had finished: only work due before the range closes keeps it awake."""
    values = Settings()
    values.stored[when_key("backup")] = WHEN_QUIET
    calls = Calls()
    night = _service(queue, temp_db, values, calls, hour=1)

    await queue.enqueue(
        "backup_run", {}, run_after=int(_clock_at(1)) + 22 * 3600, require_handler=False
    )
    assert await night.keep_awake_once() is False, "tomorrow night's backup is not tonight's work"
    assert calls.flags == []

    await queue.enqueue(
        "backup_run", {"retry": 1}, run_after=int(_clock_at(2)), require_handler=False
    )
    assert await night.keep_awake_once() is True, "a run due before seven is"


async def test_the_tasks_are_read_in_the_order_the_work_happens(
    queue: JobQueue, temp_db: Database
) -> None:
    """Not the order the slices were imported in, which would open the screen on Identify faces
    and Automatic backup with Scan fifth: the order handed in, which names every task once."""
    service = _service(queue, temp_db, Settings(), Calls(), hour=13)
    viewer = Viewer(id="admin-1", role=Role.ADMIN)

    ids = [state.task.id for state in await service.states(viewer)]

    assert ids == list(TASK_ORDER)
    assert ids[:3] == ["scan", "generate", "identify"]

    with pytest.raises(ValueError, match="unplaced backup"):
        TasksService(
            queue=queue,
            read=Settings().get,
            database=temp_db,
            ledger=None,
            governs={},
            starters={one: _nothing for one in _STARTERS},
            keep_awake=KeepAwake(Calls()),
            order=[one for one in TASK_ORDER if one != "backup"],
        )


async def test_work_held_to_quiet_hours_is_the_work_of_tasks_set_to_them(
    queue: JobQueue, temp_db: Database
) -> None:
    values = Settings()
    values.stored[when_key("generate")] = WHEN_QUIET
    values.stored[when_key("scan")] = WHEN_WORK
    service = _service(queue, temp_db, values, Calls(), hour=13)

    hold = await service.quiet_hold()

    assert hold.open is False
    assert {"generate", "thumbnail"} <= set(hold.types), "the task's own type and what it governs"
    assert "library_scan" not in hold.types
    values.stored[FROM_KEY] = "12:00"
    values.stored[UNTIL_KEY] = "14:00"
    assert (await service.quiet_hold()).open is True


async def test_a_press_runs_whatever_the_task_is_set_to(queue: JobQueue, temp_db: Database) -> None:
    """Only when I press it is exactly the case a press exists for; the Scan runs now, marked as a
    press, and "Run during quiet hours" says when the range opens."""
    values = Settings()
    values.stored[when_key("scan")] = WHEN_PRESS
    service = _service(queue, temp_db, values, Calls(), hour=13)
    viewer = Viewer(id="admin-1", role=Role.ADMIN)

    async def walk(context: JobContext) -> None:
        return None

    register_handler("library_scan", walk, name="Scanning")

    later_ids, starts = await service.run("scan", at="quiet", viewer=viewer)
    job = await queue.get(later_ids[0])
    assert job is not None and job.timing == "quiet" and job.state is JobState.QUEUED
    assert starts == int(_clock_at(23))

    # Pressed for now while that one waits: the same walk, pulled forward (a press onto waiting
    # work collapses; see the test below), and never the other way round.
    now_ids, starts = await service.run("scan", at="now", viewer=viewer)
    assert starts is None
    job = await queue.get(now_ids[0])
    assert job is not None and job.timing == "now" and job.requested_by == "admin-1"


async def test_a_second_press_collapses_onto_the_first(queue: JobQueue, temp_db: Database) -> None:
    """Two presses of Scan now are one walk of the library, not two. The second hands back the
    first's job, and a press onto a run waiting for quiet hours pulls it forward to now."""
    values = Settings()
    values.stored[when_key("scan")] = WHEN_PRESS
    service = _service(queue, temp_db, values, Calls(), hour=13)
    viewer = Viewer(id="admin-1", role=Role.ADMIN)

    async def walk(context: JobContext) -> None:
        return None

    register_handler("library_scan", walk, name="Scanning")

    quiet_ids, _ = await service.run("scan", at="quiet", viewer=viewer)
    now_ids, _ = await service.run("scan", at="now", viewer=viewer)
    again_ids, _ = await service.run("scan", at="now", viewer=viewer)

    assert now_ids == quiet_ids == again_ids, "every press is the one walk already waiting"
    job = await queue.get(quiet_ids[0])
    assert job is not None and job.timing == "now" and job.state is JobState.QUEUED
    page = await queue.list(job_type="library_scan")
    assert len(page.jobs) == 1


async def test_at_quiet_hours_on_a_run_already_going_says_it_is_not_waiting(
    queue: JobQueue, temp_db: Database
) -> None:
    """Run during quiet hours pressed on a Scan already running must not answer "It starts at
    11:00 PM" over a walk that is under way. The answer is the row's: running or pulled forward,
    no hour."""
    values = Settings()
    values.stored[when_key("scan")] = WHEN_PRESS
    service = _service(queue, temp_db, values, Calls(), hour=13)
    viewer = Viewer(id="admin-1", role=Role.ADMIN)

    async def walk(context: JobContext) -> None:
        return None

    register_handler("library_scan", walk, name="Scanning")

    held_ids, starts = await service.run("scan", at="quiet", viewer=viewer)
    assert starts == int(_clock_at(23)), "a row waiting for the range says when it opens"
    await service.run("scan", at="now", viewer=viewer)
    same, starts = await service.run("scan", at="quiet", viewer=viewer)
    assert same == held_ids and starts is None, "pulled forward: it is not waiting any more"

    # Once it is RUNNING a press does not join it (a walk already under way built its list before
    # the press): a second walk is queued for the range, and saying when it starts is then true.
    assert await queue.claim("a-worker") is not None
    later, starts = await service.run("scan", at="quiet", viewer=viewer)
    assert later != held_ids and starts == int(_clock_at(23))


async def test_every_task_is_read_back_with_its_when(queue: JobQueue, temp_db: Database) -> None:
    values = Settings()
    service = _service(queue, temp_db, values, Calls(), hour=13)
    viewer = Viewer(id="admin-1", role=Role.ADMIN)

    states = {state.task.id: state for state in await service.states(viewer)}

    # Backups run only when pressed out of the box; a person turns them on.
    assert states["backup"].when == WHEN_PRESS
    # Generate starts as soon as there is work, so a new library is usable without a press; music
    # fingerprints are the one pass that waits to be asked for.
    assert states["generate"].when == WHEN_WORK
    assert states["music"].when == WHEN_PRESS
    assert states["update-check"].when == WHEN_WORK
    assert states["backup"].last is None


async def test_a_task_whose_run_is_one_job_says_when_it_last_ran(
    queue: JobQueue, temp_db: Database
) -> None:
    """Find duplicate files writes no line of its own and is no long pass, so the work ledger cannot
    say which task ran, and read from there it would say "never" while Activity showed its run. It
    is read from the job row, the way Activity's housekeeping reads it."""
    from sift.kernel.jobs.schedules import get_schedule

    task = get_schedule("duplicates")
    assert task is not None and task.job_type is not None and not task.records_runs
    service = _service(queue, temp_db, Settings(), Calls(), hour=13)
    viewer = Viewer(id="admin-1", role=Role.ADMIN)
    assert {s.task.id: s for s in await service.states(viewer)}["duplicates"].last is None

    async def look(ctx: JobContext) -> None:
        return None

    register_handler(task.job_type, look, name="Looking for duplicates")
    await queue.enqueue(task.job_type, {})
    job = await queue.claim("worker-1")
    assert job is not None and job.type == task.job_type
    await queue.complete(job.id, "worker-1")

    last = {s.task.id: s for s in await service.states(viewer)}["duplicates"].last
    assert last is not None
    assert last.outcome == "done"


async def test_the_update_check_writes_no_history_line_and_still_says_when_it_last_ran(
    queue: JobQueue, temp_db: Database
) -> None:
    """The update check runs at every start and every few hours, and a line in the history for
    each run would say nothing a person did or owns. It records no runs, and its "last ran" is its
    job row, which the prune keeps as the type's newest run."""
    from sift.kernel.access.history_events import events_of_entity
    from sift.kernel.jobs.schedules import get_schedule, registered_schedules

    task = get_schedule("update-check")
    assert task is not None and task.job_type is not None
    # As the composition root does (`sift.wiring.tasks.build_tasks`).
    for one in registered_schedules().values():
        if one.records_runs and one.job_type is not None:
            queue.record_runs_of(one.job_type, task_id=one.id, title=one.title)
    service = _service(queue, temp_db, Settings(), Calls(), hour=13)
    viewer = Viewer(id="admin-1", role=Role.ADMIN)

    async def check(ctx: JobContext) -> None:
        return None

    register_handler(task.job_type, check, name="Checking for a new version")
    await queue.enqueue(task.job_type, {})
    job = await queue.claim("worker-1")
    assert job is not None and job.type == task.job_type
    await queue.complete(job.id, "worker-1")

    last = {s.task.id: s for s in await service.states(viewer)}["update-check"].last
    assert last is not None and last.outcome == "done"
    assert await events_of_entity(temp_db, viewer, "run", "update-check", limit=5) == []


async def test_a_task_whose_work_is_a_product_reads_its_own_runs_not_its_familys(
    queue: JobQueue, temp_db: Database
) -> None:
    """Music's Run now is a Generate run of the music product, and Faces and Watermarks are both
    Identify. Read by family, Music's row would never move after its own press, and a run of faces
    alone would read as Watermarks' last run too. A run records the products it was for; a row
    reads its task's own. A run recorded without products answers by family.
    """
    from sift.kernel.jobs.families import Family
    from sift.kernel.jobs.ledger import Ledger

    book = Ledger(
        temp_db,
        families_of={
            "generate_file": Family.GENERATE,
            "face_scan": Family.IDENTIFY,
            "watermark_read": Family.IDENTIFY,
        },
        products_of={"face_scan": ("faces",), "watermark_read": ("watermarks",)},
    )
    service = TasksService(
        queue=queue,
        read=Settings().get,
        database=temp_db,
        ledger=book,
        governs={"generate": ("thumbnail",)},
        starters={one: _nothing for one in _STARTERS},
        keep_awake=KeepAwake(Calls()),
        order=TASK_ORDER,
        products={
            "generate": ("thumbnails", "previews", "sprites", "fingerprints"),
            "faces": ("faces",),
            "watermarks": ("watermarks",),
            "music": ("music",),
        },
    )
    viewer = Viewer(id="admin-1", role=Role.ADMIN)

    async def lasts() -> dict[str, object]:
        return {s.task.id: s.last for s in await service.states(viewer)}

    # Music pressed: a Generate-family run, handed the music product by its task's payload.
    book.started("generate_file", products=["music"])
    book.finished("generate_file", duration_ms=5.0, ok=True)
    await book.settle({}, settings={})
    after_music = await lasts()
    assert after_music["music"] is not None, "Music's own press is Music's last run"
    assert after_music["generate"] is None, "and it is not Generate's"

    # A file arrives and only its faces are read: an Identify run for faces alone.
    book.started("face_scan")
    book.finished("face_scan", duration_ms=5.0, ok=True)
    await book.settle({}, settings={})
    after_faces = await lasts()
    assert after_faces["faces"] is not None
    assert after_faces["watermarks"] is None, "a run of faces alone is not Watermarks' run"

    # One run doing both is both tasks' last run, to the second.
    book.started("face_scan")
    book.started("watermark_read")
    book.finished("face_scan", duration_ms=5.0, ok=True)
    book.finished("watermark_read", duration_ms=5.0, ok=True)
    await book.settle({}, settings={})
    both = await lasts()
    assert both["faces"] == both["watermarks"]
    assert both["watermarks"] is not None


async def test_activity_and_tasks_say_one_last_run_for_a_chore_that_is_a_task(
    queue: JobQueue, temp_db: Database
) -> None:
    """Enrichment's chore on Activity is the files' questions and its task is the sweep. Activity
    and Tasks both read the task's own last finished run through one function, so they agree.
    """
    from sift.kernel.jobs import WorkSummary
    from sift.kernel.jobs.schedules import get_schedule
    from sift.slices.media_jobs.router import _housekeeping

    task = get_schedule("enrichment")
    assert task is not None and task.job_type is not None
    service = _service(queue, temp_db, Settings(), Calls(), hour=13)
    viewer = Viewer(id="admin-1", role=Role.ADMIN)

    async def ask(ctx: JobContext) -> None:
        return None

    register_handler(task.job_type, ask, name="Looking up the library")
    register_handler("stash_box_scan", ask, name="Asking about a file")
    # The sweep finishes; then a file's own question, newer, fails, so the two read apart.
    for job_type in (task.job_type, "stash_box_scan"):
        await queue.enqueue(job_type, {})
        job = await queue.claim("worker-1")
        assert job is not None and job.type == job_type
        if job_type == task.job_type:
            await queue.complete(job.id, "worker-1")
        else:
            await queue.fail(job.id, "worker-1", "no answer", permanent=True)

    tasks_last = {s.task.id: s for s in await service.states(viewer)}["enrichment"].last
    summary = WorkSummary(states={}, run={}, since=None)
    chores = await _housekeeping(queue, summary, None, None)
    activity = next(one for one in chores if one.task == "enrichment")
    swept = (await queue.last_finished_runs([task.job_type]))[task.job_type]
    assert activity.last_state == "done", "the sweep, not a file's failed question"
    assert activity.last_started_at == swept.started_at
    assert tasks_last is not None and tasks_last.outcome == "done"
    assert tasks_last.ended_at == swept.finished_at


# --- how a run ended, in the row's words ------------------------------------------------------


def _outcome(
    *, finished: int | None = 100, stopped: bool = False, done: int = 1, failed: int = 0
) -> str | None:
    """What the row says of a pass that ended this way, or None where it says nothing."""
    from sift.kernel.jobs.ledger import RunRecord
    from sift.slices.tasks.service import _last_of

    last = _last_of(
        RunRecord(
            id="run-1",
            family="scan",
            started_at=40,
            finished_at=finished,
            stopped=stopped,
            jobs_done=done,
            jobs_failed=failed,
            worker_ms=0,
            files={},
            stages={},
            machine="",
            profile="",
            settings={},
            version="",
        )
    )
    return None if last is None else last.outcome


def test_a_pass_reads_canceled_when_stopped_and_failed_only_when_nothing_went_right() -> None:
    """A pass somebody stopped part way is CANCELED, never "done" as though it had looked through
    the library; and one whose every job failed is FAILED, where a few failures among work that
    was done is still a run that happened."""
    from sift.slices.tasks.service import _last_of

    assert _last_of(None) is None
    assert _outcome(finished=None) is None, "a run still going is not the last one"
    assert _outcome() == "done"
    assert _outcome(stopped=True, failed=3) == "canceled"
    assert _outcome(done=0, failed=2) == "failed"
    assert _outcome(done=5, failed=2) == "done"


# --- the boot refuses a wiring that cannot be right ------------------------------------------


async def test_work_mapped_to_a_task_nobody_declared_is_refused_at_boot(
    queue: JobQueue, temp_db: Database
) -> None:
    with pytest.raises(ValueError, match="nobody declared: not-a-task"):
        TasksService(
            queue=queue,
            read=Settings().get,
            database=temp_db,
            ledger=None,
            governs={"not-a-task": ("thumbnail",)},
            starters={one: _nothing for one in _STARTERS},
            keep_awake=KeepAwake(Calls()),
            order=TASK_ORDER,
        )


async def test_a_task_whose_run_needs_a_starter_and_has_none_is_refused_at_boot(
    queue: JobQueue, temp_db: Database
) -> None:
    """its run now would otherwise queue one bare job where a counted pass was meant."""
    with pytest.raises(ValueError, match="need a starter have none: faces"):
        TasksService(
            queue=queue,
            read=Settings().get,
            database=temp_db,
            ledger=None,
            governs={},
            starters={one: _nothing for one in _STARTERS if one != "faces"},
            keep_awake=KeepAwake(Calls()),
            order=TASK_ORDER,
        )


# --- the power request when something goes wrong ---------------------------------------------


async def test_a_setting_that_cannot_be_read_withdraws_the_request(
    queue: JobQueue, temp_db: Database
) -> None:
    """A request left standing over a question that did not come back is a device that never
    sleeps, so a failed read withdraws it rather than leaving it as it was."""
    values = Settings()
    values.stored[when_key("backup")] = WHEN_QUIET
    calls = Calls()
    night = _service(queue, temp_db, values, calls, hour=1)
    await queue.enqueue("backup_run", {}, require_handler=False)
    assert await night.keep_awake_once() is True

    async def broken(key: str) -> object:
        raise RuntimeError("the settings store is gone")

    night._read = broken

    assert await night.keep_awake_once() is False
    assert calls.flags[-1] == ES_CONTINUOUS


async def test_a_request_the_system_refuses_is_not_held_and_is_asked_for_again(
    queue: JobQueue, temp_db: Database
) -> None:
    """The call answers 0 when it did not take. Believing it had would report a device kept awake
    that is going to sleep, and never ask again."""
    values = Settings()
    values.stored[when_key("backup")] = WHEN_QUIET
    refusals: list[int] = []

    def refuse(flags: int) -> int:
        refusals.append(flags)
        return 0

    service = _service(queue, temp_db, values, Calls(), hour=1)
    service._awake = KeepAwake(refuse)
    await queue.enqueue("backup_run", {}, require_handler=False)

    assert await service.keep_awake_once() is False
    assert await service.keep_awake_once() is False
    assert len(refusals) == 2, "asked again at the next look"


async def test_the_watch_looks_again_until_stopped_and_then_withdraws_the_request(
    queue: JobQueue, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from sift.slices.tasks import service as tasks_service

    monkeypatch.setattr(tasks_service, "KEEP_AWAKE_EVERY_SECONDS", 0.001)
    values = Settings()
    values.stored[when_key("backup")] = WHEN_QUIET
    calls = Calls()
    night = _service(queue, temp_db, values, calls, hour=1)
    await queue.enqueue("backup_run", {}, require_handler=False)
    stop = asyncio.Event()
    looks = 0
    look = night.keep_awake_once

    async def counted() -> bool:
        nonlocal looks
        looks += 1
        if looks == 3:
            stop.set()
        return await look()

    night.keep_awake_once = counted  # type: ignore[method-assign]

    await asyncio.wait_for(night.keep_awake(stop), timeout=10)

    assert looks == 3
    assert calls.flags == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED, ES_CONTINUOUS]
    assert night.awake_now is False


def test_the_real_call_is_there_only_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from sift.slices.tasks import power

    if sys.platform == "win32":
        call = power._windows_call()
        assert call is not None
        # The continuous flag alone asks for nothing: it clears this thread's request, which is
        # the one thing that can be asked of the real call without keeping the machine awake.
        assert call(ES_CONTINUOUS) != 0
    monkeypatch.setattr(sys, "platform", "linux")
    assert power._windows_call() is None
    assert KeepAwake().held is False


# --- reading a task back -------------------------------------------------------------------------


async def test_a_task_with_a_run_under_way_says_it_is_running(
    queue: JobQueue, temp_db: Database
) -> None:
    async def look(ctx: JobContext) -> None:
        return None

    register_handler("dedup_scan", look, name="Looking for duplicates")
    service = _service(queue, temp_db, Settings(), Calls(), hour=13)
    viewer = Viewer(id="admin-1", role=Role.ADMIN)
    await queue.enqueue("dedup_scan", {})
    assert await queue.claim("worker-1") is not None

    states = {s.task.id: s for s in await service.states(viewer)}

    assert states["duplicates"].running is True
    assert states["shoots"].running is False


@pytest.mark.parametrize(("hour", "shut"), [(13, True), (1, False)])
async def test_a_task_that_waits_for_work_next_runs_when_its_held_work_can_start(
    queue: JobQueue, temp_db: Database, hour: int, shut: bool
) -> None:
    """No clock of its own: its next run is the held work's, at the range's opening while it is
    shut and this moment while it is open."""
    values = Settings()
    values.stored[when_key("generate")] = WHEN_QUIET
    service = _service(queue, temp_db, values, Calls(), hour=hour)
    viewer = Viewer(id="admin-1", role=Role.ADMIN)

    def generate_next() -> object:
        return {s.task.id: s for s in states}["generate"].next_run

    states = await service.states(viewer)
    assert generate_next() is None, "nothing held, nothing coming"

    await queue.enqueue("thumbnail", {}, require_handler=False)
    states = await service.states(viewer)

    expected = int(_clock_at(23)) if shut else int(_clock_at(hour))
    assert generate_next() == expected


@pytest.mark.parametrize(
    ("hour", "when", "held_to"),
    [(1, WHEN_QUIET, "the row"), (13, WHEN_WORK, "the row"), (13, WHEN_QUIET, "the range")],
)
async def test_a_timed_tasks_next_run_is_its_waiting_rows_own_moment(
    queue: JobQueue, temp_db: Database, hour: int, when: str, held_to: str
) -> None:
    """Where quiet hours have no say (the range open now, or the task not held to it) the queued
    row's moment is the answer as it stands, never one worked out from the cadence. Held to a
    range still to open, the answer is the opening: the row will not start before it."""
    values = Settings()
    values.stored[when_key("backup")] = when
    values.stored[EVERY_DAYS_KEY] = 1
    service = _service(queue, temp_db, values, Calls(), hour=hour)
    viewer = Viewer(id="admin-1", role=Role.ADMIN)
    moment = int(_clock_at(hour)) + 3600
    await queue.enqueue("backup_run", {}, run_after=moment, require_handler=False)

    states = {s.task.id: s for s in await service.states(viewer)}

    expected = moment if held_to == "the row" else int(_clock_at(23))
    assert states["backup"].next_run == expected


async def test_a_timed_tasks_placed_run_is_its_next_run_not_work_waiting(
    queue: JobQueue, temp_db: Database
) -> None:
    """The row a clock places is said as "Next" with its moment. Counted as waiting as well, a
    clocked task would say "1 waiting" beside its next run."""
    values = Settings()
    values.stored[when_key("backup")] = WHEN_WORK
    values.stored[EVERY_DAYS_KEY] = 1
    service = _service(queue, temp_db, values, Calls(), hour=13)
    viewer = Viewer(id="admin-1", role=Role.ADMIN)
    await queue.enqueue(
        "backup_run", {}, run_after=int(_clock_at(13)) + 3600, require_handler=False
    )

    backup = {s.task.id: s for s in await service.states(viewer)}["backup"]

    assert backup.next_run is not None
    assert backup.waiting == 0 and backup.held == 0

    await queue.enqueue("backup_run", {}, require_handler=False)
    backup = {s.task.id: s for s in await service.states(viewer)}["backup"]
    assert backup.waiting == 1, "a pressed run beside the placed one is real work waiting"


async def test_a_stage_reading_a_task_nobody_declared_is_refused_at_boot(
    queue: JobQueue, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from sift.kernel.jobs import schedules

    declared = dict(schedules._REGISTRY)
    declared["identify"] = replace(declared["identify"], reads=("faces", "not-a-task"))
    monkeypatch.setattr(schedules, "_REGISTRY", declared)
    with pytest.raises(ValueError, match="identify reads not-a-task"):
        _service(queue, temp_db, Settings(), Calls(), hour=13)


def test_a_task_whose_feature_is_off_names_the_switch_and_where_it_is() -> None:
    """The row says which switch to turn on and on which pane, rather than drawing a row that
    does nothing with no word about why."""
    from dataclasses import replace

    from sift.kernel.jobs.schedules import get_schedule
    from sift.slices.tasks import TaskState
    from sift.slices.tasks.router import _switched_off

    declared = get_schedule("backup")
    assert declared is not None
    task = replace(declared, switch=EVERY_DAYS_KEY)

    def state(off: bool) -> TaskState:
        return TaskState(
            task=task,
            when=WHEN_WORK,
            on=True,
            cadence="",
            labels={},
            last=None,
            next_run=None,
            waiting=0,
            held=0,
            running=False,
            switched_off=off,
        )

    said = _switched_off(state(True))
    registered = get_registered(EVERY_DAYS_KEY)
    assert registered is not None
    assert said is not None and said.key == EVERY_DAYS_KEY and said.section == registered.section
    assert _switched_off(state(False)) is None


async def test_a_task_that_writes_its_own_line_says_how_its_last_run_ended(
    queue: JobQueue, temp_db: Database
) -> None:
    """Read from the history line the run wrote: its outcome, how long, and what it said."""
    from sift.kernel.ledger import Actor, record_event
    from sift.kernel.vocabulary import Subject

    service = _service(queue, temp_db, Settings(), Calls(), hour=13)
    viewer = Viewer(id="admin-1", role=Role.ADMIN)

    async def last() -> tuple[str, int | None, str | None] | None:
        ended = {s.task.id: s for s in await service.states(viewer)}["backup"].last
        return None if ended is None else (ended.outcome, ended.seconds, ended.said)

    assert await last() is None, "never run"

    async def wrote(payload: str) -> None:
        async with temp_db.write() as connection:
            await record_event(
                connection,
                actor=Actor.sift(),
                verb="ran",
                subject=Subject("run", "backup", "Automatic backup"),
                payload=payload,
            )

    await wrote('{"outcome": "failed", "seconds": 12, "said": "The disk is full."}')
    assert await last() == ("failed", 12, "The disk is full.")

    # A line some other tool garbled is still a run that happened, told as plainly as it can be.
    await wrote("not json")
    assert await last() == ("done", None, None)


async def test_a_long_pass_with_no_work_ledger_has_no_last_run(
    queue: JobQueue, temp_db: Database
) -> None:
    """A pass's runs are the ledger's to say; with no ledger there is no answer, not a guess from
    the job rows (which hold every page of a pass, not its runs)."""
    from sift.kernel.jobs.families import Family

    async def walk(ctx: JobContext) -> None:
        return None

    register_handler("library_scan", walk, name="Scanning", family=Family.SCAN)
    await queue.enqueue("library_scan", {})
    job = await queue.claim("worker-1")
    assert job is not None
    await queue.complete(job.id, "worker-1")
    service = _service(queue, temp_db, Settings(), Calls(), hour=13)

    states = {s.task.id: s for s in await service.states(Viewer(id="admin-1", role=Role.ADMIN))}

    assert states["scan"].last is None


# --- running one -----------------------------------------------------------------------------


async def test_a_press_on_a_counted_pass_is_its_starters_and_queues_nothing_else(
    queue: JobQueue, temp_db: Database
) -> None:
    pressed: list[tuple[str, str]] = []

    async def start(at: str, viewer: Viewer, only: Selection) -> list[str]:
        pressed.append((at, viewer.id))
        return ["job-from-the-starter"]

    service = _service(queue, temp_db, Settings(), Calls(), hour=13)
    service._starters["faces"] = start

    ids, starts = await service.run("faces", at="now", viewer=Viewer(id="admin-1", role=Role.ADMIN))

    assert ids == ["job-from-the-starter"] and starts is None
    assert pressed == [("now", "admin-1")]
    assert (await queue.list(limit=10)).total == 0


async def test_at_quiet_hours_on_work_that_is_no_longer_waiting_names_no_hour(
    queue: JobQueue, temp_db: Database
) -> None:
    """A row the press landed on that has gone (finished and pruned, or cancelled) is not waiting
    for the range, so the answer does not promise it an hour."""

    async def start(at: str, viewer: Viewer, only: Selection) -> list[str]:
        return ["a-row-that-is-gone"]

    service = _service(queue, temp_db, Settings(), Calls(), hour=13)
    service._starters["faces"] = start

    _ids, starts = await service.run("faces", at="quiet", viewer=Viewer(id="a", role=Role.ADMIN))

    assert starts is None


async def test_a_press_asked_for_at_neither_moment_is_refused(
    queue: JobQueue, temp_db: Database
) -> None:
    service = _service(queue, temp_db, Settings(), Calls(), hour=13)

    with pytest.raises(ValueError, match="now or quiet"):
        await service.run("backup", at="tomorrow", viewer=Viewer(id="a", role=Role.ADMIN))
    assert (await queue.list(limit=10)).total == 0


# --- part of a task, and its dry run -------------------------------------------------------------


def _parted(
    queue: JobQueue, database: Database, started: list[tuple[str, Selection]]
) -> TasksService:
    """A service where Generate has parts and a plan, and Generate and Scan run over some folders.
    The plan records the selection it was asked for."""

    async def start(at: str, viewer: Viewer, only: Selection = EVERYTHING) -> list[str]:
        started.append((at, only))
        return ["a-part-run"]

    async def plan(only: Selection, viewer: Viewer) -> Plan:
        started.append(("plan", only))
        return Plan(files=3, lines=(PlanLine("Thumbnails", 3),), names=("beach.mp4",))

    async def folders() -> list[TaskPart]:
        return [TaskPart("root-a", "Holidays", "D:/Holidays")]

    async def viewer_for(user_id: str) -> Viewer | None:
        return Viewer(id=user_id, role=Role.ADMIN)

    return TasksService(
        queue=queue,
        read=Settings().get,
        database=database,
        ledger=None,
        governs={},
        starters={**{one: _nothing for one in _STARTERS}, "generate": start, "scan": start},
        parts={
            "generate": TaskParts(subtasks=(TaskPart("thumbnails", "Thumbnails"),), locations=True),
            "scan": TaskParts(locations=True),
        },
        planners={"generate": plan},
        folders=folders,
        viewer_for=viewer_for,
        keep_awake=KeepAwake(Calls()),
        order=TASK_ORDER,
    )


async def test_a_part_press_hands_its_selection_to_the_starter_and_whole_scan_stays_one_job(
    queue: JobQueue, temp_db: Database
) -> None:
    """Scan's starter is only for folders: its whole press is still the one deduped walk."""
    started: list[tuple[str, Selection]] = []
    service = _parted(queue, temp_db, started)
    admin = Viewer(id="admin-1", role=Role.ADMIN)

    only = await service.selection("scan", None, ["root-a"])
    assert await service.run("scan", at="now", viewer=admin, only=only) == (["a-part-run"], None)
    assert started == [("now", Selection(locations=("root-a",)))]
    assert await service.described("scan", only) == "Holidays"

    async def walk(_context: JobContext) -> None: ...

    register_handler("library_scan", walk, name="Scanning library")
    await service.run("scan", at="now", viewer=admin)
    assert len(started) == 1, "the whole press did not go through the folders' starter"
    assert (await queue.list(job_type="library_scan", limit=5)).total == 1


async def test_a_press_naming_a_folder_that_is_gone_is_refused(
    queue: JobQueue, temp_db: Database
) -> None:
    service = _parted(queue, temp_db, [])
    with pytest.raises(NotAPart):
        await service.selection("scan", None, ["root-gone"])
    with pytest.raises(NotAPart):
        await service.selection("backup", ["thumbnails"], None)


async def test_a_dry_run_queues_only_itself_and_its_report_comes_back_on_the_row(
    queue: JobQueue, temp_db: Database
) -> None:
    """The dry press queues the plan and nothing of the task; the plan's report is what the row
    reads back as its last dry run."""
    started: list[tuple[str, Selection]] = []
    service = _parted(queue, temp_db, started)
    register_handlers(service.rehearse)
    admin = Viewer(id="admin-1", role=Role.ADMIN)
    only = await service.selection("generate", ["thumbnails"], None)

    ids, starts = await service.run("generate", at="quiet", viewer=admin, only=only, dry=True)

    assert starts is None and started == []
    rows = (await queue.list(limit=10)).jobs
    assert [(one.type, one.payload) for one in rows] == [
        (TASK_DRY_RUN, {"task": "generate", "parts": ["thumbnails"]})
    ]
    report = await service.rehearse("generate", only, "admin-1")
    assert report.said == (
        "Generate would work on 3 files. First files: beach.mp4, and 2 more. Nothing was changed."
    )
    with pytest.raises(TaskRefused, match="no dry run"):
        await service.run("backup", at="now", viewer=admin, dry=True)
    waiting = {one.task.id: one for one in await service.states(admin)}
    assert waiting["generate"].dry_running, "the row says so while the plan is out"
    assert not waiting["backup"].dry_running

    # Run as a worker runs it: the handler the dry run registered writes the note.
    claimed = await queue.claim("w")
    assert claimed is not None and claimed.id == ids[0]
    await registered_handlers()[TASK_DRY_RUN](JobContext(job=claimed, worker_id="w", queue=queue))
    await queue.complete(ids[0], "w")
    states = {one.task.id: one for one in await service.states(admin)}
    last = states["generate"].dry_run
    assert last is not None and last.said == report.said
    assert last.report == report, "the row reads the report back as fields"
    assert not states["generate"].dry_running
    assert states["backup"].dry_run is None


async def test_a_failed_dry_run_says_why_in_plain_words(queue: JobQueue, temp_db: Database) -> None:
    """The row's hover says the failure's kind; the tool's own text stays on the job's row."""
    service = _parted(queue, temp_db, [])
    register_handlers(service.rehearse)
    admin = Viewer(id="admin-1", role=Role.ADMIN)
    ids, _ = await service.run("generate", at="now", viewer=admin, dry=True)
    claimed = await queue.claim("w")
    assert claimed is not None and claimed.id == ids[0]
    raw = "FileNotFoundError: [WinError 2] The system cannot find the file specified"
    await queue.fail(ids[0], "w", raw, permanent=True)

    last = {one.task.id: one for one in await service.states(admin)}["generate"].dry_run

    assert last is not None and last.outcome == "failed"
    assert last.said == in_plain_words(raw)
    assert "WinError" not in (last.said or "")
    assert (await queue.get(ids[0])).error == raw  # type: ignore[union-attr]


async def test_a_dry_run_over_some_folders_plans_them_and_says_which(
    queue: JobQueue, temp_db: Database
) -> None:
    """The plan is asked for the folders the press named, and its report names them; a folder
    that left the library while the dry run waited is refused rather than planned as nothing."""
    asked: list[tuple[str, Selection]] = []
    service = _parted(queue, temp_db, asked)
    only = await service.selection("generate", None, ["root-a"])

    report = await service.rehearse("generate", only, "admin-1")

    assert asked == [("plan", Selection(locations=("root-a",)))]
    assert report.said.startswith("Generate for Holidays would work on 3 files.")
    with pytest.raises(TaskRefused, match="no longer in your library"):
        await service.rehearse("generate", Selection(locations=("root-gone",)), "admin-1")
    assert len(asked) == 1, "a folder that is gone is never planned"


def test_a_part_nothing_can_start_is_refused_at_boot(queue: JobQueue, temp_db: Database) -> None:
    with pytest.raises(ValueError, match="tasks with parts have no starter"):
        TasksService(
            queue=queue,
            read=Settings().get,
            database=temp_db,
            ledger=None,
            governs={},
            starters={one: _nothing for one in _STARTERS},
            parts={"backup": TaskParts(subtasks=(TaskPart("x", "X"),))},
            keep_awake=KeepAwake(Calls()),
            order=TASK_ORDER,
        )


@pytest.mark.parametrize(
    ("parts", "refused"),
    [
        ({"scan": TaskParts(locations=True)}, "nothing reads the folders"),
        ({"not-a-task": TaskParts(subtasks=(TaskPart("x", "X"),))}, "nobody declared: not-a-task"),
    ],
    ids=["folders-unread", "undeclared"],
)
def test_parts_the_server_could_not_check_or_run_are_refused_at_boot(
    queue: JobQueue, temp_db: Database, parts: dict[str, TaskParts], refused: str
) -> None:
    """A folder list with nothing to read the folders from, or parts of a task nobody declared,
    would be ticks in the menu the press could not honour."""
    with pytest.raises(ValueError, match=refused):
        TasksService(
            queue=queue,
            read=Settings().get,
            database=temp_db,
            ledger=None,
            governs={},
            starters={**{one: _nothing for one in _STARTERS}, **{one: _nothing for one in parts}},
            parts=parts,
            keep_awake=KeepAwake(Calls()),
            order=TASK_ORDER,
        )


async def test_a_press_on_sub_tasks_alone_is_described_by_their_names(
    queue: JobQueue, temp_db: Database
) -> None:
    service = _parted(queue, temp_db, [])
    only = await service.selection("generate", ["thumbnails"], None)

    assert await service.described("generate", only) == "Thumbnails"


async def test_a_dry_run_is_refused_for_a_task_without_one_and_for_nobody(
    queue: JobQueue, temp_db: Database
) -> None:
    """A dry run is planned for the person who pressed it; one with no such person (a row queued
    by hand, or a user deleted while it waited) is refused rather than planned for everybody."""
    service = _parted(queue, temp_db, [])
    admin = Viewer(id="admin-1", role=Role.ADMIN)

    with pytest.raises(UnknownTask):
        await service.rehearse("backup", EVERYTHING, "admin-1")
    with pytest.raises(TaskRefused, match="no longer a user here"):
        await service.rehearse("generate", EVERYTHING, None)
    with pytest.raises(UnknownTask):
        await service.run("not-a-task", at="now", viewer=admin)


async def test_a_dry_run_over_some_folders_is_queued_with_them(
    queue: JobQueue, temp_db: Database
) -> None:
    service = _parted(queue, temp_db, [])
    register_handlers(service.rehearse)
    admin = Viewer(id="admin-1", role=Role.ADMIN)
    only = await service.selection("generate", None, ["root-a"])

    await service.run("generate", at="now", viewer=admin, only=only, dry=True)

    rows = (await queue.list(job_type=TASK_DRY_RUN, limit=5)).jobs
    assert [one.payload for one in rows] == [{"task": "generate", "locations": ["root-a"]}]


# --- what a row says while its work runs ----------------------------------------------------------

#: The job types a run over the library and its tasks are, as the composition root hands them in.
_CARRIERS = ("generate", "generate_file", "identify", "identify_file")


def _working(queue: JobQueue, database: Database, values: Settings | None = None) -> TasksService:
    """A service told which products each row answers for and which types carry products, as the
    composition root tells it (`rows_products`)."""
    return TasksService(
        queue=queue,
        read=(values or Settings()).get,
        database=database,
        ledger=None,
        governs={"generate": ("thumbnail",)},
        starters={one: _nothing for one in _STARTERS},
        keep_awake=KeepAwake(Calls()),
        order=TASK_ORDER,
        products=rows_products(
            {
                "generate": ("thumbnails", "previews"),
                "faces": ("faces",),
                "smart-search": ("meaning",),
                "watermarks": ("watermarks",),
                "music": ("music",),
            }
        ),
        carriers=_CARRIERS,
        passes=("generate", "identify"),
        now=lambda: _clock_at(13),
    )


async def _rows(service: TasksService) -> dict[str, TaskState]:
    return {one.task.id: one for one in await service.states(Viewer(id="admin-1", role=Role.ADMIN))}


async def _idle(ctx: JobContext) -> None:
    return None


@pytest.mark.regression
async def test_a_running_job_is_running_and_never_counted_as_waiting(
    queue: JobQueue, temp_db: Database
) -> None:
    """One job running is "Running now" and nothing waiting: waiting is what has not started. And
    the held number the row carries is of work not started too, since the screen compares the two."""
    values = Settings()
    values.stored[when_key("duplicates")] = WHEN_QUIET
    service = _working(queue, temp_db, values)
    task = get_schedule("duplicates")
    assert task is not None and task.job_type is not None
    register_handler(task.job_type, _idle, name="Finding duplicate files")

    await queue.enqueue(task.job_type, {"n": 1})
    assert await queue.claim("worker-1") is not None
    alone = (await _rows(service))["duplicates"]
    assert (alone.running, alone.waiting, alone.held) == (True, 0, 0)

    await queue.enqueue(task.job_type, {"n": 2})
    beside = (await _rows(service))["duplicates"]
    assert (beside.running, beside.waiting, beside.held) == (True, 1, 1)


@pytest.mark.regression
async def test_a_run_over_the_library_is_the_work_of_the_tasks_whose_products_it_names(
    queue: JobQueue, temp_db: Database
) -> None:
    """Identify faces pressed: an `identify` run hands out `identify_file` tasks, none of them the
    task's own type. The row of every task whose product the run names says it is running and
    how much waits; a task whose product it does not name says nothing."""
    service = _working(queue, temp_db)
    run = await queue.enqueue(
        "identify", {"products": ["faces"], "files": 3}, require_handler=False, at=AT_NOW
    )
    register_handler("identify", _idle, name="Checking what to identify")
    claimed = await queue.claim("worker-1")
    assert claimed is not None and claimed.id == run
    for asset in ("a-1", "a-2"):
        await queue.enqueue(
            "identify_file",
            {"asset_id": asset, "products": ["faces"]},
            parent_id=run,
            require_handler=False,
            at=AT_NOW,
        )
    await queue.enqueue(
        "identify_file",
        {"asset_id": "a-3", "products": ["faces", "watermarks"]},
        require_handler=False,
        at=AT_QUIET,
    )

    rows = await _rows(service)

    faces, marks, stage = rows["faces"], rows["watermarks"], rows["identify"]
    assert (faces.running, faces.waiting, faces.held) == (True, 3, 1)
    assert (marks.running, marks.waiting, marks.held) == (False, 1, 1)
    assert (stage.running, stage.waiting) == (True, 3), "the stage answers for all three"
    for other in ("smart-search", "generate", "music"):
        assert (rows[other].running, rows[other].waiting) == (False, 0), other


@pytest.mark.regression
async def test_a_row_counting_files_never_counts_the_run_that_walks_for_them(
    queue: JobQueue, temp_db: Database
) -> None:
    """A row's count says what it counts: Identify faces counts files, so the run pressed for it
    and held to quiet hours is not "1 file waiting" (it places the next run instead), while a run
    task counts its runs."""
    service = _working(queue, temp_db)
    await queue.enqueue("identify", {"products": ["faces"]}, require_handler=False, at=AT_QUIET)

    faces = (await _rows(service))["faces"]
    assert faces.task.unit == ("file", "files")
    assert (faces.waiting, faces.held) == (0, 0)

    await queue.enqueue(
        "identify_file",
        {"asset_id": "a-1", "products": ["faces"]},
        require_handler=False,
        at=AT_QUIET,
    )
    faces = (await _rows(service))["faces"]
    assert (faces.waiting, faces.held) == (1, 1)

    shoots = get_schedule("shoots")
    assert shoots is not None and shoots.unit == ("run", "runs")


@pytest.mark.regression
async def test_a_task_whose_job_hands_out_work_ran_for_as_long_as_its_family(
    temp_db: Database,
) -> None:
    """Look up new files on stash-boxes: the sweep finishes its own row in two seconds and leaves
    thousands of questions queued under it. Its run is the family: not over while any of it waits,
    over when the last of it is, said in the sentence the walk ends with, folded the way Activity
    folds it, and Activity's row for the chore says the same."""
    from sift.kernel.jobs import WorkSummary
    from sift.slices.media_jobs.router import _housekeeping
    from sift.testing.fixtures import FakeClock

    await temp_db.initialize_schema()
    clock = FakeClock(1000)
    queue = JobQueue(temp_db, clock=clock.now)
    service = _service(queue, temp_db, Settings(), Calls(), hour=13)
    task = get_schedule("enrichment")
    assert task is not None and task.job_type is not None
    sweep = task.job_type
    register_handler(sweep, _idle, name="Looking up the library")
    register_handler("stash_box_scan", _idle, name="Asking about a file")

    async def finish(job_id: str, note: str | None = None, *, after: int = 1) -> None:
        claimed = await queue.claim("worker-1")
        assert claimed is not None and claimed.id == job_id
        clock.advance(after)
        if note is not None:
            await queue.set_note(job_id, "worker-1", note)
        await queue.complete(job_id, "worker-1")

    async def last() -> LastRun | None:
        return (await _rows(service))["enrichment"].last

    first = await queue.enqueue(sweep, {"n": 1})
    await finish(first, "Every file has already been asked about.", after=2)
    assert (await last()) == LastRun(
        ended_at=1002, outcome="done", seconds=2, said="Every file has already been asked about."
    )

    clock.advance(10)
    head = await queue.enqueue(sweep, {"n": 2})
    started = await queue.claim("worker-1")
    assert started is not None and started.id == head
    page = await queue.enqueue(sweep, {"n": 2, "offset": 1}, parent_id=head)
    # The next page's questions are the page's own children: the last of the family to end is a
    # row two levels under the head, whose own row stops moving when the page ends.
    asks = [
        await queue.enqueue("stash_box_scan", {"asset_id": one}, parent_id=page)
        for one in ("f-1", "f-2")
    ]
    clock.advance(2)
    await queue.set_note(head, "worker-1", "Working out which files to ask about\u2026")
    await queue.complete(head, "worker-1")
    going = await last()
    assert going is not None and going.ended_at == 1002, "a run still going is not the last one"

    await finish(page, "2 files queued to ask about.", after=6)
    for ask in asks:
        await finish(ask, after=10)
    ended = await last()
    assert ended == LastRun(
        ended_at=1040, outcome="done", seconds=28, said="2 files queued to ask about."
    ), "the family's span, and the sentence the walk ended with"

    clock.advance(10)
    stopped = await queue.enqueue(sweep, {"n": 3})
    assert await queue.claim("worker-1") is not None
    await queue.enqueue("stash_box_scan", {"asset_id": "f-3"}, parent_id=stopped)
    await queue.cancel(stopped)
    canceled = await last()
    assert canceled is not None and canceled.outcome == "canceled" and canceled.said is None

    chores = await _housekeeping(queue, WorkSummary(states={}, run={}, since=None), None, None)
    activity = next(one for one in chores if one.task == "enrichment")
    assert activity.last_state == "canceled", "Activity's row reads the same run"


@pytest.mark.regression
async def test_the_identify_stage_reads_a_stopped_identify_run_as_its_last(
    queue: JobQueue, temp_db: Database
) -> None:
    """Identify pressed and stopped: Faces, Smart Search and Watermarks each say "Last run canceled
    just now", and so does the stage, which answers for the three products through the same record
    rather than the pace's, which leaves a stopped run out."""
    from sift.kernel.jobs.families import Family
    from sift.kernel.jobs.ledger import Ledger

    book = Ledger(temp_db, families_of={"identify_file": Family.IDENTIFY})
    service = TasksService(
        queue=queue,
        read=Settings().get,
        database=temp_db,
        ledger=book,
        governs={},
        starters={one: _nothing for one in _STARTERS},
        keep_awake=KeepAwake(Calls()),
        order=TASK_ORDER,
        products=rows_products({"faces": ("faces",), "watermarks": ("watermarks",)}),
        carriers=_CARRIERS,
    )
    book.started("identify_file", products=list(IDENTIFY_PRODUCTS))
    book.finished("identify_file", duration_ms=5.0, ok=True)
    await book.settle({}, settings={})
    book.started("identify_file", products=list(IDENTIFY_PRODUCTS), requested_by="admin-1")
    book.stopped_by_hand(["identify_file"])
    await book.settle({}, settings={})

    rows = await _rows(service)

    stage = rows["identify"].last
    assert stage is not None and stage.outcome == "canceled"
    assert stage == rows["faces"].last, "the stage and its tasks say one run"


@pytest.mark.regression
async def test_a_whole_library_scan_is_the_scan_rows_last_run_and_a_stopped_one_says_so(
    queue: JobQueue, temp_db: Database
) -> None:
    """Scan pressed over the whole library: a `library_scan` hands a walk to each folder, and the
    row says the run ended when the last walk did, as one run. A folder's walk the watcher put off
    to a later moment begins the next run and does not hold this one open: held open, the row
    would go on saying the run before. Stopped by hand, the row says canceled, as every other
    row does."""
    from sift.kernel.jobs.families import Family
    from sift.kernel.jobs.ledger import Ledger

    async def walk(ctx: JobContext) -> None:
        return None

    register_handler("library_scan", walk, name="Scanning every folder", family=Family.SCAN)
    register_handler("scan", walk, name="Scanning folder", family=Family.SCAN)
    book = Ledger(temp_db, families_of={"library_scan": Family.SCAN, "scan": Family.SCAN})
    service = TasksService(
        queue=queue,
        read=Settings().get,
        database=temp_db,
        ledger=book,
        governs={},
        starters={one: _nothing for one in _STARTERS},
        keep_awake=KeepAwake(Calls()),
        order=TASK_ORDER,
    )

    async def work(job_type: str, *, stop: bool = False) -> None:
        job = await queue.claim("worker-1")
        assert job is not None and job.type == job_type
        book.started(job.type, requested_by=job.requested_by)
        if stop:
            book.stopped_by_hand([job.type])
            await queue.cancel(job.id)
            return
        book.finished(job.type, duration_ms=5.0, ok=True)
        await queue.complete(job.id, "worker-1")

    head = await queue.enqueue("library_scan", {"scan_only": True}, requested_by="admin-1")
    await work("library_scan")
    for root in ("root-a", "root-b", "root-c"):
        await queue.enqueue("scan", {"root_id": root, "scan_only": True}, parent_id=head)
    await queue.enqueue("scan", {"root_id": "root-a"}, run_after=int(_clock_at(13)) + 10**8)
    await book.settle(await queue.due_by_type(), settings={})
    assert (await _rows(service))["scan"].last is None, "the walks are still to come"
    for _ in range(3):
        await work("scan")
    await book.settle(await queue.due_by_type(), settings={})

    ran = (await _rows(service))["scan"].last
    assert ran is not None and ran.outcome == "done"

    await queue.enqueue("library_scan", {"scan_only": True, "again": 1}, requested_by="admin-1")
    await work("library_scan", stop=True)
    await book.settle(await queue.due_by_type(), settings={})
    stopped = (await _rows(service))["scan"].last
    assert stopped is not None and stopped.outcome == "canceled"


@pytest.mark.regression
async def test_a_paused_run_is_neither_running_nor_waiting_on_the_rows_it_names(
    queue: JobQueue, temp_db: Database
) -> None:
    """Paused is held by somebody who means to start it again: not under way, and not work that
    is waiting to start. The row counts only the rows beside it that are."""
    service = _working(queue, temp_db)
    paused = await queue.enqueue(
        "identify_file",
        {"asset_id": "a-1", "products": ["faces"]},
        require_handler=False,
        at=AT_NOW,
    )
    assert await queue.pause(paused)
    await queue.enqueue(
        "identify_file",
        {"asset_id": "a-2", "products": ["faces"]},
        require_handler=False,
        at=AT_NOW,
    )

    faces = (await _rows(service))["faces"]

    assert (faces.running, faces.waiting) == (False, 1)
