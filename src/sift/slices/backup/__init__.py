# SPDX-License-Identifier: AGPL-3.0-or-later
"""Backup: export the library's records, keep the last few automatically, and put one back.

What a backup holds is the database, and that is the whole of it. Tags, ratings, People,
collections, settings and the fingerprints that tie them to files: those are what somebody spent
their time on and the only thing here that cannot be made again. The media is not copied and is
not meant to be: it is the user's own, it is usually far too large to keep a second copy of, and
Sift only ever reads it. The thumbnails, previews and the search index are not copied either,
because a scan rebuilds them from the media.

That has to be said on the screen rather than merely be true. Somebody who believes their files
are safe here, and then loses a drive, has been failed by the wording as surely as by a bug.

Two ways to take one, both of them admin-only. **Export now** hands the file to the browser to
save. **Scheduled** writes into a chosen folder on the machine, every few days at a time of day,
keeping the last few and deleting the ones past that. The schedule is a few preferences rather
than a table: a table with one row in it forever is not a schema.

Importing this package declares those preferences. It registers no tables: backup reads the
database and does not extend it.

It also holds the LIBRARIES: making a new one, importing a backup as a new one, and switching the
running server between them (`libraries`). Here rather than in a slice of its own because a new
library made from an upload IS a restore into somewhere else, and the unpacking that does it is this
slice's; a slice may not import another, and two copies of the unpacking would be two readings of
one archive format. The switch itself is a clean stop and a start, asked of whatever supervises the
process: which library is running is a fact about the process, not a preference stored inside one
library, so this registers nothing for it either.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sift.kernel.config import LIBRARIES_FOLDER, libraries_folder
from sift.kernel.jobs.quiet_hours import WHEN_PRESS, WHEN_QUIET, WHEN_WORK, time_of_day
from sift.kernel.jobs.schedules import ScheduledTask, register_schedule, when_key
from sift.kernel.settings_registry import SettingError, register_setting, retire_setting
from sift.slices.backup.jobs import BACKUP_RUN, register_handlers
from sift.slices.backup.libraries import (
    COPIED_NOTE,
    HANDOFF_FILENAME,
    LIBRARIES,
    LIBRARY_DUPLICATE,
    REGISTRY_FILENAME,
    LibrariesService,
    LibraryError,
    leave_origin_note,
    library_id,
    make_its_own_library,
    register_library_handlers,
    sign_everyone_out,
)
from sift.slices.backup.libraries_router import router as libraries_router
from sift.slices.backup.naming import filename_for, is_backup_filename
from sift.slices.backup.router import router
from sift.slices.backup.service import (
    AT_KEY,
    DAY_SECONDS,
    DEFAULT_AT,
    DEFAULT_KEEP_DAYS,
    EVERY_DAYS_KEY,
    FOLDER_KEY,
    INCLUDE_DETECTED_KEY,
    KEEP_DAYS_KEY,
    KEEP_KEY,
    MAX_EVERY_DAYS,
    MAX_KEEP_DAYS,
    RETIRED_SCHEDULE_KEY,
    SERVICE,
    TASK_ID,
    BackupError,
    BackupService,
    BackupTooNew,
    Busy,
    DestinationRefused,
    NotABackup,
    Workers,
)

#: The most backups the rotation will keep. A ceiling rather than a preference: these are copies of
#: one database, and a number in the hundreds is a mistyped one rather than a plan.
MAX_KEEP = 60


def _folder_validator(value: Any) -> str:
    """Check the shape of a destination folder, which is all that can be checked from here.

    Whether the folder exists, sits inside a folder Sift has been given (and outside its libraries)
    and can be written to are questions about a disk, and the answers change; they are asked at the
    moment a backup is written and again when the schedule is saved. What is checked here is what cannot ever be right: a relative path, which
    would be resolved against wherever Sift happens to be running, and a `..` segment, which is an
    attempt to climb out of somewhere.

    Empty means "Sift's own folder", which is the default and always works.
    """
    if not isinstance(value, str):
        raise SettingError("expected a folder")
    folder = value.strip()
    if not folder:
        return ""
    # Asked of the site rather than looked for at the front of the string. A leading slash is
    # how POSIX spells an absolute path and is NOT how Windows spells one: `C:\\backups` does not
    # start with a slash, so a check for one would refuse every valid folder there and the setting
    # could not be saved at all. It is not merely a stricter rule either: on Windows a path that
    # DOES begin with a slash is resolved against whichever drive happens to be current, which is
    # exactly the thing this refuses a relative path for.
    #
    # `is_absolute` also accepts a UNC path, which is what a folder on another machine looks like.
    if not Path(folder).is_absolute():
        raise SettingError("that is not a full path to a folder")
    if ".." in folder.replace("\\", "/").split("/"):
        raise SettingError("a folder cannot be named with ..")
    return folder


def _clock_time(value: Any) -> str:
    """A 24-hour clock time as `HH:MM`, refused with a sentence where it is not one."""
    try:
        return time_of_day(value)
    except ValueError as exc:
        raise SettingError(str(exc)) from exc


# Filed with the other things that run on a clock rather than with the rest of Backup. The folder
# and the number to keep stay on the Backup pane: they are about the FILE, and these two are the
# only ones that answer "when". Drawn under the task's own row on Tasks, which already says
# "Automatic backup" and what it saves, and only while its When starts it on its own
# (`drawn_under` below), so neither help sentence has to name the When it needs.
register_setting(
    key=EVERY_DAYS_KEY,
    scope="app",
    default=1,
    minimum=1,
    maximum=MAX_EVERY_DAYS,
    # A count of days, so the screen offers it in days or in weeks through the one unit ladder
    # every other count of days uses: "every N days or weeks" with no control of its own.
    unit="days",
    section="Scheduled tasks",
    label="How often",
    help="How many days apart the backups are. Your media files aren't copied.",
)

register_setting(
    key=AT_KEY,
    scope="app",
    default=DEFAULT_AT,
    validator=_clock_time,
    section="Scheduled tasks",
    label="Time of day",
    help="When the backup starts on the day it falls due, in this device's time zone.",
)


def _schedule_read(values: tuple[Any, ...]) -> str:
    """The retired "How often", read from what answers it now: off while only a press runs the
    backup, else weekly for a whole number of weeks and daily for anything else."""
    days, when = values
    if str(when) == WHEN_PRESS:
        return "off"
    return "weekly" if int(days) % 7 == 0 else "daily"


def _schedule_write(value: Any, current: tuple[Any, ...]) -> tuple[Any, ...]:
    """The retired "How often", written: off makes the backup press-only, daily and weekly set the
    count of days and leave the When as it is (choosing a cadence never arms the schedule)."""
    days, when = current
    if value == "off":
        return (days, WHEN_PRESS)
    if value == "daily":
        return (1, when)
    if value == "weekly":
        return (7, when)
    raise SettingError("expected off, daily or weekly")


# An older screen or script still reads and writes the three words; each is said through the
# count of days and the When, which is where "off" is said: whether the backup starts on its own
# is its When's alone.
retire_setting(
    RETIRED_SCHEDULE_KEY,
    into=(EVERY_DAYS_KEY, when_key(TASK_ID)),
    read=_schedule_read,
    write=_schedule_write,
    why="How often is a count of days and a time of day, and Off is the When's",
)

#: The backup, on the Tasks screen. Declared beside its settings so the two cannot drift: the row's
#: cadence IS those settings' own rows. Only when pressed out of the box: where the file goes and
#: how many to keep are worth a look before anything is written. On a schedule it runs at its
#: time of day on the day it falls due; In quiet hours, as the range opens on that day. Each run
#: writes its own line in the history, which is what answers "did my backup run" for longer than
#: the job row's week.
register_schedule(
    ScheduledTask(
        id=TASK_ID,
        title="Automatic backup",
        explain="Saves your library's records into a folder, and keeps the last few.",
        setting_keys=(EVERY_DAYS_KEY, AT_KEY),
        drawn_under={EVERY_DAYS_KEY: (WHEN_WORK, WHEN_QUIET), AT_KEY: (WHEN_WORK,)},
        job_type=BACKUP_RUN,
        when_default=WHEN_PRESS,
        every=lambda values: int(values.get(EVERY_DAYS_KEY) or 1) * DAY_SECONDS,
        at=lambda values: str(values.get(AT_KEY) or DEFAULT_AT),
        set_in="backup",
        records_runs=True,
    )
)

register_setting(
    key=KEEP_KEY,
    scope="app",
    default=7,
    minimum=1,
    maximum=MAX_KEEP,
    section="Backup",
    label="Backups to keep",
    # Rotation matches only this library's own automatic names (`is_backup_filename`): a backup
    # saved by hand carries `SAVED_MARK`, and another library's carries another mark.
    help=(
        "Older automatic backups of this library are deleted once there are more than this. "
        "Backups you save yourself, and other libraries' backups, are never deleted."
    ),
    disclosure=(
        "Sift only deletes a backup whose name shows it came from this library. Backups made by "
        "an older version of Sift don't show that, so they stay until you remove them."
    ),
)

#: The age rule beside the count. Seven days for a new library; a library made before the rule has
#: "never" written for it by the settings step, so an upgrade deletes nothing it did not before.
#: Zero is "never", so the count alone decides.
register_setting(
    key=KEEP_DAYS_KEY,
    scope="app",
    default=DEFAULT_KEEP_DAYS,
    minimum=0,
    maximum=MAX_KEEP_DAYS,
    unit="days",
    section="Backup",
    label="Delete automatic backups after",
    automatic_label="Never",
    help=(
        "Automatic backups of this library older than this are deleted after the next automatic "
        "backup. Backups you save yourself are never deleted."
    ),
    disclosure=(
        "Leave it empty to keep automatic backups however old they are; the number above still "
        "applies."
    ),
)

register_setting(
    key=FOLDER_KEY,
    scope="app",
    default="",
    validator=_folder_validator,
    section="Backup",
    label="Backup folder",
    help=(
        "Choose a folder on another drive, outside your libraries. With none chosen, backups "
        "are kept in the same folder as Sift's own data."
    ),
)

register_setting(
    key=INCLUDE_DETECTED_KEY,
    scope="app",
    default=False,
    section="Backup",
    label="Include detected face pictures",
    help=(
        "Adds the image of every face Sift has detected, which makes each backup larger. The "
        "faces you confirmed are always included."
    ),
    disclosure=(
        "Identify now finds these faces again from your media, so they aren't needed to get "
        "your library back."
    ),
)

__all__ = [
    "AT_KEY",
    "BACKUP_RUN",
    "COPIED_NOTE",
    "EVERY_DAYS_KEY",
    "FOLDER_KEY",
    "HANDOFF_FILENAME",
    "INCLUDE_DETECTED_KEY",
    "KEEP_DAYS_KEY",
    "KEEP_KEY",
    "LIBRARIES",
    "LIBRARIES_FOLDER",
    "LIBRARY_DUPLICATE",
    "MAX_KEEP",
    "REGISTRY_FILENAME",
    "RETIRED_SCHEDULE_KEY",
    "SERVICE",
    "BackupError",
    "BackupService",
    "BackupTooNew",
    "Busy",
    "DestinationRefused",
    "LibrariesService",
    "LibraryError",
    "NotABackup",
    "Workers",
    "filename_for",
    "is_backup_filename",
    "leave_origin_note",
    "libraries_folder",
    "libraries_router",
    "library_id",
    "make_its_own_library",
    "register_handlers",
    "register_library_handlers",
    "router",
    "sign_everyone_out",
]
