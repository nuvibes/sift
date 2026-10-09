# SPDX-License-Identifier: AGPL-3.0-or-later
"""Updates: noticing a new version, and saying so; installing is the desktop app's."""

from __future__ import annotations

from sift.kernel.jobs.quiet_hours import WHEN_PRESS, WHEN_WORK
from sift.kernel.jobs.schedules import ScheduledTask, register_schedule
from sift.kernel.settings_registry import register_setting
from sift.slices.update_notify.jobs import UPDATE_CHECK, register_handlers
from sift.slices.update_notify.router import router
from sift.slices.update_notify.service import (
    CHECK_INTERVAL_SECONDS,
    DISMISSED_KEY,
    ENABLED_KEY,
    SERVICE,
    Release,
    UpdateReport,
    UpdateService,
    fetch_release,
)
from sift.slices.update_notify.version import is_newer, parse

#: The check writes no History line: it changes nothing and runs often; Activity reads its job row.
register_schedule(
    ScheduledTask(
        id="update-check",
        title="Check for a new version",
        explain=(
            "Reads the public list of Sift releases and tells you when a new version is out. "
            "The check sends nothing about you or your library, and Sift never updates itself "
            "without asking you."
        ),
        job_type=UPDATE_CHECK,
        every=lambda _values: CHECK_INTERVAL_SECONDS,
        set_in="updates",
        records_runs=False,
        shown=False,
        # Updates and Info draws this When as one switch: the schedule, or Only when I press it.
        whens=(WHEN_WORK, WHEN_PRESS),
        when_label="Check for new versions automatically",
        when_help=(
            "You hear about a new version within a few hours. When it's off, Sift checks only "
            "when you press Check now."
        ),
    )
)

# Retired into the update check's When; the key stays so an old screen is answered from it.

# Written by the dismiss button, shown so a dismissed notice can be brought back.
register_setting(
    key=DISMISSED_KEY,
    scope="app",
    default="",
    section="Updates",
    label="Hidden update notice",
    help=("The version whose notice you hid. Clearing it brings the notice back."),
)

__all__ = [
    "CHECK_INTERVAL_SECONDS",
    "DISMISSED_KEY",
    "ENABLED_KEY",
    "SERVICE",
    "UPDATE_CHECK",
    "Release",
    "UpdateReport",
    "UpdateService",
    "fetch_release",
    "is_newer",
    "parse",
    "register_handlers",
    "router",
]
