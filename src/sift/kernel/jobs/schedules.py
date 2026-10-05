# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Sift does on a clock, declared where a person can be shown all of it at once.

A backup runs every few days at a time of day, a sweep clears the quarantine folder every
twenty-four hours, recognition may be held back to an overnight window: three answers to one question ("what runs
without me, and when"), each owned by a different feature: Backup, Maintenance, Performance.
Somebody who wants the answer should not have to know all three places.

So a scheduled task declares itself here, the way a setting declares itself into the settings
registry, and one screen is generated from what has been declared. The declaration is the feature's
(it is the feature that knows what its cadence means), and the registration point is the
kernel's, so no feature has to import another to be listed beside it.

## What is NOT declared here, and why each is a deliberate absence

This list is as load-bearing as the one below: without it the easy mistake is to widen
"scheduled" until the screen lists things nobody can manage.

**The process's own loops.** The write-ahead checkpoint, the statistics refresh, the job watchdog's
sweep, a worker's heartbeat, the pool's reconfigure, the keep-awake request looked at again every
half a minute, the look for a quiet moment, and the settings pushed onto the running process. Every
one is a timer, none has a cadence anybody chooses, and none is a task: they are how the process
stays alive. A screen listing them would be a screen of numbers with no controls.

**Derived counts nobody chooses a time for.** Insights' adder-up (`slices/insights/rollup.py`) adds
up each finished day of each User a piece at a time, beside the two database loops above and started
and stopped with them. It is not a task for the same reason they are not: there is no right time for
it other than "soon, and never in anybody's way", it takes no worker, and its adding-up gives way
while work somebody pressed is queued. Listed here, it would be a row with a When nobody could
sensibly set.

**The settle waits.** The folder watcher's quiet period and the scan's settle pass re-arm
themselves, and they re-arm only while work is arriving. They are the shape of one run rather than
a cadence, and they stop when it stops.

**The update check** is not an exception: it is a task that runs on its own clock and is recorded
like any other. It is declared unshown (`shown=False`), as the two prunes are: nobody decides when
upkeep like that runs, so no pane draws a When for it. Its retired on/off switch still reads and
writes that When.

**Log rotation.** Triggered by the file's SIZE, not by the clock.

**The self-test.** Somebody presses it.

## Each task has one When, and where it is read

Every task declares a When: As files arrive (On a schedule, for a timed task), During quiet hours,
Only when I press it, and this registry registers it as the setting `tasks.<id>.when`. The work
it governs reads it in two
places and nowhere else: the enqueue refuses work nobody pressed for a task that only runs when
pressed (through the switchboard's switch, for the passes that have one), and the claim holds work
nobody pressed for a task set to quiet hours until the range is open (`Switchboard.quiet_hold`). A
press is marked on its row (`jobs.timing`) and is never held or refused by either.

A timed task's next run is a row with a time on it, placed by the scheduler from the moment the last
run ended (`quiet_hours.due_at`): the queue is still the authority on what is going to happen, and
a device that was off comes back to a row whose time has passed and runs it.

## Last ran

A task whose run is one job writes a line in the history as that run settles (`JobQueue.
record_runs_of`), so its last run is the newest line about it and is never forgotten. A long pass
made of pages is written per family by the work ledger, and its task reads its last run there.

The job rows keep each type's newest run as well, whatever its age (`_PRUNE_SETTLED`), because the
scheduler places the next run from the last one it finds there and Activity's chores read theirs.

"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sift.kernel.jobs.quiet_hours import (
    ON_A_SCHEDULE,
    WHEN_LABELS,
    WHEN_PRESS,
    WHEN_QUIET,
    WHEN_WORK,
    WHENS,
)
from sift.kernel.settings_registry import (
    SettingError,
    get_registered,
    register_setting,
    retire_setting,
)


class ScheduleError(ValueError):
    """A scheduled task was declared wrongly. The message is meant to be read."""


#: The settings values a task is judged against: every app setting, by key. Handed in rather than
#: read, because the registry knows nothing about where a value is stored and must not learn.
Values = Mapping[str, Any]

#: Where every task's When is filed. The Tasks screen draws the section; the owning section draws
#: the same row beside the thing the task does: one setting, two doors.
SECTION = "Scheduled tasks"

#: What a task's press says where the task names no verb of its own.
RUN_NOW = "Run now"

#: The reading of a task whose When is its children's, while they do not agree. Never stored and
#: never offered: choosing an answer writes it to every child.
WHEN_MIXED = "mixed"


def when_key(task_id: str) -> str:
    """The setting that holds a task's When. One per task, and named from its id so it cannot drift."""
    return f"tasks.{task_id}.when"


PER_FILE = ("file", "files")


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    """One kind of work Sift does over the library, and when it may start without being asked.

    EVERY TASK HAS ONE WHEN, and it is a setting this declaration registers: As files arrive,
    During quiet hours, or Only when I press it. What the task DOES (which pictures, how long to
    keep a search, how often to back up) stays with the section that owns it; when it may start is
    this one answer, drawn on the Tasks screen and again beside the thing on the owning section's
    pane.

    Two shapes. A task that WAITS FOR WORK (a file arriving, a scan settling) has its When read by
    the work it governs: which job types those are is the composition root's to say, because the
    types belong to several features and only it can name them all. A TIMED task says how often it
    runs (`every`), and the scheduler places its next run from when the last one ended: in quiet
    hours at the range's opening, never at all when only a press runs it.

    A PRESS ALWAYS RUNS. Run now, the Build, a queue's Scan now and a file's Run task are somebody
    answering the question for themselves, and neither the When nor any switch refuses them.
    """

    #: The address of this task, in the route and on the screen. Never renamed once shipped.
    id: str
    #: What it is called. Sentence case, a few words, the way a setting's label is written.
    title: str
    #: One line saying what it does. Not what the machinery is.
    explain: str
    #: The queue type that carries one run of it: what Run now queues where nothing better is
    #: bound to the task, what a timed task puts in the queue on its clock, and what its runs are
    #: recorded under.
    job_type: str | None = None
    #: What Run now's job carries, where it needs anything. Read, never mutated.
    payload: Mapping[str, Any] = field(default_factory=dict)
    #: Whether Run now needs more than a job of `job_type`: a pass over the library that has to be
    #: counted and told which products it is for. The composition root binds a starter for each such
    #: task, and the boot refuses a task that says so and has none.
    needs_starter: bool = False
    #: The When a value never written reads as. Changing it moves no install that chose: a When
    #: somebody set is a stored row (a save writes the value even where it equals the default), and
    #: every install from before Whens existed had its answer written by the settings step that
    #: brought them in. Only an install that never set this task's When takes a new default.
    when_default: str = WHEN_WORK
    #: The Whens this task offers, in the reading order of `WHENS`.
    whens: tuple[str, ...] = WHENS
    #: For a timed task: how many seconds between runs, from the values, or None where there is
    #: nothing to do at all (a retention of zero days). None for a task that waits for work.
    every: Callable[[Values], int | None] | None = None
    #: For a timed task that keeps a time of day: the `HH:MM` on the server machine's clock
    #: (`kernel/when.py`) its run starts at, from the values, or None for none. Read only On a
    #: schedule; during quiet hours the range's opening is the time. Its keys are named in
    #: `setting_keys` or `also_reads`, as `every`'s are.
    at: Callable[[Values], str | None] | None = None
    #: The settings that decide what the task does and how often, in reading order. The Tasks screen
    #: draws each beside the task; each writes through the ordinary settings write.
    setting_keys: tuple[str, ...] = ()
    #: Settings the answers below depend on that are NOT drawn as rows here. Named rather than
    #: worked out, because `every` is a function and nothing can read a function to find out which
    #: keys it touches: a key left out here is a value the route does not load.
    also_reads: tuple[str, ...] = ()
    #: Which of `setting_keys` are drawn only under some Whens, by key (a cadence means nothing while
    #: only a press runs the task); the server says which apply (`drawn_keys`), so no screen keeps a copy.
    drawn_under: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: Where the rest of this task's controls live, as a settings section id: the door the owning
    #: section draws. Empty when everything about it is here.
    set_in: str = ""
    #: Whether each run of `job_type` writes its own line in the history. True for a task whose work
    #: is one job; a long pass made of pages has its run written per family by the ledger instead.
    records_runs: bool = False
    #: What its press says. The three Importing stages name their own verb ("Scan now"); every
    #: other task says Run now.
    press: str = RUN_NOW
    #: False for upkeep nobody decides the timing of (pruning old records, the update check): it
    #: keeps its default When, runs and shows in Activity, and no settings pane draws it.
    shown: bool = True
    #: The tasks whose Whens this task's When reads, for a stage that is several tasks' work. Its
    #: When is then no setting of its own: it reads as their shared answer, or `WHEN_MIXED`, and
    #: an answer written to it is written to each of them.
    reads: tuple[str, ...] = ()
    #: The setting that turns the feature on, for work that does nothing while it is off (face
    #: recognition, Smart Search). The row then says so and names where it is turned on.
    switch: str = ""
    #: What one of its waiting rows is, one and many: a file, or a run for whole passes.
    unit: tuple[str, str] = ("run", "runs")
    #: The When's own label and help where a pane draws it as something other than the task's row
    #: (the update check's is a switch); the search and the docs read them. Empty: title and explain.
    when_label: str = ""
    when_help: str = ""

    @property
    def when_key(self) -> str:
        """The setting that holds this task's When."""
        return when_key(self.id)

    def when(self, values: Values) -> str:
        """The stored When, or the default when nothing is stored or the stored word is not offered.

        A task whose When reads its children's answers `WHEN_MIXED` while they disagree."""
        chosen = str(values.get(self.when_key, self.when_default))
        if self.reads and chosen == WHEN_MIXED:
            return WHEN_MIXED
        return chosen if chosen in self.whens else self.when_default

    def switched_off(self, values: Values) -> bool:
        """Whether the feature this task works for is turned off, so it has nothing to do."""
        return bool(self.switch) and not bool(values.get(self.switch))

    def interval(self, values: Values) -> int | None:
        """Seconds between runs for a timed task, or None: not timed, or nothing to do."""
        if self.every is None:
            return None
        seconds = self.every(values)
        return seconds if seconds is not None and seconds > 0 else None

    def time_of_day(self, values: Values) -> str | None:
        """The `HH:MM` its run starts at On a schedule, or None where it keeps no time of day."""
        if self.at is None or self.when(values) != WHEN_WORK:
            return None
        return self.at(values)

    def drawn_keys(self, values: Values) -> tuple[str, ...]:
        """Its settings that mean something under the When it has now, in reading order."""
        when = self.when(values)
        return tuple(
            key
            for key in self.setting_keys
            if key not in self.drawn_under or when in self.drawn_under[key]
        )

    def is_on(self, values: Values) -> bool:
        """Whether it starts on its own at all. A press runs it either way."""
        if self.when(values) == WHEN_PRESS:
            return False
        return self.every is None or self.interval(values) is not None

    def when_labels(self, values: Values | None = None) -> dict[str, str]:
        """What each When this task offers is called on screen. The first answer is "As files
        arrive" for a task that waits for files and "On a schedule" for a timed one.

        The interval is not in the answer: a task row is one line, and one task's longer answer
        would make its choice wider than every other row's and push its press onto a second line.
        How often is the task's own setting, and when it runs next is a fact on the row. `values`
        is kept for the callers that hand it."""
        _ = values
        labels = dict(zip(WHENS, WHEN_LABELS, strict=True))
        if self.every is not None:
            labels[WHEN_WORK] = ON_A_SCHEDULE
        return {one: labels[one] for one in self.whens}

    def cadence(self, values: Values) -> str:
        """When it runs, in the words a person reads. Answered even when it is off."""
        when = self.when(values)
        if when == WHEN_MIXED:
            return "Mixed"
        if when == WHEN_PRESS:
            return "Only when you run it"
        if self.every is None:
            return WHEN_LABELS[1] if when == WHEN_QUIET else WHEN_LABELS[0]
        seconds = self.interval(values)
        if seconds is None:
            return "Off"
        words = _every(seconds)
        return f"{words}, during quiet hours" if when == WHEN_QUIET else words

    def keys_read(self) -> tuple[str, ...]:
        """Every setting whose value has to be in hand before this task can answer anything."""
        switch = (self.switch,) if self.switch else ()
        return tuple(dict.fromkeys((*self.setting_keys, *self.also_reads, *switch, self.when_key)))


def _every(seconds: int) -> str:
    """An interval as it is said: every day, every week, every 6 hours."""
    day = 24 * 3600
    if seconds % (7 * day) == 0:
        weeks = seconds // (7 * day)
        return "Every week" if weeks == 1 else f"Every {weeks} weeks"
    if seconds % day == 0:
        days = seconds // day
        return "Every day" if days == 1 else f"Every {days} days"
    hours = max(1, seconds // 3600)
    return "Every hour" if hours == 1 else f"Every {hours} hours"


def _still_starts(values: tuple[Any, ...]) -> bool:
    """A retired on/off switch, read from the When it became: on unless only a press runs it."""
    return any(str(value) != WHEN_PRESS for value in values)


def _shared_when(values: tuple[Any, ...]) -> str:
    """Several tasks' Whens read as one: their answer where they agree, `WHEN_MIXED` where not."""
    answers = {str(one) for one in values}
    return answers.pop() if len(answers) == 1 else WHEN_MIXED


def _written_as_when(value: Any, current: tuple[Any, ...]) -> tuple[Any, ...]:
    """A master over tasks' Whens, written into each of them.

    A When is written to every one of them as it is. An on/off switch is read as one: off is
    "Only when I press it", and on keeps whichever of the two starting answers is already chosen
    (writing "on" to a task set to "During quiet hours" must not move it to the daytime) and is "As
    files arrive" where it was off.
    """
    if isinstance(value, str):
        # "mixed" is a reading and never an answer; any other word is a typo, not "on".
        if value not in WHENS:
            raise SettingError(f"{value!r} is not a When: choose one of {', '.join(WHENS)}")
        return tuple(value for _ in current)
    if not bool(value):
        return tuple(WHEN_PRESS for _ in current)
    return tuple(WHEN_WORK if str(one) == WHEN_PRESS else one for one in current)


_REGISTRY: dict[str, ScheduledTask] = {}


def register_schedule(task: ScheduledTask) -> None:
    """Declare a task and register its When.

    Called at import time, by the slice that owns the work. Everything checkable is checked here
    rather than on the screen, so a mistake fails at the declaration that caused it instead of
    appearing as a blank row on somebody's settings pane.
    """
    if task.id in _REGISTRY:
        raise ScheduleError(f"scheduled task {task.id!r} is registered twice")
    if not task.id.strip():
        raise ScheduleError("a scheduled task needs an id")
    if not task.title.strip():
        raise ScheduleError(f"scheduled task {task.id!r} needs a title")
    if not task.explain.strip():
        raise ScheduleError(f"scheduled task {task.id!r} needs a line saying what it does")
    # A task nothing can run cannot answer Run now, and a timed one would have nothing to put on
    # its clock. Refused rather than drawn with a button that does nothing.
    if task.job_type is None:
        raise ScheduleError(f"scheduled task {task.id!r} has no job type, so nothing could run it")
    if not task.whens or any(one not in WHENS for one in task.whens):
        raise ScheduleError(f"scheduled task {task.id!r} offers a When Sift does not know")
    if task.when_default not in task.whens:
        raise ScheduleError(f"scheduled task {task.id!r} starts on a When it does not offer")
    if task.id in task.reads or len(set(task.reads)) != len(task.reads):
        raise ScheduleError(f"scheduled task {task.id!r} reads a When twice or reads its own")
    if task.reads and task.every is not None:
        raise ScheduleError(
            f"scheduled task {task.id!r} reads other Whens and has a clock of its own"
        )
    if task.at is not None and task.every is None:
        raise ScheduleError(f"scheduled task {task.id!r} keeps a time of day and has no clock")
    for key, under in task.drawn_under.items():
        if key not in task.setting_keys or any(one not in task.whens for one in under):
            raise ScheduleError(
                f"scheduled task {task.id!r} draws {key!r} under a When it does not offer, "
                "or draws a setting that is not its own"
            )
    for key in (*task.setting_keys, *task.also_reads, *((task.switch,) if task.switch else ())):
        if get_registered(key) is None:
            raise ScheduleError(
                f"scheduled task {task.id!r} names setting {key!r}, which nothing has registered"
            )
    if task.reads:
        # Its When is its children's, through the same master rule as every retired switch: read
        # as their shared answer, written to each. Nothing of its own is stored to disagree.
        retire_setting(
            when_key(task.id),
            into=tuple(when_key(one) for one in task.reads),
            read=_shared_when,
            write=_written_as_when,
            why=f"{task.title} is when {', '.join(task.reads)} run, read as one",
        )
        _REGISTRY[task.id] = task
        return
    labels = task.when_labels()
    register_setting(
        # Through the key-maker rather than the property, so a reader of the source can see every
        # When is `tasks.<id>.when`: the shape the dead-setting gate follows.
        key=when_key(task.id),
        scope="app",
        default=task.when_default,
        section=SECTION,
        label=task.when_label or task.title,
        help=task.when_help or task.explain,
        choices=task.whens,
        choice_labels=tuple(labels[one] for one in task.whens),
    )
    _REGISTRY[task.id] = task


def retire_into_whens(
    key: str,
    task_ids: tuple[str, ...],
    *,
    why: str,
    folder_label: str | None = None,
    folder_help: str | None = None,
) -> None:
    """Retire an on/off switch that answered "does this start on its own" into the tasks' Whens.

    Read as "anything but Only when I press it": for a group master over several tasks, on while
    any of them starts on its own. Written: off makes every one of them press-only, and on starts
    those that were press-only as files arrive while leaving "During quiet hours" where it is.
    Every caller and every folder's own answer stored under the old key goes on reading the one
    stored value. Called by the composition root, which is the only place that knows both the old
    key's feature and the task's.

    A folder's own answer under the old key decides only whether a file arriving in that folder is
    worked on; a press of the task reads every folder. `folder_label` and `folder_help` say that on
    the folder's page, where the task's title alone would promise a run that never happens.
    """
    retire_setting(
        key,
        into=tuple(when_key(one) for one in task_ids),
        read=_still_starts,
        write=_written_as_when,
        why=why,
        folder_label=folder_label,
        folder_help=folder_help,
    )


def registered_schedules() -> dict[str, ScheduledTask]:
    """The registry, copied, in declaration order. Callers read it; nothing mutates it through here."""
    return dict(_REGISTRY)


def shown_schedules() -> dict[str, ScheduledTask]:
    """The tasks a settings pane draws, in declaration order: every one but the upkeep nobody times."""
    return {task_id: task for task_id, task in _REGISTRY.items() if task.shown}


def get_schedule(task_id: str) -> ScheduledTask | None:
    """The declaration for one task, or None if nothing has registered it."""
    return _REGISTRY.get(task_id)


def scheduled_job_types() -> frozenset[str]:
    """Every queue type a declared task runs. What the gate over re-arming timers reads."""
    return frozenset(task.job_type for task in _REGISTRY.values() if task.job_type is not None)
