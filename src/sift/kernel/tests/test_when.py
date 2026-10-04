# SPDX-License-Identifier: AGPL-3.0-or-later
"""The machine's clock: a day and a time of day are the server machine's, in Python and in SQL."""

from __future__ import annotations

import os
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import pytest

from sift.kernel import when
from sift.kernel.jobs.quiet_hours import WHEN_WORK, due_at, is_open, next_clock_time

#: 23:30 on 15 September 2026 in New York (daylight time, four hours behind UTC), which is 03:30
#: on the 16th in UTC and 12:30 on the 16th in Tokyo.
LATE_EVENING = int(datetime.fromisoformat("2026-09-16T03:30:00+00:00").timestamp())

#: The zones, as POSIX rules: the one spelling the Universal C Runtime and the C library both read.
NEW_YORK = "EST5EDT"
TOKYO = "JST-9"

SetZone = Callable[[str], None]


#: The expression every History statement groups a day by, around one bound moment.
_SQL_DAY = "SELECT unixepoch(?, 'unixepoch', 'localtime') / 86400"


def _sql_day(at: int) -> int:
    """The day SQL groups `at` under, by the expression every History statement spells."""
    with sqlite3.connect(":memory:") as connection:
        (day,) = connection.execute(_SQL_DAY, (at,)).fetchone()
    return int(day)


def test_the_statement_here_is_the_clocks_own_expression() -> None:
    assert _SQL_DAY.removeprefix("SELECT ") == when.LOCAL_DAY_SQL.format(column="?")


def test_a_moment_at_half_past_eleven_is_on_that_evenings_day(machine_zone: SetZone) -> None:
    """A filing late in the evening opens that evening's day, not the next: a day is the
    machine's."""
    machine_zone(NEW_YORK)
    assert when.day_of(LATE_EVENING) == date(2026, 9, 15)
    assert when.day_from_number(when.day_number(LATE_EVENING)) == date(2026, 9, 15)
    assert when.stamp(LATE_EVENING, "%Y-%m-%d %H:%M %z") == "2026-09-15 23:30 -0400"

    machine_zone(TOKYO)
    assert when.day_of(LATE_EVENING) == date(2026, 9, 16)
    assert when.stamp(LATE_EVENING, "%H:%M") == "12:30"


def test_a_daylight_rule_moves_the_clock_where_the_machines_own_zone_has_none(
    machine_zone: SetZone,
) -> None:
    """On Windows the runtime keeps the length of the daylight shift it last read from the
    system, which is nothing on a machine in UTC; the fixture gives the rule its hour."""
    if os.name == "nt":
        import ctypes

        shift = ctypes.CDLL("ucrtbase").__dstbias
        shift.restype = ctypes.POINTER(ctypes.c_long)
        shift()[0] = 0
    machine_zone(NEW_YORK)
    assert when.stamp(LATE_EVENING, "%H:%M %z") == "23:30 -0400"


def test_sql_groups_a_moment_under_the_same_day_python_does(machine_zone: SetZone) -> None:
    """A count made in a statement and a group made in Python agree about every moment's day."""
    for zone in (NEW_YORK, TOKYO, "UTC0"):
        machine_zone(zone)
        for at in (LATE_EVENING, LATE_EVENING + 1800, LATE_EVENING - 86_400, 1_700_000_000):
            assert _sql_day(at) == when.day_number(at), (zone, at)
    machine_zone(NEW_YORK)
    assert when.day_from_number(_sql_day(LATE_EVENING)) == date(2026, 9, 15)


def test_a_day_runs_from_the_machines_midnight_to_the_next(machine_zone: SetZone) -> None:
    """From the two midnights: the day the clocks go back is 25 hours long."""
    machine_zone(NEW_YORK)
    start, end = when.day_bounds(date(2026, 9, 15))
    assert start == int(datetime.fromisoformat("2026-09-15T04:00:00+00:00").timestamp())
    assert end - start == 86_400
    assert start <= LATE_EVENING < end
    autumn, after = when.day_bounds(date(2026, 11, 1))
    assert after - autumn == 25 * 3600
    assert when.offset(LATE_EVENING) == -4 * 3600
    assert when.local(LATE_EVENING).utcoffset() is not None
    assert when.today(LATE_EVENING) == date(2026, 9, 15)
    assert when.moment_of(when.wall(LATE_EVENING)) == LATE_EVENING


def test_a_timed_task_at_three_runs_at_the_machines_three(machine_zone: SetZone) -> None:
    """ "On a schedule" at 03:00 is three o'clock on the clock in the machine's room."""
    machine_zone(NEW_YORK)
    due = next_clock_time("03:00", LATE_EVENING)
    assert when.stamp(due, "%Y-%m-%d %H:%M") == "2026-09-16 03:00"
    assert due == int(datetime.fromisoformat("2026-09-16T07:00:00+00:00").timestamp())
    first = due_at(
        when=WHEN_WORK,
        every=86_400,
        since=None,
        now=LATE_EVENING,
        start="23:00",
        end="07:00",
        at="03:00",
    )
    assert first == due

    machine_zone(TOKYO)
    assert when.stamp(next_clock_time("03:00", LATE_EVENING), "%H:%M") == "03:00"
    # 12:30 in Tokyo is outside a range from eleven at night to seven in the morning; 23:30 in
    # New York is inside it.
    assert not is_open("23:00", "07:00", LATE_EVENING)
    machine_zone(NEW_YORK)
    assert is_open("23:00", "07:00", LATE_EVENING)


def test_the_clock_reads_the_zone_again_after_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zone changed on the machine is read within a minute, with no restart."""
    asked: list[bool] = []
    monkeypatch.setattr(when, "refresh", lambda: asked.append(True))
    monkeypatch.setattr(when, "_asked_at", None)
    when.wall(0)
    assert asked == [True]
    monkeypatch.setattr(when, "_asked_at", time.monotonic())
    when.wall(0)
    assert asked == [True]
    monkeypatch.setattr(when, "_asked_at", time.monotonic() - 61)
    when.wall(0)
    assert asked == [True, True]


def test_asking_the_runtime_again_holds_on_either_operating_system() -> None:
    """Both halves run on any machine: a runtime that is not there is left alone."""
    when.refresh(windows=True)
    when.refresh(windows=False)
    assert when._asked_at is not None


def test_the_zone_is_named_for_the_client(tmp_path: Path) -> None:
    """An IANA name, or nothing: a POSIX rule is no name a browser can write a time in."""
    nowhere = tmp_path / "missing"
    assert when.zone_name(windows=False, environ={"TZ": ":America/New_York"}, link=nowhere) == (
        "America/New_York"
    )
    assert when.zone_name(windows=False, environ={"TZ": NEW_YORK}, link=nowhere) is None
    assert when.zone_name(windows=False, environ={}, link=nowhere) == "UTC"
    zone_file = tmp_path / "zoneinfo" / "Etc" / "GMT+5"
    zone_file.parent.mkdir(parents=True)
    zone_file.write_bytes(b"TZif")
    assert when.zone_name(windows=False, environ={}, link=zone_file) == "Etc/GMT+5"
    elsewhere = tmp_path / "localtime"
    elsewhere.write_bytes(b"TZif")
    assert when.zone_name(windows=False, environ={}, link=elsewhere) is None
    # Windows asks ICU; elsewhere there is no ICU to ask. Either way a name or nothing.
    named = when.zone_name(windows=True)
    assert named is None or "/" in named or named == "UTC"


def test_an_icu_answer_that_is_an_error_is_no_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """ICU's errors are positive codes, and its "Etc/Unknown" is no zone at all."""

    def failing(written: object, size: int, status: object) -> int:
        status._obj.value = 1  # type: ignore[attr-defined]
        return 0

    monkeypatch.setattr(when, "_icu_zone_call", lambda: failing)
    assert when._windows_zone() is None
    monkeypatch.setattr(when, "_icu_zone_call", lambda: None)
    assert when._windows_zone() is None
    monkeypatch.setattr(when, "_windows_zone", lambda: "Etc/Unknown")
    assert when.zone_name(windows=True) is None


def test_a_runtime_that_cannot_be_loaded_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows without its C runtime library, or another operating system: nothing to reset."""
    import ctypes

    def refusing(name: str) -> object:
        raise OSError(name)

    real = when._runtime
    monkeypatch.setattr(ctypes, "CDLL", refusing)
    real.cache_clear()
    try:
        assert real() is None
        monkeypatch.setattr(when, "_runtime", lambda: None)
        when.refresh(windows=True)
    finally:
        real.cache_clear()


def test_the_other_reset_is_the_c_librarys_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anywhere but Windows, the C library's `tzset` re-reads the zone where the system has one."""
    calls: list[bool] = []
    monkeypatch.setattr(time, "tzset", lambda: calls.append(True), raising=False)
    when.refresh(windows=False)
    assert calls == [True]


def test_icu_that_cannot_be_loaded_or_lacks_the_newer_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ICU is no name; an ICU before 65 answers through its older call."""
    import ctypes
    from types import SimpleNamespace

    def refusing(name: str) -> object:
        raise OSError(name)

    monkeypatch.setattr(ctypes, "WinDLL", refusing, raising=False)
    when._icu_zone_call.cache_clear()
    try:
        assert when._icu_zone_call() is None
        older = SimpleNamespace(ucal_getDefaultTimeZone=SimpleNamespace())
        monkeypatch.setattr(ctypes, "WinDLL", lambda name: older, raising=False)
        when._icu_zone_call.cache_clear()
        assert when._icu_zone_call() is older.ucal_getDefaultTimeZone
        assert older.ucal_getDefaultTimeZone.restype is ctypes.c_int32
    finally:
        when._icu_zone_call.cache_clear()
