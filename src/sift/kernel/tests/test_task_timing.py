# SPDX-License-Identifier: AGPL-3.0-or-later
"""When work may start: a task's When at the queue, the press that is never held, and the clock.

Each rule here is one the Tasks model promises out loud, so each is held by a test that fails if it
stops being true:

* work nobody pressed for a task set to "In quiet hours" is not handed out while the range is shut,
  and stops being handed out when it closes;
* a press is never refused by a switch and never held by the range, and the pages and tasks it hands
  out in its own family are the press too;
* a press of "Run during quiet hours" waits for the range whatever the task is set to;
* a timed task's next run is placed by one scheduler, moved by a change and taken back by
  "Only when I press it";
* a declared task's run writes its own line in the history, which is what its row reads.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import sift.main  # noqa: F401 (every schema, the history's among them, is registered by it)
from sift.kernel import settings_registry
from sift.kernel.db import Database
from sift.kernel.jobs import (
    JobContext,
    JobQueue,
    JobState,
    JobSwitchedOff,
    Switch,
    register_handler,
    schedules,
)
from sift.kernel.jobs.clock import TaskClock
from sift.kernel.jobs.quiet_hours import WHEN_PRESS, WHEN_QUIET, WHEN_WORK
from sift.kernel.jobs.schedules import ScheduledTask, register_schedule
from sift.kernel.jobs.switchboard import QuietHold
from sift.testing.fixtures import FakeClock

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("clean_handlers")]

WORKER = "worker-one"
DAY = 24 * 3600


def _noop(job_type: str) -> str:
    async def handler(context: JobContext) -> None:
        return None

    register_handler(job_type, handler, name="Test job")
    return job_type


class Hold:
    """Quiet hours as the queue asks them, set by the test."""

    def __init__(self) -> None:
        self.open = False
        self.types: frozenset[str] = frozenset({"quiet_work"})

    async def __call__(self) -> QuietHold:
        return QuietHold(open=self.open, types=self.types)


@pytest.fixture
async def held_queue(temp_db: Database) -> tuple[JobQueue, Hold]:
    await temp_db.initialize_schema()
    queue = JobQueue(temp_db)
    hold = Hold()
    queue.switchboard.declare_quiet_hours(hold)
    return queue, hold


async def _claim(queue: JobQueue) -> str | None:
    queue.forget_quiet_hours()
    job = await queue.claim(WORKER)
    return None if job is None else job.type


# --- quiet hours at the claim --------------------------------------------------------------------


async def test_work_nobody_pressed_waits_for_quiet_hours_and_pauses_when_they_close(
    held_queue: tuple[JobQueue, Hold],
) -> None:
    queue, hold = held_queue
    _noop("quiet_work")
    await queue.enqueue("quiet_work", {"n": 1})
    await queue.enqueue("quiet_work", {"n": 2})

    assert await _claim(queue) is None, "held while the range is shut"
    hold.open = True
    assert await _claim(queue) == "quiet_work", "handed out once it opens"
    hold.open = False
    assert await _claim(queue) is None, "and the rest waits again at the close"


async def test_a_press_is_never_held_by_quiet_hours(held_queue: tuple[JobQueue, Hold]) -> None:
    queue, _hold = held_queue
    _noop("quiet_work")
    await queue.enqueue("quiet_work", {"n": 1}, requested_by="someone")
    assert await _claim(queue) == "quiet_work"


async def test_a_press_at_quiet_hours_waits_whatever_its_task_says(
    held_queue: tuple[JobQueue, Hold],
) -> None:
    queue, hold = held_queue
    _noop("any_work")
    job_id = await queue.enqueue("any_work", {}, requested_by="someone", at="quiet")
    assert (await queue.get(job_id)).timing == "quiet"  # type: ignore[union-attr]
    assert await _claim(queue) is None
    hold.open = True
    assert await _claim(queue) == "any_work"


async def test_a_press_collapsing_onto_held_work_runs_it_now(
    held_queue: tuple[JobQueue, Hold],
) -> None:
    """A press that collapses onto a row waiting for eleven at night must not wait with it."""
    queue, _hold = held_queue
    _noop("quiet_work")
    waiting = await queue.enqueue("quiet_work", {"folder": "a"}, dedupe=True)
    same = await queue.enqueue("quiet_work", {"folder": "a"}, dedupe=True, requested_by="someone")
    assert same == waiting
    assert await _claim(queue) == "quiet_work"


async def test_held_work_asks_for_none_of_the_machine(held_queue: tuple[JobQueue, Hold]) -> None:
    queue, hold = held_queue
    _noop("quiet_work")
    await queue.enqueue("quiet_work", {})
    assert (await queue.demand_by_type()).get("quiet_work", 0) == 0
    assert (await queue.unfinished_by_type())["quiet_work"] == 1
    assert (await queue.held_by_type())["quiet_work"] == 1
    hold.open = True
    queue.forget_quiet_hours()
    assert (await queue.demand_by_type())["quiet_work"] == 1


# --- a press is never refused by a switch ---------------------------------------------------------


async def test_a_switch_refuses_work_nobody_pressed_and_never_a_press(job_queue: JobQueue) -> None:
    _noop("walk")

    async def off() -> bool:
        return False

    job_queue.switchboard.declare(Switch(key="k", refusal="Off.", on=off), "walk")
    with pytest.raises(JobSwitchedOff):
        await job_queue.enqueue("walk", {})
    pressed = await job_queue.enqueue("walk", {}, requested_by="someone")
    assert (await job_queue.get(pressed)).timing == "now"  # type: ignore[union-attr]


async def test_a_pressed_pass_hands_its_press_to_its_own_family_only(job_queue: JobQueue) -> None:
    from sift.kernel.jobs.families import Family

    handed: dict[str, str] = {}

    async def page(context: JobContext) -> None:
        handed["same"] = await context.enqueue_child("walk_child", {})
        handed["other"] = await context.enqueue_child("picture", {})

    register_handler("walk", page, name="Walking", family=Family.SCAN)
    register_handler("walk_child", page, name="Walking on", family=Family.SCAN)
    register_handler("picture", page, name="Picturing", family=Family.GENERATE)
    await job_queue.enqueue("walk", {}, requested_by="someone")
    job = await job_queue.claim(WORKER)
    assert job is not None
    await page(JobContext(job=job, worker_id=WORKER, queue=job_queue))

    assert (await job_queue.get(handed["same"])).timing == "now"  # type: ignore[union-attr]
    assert (await job_queue.get(handed["other"])).timing is None  # type: ignore[union-attr]


# --- the scheduler -----------------------------------------------------------------------------


@pytest.fixture
def task_registry() -> Iterator[None]:
    kept_tasks = dict(schedules._REGISTRY)
    kept_settings = dict(settings_registry._REGISTRY)
    schedules._REGISTRY.clear()
    try:
        yield
    finally:
        schedules._REGISTRY.clear()
        schedules._REGISTRY.update(kept_tasks)
        settings_registry._REGISTRY.clear()
        settings_registry._REGISTRY.update(kept_settings)


class Values:
    """The settings a scheduler reads, held by the test."""

    def __init__(self) -> None:
        self.stored: dict[str, object] = {}

    async def get(self, key: str) -> object:
        if key in self.stored:
            return self.stored[key]
        declared = settings_registry.get_registered(key)
        return None if declared is None else declared.default


async def test_the_scheduler_places_moves_and_takes_back_a_timed_task(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    await temp_db.initialize_schema()
    queue = JobQueue(temp_db, clock=fake_clock.now)
    _noop("tidy_up")
    register_schedule(
        ScheduledTask(
            id="tidy",
            title="Tidy up",
            explain="Tidies up.",
            job_type="tidy_up",
            every=lambda _values: DAY,
        )
    )
    values = Values()
    values.stored["tasks.tidy.when"] = WHEN_WORK

    async def quiet_range() -> tuple[str, str]:
        return "23:00", "07:00"

    clock = TaskClock(queue, read=values.get, quiet_range=quiet_range, clock=fake_clock.now)
    now = int(fake_clock.now())

    assert await clock.ensure("tidy", since=now) == now + DAY
    await clock.ensure("tidy", since=now)
    waiting = await queue.list(job_type="tidy_up", state=JobState.QUEUED)
    assert waiting.total == 1, "one run waiting, never a second"

    values.stored["tasks.tidy.when"] = WHEN_PRESS
    assert await clock.reschedule("tidy") is None
    assert (await queue.list(job_type="tidy_up", state=JobState.QUEUED)).total == 0, (
        "Only when I press it leaves nothing waiting"
    )

    values.stored["tasks.tidy.when"] = WHEN_QUIET
    moment = await clock.reschedule("tidy")
    assert moment is not None
    waiting = await queue.list(job_type="tidy_up", state=JobState.QUEUED)
    assert [one.run_after for one in waiting.jobs] == [moment]

    pressed = await queue.enqueue("tidy_up", {}, requested_by="someone")
    await clock.reschedule("tidy")
    assert (await queue.get(pressed)).state is JobState.QUEUED, "a press is never taken back"  # type: ignore[union-attr]


async def test_a_run_settling_places_the_next_from_when_it_ended(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    await temp_db.initialize_schema()
    queue = JobQueue(temp_db, clock=fake_clock.now)
    _noop("tidy_up")
    register_schedule(
        ScheduledTask(
            id="tidy",
            title="Tidy up",
            explain="Tidies up.",
            job_type="tidy_up",
            every=lambda _: DAY,
        )
    )
    values = Values()
    values.stored["tasks.tidy.when"] = WHEN_WORK

    async def quiet_range() -> tuple[str, str]:
        return "23:00", "07:00"

    TaskClock(queue, read=values.get, quiet_range=quiet_range, clock=fake_clock.now)
    await queue.enqueue("tidy_up", {}, requested_by="someone")
    job = await queue.claim(WORKER)
    assert job is not None
    fake_clock.advance(60)
    await queue.complete(job.id, WORKER)

    waiting = await queue.list(job_type="tidy_up", state=JobState.QUEUED)
    assert [one.run_after for one in waiting.jobs] == [int(fake_clock.now()) + DAY]


async def test_a_run_canceled_before_it_started_places_the_next_from_now_not_from_the_last_run(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    """A waiting run taken back never ran, so the next one is counted from the moment it was
    taken back, not from the last run that finished: counted from that, a daily task whose
    waiting run was cancelled an hour before it was due would be placed an hour from now."""
    _noop("tidy_up")
    task = _tidy()
    register_schedule(task)
    queue, _clock = await _scheduler(temp_db, fake_clock)
    await queue.enqueue("tidy_up", {}, requested_by="someone")
    job = await queue.claim(WORKER)
    assert job is not None
    await queue.complete(job.id, WORKER)
    (placed,) = (await queue.list(job_type="tidy_up", state=JobState.QUEUED)).jobs
    fake_clock.advance(3600)

    await queue.cancel(placed.id)

    waiting = await queue.list(job_type="tidy_up", state=JobState.QUEUED)
    assert [one.run_after for one in waiting.jobs] == [int(fake_clock.now()) + DAY]


# --- the history line a task's run writes ---------------------------------------------------------


async def test_a_declared_tasks_run_writes_its_line_in_the_history(job_queue: JobQueue) -> None:
    _noop("tidy_up")
    job_queue.record_runs_of("tidy_up", task_id="tidy", title="Tidy up")
    waiting = await job_queue.enqueue("tidy_up", {})
    await job_queue.cancel(waiting)
    await job_queue.enqueue("tidy_up", {})
    job = await job_queue.claim(WORKER)
    assert job is not None
    await job_queue.set_note(job.id, WORKER, "Tidied three things.")
    await job_queue.complete(job.id, WORKER)

    database = job_queue._db
    rows = await database.fetch_all(
        "SELECT d.verb, d.payload FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE s.kind = 'run' AND s.subject_id = 'tidy'",
        (),
    )
    assert [row["verb"] for row in rows] == ["ran"], "a run that never started writes nothing"
    assert '"outcome": "done"' in rows[0]["payload"]
    assert "Tidied three things." in rows[0]["payload"]


def _tidy(task_id: str = "tidy", job_type: str = "tidy_up", **more: object) -> ScheduledTask:
    fields: dict[str, object] = {
        "id": task_id,
        "title": "Tidy up",
        "explain": "Tidies up.",
        "job_type": job_type,
        "every": lambda _values: DAY,
    }
    fields.update(more)
    return ScheduledTask(**fields)  # type: ignore[arg-type]


async def _day_range() -> tuple[str, str]:
    return "23:00", "07:00"


async def _scheduler(
    temp_db: Database, fake_clock: FakeClock, values: Values | None = None
) -> tuple[JobQueue, TaskClock]:
    await temp_db.initialize_schema()
    queue = JobQueue(temp_db, clock=fake_clock.now)
    read = (values or Values()).get
    return queue, TaskClock(queue, read=read, quiet_range=_day_range, clock=fake_clock.now)


async def _waiting_at(queue: JobQueue, job_type: str) -> list[int | None]:
    page = await queue.list(job_type=job_type, state=JobState.QUEUED)
    return [one.run_after for one in page.jobs]


async def test_a_task_that_is_not_there_or_not_timed_is_never_placed(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    """Only a timed task has a clock: an id nobody declared, and a task that waits for work, are
    answered with no moment and put nothing in the queue."""
    _noop("walk_up")
    register_schedule(_tidy("walk", "walk_up", every=None))
    queue, clock = await _scheduler(temp_db, fake_clock)

    assert await clock.ensure("nobody") is None
    assert await clock.ensure("walk") is None
    assert await _waiting_at(queue, "walk_up") == []


async def test_two_waiting_runs_of_one_schedule_are_put_back_to_one(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    """However it happened (an older version, two boots racing), two of one schedule waiting is
    one too many, and a reschedule leaves exactly one, at the moment worked out now."""
    _noop("tidy_up")
    register_schedule(_tidy())
    queue, clock = await _scheduler(temp_db, fake_clock)
    now = int(fake_clock.now())
    for later in (100, 200):
        await queue.enqueue("tidy_up", {}, run_after=now + later)

    moment = await clock.reschedule("tidy")

    assert moment is not None
    assert await _waiting_at(queue, "tidy_up") == [moment]


async def test_a_waiting_run_is_moved_to_the_moment_worked_out_now(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    """Placed from the old answer, a waiting run would run once more at the old time: moving it is
    what makes a change take effect on the next run rather than the one after."""
    _noop("tidy_up")
    register_schedule(_tidy())
    queue, clock = await _scheduler(temp_db, fake_clock)
    now = int(fake_clock.now())
    assert await clock.ensure("tidy", since=now) == now + DAY

    moved = await clock.ensure("tidy", since=now + 600, move=True)

    assert moved == now + DAY + 600
    assert await _waiting_at(queue, "tidy_up") == [now + DAY + 600]


async def test_a_press_waiting_is_the_next_run_and_nothing_is_placed_beside_it(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    """Somebody pressed Run now and it has not started: the run after it is placed once it has
    settled, from when it ended, rather than queued behind it now."""
    _noop("tidy_up")
    register_schedule(_tidy())
    queue, clock = await _scheduler(temp_db, fake_clock)
    pressed = await queue.enqueue("tidy_up", {}, requested_by="someone")

    assert await clock.ensure("tidy") is not None

    page = await queue.list(job_type="tidy_up", state=JobState.QUEUED)
    assert [one.id for one in page.jobs] == [pressed]


async def test_a_press_running_is_the_run_and_nothing_is_placed_beside_it_until_it_settles(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    """A backup set to run on a schedule while a pressed one is running queues no second one
    behind it, due immediately: a press under way counts, not only a press WAITING. A run under way
    is the run; the next is placed when it settles, from when it ended."""
    _noop("tidy_up")
    register_schedule(_tidy())
    queue, clock = await _scheduler(temp_db, fake_clock)
    await queue.enqueue("tidy_up", {}, requested_by="someone")
    job = await queue.claim(WORKER)
    assert job is not None

    assert await clock.reschedule("tidy") is not None
    assert await _waiting_at(queue, "tidy_up") == [], "a second run queued beside the running one"

    await queue.complete(job.id, WORKER)
    await clock.settled(job.id)
    assert len(await _waiting_at(queue, "tidy_up")) == 1


async def test_a_change_to_a_setting_moves_only_the_tasks_that_read_it(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    _noop("tidy_up")
    _noop("sweep_up")
    register_schedule(_tidy())
    register_schedule(_tidy("sweep", "sweep_up"))
    queue, clock = await _scheduler(temp_db, fake_clock)

    await clock.reschedule_reading({"tasks.tidy.when"})

    assert len(await _waiting_at(queue, "tidy_up")) == 1
    assert await _waiting_at(queue, "sweep_up") == []

    await clock.reschedule_all()

    assert len(await _waiting_at(queue, "tidy_up")) == 1
    assert len(await _waiting_at(queue, "sweep_up")) == 1


async def test_at_boot_one_task_that_cannot_be_placed_does_not_stop_the_others(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    """A setting that fails to read is one task unscheduled and a line in the log, never every
    other task unscheduled with it."""
    _noop("tidy_up")
    _noop("sweep_up")

    def broken(_values: object) -> int:
        raise ValueError("a stored value nobody can read")

    register_schedule(_tidy("broken", "sweep_up", every=broken))
    register_schedule(_tidy())
    queue, clock = await _scheduler(temp_db, fake_clock)

    await clock.ensure_all()

    assert len(await _waiting_at(queue, "tidy_up")) == 1
    assert await _waiting_at(queue, "sweep_up") == []


async def test_a_job_that_is_gone_or_belongs_to_no_timed_task_places_nothing_when_it_settles(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    _noop("tidy_up")
    _noop("other_work")
    register_schedule(_tidy())
    queue, clock = await _scheduler(temp_db, fake_clock)
    other = await queue.enqueue("other_work", {})

    await clock.settled("01HX0000000000000000000099")
    await clock.settled(other)

    assert await _waiting_at(queue, "tidy_up") == []


async def test_with_no_since_the_next_run_is_counted_from_the_last_run_that_finished(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    """The queue's own record of the task's runs is where the clock starts from when nobody says:
    a daily task that last finished an hour ago runs again twenty-three hours from now, whatever
    is running now."""
    _noop("tidy_up")
    task = _tidy()
    register_schedule(task)
    queue, clock = await _scheduler(temp_db, fake_clock)
    await queue.enqueue("tidy_up", {}, requested_by="someone")
    job = await queue.claim(WORKER)
    assert job is not None
    await queue.complete(job.id, WORKER)
    finished = int(fake_clock.now())
    fake_clock.advance(3600)
    # And a press running now: a run still going is not the last one that ran.
    await queue.enqueue("tidy_up", {}, requested_by="someone")
    assert await queue.claim(WORKER) is not None

    assert await clock.next_run(task) == finished + DAY


async def test_a_slice_reaches_the_installed_scheduler_and_nothing_when_none_is(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    """A route that saves a schedule of its own says "that moved" through the one the composition
    root installed; a process that never installed one schedules nothing."""
    from sift.kernel.jobs import clock as clock_module

    _noop("tidy_up")
    register_schedule(_tidy())
    queue, clock = await _scheduler(temp_db, fake_clock)
    try:
        clock_module.install(None)
        assert await clock_module.reschedule("tidy") is None
        assert await _waiting_at(queue, "tidy_up") == []

        clock_module.install(clock)
        moment = await clock_module.reschedule("tidy")
    finally:
        clock_module.install(None)

    assert moment is not None
    assert await _waiting_at(queue, "tidy_up") == [moment]


async def test_a_run_somebody_pressed_is_written_as_theirs(job_queue: JobQueue) -> None:
    """A press by an account that still exists is that person's act on the record; the line says
    who ran it rather than that Sift did."""
    from sift.kernel.access import Role
    from sift.testing.fixtures import create_user

    presser = await create_user(job_queue._db, Role.ADMIN)
    _noop("tidy_up")
    job_queue.record_runs_of("tidy_up", task_id="tidy", title="Tidy up")
    await job_queue.enqueue("tidy_up", {}, requested_by=presser.id)
    job = await job_queue.claim(WORKER)
    assert job is not None
    await job_queue.complete(job.id, WORKER)

    rows = await job_queue._db.fetch_all(
        "SELECT d.actor_kind, d.actor_id FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE s.kind = 'run' AND s.subject_id = 'tidy'",
        (),
    )
    assert [(row["actor_kind"], row["actor_id"]) for row in rows] == [("user", presser.id)]


# --- a press never moves the schedule --------------------------------------------------------------

#: Midnight UTC, 15 November 2023, the day the tests below start on (the suite's zone is UTC).
_DAY_ONE = 1_700_006_400
_HOUR = 3600


def _at_three() -> ScheduledTask:
    """A daily task at three in the afternoon, the shape of a backup on a schedule."""
    return _tidy(at=lambda _values: "15:00")


async def _ran(queue: JobQueue, fake_clock: FakeClock, *, by: str | None, minutes: int = 5) -> None:
    """One run of the task, pressed by `by` or started by the schedule (its waiting row)."""
    if by is not None:
        await queue.enqueue("tidy_up", {}, requested_by=by)
    job = await queue.claim(WORKER)
    assert job is not None
    assert job.requested_by == by
    fake_clock.advance(minutes * 60)
    await queue.complete(job.id, WORKER)


async def _the_schedule_ran_at_three_on_day_one(
    temp_db: Database, fake_clock: FakeClock
) -> tuple[JobQueue, TaskClock]:
    _noop("tidy_up")
    register_schedule(_at_three())
    fake_clock.advance(_DAY_ONE + 15 * _HOUR - fake_clock.now())
    queue, clock = await _scheduler(temp_db, fake_clock)
    await queue.enqueue("tidy_up", {})
    await _ran(queue, fake_clock, by=None)
    assert await _waiting_at(queue, "tidy_up") == [_DAY_ONE + DAY + 15 * _HOUR]
    return queue, clock


async def test_a_press_before_the_time_of_day_never_moves_the_next_run(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    """ "Every day at 3 PM" means 3 PM. A press eleven hours before it, then a setting change, then
    a boot: the next run is today's at each step. Counted from the press, the change and the boot
    would put it off to tomorrow's."""
    queue, clock = await _the_schedule_ran_at_three_on_day_one(temp_db, fake_clock)
    today_at_three = _DAY_ONE + DAY + 15 * _HOUR
    fake_clock.advance(today_at_three - 11 * _HOUR - fake_clock.now())

    await _ran(queue, fake_clock, by="someone")
    assert await _waiting_at(queue, "tidy_up") == [today_at_three], "the press settling"

    assert await clock.reschedule("tidy") == today_at_three
    assert await _waiting_at(queue, "tidy_up") == [today_at_three], "a setting changed"

    booted = TaskClock(queue, read=Values().get, quiet_range=_day_range, clock=fake_clock.now)
    await booted.ensure_all()
    assert await _waiting_at(queue, "tidy_up") == [today_at_three], "a boot"


async def test_a_press_settling_with_nothing_waiting_places_the_schedules_own_next_run(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    """With the waiting row gone (taken back, or never placed), the press settling places the next
    run from the schedule's own last run, not from the press."""
    queue, _clock = await _the_schedule_ran_at_three_on_day_one(temp_db, fake_clock)
    today_at_three = _DAY_ONE + DAY + 15 * _HOUR
    await queue.withdraw_waiting("tidy_up")
    fake_clock.advance(today_at_three - 11 * _HOUR - fake_clock.now())

    await _ran(queue, fake_clock, by="someone")

    assert await _waiting_at(queue, "tidy_up") == [today_at_three]


async def test_a_press_after_the_run_fell_due_is_the_catch_up(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    """A schedule that is behind runs immediately; a press made since it fell due was that run, so the
    schedule goes on from the press rather than running a second one straight after it."""
    queue, clock = await _the_schedule_ran_at_three_on_day_one(temp_db, fake_clock)
    await queue.withdraw_waiting("tidy_up")
    fake_clock.advance(_DAY_ONE + 3 * DAY + 10 * _HOUR - fake_clock.now())
    await _ran(queue, fake_clock, by="someone")

    assert await clock.reschedule("tidy") == _DAY_ONE + 4 * DAY + 15 * _HOUR


async def test_a_press_before_the_run_fell_due_is_not_the_catch_up(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    """A press made before the schedule's run fell due was not that run: the schedule, behind,
    runs immediately."""
    queue, clock = await _the_schedule_ran_at_three_on_day_one(temp_db, fake_clock)
    await queue.withdraw_waiting("tidy_up")
    fake_clock.advance(_DAY_ONE + 20 * _HOUR - fake_clock.now())
    await _ran(queue, fake_clock, by="someone")
    fake_clock.advance(2 * DAY)

    assert await clock.reschedule("tidy") == int(fake_clock.now())


async def test_the_prune_keeps_the_schedules_last_run_behind_a_newer_press(
    temp_db: Database, task_registry: None, fake_clock: FakeClock
) -> None:
    """The scheduler counts from the schedule's own last run, so the prune keeps it for as long as
    it is that run, a newer press beside it; an older run of the schedule goes."""
    _noop("tidy_up")
    queue, _clock = await _scheduler(temp_db, fake_clock)
    await queue.enqueue("tidy_up", {})
    await _ran(queue, fake_clock, by=None)
    await queue.enqueue("tidy_up", {})
    await _ran(queue, fake_clock, by=None)
    await _ran(queue, fake_clock, by="someone")
    fake_clock.advance(30 * DAY)

    while await queue.prune_settled():
        pass

    runs = await queue.task_runs(["tidy_up"], most=5)
    assert [run.runs_total for run in runs["tidy_up"]][:1] == [2]
    own = await queue.last_finished_runs(["tidy_up"], started_by="schedule")
    pressed = await queue.last_finished_runs(["tidy_up"], started_by="press")
    assert own["tidy_up"].id != pressed["tidy_up"].id


async def test_who_started_a_run_is_one_of_three_answers(job_queue: JobQueue) -> None:
    with pytest.raises(ValueError, match="started_by"):
        await job_queue.last_finished_runs(["tidy_up"], started_by="somebody")
