# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tasks: every piece of work Sift does over the library, and WHEN each may start on its own.

One row per task (Scan, Generate, recognition, Smart Search, music fingerprints, duplicates,
folder suggestions, shoots, stash-box lookups, the backup, the two clean-ups and the update check)
with one choice: As files arrive (On a schedule, for a timed task), In quiet hours, or Only when
I press it. Beside it, Run now | Run during quiet hours. Quiet hours is one range for the whole install,
set here, and the only clock.

The section that owns a task decides WHAT it does and draws the same When beside it; this is the
overview of every When, with quiet hours at the top. One setting, two doors. See
`kernel.jobs.schedules` for where a task is declared and `kernel.jobs.clock` for where a timed
task's next run is placed.

A task may declare parts that run on their own (its sub-tasks, some library folders) and a dry run
that says what a run would do without doing it: see `parts.py` and `jobs.py`.

Importing this package declares quiet hours and the keep-awake switch. It registers no tables: a
task's runs are the queue's rows and the history's lines.
"""

from __future__ import annotations

from sift.slices.tasks import settings
from sift.slices.tasks.jobs import TASK_DRY_RUN, register_handlers
from sift.slices.tasks.parts import (
    EVERYTHING,
    NotAPart,
    Plan,
    PlanLine,
    Planner,
    Selection,
    TaskPart,
    TaskParts,
)
from sift.slices.tasks.power import KeepAwake
from sift.slices.tasks.router import router
from sift.slices.tasks.service import (
    SERVICE,
    LastRun,
    QuietHours,
    Starter,
    TaskRefused,
    TasksService,
    TaskState,
    UnknownTask,
)
from sift.slices.tasks.settings import FROM_KEY, KEEP_AWAKE_KEY, UNTIL_KEY

settings.register()

__all__ = [
    "EVERYTHING",
    "FROM_KEY",
    "KEEP_AWAKE_KEY",
    "SERVICE",
    "TASK_DRY_RUN",
    "UNTIL_KEY",
    "KeepAwake",
    "LastRun",
    "NotAPart",
    "Plan",
    "PlanLine",
    "Planner",
    "QuietHours",
    "Selection",
    "Starter",
    "TaskPart",
    "TaskParts",
    "TaskRefused",
    "TaskState",
    "TasksService",
    "UnknownTask",
    "register_handlers",
    "router",
    "settings",
]
