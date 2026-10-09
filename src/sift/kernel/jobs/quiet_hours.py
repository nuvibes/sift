# SPDX-License-Identifier: AGPL-3.0-or-later
"""Quiet hours, one range for the whole install, and the arithmetic a task's When reads."""

from __future__ import annotations

from datetime import time, timedelta

from sift.kernel.when import moment_of, wall

#: As files arrive, or on a schedule for a timed task: the work runs when it arrives or falls due.
WHEN_WORK = "work"
#: During quiet hours: the work waits for the range to open and pauses when it closes.
WHEN_QUIET = "quiet"
#: Only when I press it: nothing starts on its own. A press still runs, always.
WHEN_PRESS = "press"

WHENS: tuple[str, ...] = (WHEN_WORK, WHEN_QUIET, WHEN_PRESS)

#: On-screen words for a task waiting on files; a timed task says `ON_A_SCHEDULE` first.
WHEN_LABELS: tuple[str, ...] = (
    "As files are imported",
    "During quiet hours",
    "Only when I press it",
)
ON_A_SCHEDULE = "On a schedule"

#: The range a new install starts with: eleven at night to seven in the morning.
DEFAULT_FROM = "23:00"
DEFAULT_UNTIL = "07:00"

#: A row's own timing mark: `now` runs whatever the range, `quiet` waits for it.
AT_NOW = "now"
AT_QUIET = "quiet"
ATS: tuple[str, ...] = (AT_NOW, AT_QUIET)


def clock(value: str, *, fallback: str = DEFAULT_FROM) -> time:
    """`HH:MM` as a time; anything unreadable is the fallback."""
    for candidate in (value, fallback):
        try:
            hours, minutes = str(candidate).split(":", 1)
            return time(int(hours), int(minutes))
        except (ValueError, TypeError):
            continue
    return time(0, 0)


def within(now: time, start: str, end: str) -> bool:
    """Whether a clock time is in the range; past midnight wraps, and equal ends mean always."""
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
    """When the range next opens at or after `at`, on the machine's clock; `at` while open."""
    if within(wall(at).time(), start, end):
        return at
    return next_clock_time(start, at)


def next_clock_time(value: str, at: int) -> int:
    """The first moment at or after `at` whose clock reads `value`, the one local-time walk."""
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


#: At most half a day early, so a daily task meets tomorrow's opening, never the next.
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
    """When a timed task next runs: `every` after `since`, placed by its When; never-run is soon."""
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
    """A 24-hour `HH:MM`, or a `ValueError`: ASCII digits only, as `isdigit` admits superscripts."""
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
