# SPDX-License-Identifier: AGPL-3.0-or-later
"""Quiet hours, and the three answers a task gives to "when does this run".

Quiet hours is ONE range of time for the whole install, set by the person, in which Sift starts the
work they chose for it and at whose end that work is paused again. It is not a task. It is the only
clock a task's timing is read against: a task set to "During quiet hours" waits for the range to
open, a timed task (the backup, the two clean-ups) runs at its start, and work nobody asked for stops
being handed out the moment it closes.

This module is the arithmetic and nothing else: whether a moment is inside the range, when it
next opens, when a timed task falls due. It holds no setting and reads no store: the queue asks the
switchboard, the switchboard asks whoever the composition root handed it, and that is the one
place the stored range is read. Kept pure so every reader of the range (the claim, the Tasks
screen, the scheduler that places a timed task's next run) answers from the same few lines.

## How the range stops work

Face recognition held to the night is its When set to "During quiet hours", the same answer every
other task can give. The stop at the range's end is the claim refusing to hand out held work,
which, because every long pass here is made of small tasks, is a pause within seconds rather than
a promise: a pass still going at seven in the morning does not go on going.
"""

from __future__ import annotations

from datetime import time, timedelta

from sift.kernel.when import moment_of, wall

#: As files arrive, or on a schedule for a timed task: the work runs when it arrives or falls due.
WHEN_WORK = "work"
#: During quiet hours: the work waits for the range to open and pauses when it closes.
WHEN_QUIET = "quiet"
#: Only when I press it: nothing starts on its own. A press still runs, always.
WHEN_PRESS = "press"

#: The three answers, in the order a person reads them.
WHENS: tuple[str, ...] = (WHEN_WORK, WHEN_QUIET, WHEN_PRESS)

#: What each answer is called on screen, in the same order, for a task that waits for files to
#: arrive. A timed task calls the first one `ON_A_SCHEDULE`: the stored value is the same, and
#: what starts it is its own clock rather than a file. See `ScheduledTask.when_labels`.
WHEN_LABELS: tuple[str, ...] = (
    "As files arrive",
    "During quiet hours",
    "Only when I press it",
)
ON_A_SCHEDULE = "On a schedule"

#: The range a new install starts with: eleven at night to seven in the morning.
DEFAULT_FROM = "23:00"
DEFAULT_UNTIL = "07:00"

#: How a row asked for AT A TIME is marked, on the job itself. `now` is a press that runs immediately
#: whatever the range says; `quiet` is work held to the range, a press of "Run during quiet hours" or the
#: children of one. No mark is the ordinary case: work nobody pressed, whose timing is its task's.
AT_NOW = "now"
AT_QUIET = "quiet"
ATS: tuple[str, ...] = (AT_NOW, AT_QUIET)


def clock(value: str, *, fallback: str = DEFAULT_FROM) -> time:
    """`HH:MM` as a time. Anything unreadable is the fallback, which the validator already prevents."""
    for candidate in (value, fallback):
        try:
            hours, minutes = str(candidate).split(":", 1)
            return time(int(hours), int(minutes))
        except (ValueError, TypeError):
            continue
    return time(0, 0)


def within(now: time, start: str, end: str) -> bool:
    """Whether a clock time falls inside the range written as two `HH:MM` strings.

    A range that ends earlier than it starts runs through midnight, which is the ordinary case and
    is why this cannot be a plain comparison. A range whose ends are equal is the WHOLE day rather
    than none of it: somebody who set both to the same hour meant "always", and reading it as
    "never" would hold every quiet-hours task back for good with nothing on screen saying why.
    """
    opens = clock(start)
    shuts = clock(end, fallback=DEFAULT_UNTIL)
    if opens == shuts:
        return True
    if opens < shuts:
        return opens <= now < shuts
    return now >= opens or now < shuts


def is_open(start: str, end: str, at: int) -> bool:
    """Whether the range is open at `at`, seconds since the epoch, on the machine's clock."""
    return within(wall(at).time(), start, end)


def next_opening(start: str, end: str, at: int) -> int:
    """When the range next opens at or after `at`. `at` itself while it is open.

    In the machine's own time (`kernel/when.py`), which is what the setting's help says the two
    hours are written in.
    """
    if within(wall(at).time(), start, end):
        return at
    return next_clock_time(start, at)


def next_clock_time(value: str, at: int) -> int:
    """The first moment at or after `at` whose clock on the machine reads `value` (`HH:MM`).

    The one walk through local time a timed task's moment takes: the range's opening and a task's
    own time of day both come here, so the two cannot come to disagree about a day's boundary.
    """
    here = wall(at)
    wanted = clock(value)
    moment = here.replace(hour=wanted.hour, minute=wanted.minute, second=0, microsecond=0)
    if moment < here:
        moment += timedelta(days=1)
    return moment_of(moment)


def next_closing(start: str, end: str, at: int) -> int | None:
    """When the range next closes after `at`, or None for a range that is the whole day."""
    if clock(start) == clock(end, fallback=DEFAULT_UNTIL):
        return None
    here = wall(at)
    shuts = clock(end, fallback=DEFAULT_UNTIL)
    moment = here.replace(hour=shuts.hour, minute=shuts.minute, second=0, microsecond=0)
    if moment <= here:
        moment += timedelta(days=1)
    return moment_of(moment)


#: The most a timed task's due moment is brought FORWARD to meet the range's opening. Half a day,
#: so a daily task that last ran at 23:05 is due at tomorrow's 23:00 opening rather than the
#: opening after it (which is what "a daily backup runs at the window's start" means), and a
#: weekly one is never moved to an earlier day of the week.
_EARLIEST_SLACK = 12 * 3600


def due_at(
    *,
    when: str,
    every: int,
    since: int | None,
    now: int,
    start: str,
    end: str,
    at: str | None = None,
) -> int:
    """When a timed task next runs: `every` seconds after `since`, placed by its When.

    `since` is when the last run its schedule started finished, or None where it never has (a
    press is not the schedule's run: see `TaskClock`, which picks the run). On a schedule, that is
    simply `since + every`; with a time of day (`at`, `HH:MM`) it is that time on the day the task
    falls due, up to half a day early by the same allowance quiet hours take, so a daily backup
    that finished at 03:05 runs at tomorrow's 03:00 and not the day after. During quiet hours it is
    the opening of the range on the day that falls due: up to half a day early, never late by a whole
    day for having finished a few minutes after the range opened; a time of day is not read there,
    because the range's opening is the time. A moment already past is `now`: a device that was off
    catches up, which is what a claimable-after column already does.

    A task that has never run falls due immediately: now, at the range's next opening during quiet
    hours, or at the next time of day it names (somebody who chose three in the morning this afternoon
    means tonight, not this minute).

    Only asked for a task that runs on its own. "Only when I press it" has no next run, and the
    caller does not ask.
    """
    step = max(1, every)
    slack = min(_EARLIEST_SLACK, step // 2)
    if since is None:
        earliest = now
    else:
        earliest = since + step - (slack if when == WHEN_QUIET or at is not None else 0)
    if when == WHEN_QUIET:
        return max(now, next_opening(start, end, max(now, earliest)))
    if at is not None:
        return max(now, next_clock_time(at, earliest))
    return max(now, earliest)


def time_of_day(value: object) -> str:
    """A 24-hour clock time, as `HH:MM`, or a `ValueError` saying what was wanted.

    Checked where the value arrives: it is read back by work that runs unattended at three in the
    morning, and a malformed one there would either stop the work silently or run it at the wrong
    time. Both halves must be plain ASCII digits AND convert: `isdigit()` admits a superscript
    that `int()` refuses, and a bare `int()` admits a sign and spaces nobody typed.
    """
    if not isinstance(value, str):
        raise ValueError("expected a time like 23:00")
    hours, separator, minutes = value.partition(":")
    ok = (
        separator == ":"
        and hours.isascii()
        and hours.isdigit()
        and minutes.isascii()
        and minutes.isdigit()
        and len(minutes) == 2
        and len(hours) <= 2
    )
    if not ok:
        raise ValueError("expected a time like 23:00")
    hour, minute = int(hours), int(minutes)
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        raise ValueError("expected a time like 23:00, between 00:00 and 23:59")
    return f"{hour:02d}:{minute:02d}"
