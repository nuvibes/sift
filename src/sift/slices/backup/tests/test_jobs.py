# SPDX-License-Identifier: AGPL-3.0-or-later
"""The schedule, which is a row in the queue rather than a timer, placed by the one scheduler.

The property worth proving is the one a timer does not have: a machine that was off when the
backup was due comes back to a job whose moment has passed and runs it, rather than to a timer
that never fired and a night with no backup and nothing anywhere saying so.

The next run is placed by `kernel.jobs.clock.TaskClock`, not by the backup: every timed task's is.
These tests drive it with the backup's own declaration, so they prove the backup's schedule rather
than the clock's arithmetic (which `kernel/tests/test_task_clock.py` holds).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sift.kernel.db import Database
from sift.kernel.jobs import JobQueue, JobState, registered_handlers
from sift.kernel.jobs.clock import TaskClock
from sift.kernel.jobs.quiet_hours import next_clock_time
from sift.kernel.jobs.schedules import when_key
from sift.slices.backup.jobs import BACKUP_RUN, register_handlers, run_backup
from sift.slices.backup.naming import SAVED_MARK, is_backup_filename
from sift.slices.backup.service import (
    AT_KEY,
    DEFAULT_AT,
    EVERY_DAYS_KEY,
    FOLDER_KEY,
    KEEP_KEY,
    BackupService,
)
from sift.slices.settings_hub import SettingsService
from sift.testing.fixtures import Actors, FakeClock

pytestmark = pytest.mark.anyio

WHEN = when_key("backup")

DAY = 24 * 3600


def placed(since: float, days: int, at: str = DEFAULT_AT) -> int:
    """Where a backup on a schedule that last ended at `since` runs next: at its time of day on
    the day it falls due, up to half a day early (`quiet_hours.due_at`)."""
    every = days * DAY
    return next_clock_time(at, int(since) + every - min(12 * 3600, every // 2))


class Ran:
    """A job context that records the progress a handler reports, for a run pressed or not."""

    def __init__(self, *, pressed: bool = False, by: str | None = None) -> None:
        self.progress: list[float] = []
        self.notes: list[str] = []
        self.job = SimpleNamespace(timing="now" if pressed else None)
        #: Who pressed it (`JobContext.pressed_by`): None for a run nobody pressed.
        self.pressed_by = by

    async def set_progress(self, fraction: float) -> None:
        self.progress.append(fraction)

    async def set_note(self, note: str) -> None:
        self.notes.append(note)


@pytest.fixture
async def queue(prepared_db: Database, fake_clock: FakeClock) -> JobQueue:
    """On the same clock as the backup service, so "is this job's time yet" means what it says."""
    return JobQueue(prepared_db, clock=fake_clock.now)


@pytest.fixture
def clock(queue: JobQueue, preferences: SettingsService, fake_clock: FakeClock) -> TaskClock:
    """The scheduler, reading the real settings, with quiet hours at their defaults."""

    async def quiet_range() -> tuple[str, str]:
        return "23:00", "07:00"

    return TaskClock(queue, read=preferences.get_app, quiet_range=quiet_range, clock=fake_clock.now)


async def turn_on(
    preferences: SettingsService,
    actors: Actors,
    folder: Path,
    *,
    every: int = 1,
    keep: int = 7,
    when: str = "work",
) -> None:
    await preferences.apply(
        actors.admin,
        {EVERY_DAYS_KEY: every, FOLDER_KEY: str(folder), KEEP_KEY: keep, WHEN: when},
    )


async def test_a_run_writes_a_backup_and_leaves_its_next_run_to_the_scheduler(
    backup: BackupService,
    queue: JobQueue,
    preferences: SettingsService,
    elsewhere: Path,
    actors: Actors,
) -> None:
    """The backup does not queue its own successor: a second writer of that row would let a
    schedule change leave the old moment in place."""
    await turn_on(preferences, actors, elsewhere)
    register_handlers(service=backup, queue=queue)

    ran = Ran()
    await run_backup(ran, service=backup, queue=queue)  # type: ignore[arg-type]

    (saved,) = list(elsewhere.iterdir())
    assert (await queue.list(job_type=BACKUP_RUN, state=JobState.QUEUED)).total == 0
    # The run's own sentence names the file it saved, never its path.
    assert ran.notes == [f"Saved {saved.name}."]


async def test_a_run_nobody_pressed_while_only_a_press_runs_it_backs_nothing_up(
    backup: BackupService,
    queue: JobQueue,
    elsewhere: Path,
    preferences: SettingsService,
    actors: Actors,
) -> None:
    await preferences.apply(actors.admin, {WHEN: "press", FOLDER_KEY: str(elsewhere)})
    register_handlers(service=backup, queue=queue)

    await run_backup(Ran(), service=backup, queue=queue)  # type: ignore[arg-type]

    assert list(elsewhere.iterdir()) == []


async def test_a_pressed_run_backs_up_whatever_the_schedule_says(
    backup: BackupService,
    queue: JobQueue,
    elsewhere: Path,
    preferences: SettingsService,
    actors: Actors,
) -> None:
    """Run now on a backup set to Only when I press it is somebody asking for a backup now, and it
    must not return having saved nothing."""
    await preferences.apply(actors.admin, {WHEN: "press", FOLDER_KEY: str(elsewhere)})
    register_handlers(service=backup, queue=queue)

    await run_backup(Ran(pressed=True), service=backup, queue=queue)  # type: ignore[arg-type]

    assert len(list(elsewhere.iterdir())) == 1


async def test_a_pressed_run_is_kept_as_a_backup_saved_by_hand_and_deletes_nothing(
    backup: BackupService,
    queue: JobQueue,
    elsewhere: Path,
    preferences: SettingsService,
    actors: Actors,
    fake_clock: FakeClock,
) -> None:
    """A backup somebody pressed for is theirs: its name carries the saved mark, so neither the
    count nor the age rule ever takes it, and the press itself rotates no automatic backup away.
    A run nobody pressed, in the same folder, is an automatic one and still rotates."""
    await turn_on(preferences, actors, elsewhere, keep=1)
    register_handlers(service=backup, queue=queue)
    library = await backup.library_mark()

    await run_backup(Ran(), service=backup, queue=queue)  # type: ignore[arg-type]
    (automatic,) = list(elsewhere.iterdir())
    assert is_backup_filename(automatic.name, library=library)

    fake_clock.advance(60)
    await run_backup(Ran(pressed=True), service=backup, queue=queue)  # type: ignore[arg-type]
    (pressed,) = [one for one in elsewhere.iterdir() if one != automatic]
    assert SAVED_MARK in pressed.name
    assert not is_backup_filename(pressed.name, library=library)
    # Keeping one, and the press took nothing: the automatic backup before it is still there.
    assert automatic.exists()

    fake_clock.advance(60)
    await run_backup(Ran(), service=backup, queue=queue)  # type: ignore[arg-type]
    left = {one.name for one in elsewhere.iterdir()}
    assert pressed.name in left
    assert automatic.name not in left
    assert len(left) == 2


async def test_run_now_is_said_on_history_as_the_person_s_saved_backup_and_a_schedule_is_not(
    backup: BackupService,
    queue: JobQueue,
    elsewhere: Path,
    preferences: SettingsService,
    actors: Actors,
    prepared_db: Database,
    fake_clock: FakeClock,
) -> None:
    """The task's Run now writes the line the pane's Save a backup writes: who saved which backup
    and the folder it went to. A run nobody pressed, and a press by a user removed since, say
    nothing there."""
    await turn_on(preferences, actors, elsewhere, keep=5)
    register_handlers(service=backup, queue=queue)

    for run in (Ran(), Ran(pressed=True, by="nobody"), Ran(pressed=True, by=actors.admin.id)):
        fake_clock.advance(60)
        await run_backup(run, service=backup, queue=queue)  # type: ignore[arg-type]

    rows = await prepared_db.fetch_all(
        "SELECT d.actor_id AS who, d.payload AS payload, s.kind AS kind, s.subject_id AS id"
        " FROM workbench_decisions d JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE d.verb = 'saved'"
    )
    [(who, payload, kind, name)] = [tuple(row) for row in rows]
    assert (who, kind) == (actors.admin.id, "backup")
    assert Path(json.loads(payload)["folder"]).resolve() == elsewhere.resolve()
    assert SAVED_MARK in name and (elsewhere / name).is_file()


async def test_a_backup_missed_while_the_machine_was_off_is_claimable_the_moment_it_is_back(
    backup: BackupService,
    queue: JobQueue,
    clock: TaskClock,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    """The reason the schedule is a row and not a timer: a week with nothing running, and a worker
    claims it immediately rather than waiting for another day."""
    await turn_on(preferences, actors, elsewhere)
    register_handlers(service=backup, queue=queue)
    # A run on record, so the next is on the day after it rather than due immediately.
    moment = await clock.ensure("backup", since=int(fake_clock.now()))
    assert moment == placed(fake_clock.now(), 1)
    assert moment > fake_clock.now()

    assert await queue.claim("a-worker") is None, "nothing claims it while its time is ahead"

    fake_clock.advance(7 * 24 * 3600)
    claimed = await queue.claim("a-worker")

    assert claimed is not None
    assert claimed.type == BACKUP_RUN


async def test_a_second_schedule_is_not_queued_beside_a_pending_one(
    backup: BackupService,
    queue: JobQueue,
    clock: TaskClock,
    preferences: SettingsService,
    elsewhere: Path,
    actors: Actors,
) -> None:
    await turn_on(preferences, actors, elsewhere)
    register_handlers(service=backup, queue=queue)

    await clock.ensure("backup")
    await clock.ensure("backup")

    assert (await queue.list(job_type=BACKUP_RUN, state=JobState.QUEUED)).total == 1


async def test_nothing_is_queued_while_the_schedule_is_off(
    backup: BackupService, queue: JobQueue, clock: TaskClock
) -> None:
    """A job on the screen for work nobody asked for is a job somebody has to wonder about."""
    register_handlers(service=backup, queue=queue)

    assert await clock.ensure("backup") is None
    assert (await queue.list(job_type=BACKUP_RUN)).total == 0


async def test_a_weekly_schedule_waits_a_week_from_the_last_run(
    backup: BackupService,
    queue: JobQueue,
    clock: TaskClock,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    await turn_on(preferences, actors, elsewhere, every=7)
    register_handlers(service=backup, queue=queue)
    await clock.ensure("backup", since=int(fake_clock.now()))

    queued = await queue.list(job_type=BACKUP_RUN, state=JobState.QUEUED)
    assert queued.jobs[0].run_after == placed(fake_clock.now(), 7)


async def test_changing_the_schedule_moves_the_waiting_run_and_off_takes_it_back(
    backup: BackupService,
    queue: JobQueue,
    clock: TaskClock,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    """Daily to weekly moves the waiting run, and Off does not leave it queued to wake up and log
    "off". A change reschedules; Off (and Only when I press it) leaves nothing waiting."""
    await turn_on(preferences, actors, elsewhere)
    register_handlers(service=backup, queue=queue)
    # One run that really ended now, through the queue: its settling places the next, a day on.
    await queue.enqueue(BACKUP_RUN, {})
    ran = await queue.claim("a-worker")
    assert ran is not None
    await queue.complete(ran.id, "a-worker")
    waiting = await queue.list(job_type=BACKUP_RUN, state=JobState.QUEUED)
    assert [one.run_after for one in waiting.jobs] == [placed(fake_clock.now(), 1)]

    await preferences.apply(actors.admin, {EVERY_DAYS_KEY: 7})
    await clock.reschedule("backup")
    waiting = await queue.list(job_type=BACKUP_RUN, state=JobState.QUEUED)
    assert [one.run_after for one in waiting.jobs] == [placed(fake_clock.now(), 7)], (
        "moved to a week after the last run, and not doubled"
    )

    await preferences.apply(actors.admin, {AT_KEY: "14:30"})
    await clock.reschedule("backup")
    waiting = await queue.list(job_type=BACKUP_RUN, state=JobState.QUEUED)
    assert [one.run_after for one in waiting.jobs] == [placed(fake_clock.now(), 7, "14:30")], (
        "a new time of day moves it too"
    )

    await preferences.apply(actors.admin, {WHEN: "press"})
    await clock.reschedule("backup")
    assert (await queue.list(job_type=BACKUP_RUN, state=JobState.QUEUED)).total == 0

    # The retired "How often" still answers Off, through the When it went to.
    await preferences.apply(actors.admin, {WHEN: "work"})
    await preferences.apply(actors.admin, {"backup.schedule": "off"})
    assert await preferences.get_app(WHEN) == "press"
    await clock.reschedule("backup")
    assert (await queue.list(job_type=BACKUP_RUN, state=JobState.QUEUED)).total == 0


async def test_the_handler_is_claimed_before_anything_can_queue_one(
    backup: BackupService, queue: JobQueue
) -> None:
    """The queue refuses a job type nothing can execute, so registration comes first."""
    register_handlers(service=backup, queue=queue)
    assert BACKUP_RUN in registered_handlers()


async def test_the_registered_handler_really_takes_a_backup(
    backup: BackupService,
    queue: JobQueue,
    preferences: SettingsService,
    elsewhere: Path,
    actors: Actors,
) -> None:
    """Registration wires the real service through, rather than a handler that does nothing."""
    await turn_on(preferences, actors, elsewhere)
    register_handlers(service=backup, queue=queue)

    await registered_handlers()[BACKUP_RUN](Ran())  # type: ignore[arg-type]

    assert len(list(elsewhere.iterdir())) == 1
