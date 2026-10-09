# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the backup endpoints take and give back."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from sift.kernel.wire import Wire
from sift.slices.backup.libraries import MAX_NAME
from sift.slices.backup.service import MAX_EVERY_DAYS, MAX_KEEP_DAYS

#: Repeated from the setting's declaration: a body is checked before preferences are read.
MAX_KEEP = 60

MAX_FOLDER = 1024


MAX_AT = 5


class ScheduleUpdate(Wire):
    """The automatic-backup settings, saved together; how often and when are the task's."""

    model_config = ConfigDict(extra="forbid")

    keep: int = Field(ge=1, le=MAX_KEEP)
    keep_days: int | None = Field(default=None, ge=0, le=MAX_KEEP_DAYS)
    folder: str = Field(default="", max_length=MAX_FOLDER)
    every_days: int | None = Field(default=None, ge=1, le=MAX_EVERY_DAYS)
    at: str | None = Field(default=None, max_length=MAX_AT)


class ScheduleView(Wire):
    """The schedule as it now stands, and where the next one will be written."""

    every_days: int
    at: str
    keep: int
    keep_days: int
    folder: str
    beside_sift_data: bool
    working: str | None = None


class UnmarkedBackupView(Wire):
    """One backup no rule deletes: unmarked by library, or saved here by hand."""

    name: str
    taken_at: int
    size_bytes: int
    saved: bool = False


class SavedBackupView(Wire):
    """The backup a press of Save a backup wrote, and where it went."""

    name: str
    folder: str
    path: str
    taken_at: int
    size_bytes: int


class UnmarkedBackupsView(Wire):
    """The backups no library's rule deletes, newest first, and what a Delete of one does."""

    backups: list[UnmarkedBackupView]
    #: A network share or a removable drive has no bin.
    recycle_bin: bool


class RestoreResult(Wire):
    """What the restored file said about itself."""

    app_version: str
    created_at: int
    carried: list[str] = Field(default_factory=list)


class ContentPart(Wire):
    """One folder a backup can carry beside the database, and what it holds right now."""

    name: str
    label: str
    files: int
    bytes: int
    included: bool


class ContentsView(Wire):
    """What the next backup would hold beside the database. The screen says it in one sentence."""

    parts: list[ContentPart]


# --- libraries ------------------------------------------------------------------------------

MAX_ID = 64


class LibraryEntry(Wire):
    """One library the server knows of."""

    id: str
    name: str
    data_dir: str
    in_folder: bool
    current: bool
    verdict: str
    detail: str
    opens_at_start: bool = False


class LibrariesView(Wire):
    """Every library the server knows of, the running one first."""

    folder: str
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

    folder: str
    records_bytes: int
    pictures_bytes: int
    free_bytes: int
    refusal: str | None = None


class DuplicateLibrary(Wire):
    """A duplicate to make: its name, and whether the pictures Sift made come along."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME)
    pictures: bool = True


class DuplicateStarted(Wire):
    """The task making the copy, for the screen to follow."""

    job_id: str
