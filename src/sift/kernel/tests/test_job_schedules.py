# SPDX-License-Identifier: AGPL-3.0-or-later
"""The task registry: one When per task, what it refuses at declaration, and quiet hours' clock.

The refusals matter more than they look. Every one of them is a mistake that would otherwise reach
a screen as a row nobody can act on: a task nothing can run, a task naming a setting that does not
exist, a When nobody can choose. Caught here, the failure is at the declaration that caused it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest

import sift.main  # noqa: F401 (imported for its side effect: every slice declares its tasks)
from sift.kernel import settings_registry
from sift.kernel.jobs import schedules
from sift.kernel.jobs.quiet_hours import (
    WHEN_PRESS,
    WHEN_QUIET,
    WHEN_WORK,
    due_at,
    is_open,
    next_closing,
    next_opening,
    time_of_day,
    within,
)
from sift.kernel.jobs.schedules import (
    RUN_NOW,
    WHEN_MIXED,
    ScheduledTask,
    ScheduleError,
    get_schedule,
    register_schedule,
    registered_schedules,
    scheduled_job_types,
    shown_schedules,
    when_key,
)
from sift.kernel.settings_registry import get_registered, get_removed, get_retired
from sift.slices.backup.service import AT_KEY, EVERY_DAYS_KEY

DAY = 24 * 3600


@pytest.fixture
def clean_registry() -> Iterator[None]:
    """Declarations made by a test are taken back: the task AND the When it registered."""
    kept_tasks = dict(schedules._REGISTRY)
    kept_settings = dict(settings_registry._REGISTRY)
    kept_retired = dict(settings_registry._RETIRED)
    try:
        yield
    finally:
        schedules._REGISTRY.clear()
        schedules._REGISTRY.update(kept_tasks)
        settings_registry._REGISTRY.clear()
        settings_registry._REGISTRY.update(kept_settings)
        settings_registry._RETIRED.clear()
        settings_registry._RETIRED.update(kept_retired)


def _task(**changes: object) -> ScheduledTask:
    fields: dict[str, object] = {
        "id": "nightly-tidying",
        "title": "Nightly tidying",
        "explain": "Puts the library's records in order while nobody is looking.",
        "setting_keys": (EVERY_DAYS_KEY,),
        "job_type": "backup_run",
    }
    fields.update(changes)
    return ScheduledTask(**fields)  # type: ignore[arg-type]


# --- what the registry refuses ------------------------------------------------------------------


def test_a_task_nothing_can_run_is_refused(clean_registry: None) -> None:
    """Run now is the one thing every row offers, and a task with no job has nothing to queue."""
    with pytest.raises(ScheduleError, match="nothing could run it"):
        register_schedule(_task(job_type=None))


def test_a_task_naming_a_setting_nobody_registered_is_refused(clean_registry: None) -> None:
    with pytest.raises(ScheduleError, match="nothing has registered"):
        register_schedule(_task(setting_keys=("no.such.setting",)))
    assert get_registered(when_key("nightly-tidying")) is None, "and nothing was half-declared"


def test_a_when_the_task_does_not_offer_cannot_be_its_default(clean_registry: None) -> None:
    with pytest.raises(ScheduleError, match="does not offer"):
        register_schedule(_task(whens=(WHEN_WORK, WHEN_PRESS), when_default=WHEN_QUIET))


@pytest.mark.parametrize(
    ("changes", "refusal"),
    [
        ({"id": "  "}, "needs an id"),
        ({"title": ""}, "needs a title"),
        ({"explain": " "}, "needs a line saying what it does"),
        ({"whens": (WHEN_WORK, "whenever")}, "does not know"),
        ({"whens": ()}, "does not know"),
    ],
)
def test_a_task_with_nothing_to_draw_its_row_from_is_refused(
    clean_registry: None, changes: dict[str, object], refusal: str
) -> None:
    """Each of these is a row on the Tasks screen with a blank where its name or its line goes, or
    a When nobody can choose, refused at the declaration rather than drawn."""
    with pytest.raises(ScheduleError, match=refusal):
        register_schedule(_task(**changes))


def test_a_task_that_waits_for_work_has_no_interval() -> None:
    """Only a timed task has seconds between runs; one that waits for work is placed by the work."""
    assert _task().interval({}) is None


def test_a_task_cannot_be_declared_twice(clean_registry: None) -> None:
    register_schedule(_task())
    with pytest.raises(ScheduleError, match="twice"):
        register_schedule(_task())


def test_declaring_a_task_registers_its_when_as_a_setting(clean_registry: None) -> None:
    """ONE setting per task, and the declaration makes it: the Tasks screen and the owning pane
    draw the same row, and there is no second path to the stored value."""
    register_schedule(_task(when_default=WHEN_QUIET))
    declared = get_registered(when_key("nightly-tidying"))
    assert declared is not None
    assert declared.default == WHEN_QUIET
    assert declared.choices == (WHEN_WORK, WHEN_QUIET, WHEN_PRESS)
    assert declared.choice_labels == (
        "As files arrive",
        "During quiet hours",
        "Only when I press it",
    )
    assert get_schedule("nightly-tidying") is not None


def test_the_first_when_is_named_for_what_starts_the_task() -> None:
    """One stored value, two names: a task that waits for files starts "As files arrive", a timed
    one "On a schedule". Never with its interval beside it: a longer answer would make one task's
    choice wider than every other row's and push its press onto a second line."""
    tasks = registered_schedules()
    assert tasks["generate"].when_labels({})[WHEN_WORK] == "As files arrive"
    assert tasks["scan"].cadence({}) == "As files arrive"
    backup = tasks["backup"]
    daily = {EVERY_DAYS_KEY: 1}
    assert backup.when_labels(daily)[WHEN_WORK] == "On a schedule"
    assert backup.when_labels()[WHEN_WORK] == "On a schedule"
    declared = get_registered(backup.when_key)
    assert declared is not None and declared.choice_labels is not None
    assert declared.choice_labels[0] == "On a schedule"
    for task in tasks.values():
        assert list(task.when_labels({})) == list(task.whens)
        assert task.when_labels({})[WHEN_PRESS] == "Only when I press it"


def test_a_new_library_generates_as_files_arrive() -> None:
    """The default a value never written reads as: a new library gets its pictures with nothing
    pressed. Music stays press-only."""
    tasks = registered_schedules()
    assert tasks["generate"].when({}) == WHEN_WORK
    assert tasks["scan"].when({}) == WHEN_WORK
    assert tasks["music"].when({}) == WHEN_PRESS
    # A value somebody wrote is what they wrote, whatever the default.
    assert tasks["generate"].when({when_key("generate"): WHEN_PRESS}) == WHEN_PRESS


def test_identify_runs_as_files_arrive_once_its_switch_is_on() -> None:
    """Identify's three switches stay off out of the box; their When says when they run once
    somebody turns one on, and a switch turned on means as files arrive unless somebody chose
    another answer, so one default is enough."""
    tasks = registered_schedules()
    for identify in ("faces", "smart-search", "watermarks"):
        assert tasks[identify].when({}) == WHEN_WORK, identify


# --- what the declared tasks say ----------------------------------------------------------------


def test_every_task_the_plan_names_is_declared_with_a_when() -> None:
    """Scan, Generate, recognition, Smart Search, music fingerprints, duplicates, suggestions,
    shoots, the stash-box lookups, the backup, the two clean-ups and the update check."""
    expected = {
        "scan",
        "generate",
        "faces",
        "smart-search",
        "watermarks",
        "music",
        "duplicates",
        "suggestions",
        "shoots",
        "enrichment",
        "backup",
        "quarantine-prune",
        "search-records-prune",
        "update-check",
    }
    assert expected <= set(registered_schedules())
    for task_id in expected:
        assert get_registered(when_key(task_id)) is not None, task_id
    assert "recognition-night" not in registered_schedules(), "quiet hours is not a task"


def test_the_identify_stage_reads_its_three_whens_and_writes_all_of_them() -> None:
    """Identify's When is a reading, never a stored value of its own: the three tasks' shared
    answer or mixed, and an answer chosen for it lands on every one of them."""
    identify = registered_schedules()["identify"]
    three = tuple(when_key(one) for one in ("faces", "smart-search", "watermarks"))
    assert get_registered(identify.when_key) is None, "a stored Identify When could disagree"
    master = get_retired(identify.when_key)
    assert master is not None and master.into == three
    assert master.read((WHEN_QUIET, WHEN_QUIET, WHEN_QUIET)) == WHEN_QUIET
    assert master.read((WHEN_QUIET, WHEN_WORK, WHEN_QUIET)) == WHEN_MIXED
    assert master.write(WHEN_WORK, (WHEN_QUIET, WHEN_PRESS, WHEN_QUIET)) == (WHEN_WORK,) * 3
    with pytest.raises(settings_registry.SettingError):
        master.write(WHEN_MIXED, (WHEN_QUIET, WHEN_WORK, WHEN_QUIET))
    assert identify.when({identify.when_key: WHEN_MIXED}) == WHEN_MIXED
    assert identify.cadence({identify.when_key: WHEN_MIXED}) == "Mixed"
    # A task that is not a reading never answers mixed, whatever is stored.
    assert registered_schedules()["scan"].when({when_key("scan"): WHEN_MIXED}) == WHEN_WORK


def test_the_three_stages_name_their_press_and_every_other_task_says_run_now() -> None:
    presses = {task.id: task.press for task in registered_schedules().values()}
    assert presses.pop("scan") == "Scan now"
    assert presses.pop("generate") == "Generate now"
    assert presses.pop("identify") == "Identify now"
    assert set(presses.values()) == {RUN_NOW}


def test_upkeep_nobody_times_is_on_no_pane_and_still_has_its_when() -> None:
    """The two prunes and the update check keep a When at its default and run on it; they are
    left out of what a pane draws."""
    unshown = {task.id for task in registered_schedules().values() if not task.shown}
    assert unshown == {"quarantine-prune", "search-records-prune", "update-check"}
    assert not unshown & set(shown_schedules())
    for task_id in unshown:
        assert get_registered(when_key(task_id)) is not None, task_id


def test_a_task_is_off_while_its_features_switch_is_off() -> None:
    task = _task(switch=EVERY_DAYS_KEY)
    assert task.switched_off({EVERY_DAYS_KEY: False}) is True
    assert task.switched_off({EVERY_DAYS_KEY: 1}) is False
    assert EVERY_DAYS_KEY in task.keys_read()
    assert _task().switched_off({}) is False, "a task with no switch is never switched off"


def test_a_reading_that_names_itself_or_keeps_a_clock_is_refused(clean_registry: None) -> None:
    with pytest.raises(ScheduleError, match="reads a When twice or reads its own"):
        register_schedule(_task(reads=("nightly-tidying", "scan")))
    with pytest.raises(ScheduleError, match="clock of its own"):
        register_schedule(_task(reads=("scan",), every=lambda _values: DAY))


def test_a_press_only_task_is_off_and_says_when_it_runs() -> None:
    backup = registered_schedules()["backup"]
    daily = {EVERY_DAYS_KEY: 1, backup.when_key: WHEN_WORK}
    assert backup.is_on(daily) is True
    assert backup.cadence(daily) == "Every day"
    assert backup.cadence({**daily, backup.when_key: WHEN_QUIET}) == "Every day, during quiet hours"
    assert backup.is_on({**daily, backup.when_key: WHEN_PRESS}) is False
    assert backup.cadence({**daily, backup.when_key: WHEN_PRESS}) == "Only when you run it"
    # Asked with the task starting on its own: a press-only task says so whatever its interval,
    # and the interval's own words are what this reads. The time of day is never in them: the
    # row's next run already says the moment.
    said = {
        days: backup.cadence({EVERY_DAYS_KEY: days, AT_KEY: "04:30", backup.when_key: WHEN_WORK})
        for days in (1, 3, 7, 14, 10)
    }
    assert said == {
        1: "Every day",
        3: "Every 3 days",
        7: "Every week",
        14: "Every 2 weeks",
        10: "Every 10 days",
    }
    assert backup.cadence({EVERY_DAYS_KEY: 3}) == "Only when you run it", "the default When"


def test_an_interval_shorter_than_a_day_is_said_in_hours_and_never_as_none() -> None:
    for seconds in (3600, 1800):
        said = _task(every=lambda _values, every=seconds: every).cadence({})
        assert said == "Every hour", "under an hour is said as every hour, never every 0 hours"
    assert _task(every=lambda _values: 6 * 3600).cadence({}) == "Every 6 hours"


def test_a_row_is_drawn_only_under_the_whens_it_means_something_under() -> None:
    """A time of day means nothing while only a press runs the task, so the screen is told the
    rows to draw for the When chosen now and keeps no copy of this rule."""
    task = _task(
        setting_keys=(EVERY_DAYS_KEY, AT_KEY),
        drawn_under={AT_KEY: (WHEN_WORK,)},
        every=lambda _values: DAY,
    )

    assert task.drawn_keys({task.when_key: WHEN_WORK}) == (EVERY_DAYS_KEY, AT_KEY)
    assert task.drawn_keys({task.when_key: WHEN_PRESS}) == (EVERY_DAYS_KEY,)


def test_a_time_of_day_is_read_only_on_its_schedule() -> None:
    backup = registered_schedules()["backup"]
    values = {EVERY_DAYS_KEY: 1, AT_KEY: "04:30"}
    assert backup.time_of_day({**values, backup.when_key: WHEN_WORK}) == "04:30"
    assert backup.time_of_day({**values, backup.when_key: WHEN_QUIET}) is None
    assert backup.time_of_day({**values, backup.when_key: WHEN_PRESS}) is None
    assert _task(every=lambda _values: DAY).time_of_day({}) is None, "a task that keeps none"


def test_a_time_of_day_without_a_clock_or_a_row_under_a_when_not_offered_is_refused(
    clean_registry: None,
) -> None:
    with pytest.raises(ScheduleError, match="no clock"):
        register_schedule(_task(at=lambda _values: "03:00"))
    with pytest.raises(ScheduleError, match="does not offer"):
        register_schedule(
            _task(whens=(WHEN_WORK, WHEN_PRESS), drawn_under={EVERY_DAYS_KEY: (WHEN_QUIET,)})
        )
    with pytest.raises(ScheduleError, match="not its own"):
        register_schedule(_task(drawn_under={AT_KEY: (WHEN_WORK,)}))


def test_the_quarantine_sweep_has_nothing_to_do_at_zero_days() -> None:
    sweep = registered_schedules()["quarantine-prune"]
    assert sweep.interval({"quarantine.keep_days": 0}) is None
    assert sweep.cadence({"quarantine.keep_days": 0}) == "Off"
    assert sweep.interval({"quarantine.keep_days": 30}) == DAY


def test_every_declared_task_reads_only_settings_it_has_named() -> None:
    for task in registered_schedules().values():
        assert set(task.setting_keys) <= set(task.keys_read())
        assert task.when_key in task.keys_read()


def test_every_timed_task_is_a_recurring_job_the_gate_knows() -> None:
    for task in registered_schedules().values():
        if task.every is not None:
            assert task.job_type in scheduled_job_types()


def test_every_old_switch_reads_through_its_tasks_when() -> None:
    """The on/off switches are retired into the Whens: read as anything but press-only, written as
    press-only for off, and "on" keeps quiet hours where they were chosen."""
    retired = get_retired("importing.generate")
    assert retired is not None and retired.into == (when_key("generate"),)
    assert retired.read((WHEN_PRESS,)) is False
    assert retired.read((WHEN_QUIET,)) is True
    assert retired.write(False, (WHEN_QUIET,)) == (WHEN_PRESS,)
    assert retired.write(True, (WHEN_PRESS,)) == (WHEN_WORK,)
    assert retired.write(True, (WHEN_QUIET,)) == (WHEN_QUIET,)
    master = get_retired("importing.identify")
    assert master is not None and len(master.into) == 3
    assert master.read((WHEN_PRESS, WHEN_PRESS, WHEN_WORK)) is True
    assert master.read((WHEN_PRESS, WHEN_PRESS, WHEN_PRESS)) is False
    # The two hours recognition once kept are removed keys now, named by History and read by nothing.
    assert get_retired("faces.nightly_from") is None
    assert get_removed("faces.nightly_from") is not None
    assert get_removed("faces.nightly_until") is not None


# --- quiet hours' clock -------------------------------------------------------------------------


def _at(when: str, *, days: int = 0) -> int:
    """A moment today (or `days` from today), in the device's own time."""
    hours, minutes = when.split(":")
    moment = datetime.now().replace(hour=int(hours), minute=int(minutes), second=0, microsecond=0)
    return int(moment.timestamp()) + days * DAY


def test_a_clock_time_nobody_can_read_falls_back_and_then_to_midnight() -> None:
    """The validator keeps these out; a value from before it, read anyway, is the default hour and
    never an error on the path every claim takes."""
    from datetime import time

    from sift.kernel.jobs.quiet_hours import DEFAULT_FROM, clock

    assert clock("23:30") == time(23, 30)
    assert clock("half past eleven") == clock(DEFAULT_FROM)
    assert clock("nonsense", fallback="also nonsense") == time(0, 0)


def test_the_range_closes_at_its_end_today_or_tomorrow() -> None:
    """Asked at two in the morning the range shuts at seven that morning; asked at eight, it shuts
    at seven the next."""
    early = int(datetime(2026, 3, 10, 2, 0).timestamp())
    late = int(datetime(2026, 3, 10, 8, 0).timestamp())

    assert next_closing("23:00", "07:00", early) == int(datetime(2026, 3, 10, 7, 0).timestamp())
    assert next_closing("23:00", "07:00", late) == int(datetime(2026, 3, 11, 7, 0).timestamp())


def test_a_range_through_midnight_is_open_either_side_of_it() -> None:
    assert is_open("23:00", "07:00", _at("23:30"))
    assert is_open("23:00", "07:00", _at("03:00"))
    assert not is_open("23:00", "07:00", _at("12:00"))


def test_equal_ends_are_the_whole_day_never_none_of_it() -> None:
    """Somebody who set both to the same hour meant always; never would hold every quiet-hours task
    back for good with nothing saying why."""
    assert is_open("02:00", "02:00", _at("14:00"))
    assert next_closing("02:00", "02:00", _at("14:00")) is None


def test_the_range_opens_now_tonight_or_tomorrow() -> None:
    assert next_opening("23:00", "07:00", _at("00:30")) == _at("00:30")
    assert next_opening("23:00", "07:00", _at("14:00")) == _at("23:00")
    assert next_opening("01:00", "05:00", _at("09:00")) == _at("01:00", days=1)


def test_a_timed_task_as_soon_as_there_is_work_runs_its_interval_after_the_last() -> None:
    since = _at("10:00")
    assert (
        due_at(when=WHEN_WORK, every=DAY, since=since, now=since, start="23:00", end="07:00")
        == since + DAY
    )


def test_a_nightly_task_runs_at_the_next_opening_on_the_day_it_falls_due() -> None:
    """Finished at 23:05, a daily task runs at tomorrow's 23:00, not the opening after it, which
    is what a plain "one day after" would reach."""
    since = _at("23:05")
    due = due_at(when=WHEN_QUIET, every=DAY, since=since, now=since, start="23:00", end="07:00")
    assert due == _at("23:00", days=1)


def test_a_weekly_nightly_task_is_never_moved_to_an_earlier_day() -> None:
    since = _at("23:05")
    due = due_at(when=WHEN_QUIET, every=7 * DAY, since=since, now=since, start="23:00", end="07:00")
    assert due == _at("23:00", days=7)


def test_a_task_that_fell_due_while_the_device_was_off_runs_now() -> None:
    since = _at("10:00", days=-9)
    now = _at("10:00")
    assert (
        due_at(when=WHEN_WORK, every=DAY, since=since, now=now, start="23:00", end="07:00") == now
    )


def test_a_daily_task_at_a_time_of_day_runs_then_on_the_day_after_its_last_run() -> None:
    """Finished at 03:05, a daily backup at 03:00 runs at tomorrow's 03:00, not the day after."""
    since = _at("03:05")
    due = due_at(
        when=WHEN_WORK, every=DAY, since=since, now=since, start="23:00", end="07:00", at="03:00"
    )
    assert due == _at("03:00", days=1)


def test_a_time_of_day_is_never_less_than_half_the_interval_after_the_last_run() -> None:
    """Pressed at 01:00, a daily backup at 14:30 still runs at 14:30 that day (13.5 hours on, the
    half-day allowance quiet hours take); pressed at 03:00, it waits for tomorrow's."""
    early = _at("01:00")
    due = due_at(
        when=WHEN_WORK, every=DAY, since=early, now=early, start="23:00", end="07:00", at="14:30"
    )
    assert due == _at("14:30")
    late = _at("03:00")
    due = due_at(
        when=WHEN_WORK, every=DAY, since=late, now=late, start="23:00", end="07:00", at="14:30"
    )
    assert due == _at("14:30", days=1)
    since = _at("01:00")
    weekly = due_at(
        when=WHEN_WORK,
        every=7 * DAY,
        since=since,
        now=since,
        start="23:00",
        end="07:00",
        at="02:00",
    )
    assert weekly == _at("02:00", days=7), "a week on, never a day early"


def test_a_time_of_day_missed_while_the_device_was_off_runs_now() -> None:
    since = _at("03:00", days=-3)
    now = _at("10:00")
    due = due_at(
        when=WHEN_WORK, every=DAY, since=since, now=now, start="23:00", end="07:00", at="03:00"
    )
    assert due == now


def test_a_task_that_never_ran_waits_for_its_time_of_day_and_quiet_hours_ignore_it() -> None:
    """Chosen this afternoon for three in the morning means tonight, not this minute. In quiet
    hours the range's opening is the time."""
    now = _at("14:00")
    first = due_at(
        when=WHEN_WORK, every=DAY, since=None, now=now, start="23:00", end="07:00", at="03:00"
    )
    assert first == _at("03:00", days=1)
    assert due_at(when=WHEN_WORK, every=DAY, since=None, now=now, start="23:00", end="07:00") == now
    since = _at("23:05")
    quiet = due_at(
        when=WHEN_QUIET, every=DAY, since=since, now=since, start="23:00", end="07:00", at="03:00"
    )
    assert quiet == _at("23:00", days=1)


def test_a_clock_time_is_checked_where_it_arrives() -> None:
    assert time_of_day("7:05") == "07:05"
    for wrong in ("24:00", "12:60", "12", chr(0xB9) + ":00", " 5:00", 1200):
        with pytest.raises(ValueError):
            time_of_day(wrong)
    assert within(datetime.now().replace(hour=3).time(), "23:00", "07:00")
