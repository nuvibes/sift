# SPDX-License-Identifier: AGPL-3.0-or-later
"""The machine's clock: the one place a moment becomes a day or a time of day.

Every date and time Sift shows, and every day it groups by, is the local time of the machine the
server runs on. Storage stays UTC: a moment is unix seconds with no zone beside it, and nothing
written to a row depends on a zone. Only a DAY (which calendar day a moment falls on, where a day
starts and ends) and a TIME OF DAY (what the clock on the wall read) depend on one, and both come
from here.

## Why the machine's zone, and not the reader's

A library is one place with one clock. A History line for a filing made at 9:06 in the evening
belongs to that evening, and the date under it opens that evening's files; a phone three zones
away reads the same day the desktop beside the server does. The browser's zone was the other
answer, and it is wrong for exactly that: one library, two readers, two different days for the
same filing, and a day's files that change with whoever is looking. The session's answer carries
the zone's name (`zone_name`), and the client writes every moment in it.

## Why the C runtime's clock, and not a zone database

`time.localtime` is the operating system's own reading of its own setting, daylight saving
included, and SQLite's `'localtime'` modifier reads the same C runtime, so a day grouped in SQL
(`LOCAL_DAY_SQL`) and a day worked out here cannot come to disagree. A zone database would be a
second opinion about the machine the machine already has.

## When the machine's zone changes

The C runtime reads the zone once and keeps it. `refresh` asks it again (the Universal C
Runtime's `_tzset` on Windows, `time.tzset` elsewhere), and every reading here calls it at most
once a minute, so a laptop carried across a border is on its new clock within a minute of the next
thing that asks, with no restart. SQLite reads the same runtime, so its days move with it.
"""

from __future__ import annotations

import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from datetime import time as clock_time
from functools import cache
from pathlib import Path
from typing import Any

#: Seconds in a day on the calendar's own count, which is what a day NUMBER counts in (a day that
#: holds a clock change is still one day).
SECONDS_A_DAY = 86_400

#: The local day a column's moment falls on, as every statement that groups or filters by day
#: spells it, word for word: the moment moved onto the machine's wall clock and divided into days.
#: The same number `day_number` gives. Written into each statement as a literal rather than
#: spliced in (query text is never built from pieces here); `tests/gates/
#: test_every_shown_time_is_the_machines.py` holds every `/ 86400` in the tree to this shape.
LOCAL_DAY_SQL = "unixepoch({column}, 'unixepoch', 'localtime') / 86400"

#: How long a reading of the zone is trusted before the runtime is asked again. See the module
#: docstring: a minute is nothing to a person and one cheap call to the runtime.
_FRESH_FOR = 60.0

# Read through a flag rather than tested directly: mypy narrows a literal
# `sys.platform == "win32"` and would call the other platform's branch unreachable. Same reason as
# kernel/hardware.py.
_WINDOWS = sys.platform == "win32"

_EPOCH = date(1970, 1, 1)

#: When the runtime was last asked for the zone, on the monotonic clock; None before the first time.
_asked_at: float | None = None

#: What a zone's name may look like to be handed to a browser: an IANA name ("America/New_York",
#: "Etc/GMT+5") or UTC. Anything else (a POSIX rule such as "EST5EDT", an ICU "Etc/Unknown") is no
#: name at all, and the client keeps its own zone rather than be handed one it cannot read.
_ZONE_NAME = re.compile(r"[A-Za-z_]+(?:/[A-Za-z0-9_+-]+)+|UTC")
_NOT_A_ZONE = frozenset({"Etc/Unknown"})


@cache
def _runtime() -> Any:
    """The C runtime Windows keeps the zone it read in, or None where it is absent."""
    import ctypes

    try:
        return ctypes.CDLL("ucrtbase")
    except OSError:
        return None


def refresh(*, windows: bool = _WINDOWS) -> None:
    """Read the machine's zone again, now: every day and time after this is on its current clock."""
    global _asked_at
    _asked_at = time.monotonic()
    if windows:
        runtime = _runtime()
        if runtime is not None:
            runtime._tzset()
        return
    reset: Callable[[], None] | None = getattr(time, "tzset", None)
    if reset is not None:
        reset()


def _fresh() -> None:
    """Ask the runtime for the zone again if the last reading is more than a minute old."""
    if _asked_at is None or time.monotonic() - _asked_at >= _FRESH_FOR:
        refresh()


def wall(at: float) -> datetime:
    """What the machine's clock read at `at` (unix seconds): a naive local date and time.

    Naive on purpose. Wall-clock arithmetic ("the same time tomorrow", "this day's midnight") is
    done on the wall clock and turned back into a moment by `moment_of`, which applies the
    daylight-saving rule of the day it lands on; an aware time with a fixed offset would carry
    today's offset across a clock change and land an hour out.
    """
    _fresh()
    return datetime.fromtimestamp(at)


def moment_of(when: datetime) -> int:
    """The unix second at which the machine's clock reads `when` (a naive local date and time)."""
    _fresh()
    return int(when.timestamp())


def day_of(at: float) -> date:
    """The calendar day `at` falls on, on the machine's clock."""
    return wall(at).date()


def today(now: float | None = None) -> date:
    """Today on the machine's clock. `now` names the moment for a test; nothing else passes it."""
    return day_of(time.time() if now is None else now)


def day_number(at: float) -> int:
    """The day `at` falls on as a count of days since 1 January 1970: the key a day is grouped by.

    The same number SQL gives for `LOCAL_DAY_SQL`, so a count made in a statement and one made here
    group a moment under the same day.
    """
    return (day_of(at) - _EPOCH).days


def day_from_number(number: int) -> date:
    """The calendar day a `day_number` counts to."""
    return _EPOCH + timedelta(days=number)


def day_start(day: date) -> int:
    """The first second of `day` on the machine's clock (its midnight, as a moment)."""
    return moment_of(datetime.combine(day, clock_time()))


def day_bounds(day: date) -> tuple[int, int]:
    """The day as seconds, `[start, end)`, on the machine's clock.

    From the two midnights rather than start plus a day's seconds: a day the clocks change on is
    23 or 25 hours long, and a fixed length would give one hour to the wrong day twice a year.
    """
    return day_start(day), day_start(day + timedelta(days=1))


def offset(at: float) -> int:
    """How many seconds ahead of UTC the machine's clock was at `at`, daylight saving included."""
    _fresh()
    return int(time.localtime(at).tm_gmtoff)


def local(at: float) -> datetime:
    """`at` on the machine's clock, carrying the offset it had then: for WRITING a moment down.

    Aware, so `%z` writes the offset beside the time and the moment can be read back exactly. Not
    for arithmetic: the offset is that instant's, and a day added to it keeps it across a clock
    change. Arithmetic is `wall` and `moment_of`.
    """
    return datetime.fromtimestamp(at, timezone(timedelta(seconds=offset(at))))


def stamp(at: float, shape: str) -> str:
    """`at` written in a `strftime` shape on the machine's clock: a file name's date, a report's."""
    return local(at).strftime(shape)


def zone_name(
    *,
    windows: bool = _WINDOWS,
    environ: Mapping[str, str] = os.environ,
    link: Path = Path("/etc/localtime"),
) -> str | None:
    """The machine's zone by its IANA name ("America/New_York"), or None where it cannot be named.

    What the client writes every moment in. None leaves a browser in its own zone, which is right
    whenever it runs on the server's own machine and the best that can be done elsewhere.

    On Windows the name is ICU's reading of the Windows setting (every Windows since 10 version
    1903 carries ICU as `icu.dll`, and it is what maps "Eastern Standard Time" to its IANA name for
    the system's own apps). Elsewhere it is `TZ`, else the zone file `/etc/localtime` points at, and
    UTC where there is neither, which is what the C library then runs on.
    """
    _fresh()
    named = _windows_zone() if windows else _posix_zone(environ.get("TZ"), link)
    if named is None or named in _NOT_A_ZONE or not _ZONE_NAME.fullmatch(named):
        return None
    return named


@cache
def _icu_zone_call() -> Any:
    """ICU's call that names the host's zone, typed, or None where there is no ICU to ask.

    `ucal_getHostTimeZone` reads the setting afresh on each call; `ucal_getDefaultTimeZone`, the
    only one an ICU before 65 has, reads it once, which still names the zone the process started in.
    """
    import ctypes

    try:
        library = ctypes.WinDLL("icu")  # type: ignore[attr-defined, unused-ignore]
    except (OSError, AttributeError):
        return None
    for name in ("ucal_getHostTimeZone", "ucal_getDefaultTimeZone"):
        call = getattr(library, name, None)
        if call is not None:
            call.restype = ctypes.c_int32
            call.argtypes = [ctypes.c_wchar_p, ctypes.c_int32, ctypes.POINTER(ctypes.c_int)]
            return call
    return None  # pragma: no cover  (an ICU with neither call has never shipped with Windows)


def _windows_zone() -> str | None:
    """The zone's IANA name as ICU reads it from Windows, or None."""
    call = _icu_zone_call()
    if call is None:
        return None
    import ctypes

    written = ctypes.create_unicode_buffer(128)
    status = ctypes.c_int(0)
    length = call(written, len(written), ctypes.byref(status))
    # ICU's errors are positive numbers; a warning is negative and still an answer.
    if status.value > 0 or length <= 0:
        return None
    return str(written.value)


def _posix_zone(tz: str | None, link: Path) -> str | None:
    """The zone's name from `TZ` (with its optional leading colon), else from the zone file's link."""
    if tz:
        return tz.removeprefix(":")
    target = Path(os.path.realpath(link))
    if not target.exists():
        return "UTC"
    _before, found, after = target.as_posix().partition("zoneinfo/")
    return after if found else None
