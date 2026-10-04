# SPDX-License-Identifier: AGPL-3.0-or-later
"""Quiet hours, and whether the device is kept awake for them.

One range for the whole install, set at the top of the Tasks screen: the only clock a task's timing
is read against (see `kernel.jobs.quiet_hours`). Filed with the tasks because that is the question it
answers ("when does the work I left for the night happen") and every task's When beside it.

The keys the range was stored under before, `faces.nightly_from` and `faces.nightly_until`, are
declared removed: the settings migration carried their stored values across, and History still
names a change made under them by what they were called.
"""

from __future__ import annotations

from sift.kernel.jobs.quiet_hours import DEFAULT_FROM, DEFAULT_UNTIL, time_of_day
from sift.kernel.jobs.schedules import SECTION
from sift.kernel.settings_registry import SettingError, register_setting, remove_setting

FROM_KEY = "tasks.quiet_from"
UNTIL_KEY = "tasks.quiet_until"
KEEP_AWAKE_KEY = "tasks.keep_awake"

#: The two keys the range was stored under before, named here because this is where they went.
_RETIRED_FROM = "faces.nightly_from"
_RETIRED_UNTIL = "faces.nightly_until"


def _clock_time(value: object) -> str:
    """A 24-hour clock time as `HH:MM`, refused with a sentence where it is not one."""
    try:
        return time_of_day(value)
    except ValueError as exc:
        raise SettingError(str(exc)) from exc


def register() -> None:
    """Declare the range and the keep-awake switch, and retire the two hours they replace."""
    register_setting(
        key=FROM_KEY,
        scope="app",
        default=DEFAULT_FROM,
        section=SECTION,
        label="Quiet hours start",
        validator=_clock_time,
        help="In this device's time zone, which may differ from yours.",
    )
    register_setting(
        key=UNTIL_KEY,
        scope="app",
        default=DEFAULT_UNTIL,
        section=SECTION,
        label="Quiet hours end",
        validator=_clock_time,
        help=(
            "Work set to run during quiet hours pauses at this time. An end earlier than the start "
            "runs past midnight, which is the usual case."
        ),
    )
    register_setting(
        key=KEEP_AWAKE_KEY,
        scope="app",
        default=True,
        section=SECTION,
        label="Keep this device awake",
        disclosure=(
            "Only while work set to run during quiet hours is waiting or running. The screen can "
            "still turn off, and closing the lid or choosing Sleep still puts this device to sleep."
        ),
        help="Stops this device from going to sleep on its timer while quiet-hours work runs.",
    )
    # Removed rather than read through the quiet hours: every stored value was carried across, and
    # nothing on any screen or in any caller names them.
    for old, label in ((_RETIRED_FROM, "Start at"), (_RETIRED_UNTIL, "Stop at")):
        remove_setting(
            old,
            label=label,
            why="face recognition's two hours are the install's quiet hours now",
        )
