# SPDX-License-Identifier: AGPL-3.0-or-later
"""Updates: noticing a new version, and saying so.

The backend never updates itself. It reads the release feed the desktop application hands it,
compares versions, and says what is out. Installing is the desktop application's, on a press: it
checks the release's signed manifest against the key compiled into it and opens the installer for
the person to agree to. A backend that answers on the network does not get to replace its own
program.

This feature owns no tables. What it needs to remember is one preference: the version somebody has
decided not to be reminded about.
"""

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

#: The check: upkeep nobody times, so it is drawn on no pane and shows in Activity. Every few hours
#: on a schedule, the same interval held to the range in quiet hours, and no request at all unless
#: pressed when its When is Only when I press it (which its retired switch still writes).
#:
#: IT WRITES NO LINE IN THE HISTORY (`records_runs=False`). The check reads a public list and
#: changes nothing in the library, and it runs at every start and every few hours, so its line
#: would be the most common one in the feed and say nothing a person did or owns. Its "last ran"
#: is read from its job row instead (`TasksService._last_job`), the answer Activity's housekeeping
#: row reads: the prune never takes a type's newest run (`queue._PRUNE_SETTLED`), so the row is
#: there however long ago the last check was. What it found is on its row, and a new version is
#: said by the notice, not by the history.
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
        # Updates and Info draws this When as one switch (`Updates.svelte`): on is the schedule,
        # off is Only when I press it, with Check now beside it, so those are the two it offers.
        whens=(WHEN_WORK, WHEN_PRESS),
        when_label="Check for new versions automatically",
        when_help=(
            "You hear about a new version within a few hours. When it's off, Sift checks only "
            "when you press Check now."
        ),
    )
)

# The one outbound request Sift makes on its own. On by default because an installation that never
# hears about a security fix is worse off than one that reads a public page every few hours, and
# off in one click for anyone who would rather it made no requests at all, or has no network to
# make them on. Turning it off is silence, not a check that fails quietly.
# !! RETIRED into the When of the update check (`tasks.update-check.when`): the check
# is a task now, and "Only when I press it" is how an install that wants no outbound request at all
# says so. The key stays so a screen or a link that still names it is answered from the When; the
# composition root retires it.

# Written by the dismiss button rather than typed. It is on the screen so that somebody who
# dismissed a notice and wants it back has a way to say so without waiting for the next release.
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
