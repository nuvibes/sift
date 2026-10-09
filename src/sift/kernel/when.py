# SPDX-License-Identifier: AGPL-3.0-or-later
"""The machine's clock: the one place a moment becomes a day or a time of day.

Storage is UTC; days and times are the server machine's local time, as SQLite reads it too."""

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

#: A day number counts calendar days, so a day holding a clock change is still one.
SECONDS_A_DAY = 86_400

#: The local day number in SQL, spelled word for word, as a gate holds every `/ 86400` to it.
LOCAL_DAY_SQL = "unixepoch({column}, 'unixepoch', 'localtime') / 86400"

_FRESH_FOR = 60.0

# A flag, as mypy calls a literal OS test's other branch unreachable.
_WINDOWS = sys.platform == "win32"

_EPOCH = date(1970, 1, 1)

_asked_at: float | None = None

#: An IANA name or UTC; anything else leaves the client in its own zone.
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
    """Read the machine's zone again, now."""
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
    if _asked_at is None or time.monotonic() - _asked_at >= _FRESH_FOR:
        refresh()


def wall(at: float) -> datetime:
    """The machine's naive wall-clock reading at `at`; naive so arithmetic crosses clock changes."""
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
    """The day `at` falls on as days since 1970, the same number `LOCAL_DAY_SQL` gives."""
    return (day_of(at) - _EPOCH).days


def day_from_number(number: int) -> date:
    return _EPOCH + timedelta(days=number)


def day_start(day: date) -> int:
    """The first second of `day` on the machine's clock (its midnight, as a moment)."""
    return moment_of(datetime.combine(day, clock_time()))


def day_bounds(day: date) -> tuple[int, int]:
    """The day as seconds `[start, end)`, from two midnights, as some days are 23 or 25 hours."""
    return day_start(day), day_start(day + timedelta(days=1))


def offset(at: float) -> int:
    """How many seconds ahead of UTC the machine's clock was at `at`, daylight saving included."""
    _fresh()
    return int(time.localtime(at).tm_gmtoff)


def local(at: float) -> datetime:
    """`at` on the machine's clock with its offset then: for writing a moment, not arithmetic."""
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
    """The machine's zone by IANA name, from ICU on Windows, else `TZ` or `/etc/localtime`."""
    _fresh()
    named = _windows_zone() if windows else _posix_zone(environ.get("TZ"), link)
    if named is None or named in _NOT_A_ZONE or not _ZONE_NAME.fullmatch(named):
        return None
    return named


@cache
def _icu_zone_call() -> Any:
    """ICU's call that names the host's zone, typed, or None where there is no ICU."""
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
    """The zone's name from `TZ`, else from the zone file's link."""
    if tz:
        return tz.removeprefix(":")
    target = Path(os.path.realpath(link))
    if not target.exists():
        return "UTC"
    _before, found, after = target.as_posix().partition("zoneinfo/")
    return after if found else None
