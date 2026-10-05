# SPDX-License-Identifier: AGPL-3.0-or-later
"""The library: the folders somebody pointed Sift at, and everything in them.

This is the slice that makes Sift a library rather than a downloader. It owns the two structures
the rest of the app hangs off (the roots, and the folder tree inside them) and the scanner that
fills them in.

The promise it exists to keep is that Sift indexes a library **in place**. It does not move, copy,
rename or write into somebody's folders. That is what lets a person point Sift at ten years of
files without a moment's thought about what it might do to them, and nothing here ever writes: the
operations that do are all in one feature of their own, are refused unless the library folder was
handed over read-write, and are asked for one file at a time.

The tables live in the kernel, because the access rules join against them and the kernel cannot
depend on a slice. What lives here is the behaviour: the API, the scanner, the watcher, and the
memory of what the scanner refused.
"""

from __future__ import annotations

from sift.kernel.jobs.quiet_hours import WHEN_QUIET
from sift.kernel.jobs.schedules import ScheduledTask, register_schedule
from sift.kernel.settings_registry import register_setting
from sift.slices.library_roots import quarantine, schema
from sift.slices.library_roots.jobs import (
    LIBRARY_SCAN,
    PRUNE_EVERY_SECONDS,
    QUARANTINE_PRUNE,
    RECONCILE,
    SCAN,
    SCAN_COUNT,
    ArchiveSettled,
    FolderSettled,
    queue_scan,
    register_handlers,
)
from sift.slices.library_roots.queue import QuarantineQueue, SkippedQueue
from sift.slices.library_roots.router import router
from sift.slices.library_roots.service import SERVICE, LibraryService
from sift.slices.library_roots.watcher import WATCHER, LibraryWatcher

register_setting(
    key=quarantine.KEEP_DAYS_KEY,
    scope="app",
    default=quarantine.DEFAULT_KEEP_DAYS,
    minimum=0,
    maximum=3650,
    unit="days",
    # With the quarantined files it decides about. When the sweep runs is upkeep nobody times, so
    # this number is the whole of what a person sets about it.
    section="Maintenance",
    label="Delete quarantined files after",
    automatic_label="Never",
    disclosure=(
        "It's off to begin with. Sift quarantines a file it can't read, which is as often a "
        "half-finished download as a damaged file. Enter a number of days to delete quarantined "
        "files automatically, or leave it empty to keep every file until you delete it yourself."
    ),
    help=("Files Sift couldn't import are kept in a folder of their own, so you can review them."),
)


#: Scan, on the Tasks screen and beside the Scan stage on Importing: walking the folders for files
#: that are new, changed or gone. Its When governs the walk, the whole-library pass and the catch-up
#: that finds what moved while Sift was closed; Run now is the whole-library pass, reading files and
#: stopping there (`scan_only`), which is what Importing's own Scan does.
register_schedule(
    ScheduledTask(
        id="scan",
        title="Scan",
        explain="Finds new, changed and removed files in your library folders.",
        job_type=LIBRARY_SCAN,
        payload={"scan_only": True},
        set_in="importing",
        press="Scan now",
    )
)


#: The quarantine sweep: upkeep nobody times, so it is drawn on no pane and left off Activity.
#:
#: The retention rule is WHAT it does (zero days is nothing to delete, and no run is placed), and
#: it runs once a day at the opening of quiet hours, because deleting files is the one thing here
#: nobody should be in front of.
register_schedule(
    ScheduledTask(
        id="quarantine-prune",
        title="Delete quarantined files",
        explain="Deletes quarantined files once they are older than the number of days you set.",
        setting_keys=(quarantine.KEEP_DAYS_KEY,),
        job_type=QUARANTINE_PRUNE,
        when_default=WHEN_QUIET,
        every=lambda values: (
            PRUNE_EVERY_SECONDS
            if quarantine.keep_days_from(values.get(quarantine.KEEP_DAYS_KEY)) > 0
            else None
        ),
        set_in="maintenance",
        records_runs=True,
        shown=False,
    )
)


__all__ = [
    "LIBRARY_SCAN",
    "QUARANTINE_PRUNE",
    "RECONCILE",
    "SCAN",
    "SCAN_COUNT",
    "SERVICE",
    "WATCHER",
    "ArchiveSettled",
    "FolderSettled",
    "LibraryService",
    "LibraryWatcher",
    "QuarantineQueue",
    "SkippedQueue",
    "quarantine",
    "queue_scan",
    "register_handlers",
    "router",
    "schema",
]
