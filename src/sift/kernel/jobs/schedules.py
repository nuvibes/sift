# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Sift does without being asked, declared here so one screen can show all of it.

## What is NOT declared here

The process's own loops (The write-ahead checkpoint, the statistics refresh, the job watchdog's
sweep, a worker's heartbeat, the pool's reconfigure, the keep-awake request, the look for a quiet
moment, the settings pushed onto the running process, the work after ready, the client's file
list): timers that keep the process alive, with no cadence anybody chooses. Insights' adder-up
(`slices/insights/rollup.py`): a derived count whose only right time is soon. The settle waits,
log rotation (by size) and the self-test (pressed).

## Each task has one When

As files arrive or On a schedule, During quiet hours, Only when I press it: registered as the
setting `tasks.<id>.when`, read by the enqueue and the claim; a press always runs.
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


#: Every app setting's value, by key, handed in: the registry does not know where values live.
Values = Mapping[str, Any]

#: Where every task's When is filed; the owning section draws the same row too.
SECTION = "Scheduled tasks"

#: What a task's press says where the task names no verb of its own.
RUN_NOW = "Run now"

#: How a task reads while its children's Whens disagree; never stored, never offered.
WHEN_MIXED = "mixed"


def when_key(task_id: str) -> str:
    """The setting that holds a task's When, named from its id so it cannot drift."""
    return f"tasks.{task_id}.when"


PER_FILE = ("file", "files")


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    """One kind of work Sift does over the library, and when it may start without being asked.

    A task waits for work, or is timed (`every`) and placed from when its last run ended.
    """

    #: The address of this task, in the route and on the screen; never renamed once shipped.
    id: str
    #: What it is called, in sentence case.
    title: str
    #: One line saying what it does.
    explain: str
    #: The queue type that carries one run of it.
    job_type: str | None = None
    #: What Run now's job carries. Read, never mutated.
    payload: Mapping[str, Any] = field(default_factory=dict)
    #: Whether Run now needs a starter the composition root binds, beyond a job of `job_type`.
    needs_starter: bool = False
    #: The When a value never written reads as; a When somebody chose is stored, so never moved.
    when_default: str = WHEN_WORK
    #: The Whens this task offers, in the reading order of `WHENS`.
    whens: tuple[str, ...] = WHENS
    #: For a timed task, the seconds between runs, or None where there is nothing to do.
    every: Callable[[Values], int | None] | None = None
    #: For a timed task, the `HH:MM` on the server's clock its run starts at On a schedule.
    at: Callable[[Values], str | None] | None = None
    #: The settings that decide what the task does and how often, drawn beside it in order.
    setting_keys: tuple[str, ...] = ()
    #: Settings `every` and `at` read that are not drawn; named, as a function cannot be read.
    also_reads: tuple[str, ...] = ()
    #: Which of `setting_keys` are drawn only under some Whens, by key.
    drawn_under: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: The settings section holding the rest of this task's controls, or empty.
    set_in: str = ""
    #: Whether each run of `job_type` writes its own History line; a paged pass's ledger does.
    records_runs: bool = False
    #: What its press says.
    press: str = RUN_NOW
    #: False for upkeep nobody times: it keeps its default When and no pane draws it.
    shown: bool = True
    #: The tasks whose Whens this one reads as one and writes to each, for a stage of several.
    reads: tuple[str, ...] = ()
    #: The setting that turns the feature on, for work that does nothing while it is off.
    switch: str = ""
    #: What one of its waiting rows is, one and many: a file, or a run for whole passes.
    unit: tuple[str, str] = ("run", "runs")
    #: The When's own label and help where a pane draws it differently; empty for the task's own.
    when_label: str = ""
    when_help: str = ""

    @property
    def when_key(self) -> str:
        """The setting that holds this task's When."""
        return when_key(self.id)

    def when(self, values: Values) -> str:
        """The stored When, the default for none or an unoffered word, or `WHEN_MIXED`."""
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
        """What each When this task offers is called on screen."""
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
    """A master over tasks' Whens, written into each; a switch's on keeps During quiet hours."""
    if isinstance(value, str):
        if value not in WHENS:
            raise SettingError(f"{value!r} is not a When: choose one of {', '.join(WHENS)}")
        return tuple(value for _ in current)
    if not bool(value):
        return tuple(WHEN_PRESS for _ in current)
    return tuple(WHEN_WORK if str(one) == WHEN_PRESS else one for one in current)


_REGISTRY: dict[str, ScheduledTask] = {}


def register_schedule(task: ScheduledTask) -> None:
    """Declare a task and register its When; every mistake fails here, at its declaration."""
    _refuse_declaration(task)
    _refuse_wiring(task)
    if task.reads:
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
        # The key-maker, so the dead-setting gate sees `tasks.<id>.when`.
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


def _refuse_declaration(task: ScheduledTask) -> None:
    if task.id in _REGISTRY:
        raise ScheduleError(f"scheduled task {task.id!r} is registered twice")
    if not task.id.strip():
        raise ScheduleError("a scheduled task needs an id")
    if not task.title.strip():
        raise ScheduleError(f"scheduled task {task.id!r} needs a title")
    if not task.explain.strip():
        raise ScheduleError(f"scheduled task {task.id!r} needs a line saying what it does")
    if task.job_type is None:
        raise ScheduleError(f"scheduled task {task.id!r} has no job type, so nothing could run it")
    if not task.whens or any(one not in WHENS for one in task.whens):
        raise ScheduleError(f"scheduled task {task.id!r} offers a When Sift does not know")
    if task.when_default not in task.whens:
        raise ScheduleError(f"scheduled task {task.id!r} starts on a When it does not offer")
    if task.id in task.reads or len(set(task.reads)) != len(task.reads):
        raise ScheduleError(f"scheduled task {task.id!r} reads a When twice or reads its own")


def _refuse_wiring(task: ScheduledTask) -> None:
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


def retire_into_whens(
    key: str,
    task_ids: tuple[str, ...],
    *,
    why: str,
    folder_label: str | None = None,
    folder_help: str | None = None,
) -> None:
    """Retire an on/off switch that answered "does this start on its own" into the tasks' Whens.

    A folder's own answer under the old key decides only for files arriving there.
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
    """The registry, copied, in declaration order."""
    return dict(_REGISTRY)


def shown_schedules() -> dict[str, ScheduledTask]:
    """The tasks a settings pane draws, in declaration order."""
    return {task_id: task for task_id, task in _REGISTRY.items() if task.shown}


def get_schedule(task_id: str) -> ScheduledTask | None:
    """The declaration for one task, or None if nothing has registered it."""
    return _REGISTRY.get(task_id)


def scheduled_job_types() -> frozenset[str]:
    """Every queue type a declared task runs. What the gate over re-arming timers reads."""
    return frozenset(task.job_type for task in _REGISTRY.values() if task.job_type is not None)
