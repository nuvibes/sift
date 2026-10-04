# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the backup endpoints take and give back."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from sift.kernel.wire import Wire
from sift.slices.backup.libraries import MAX_NAME
from sift.slices.backup.service import MAX_EVERY_DAYS, MAX_KEEP_DAYS

#: The most backups the rotation will keep. Repeated from the setting's declaration on purpose: a
#: request body is validated before anything reads a preference, and the two are checked against
#: each other by a test rather than by one of them importing the other at a layer it should not.
MAX_KEEP = 60

#: The longest a destination folder may be. A path is a path, not prose; anything past this is a
#: body somebody is trying to make the server hold rather than a folder.
MAX_FOLDER = 1024


#: The longest a time of day may be: `HH:MM`. What it may be is the setting's own check.
MAX_AT = 5


class ScheduleUpdate(Wire):
    """The automatic-backup settings, saved together because they only make sense together.

    How often and the time of day are left out to keep what is stored: the Backup pane saves the
    folder and the count and does not draw them (they are the task's rows on Tasks). Whether the
    backup starts on its own at all is the task's When, and is not saved here.
    """

    model_config = ConfigDict(extra="forbid")

    keep: int = Field(ge=1, le=MAX_KEEP)
    #: How many days an automatic backup is kept, zero for never. Left out, what is stored stays.
    keep_days: int | None = Field(default=None, ge=0, le=MAX_KEEP_DAYS)
    folder: str = Field(default="", max_length=MAX_FOLDER)
    every_days: int | None = Field(default=None, ge=1, le=MAX_EVERY_DAYS)
    at: str | None = Field(default=None, max_length=MAX_AT)


class ScheduleView(Wire):
    """The schedule as it now stands, and where the next one will be written."""

    #: How many days apart the automatic backups are.
    every_days: int
    #: The time of day, `HH:MM` on this device's clock, a backup on a schedule starts at.
    at: str
    keep: int
    #: How many days an automatic backup is kept beside the count, zero for never.
    keep_days: int
    folder: str
    #: Whether the destination is the directory Sift owns rather than one somebody chose. The
    #: screen says so, because a backup on the same disk as the database is a weak backup.
    beside_sift_data: bool


class UnmarkedBackupView(Wire):
    """One backup in the folder no rule deletes: one whose name says nothing about which library
    made it, or one this library saved by hand."""

    #: Its file name, which is also what a Delete names it by. Never a path.
    name: str
    #: When it was taken, from its name.
    taken_at: int
    size_bytes: int
    #: Saved by hand by this library (Save a backup, or the task's Run now), rather than unmarked.
    saved: bool = False


class SavedBackupView(Wire):
    """The backup a press of Save a backup wrote, and where it went."""

    #: Its file name, which is also what a copy of it is asked for by.
    name: str
    #: The folder it is in, as the computer running Sift names it: said on the screen as text,
    #: and opened by the desktop app on that computer.
    folder: str
    #: Its whole path on that computer, for the desktop app to show it in its folder.
    path: str
    taken_at: int
    size_bytes: int


class UnmarkedBackupsView(Wire):
    """The backups no library's rule deletes, newest first, and what a Delete of one does."""

    backups: list[UnmarkedBackupView]
    #: Whether a Delete moves the file to a Recycle Bin (True) or deletes it for good: a network
    #: share or a removable drive has no bin.
    recycle_bin: bool


class RestoreResult(Wire):
    """What the restored file said about itself."""

    app_version: str
    created_at: int
    #: The folders the file carried beside the database and put back, by their names in the
    #: archive. Empty for a backup from before the archive.
    carried: list[str] = Field(default_factory=list)


class ContentPart(Wire):
    """One folder a backup can carry beside the database, and what it holds right now."""

    name: str
    label: str
    files: int
    bytes: int
    #: Whether the next backup takes it: always for what nothing can rebuild, by setting for the
    #: rest.
    included: bool


class ContentsView(Wire):
    """What the next backup would hold beside the database. The screen says it in one sentence."""

    parts: list[ContentPart]


# --- libraries ------------------------------------------------------------------------------

#: An id is a short digest; anything longer is not one of ours.
MAX_ID = 64


class LibraryEntry(Wire):
    """One library the server knows of."""

    #: What the page names it by. Never a path: see `library_id`.
    id: str
    name: str
    #: Where it is, for an admin reading the list. Shown, never sent back.
    data_dir: str
    #: Whether it lives in the libraries folder rather than somewhere of its own.
    in_folder: bool
    #: The one running now.
    current: bool
    #: What its database says: current, older, newer, empty or unreadable.
    verdict: str
    #: Why an unreadable one can't be opened, in a sentence, where its database can say. Empty
    #: otherwise.
    detail: str
    #: The one chosen to open when Sift starts. None on the list is chosen when Sift opens whichever
    #: library was open last.
    opens_at_start: bool = False


class LibrariesView(Wire):
    """Every library the server knows of, the running one first."""

    #: Where new libraries are made.
    folder: str
    #: Whether this copy can switch at all: False when nothing would start it again.
    can_switch: bool
    libraries: list[LibraryEntry]


class NewLibrary(Wire):
    """A library to make, by name. The folder it goes in is the server's."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME)


class OpenLibrary(Wire):
    """A library from the list, by its id, and whether an older one may be upgraded."""

    model_config = ConfigDict(extra="forbid")

    library: str = Field(min_length=1, max_length=MAX_ID)
    upgrade: bool = False


class OpensAtStart(Wire):
    """Which library opens when Sift starts, by its id; null for whichever was open last."""

    model_config = ConfigDict(extra="forbid")

    library: str | None = Field(default=None, min_length=1, max_length=MAX_ID)


class DeleteLibrary(Wire):
    """A library to move to the Recycle Bin, by its id, with its name typed to say it is meant."""

    model_config = ConfigDict(extra="forbid")

    library: str = Field(min_length=1, max_length=MAX_ID)
    name: str = Field(min_length=1, max_length=MAX_NAME)


class ForgetLibrary(Wire):
    """A library kept elsewhere to take off the list, by its id. Its folder is left alone."""

    model_config = ConfigDict(extra="forbid")

    library: str = Field(min_length=1, max_length=MAX_ID)


class SwitchView(Wire):
    """What was asked. `switching` False means the library named is the one already running."""

    switching: bool
    library: str


class DuplicatePlan(Wire):
    """What the Duplicate form says before anything is pressed."""

    #: Where the copy will be made: the libraries folder "New library" uses.
    folder: str
    #: The database and the faces and covers that always come along.
    records_bytes: int
    #: The pictures Sift made, which come along only when asked for.
    pictures_bytes: int
    #: Free space on the drive the libraries folder is on.
    free_bytes: int
    #: Why a duplicate would be refused right now (a backup, restore or switch running), or None.
    refusal: str | None = None


class DuplicateLibrary(Wire):
    """A duplicate to make: its name, and whether the pictures Sift made come along."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME)
    pictures: bool = True


class DuplicateStarted(Wire):
    """The task making the copy, for the screen to follow."""

    job_id: str
