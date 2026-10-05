# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taking a snapshot of the database, keeping the last few, and putting one back.

What Sift cannot rebuild is the database and two small folders beside it (the reference faces
somebody enrolled, the covers somebody uploaded); everything else is the media, which Sift only
reads, or a cache a scan makes again. So a backup is a zip archive of a manifest, the database and
those folders, small enough to take daily; the detected faces ride along only when asked for. A
backup from before the archive (a bare `.sqlite3`) is still restored.

The snapshot is `VACUUM INTO`, never a file copy: in WAL mode the newest writes are in a side file,
so a copied `.sqlite3` opens cleanly and is quietly missing them. It refuses to write over an
existing file, and it cannot run inside a transaction, so it is the only statement in its write
block. A restore copies the current database aside, removes its stale write-ahead log and renames
the new file over it atomically, so there is never a moment with no database on disk.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import sqlite3
import time
import zipfile
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import aiosqlite

from sift.kernel.access import Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce, announce_now
from sift.kernel.config import Settings
from sift.kernel.content import LibraryStore
from sift.kernel.db import Connection, Database, registered_components, too_old_to_bring_forward
from sift.kernel.ids import new_id
from sift.kernel.jobs.quiet_hours import WHEN_PRESS
from sift.kernel.jobs.schedules import when_key
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.paths import PathEscape, confine, is_writable
from sift.kernel.version import app_version
from sift.kernel.vocabulary import VIA_BACKUP
from sift.kernel.vocabulary import Subject as LedgerSubject
from sift.kernel.when import day_of
from sift.kernel.when import stamp as machine_stamp
from sift.kernel.whole_file import write_json_whole
from sift.kernel.wiring import Part
from sift.slices.backup import recycle

log = get_logger(__name__)

#: The preferences this feature keeps in the settings hub. There is no table for them: a schedule
#: is a few choices, a count and a folder, and a table holding one row forever is not a schema.
#: How many days apart the automatic backups are, and the time of day they start at.
EVERY_DAYS_KEY = "backup.every_days"
AT_KEY = "backup.at"
KEEP_KEY = "backup.keep"
#: How many days an automatic backup of this library is kept, beside how many are kept: one older
#: than this goes after the next automatic backup. Zero is "never", which is the count alone.
KEEP_DAYS_KEY = "backup.keep_days"
FOLDER_KEY = "backup.folder"
#: Whether the pictures of every detected face ride along. Off by default: they are made again by
#: a Build, and they are what turns a backup of megabytes into one of hundreds.
INCLUDE_DETECTED_KEY = "backup.include_detected_faces"

#: The retired setting "How often": off, daily or weekly. Retired into `EVERY_DAYS_KEY` and the
#: task's When (see the slice's declaration), and its stored rows carried by the settings step.
RETIRED_SCHEDULE_KEY = "backup.schedule"

#: The task's address on the Tasks screen, and the one its When is stored under.
TASK_ID = "backup"

#: A day, in seconds: what `EVERY_DAYS_KEY` is counted in.
DAY_SECONDS = 24 * 3600
#: The longest gap between automatic backups, in days. A ceiling rather than a preference: a
#: backup more than a year old is a mistyped number rather than a plan.
MAX_EVERY_DAYS = 365
#: Three in the morning: the hour a machine left on is least likely to be in use.
DEFAULT_AT = "03:00"
#: How many days a NEW library keeps its automatic backups. A library made before the rule keeps
#: what it had, the count alone (the settings step writes "never" for it), until somebody
#: chooses: a rule that starts deleting files on an upgrade is a surprise nobody asked for.
DEFAULT_KEEP_DAYS = 7
#: The longest a backup may be kept for, in days, short of "never": ten years.
MAX_KEEP_DAYS = 3650

#: The version of the backup format itself, so a future change to what an export contains can be
#: recognized rather than guessed at. It is not the schema version and it is not the app version.
#: 1 was a bare database; 2 is the archive.
FORMAT_VERSION = 2

#: The members every archive holds. The database keeps its own stamp inside it as well (the
#: manifest table below), so the database on its own is still recognisable as Sift's.
MANIFEST_MEMBER = "manifest.json"
DATABASE_MEMBER = "sift.sqlite3"

#: The first four bytes of a zip archive, which is how a candidate is told from a bare database.
_ZIP_MAGIC = b"PK\x03\x04"

#: The table an export carries that a live database does not. It is what tells a restore that this
#: file is a Sift backup rather than some other SQLite database, and it names the version of Sift
#: that wrote it. Dropped once the file has been adopted, so the live database stays as it was.
MANIFEST_TABLE = "backup_manifest"

# Written out rather than assembled from the constant above. Nothing here is dynamic, so there is
# nothing to gain from building it, and query text that is built is query text somebody later
# builds from a variable.
_CREATE_MANIFEST = """
CREATE TABLE backup_manifest (
  format_version INTEGER NOT NULL,
  app_version    TEXT NOT NULL,
  created_at     INTEGER NOT NULL
)
"""
_INSERT_MANIFEST = (
    "INSERT INTO backup_manifest (format_version, app_version, created_at) VALUES (?, ?, ?)"
)
_READ_MANIFEST = "SELECT format_version, app_version, created_at FROM backup_manifest"
#: Whether the user a saved backup's line names is still there: the ledger keys its user.
_USER_THERE = "SELECT 1 FROM users WHERE id = ?"
_DROP_MANIFEST = "DROP TABLE IF EXISTS backup_manifest"

#: Whether the user who pressed Restore is a user of the library that came back: a backup from
#: another install has its own users, and the record names only somebody it has a row for.
_A_USER_HERE = "SELECT 1 FROM users WHERE id = ?"

#: The moment an automatic backup's name carries (`filename_for`): the machine's local time and the
#: offset it had (`-0400`). A name with no offset is one written before names carried it: UTC.
_STAMP_IN_NAME = re.compile(r"-(?P<stamp>\d{8}-\d{6})(?P<offset>[+-]\d{4})?-")


def the_backup_from(when: float) -> str:
    """How a History line names a backup file: by the day it was taken ("the backup from 12
    September 2026"). The day and not the file name: the name is a mark and a timestamp nobody
    reads, and the day is what somebody choosing between backups looks at. The day on the server
    machine's clock (`kernel/when.py`), the one every date Sift shows is in."""
    day = day_of(when)
    return f"the backup from {day.day} {day:%B %Y}"


def moment_in_name(name: str) -> float | None:
    """When an automatic backup was taken, read from its name, or None for a name with no stamp.

    Exact either way: a name carries its offset, and one written before names carried an offset
    is UTC, which is what those names were written in.
    """
    found = _STAMP_IN_NAME.search(name)
    if found is None:
        return None
    return datetime.strptime(
        found["stamp"] + (found["offset"] or "+0000"), "%Y%m%d-%H%M%S%z"
    ).timestamp()


def _by_age(entry: Path) -> tuple[float, str]:
    """The order rotation keeps backups in: by the moment each name carries, oldest first.

    Not by the name itself. The name is the machine's local time, which repeats an hour when the
    clocks go back, reads hours apart on either side of a trip across zones, and sat beside the
    UTC names written before it; the moment it stands for is none of those things.
    """
    return (moment_in_name(entry.name) or 0.0, entry.name)


def _taken_at(entry: Path) -> float:
    """When an automatic backup was taken, from its name, else from the file itself. Blocking."""
    found = moment_in_name(entry.name)
    return entry.stat().st_mtime if found is None else found


#: Set on every connection to a database that arrived from outside. Its views and triggers may
#: then use only the functions and virtual tables SQLite knows to be harmless.
_DISTRUST_SCHEMA = "PRAGMA trusted_schema=OFF"

#: What an export is called: the library's mark, then the moment on the server machine's clock with
#: its offset (`20260930-213000-0400`), then the version. Rotation reads the moment back out of the
#: name (`moment_in_name`) instead of asking the filesystem for a date.
FILENAME_PREFIX = "sift-backup-"
FILENAME_SUFFIX = ".zip"
#: What a backup is called while it is packed, which no listing of backups matches (`_pack`).
PARTIAL_SUFFIX = ".partial"
#: The word a backup SAVED BY HAND carries before its extension, and rotation never matches it:
#: the name is the one thing that travels with the file into any folder, so a pressed backup under
#: the automatic name would go with the old ones the next time a scheduled backup ran.
SAVED_MARK = "-saved"

#: How long a library's mark is, in hexadecimal digits. See `mark_of_library`.
LIBRARY_MARK_LENGTH = 12
_MARK = re.compile(rf"[0-9a-f]{{{LIBRARY_MARK_LENGTH}}}")

#: The file in a library's data folder that holds the mark its backups are named with.
LIBRARY_MARK_FILENAME = "backup-mark.json"

#: Rotation deletes only names this matches, and of those only the ones carrying the running
#: library's own mark: the folder is an admin's, may hold another library's backups (a duplicate
#: starts out pointed where its original was), and a name without a mark (written before libraries
#: marked theirs) says nothing about which library made it, so no rotation takes it.
_OURS = re.compile(
    rf"^{re.escape(FILENAME_PREFIX)}(?P<library>[0-9a-f]{{{LIBRARY_MARK_LENGTH}}})"
    rf"-\d{{8}}-\d{{6}}(?:[+-]\d{{4}})?-[A-Za-z0-9._+-]+{re.escape(FILENAME_SUFFIX)}$"
)

#: Where the previous database is left when a restore replaces it. One file, replaced by the next
#: restore: it is the undo for the last restore, not an archive.
SUPERSEDED_SUFFIX = ".superseded"

#: Where an incoming backup is assembled before it is swapped in. Beside the database on purpose,
#: so the swap is a rename within one filesystem and therefore atomic.
INCOMING_SUFFIX = ".incoming"


class BackupError(Exception):
    """Base for the refusals this feature makes, so a router can catch one thing."""


class NotABackup(BackupError):
    """The file offered for restore is not a backup Sift wrote."""


class BackupTooNew(BackupError):
    """The backup came from a newer Sift than this one.

    Refused outright. Migrating a schema backwards means dropping columns and tables a newer build
    filled in, and the file being destroyed would be the good copy somebody was restoring from.
    """


class BackupTooOld(BackupError):
    """The backup came from a Sift too old for this one to bring forward.

    Refused before anything is moved, as a newer one is. Without it a restore would swap the file
    in and the start that followed would refuse it, leaving the library off the air, and an import
    would make a library that could never be opened.
    """


class DestinationRefused(BackupError):
    """The folder chosen for scheduled backups cannot be written to."""


class Busy(BackupError):
    """Work that copies, replaces or leaves this library is already running. Tried again later."""


class NotThere(BackupError):
    """The backup a press named is not in the backup folder, or could not be moved from it."""


class NoRoomToUnpack(BackupError):
    """The drive a backup would be unpacked onto has no room for what the archive holds."""


#: The four kinds of that work, by the word `BackupService.exclusively` is handed.
BACKING_UP = "backup"
RESTORING = "restore"
DUPLICATING = "duplicate"
SWITCHING = "switch"
DELETING = "delete"

#: What a refusal says, by what is already running. One sentence each, naming the work and what
#: to do; never "busy", which tells somebody nothing they can act on.
_BUSY = {
    BACKING_UP: "A backup is being saved right now. Try again when it has finished.",
    RESTORING: "A backup is being restored right now. Try again when it has finished.",
    DUPLICATING: "This library is being duplicated right now. Try again when it has finished.",
    SWITCHING: "Sift is switching libraries right now.",
    DELETING: "A library is being deleted right now. Try again when it has finished.",
}


@dataclass(frozen=True)
class Carried:
    """One folder a backup carries beside the database.

    `name` is the folder's name inside the archive and the one thing a restore matches on, so it
    must not change once a backup has been written under it. `always` says whether it goes in
    every backup or only when the setting asks.
    """

    name: str
    label: str
    path: Path
    always: bool


@dataclass(frozen=True, slots=True)
class BackupPlan:
    """What a backup now would do: the file it would write, then the older ones it would delete."""

    file: Path
    drop: tuple[Path, ...]


class Workers(Protocol):
    """The worker pool, as much of it as a restore needs.

    A restore closes the database and moves the file, and workers holding jobs against it would
    write into a file that is no longer there. Taken as a protocol so a test can pass something
    that records the two calls, and so this module does not reach into the job kernel to do it.
    """

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


def _size_of(path: Path) -> int:
    """The size of a file, off the event loop. Reading a directory entry blocks like any other
    disk read, and every one of these calls is on an async path."""
    return path.stat().st_size


def _files_under(folder: Path) -> list[Path]:
    """Every file under a folder, sorted, off the event loop. Empty for a folder that is not there."""
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.rglob("*") if path.is_file())


def _measure(folder: Path) -> tuple[int, int]:
    """How many files a folder holds and how many bytes. Blocking.

    A file taken away between the listing and its size is left out rather than failing the read:
    the faces pass rewrites the detected pictures while the Backup screen measures them."""
    count = size = 0
    for path in _files_under(folder):
        try:
            size += path.stat().st_size
        except FileNotFoundError:
            continue
        count += 1
    return count, size


def is_backup_archive(path: Path) -> bool:
    """Whether the file begins the way a zip archive does. Blocking."""
    try:
        with path.open("rb") as handle:
            return handle.read(len(_ZIP_MAGIC)) == _ZIP_MAGIC
    except OSError:
        return False


def _pack(
    destination: Path, snapshot: Path, carried: Sequence[Carried], manifest: dict[str, Any]
) -> None:
    """Write the archive. Blocking. Refuses a destination that is already there: rotation keeps
    the last few backups, and an export that wrote over one would make that a lie.

    Packed under a name no backup listing matches and renamed once whole, so a backup still being
    written is never listed, kept by rotation, offered for Delete or restored half-written."""
    if destination.exists():
        raise FileExistsError(destination)
    partial = destination.with_name(destination.name + PARTIAL_SUFFIX)
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_MEMBER, json.dumps(manifest, indent=1, sort_keys=True))
            archive.write(snapshot, DATABASE_MEMBER)
            for one in carried:
                for file in _files_under(one.path):
                    try:
                        archive.write(file, one.name + "/" + file.relative_to(one.path).as_posix())
                    except FileNotFoundError:
                        # Taken away since the listing, so no longer the library's: the faces pass
                        # rewrites the detected pictures while a backup is packed (see `_measure`).
                        continue
        os.rename(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def _manifest_of(archive_path: Path) -> dict[str, Any]:
    """The manifest inside an archive. Blocking. Refuses anything that is not a backup."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            if MANIFEST_MEMBER not in names or DATABASE_MEMBER not in names:
                raise NotABackup("That file isn't a Sift backup.")
            manifest = json.loads(archive.read(MANIFEST_MEMBER))
    except (zipfile.BadZipFile, ValueError) as broken:
        raise NotABackup("That file isn't a Sift backup.") from broken
    if not isinstance(manifest, dict) or not isinstance(manifest.get("carried"), list):
        raise NotABackup("That file isn't a Sift backup.")
    return manifest


#: The room unpacking a backup must leave on the drive beyond what it writes. A drive filled to
#: the last byte fails the next write anything on the device makes.
ROOM_TO_SPARE = 512 * 1024 * 1024

#: Characters no member Sift writes has in its name. On Windows a backslash is a separator and a
#: colon names a drive or a stream, so either can carry a member out of its folder.
_REFUSED_IN_NAMES = frozenset("\\:\x00")


def free_bytes(place: Path) -> int:
    """The free space on the drive `place` is, or will be, on. Blocking."""
    probe = place
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def _refuse_without_room(members: Sequence[zipfile.ZipInfo], into: Path) -> None:
    """Refuse members that would not fit on the drive `into` is on. Blocking.

    The size an archive declares for a member is a ceiling as well as a claim: zipfile stops
    reading a member there, so nothing written can be larger than what was checked.
    """
    need = sum(member.file_size for member in members)
    if free_bytes(into) < need + ROOM_TO_SPARE:
        gigabytes = (need + ROOM_TO_SPARE) / 1024**3
        raise NoRoomToUnpack(
            f"There isn't enough free space to unpack that backup. It needs about "
            f"{gigabytes:.1f} GB. Free some space, then try again. Nothing has been changed."
        )


def _member_target(into: Path, relative: str) -> Path:
    """Where the member called `relative` inside its folder is written, proved to be inside `into`.

    Blocking (it resolves). The name is checked as text first, because a name that is harmless on
    one system can be a drive, a root or a network share on another; the resolved path is then
    confined, so no name reaches a place outside `into` whatever it looked like.
    """
    parts = relative.split("/")
    if any(part in ("", ".", "..") or _REFUSED_IN_NAMES.intersection(part) for part in parts):
        raise NotABackup("That file isn't a Sift backup.")
    try:
        return confine(into, into.joinpath(*parts))
    except PathEscape as escape:
        raise NotABackup("That file isn't a Sift backup.") from escape


def _extract_database(archive_path: Path, into: Path) -> None:
    """The database member, written to `into`. Blocking."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            _refuse_without_room([archive.getinfo(DATABASE_MEMBER)], into.parent)
            with archive.open(DATABASE_MEMBER) as member, into.open("wb") as sink:
                shutil.copyfileobj(member, sink)
    except zipfile.BadZipFile as broken:
        raise NotABackup("That file isn't a Sift backup.") from broken


def _extract_folder(archive_path: Path, name: str, into: Path) -> None:
    """Every member under `name/` written under `into`, with the prefix taken off. Blocking.

    Every name is checked, and the room for all of them, before anything is written: a member
    that climbs out of its folder is a file somebody put in an archive, not one Sift wrote.
    """
    prefix = name + "/"
    # Made even when the archive holds nothing under the name: a folder that was empty when the
    # backup was taken comes back empty, which is a different thing from not coming back.
    into.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = [
                member
                for member in archive.infolist()
                if member.filename.startswith(prefix) and not member.is_dir()
            ]
            targets = [_member_target(into, one.filename[len(prefix) :]) for one in members]
            _refuse_without_room(members, into)
            for member, target in zip(members, targets, strict=True):
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
    except zipfile.BadZipFile as broken:
        raise NotABackup("That file isn't a Sift backup.") from broken


def _swap_folder(live: Path, incoming: Path) -> None:
    """Put `incoming` where `live` is, keeping what was there as the undo. Blocking.

    The same shape the database's own swap has: the folder being replaced is renamed aside
    first, so it is never gone, and the incoming one is renamed into place.
    """
    superseded = live.with_name(live.name + SUPERSEDED_SUFFIX)
    if superseded.exists():
        shutil.rmtree(superseded, ignore_errors=True)
    if live.exists():
        os.replace(live, superseded)
    live.parent.mkdir(parents=True, exist_ok=True)
    os.replace(incoming, live)


def _listing(folder: Path) -> list[Path]:
    """What is in a folder, off the event loop, as a list rather than a lazy iterator: the
    iterator would do its reading back on the loop it was moved off."""
    return list(folder.iterdir())


def _version_stamp() -> str:
    """The version as it goes ONTO a backup, which is a filename and a manifest row.

    Both are read long after they were written, and a blank reads as damage. It also has to stay
    matchable by the rotation rule below, which is what decides whether Sift may ever delete the
    file: a name with nothing between the timestamp and the extension would never match, so an
    export taken from a source tree would accumulate for ever.
    """
    return app_version() or "unknown"


def filename_for(when: float, *, library: str, saved: bool = False) -> str:
    """The name an export takes, from the library that made it and the moment it was made.

    The machine's local time, because a person reads it in a folder beside the clock on the wall,
    with the offset it had written after it, so the name still says the exact moment across a clock
    change or a trip to another zone (`moment_in_name`). Not UTC, though a UTC name sorts by age as
    it is spelled: it reads hours away from the day the backup was taken for anybody far from
    Greenwich, and rotation orders by the moment read back (`_by_age`), not by the spelling. The
    version rides along after the timestamp, where it tells somebody looking at a folder full of
    files what wrote each one. `library` is the library's mark
    (`mark_of_library`); `saved` marks one somebody saved by hand, which rotation never deletes
    (`SAVED_MARK`).
    """
    stamp = machine_stamp(when, "%Y%m%d-%H%M%S%z")
    mark = SAVED_MARK if saved else ""
    return f"{FILENAME_PREFIX}{library}-{stamp}-{_version_stamp()}{mark}{FILENAME_SUFFIX}"


def is_backup_filename(name: str, *, library: str) -> bool:
    """Whether this library's rotation may delete this file: an automatic backup this library
    made, and never one saved by hand. See `_OURS` and `SAVED_MARK`."""
    matched = _OURS.match(name)
    if matched is None or matched["library"] != library:
        return False
    return not name.endswith(SAVED_MARK + FILENAME_SUFFIX)


#: A backup named before libraries marked theirs: the moment and the version, no mark. The older
#: bare database (`.sqlite3`) is one too. Its name says nothing about which library made it, so no
#: rule ever deletes one: the Backup pane lists them, each with its own Delete.
_UNMARKED = re.compile(
    rf"^{re.escape(FILENAME_PREFIX)}\d{{8}}-\d{{6}}(?:[+-]\d{{4}})?-[A-Za-z0-9._+-]+"
    rf"(?:{re.escape(FILENAME_SUFFIX)}|\.sqlite3)$"
)

#: The most unmarked backups the pane lists. A folder holds a handful from the versions before the
#: mark; a list past this is a folder of something else, and the newest are the ones shown.
MAX_UNMARKED = 200


def is_unmarked_backup(name: str) -> bool:
    """Whether this is a Sift backup whose name carries no library's mark. See `_UNMARKED`."""
    return _UNMARKED.match(name) is not None


def is_saved_by_hand(name: str, *, library: str) -> bool:
    """Whether this is a backup THIS library saved by hand (`SAVED_MARK`): no rule deletes it."""
    matched = _OURS.match(name)
    return (
        matched is not None
        and matched["library"] == library
        and name.endswith(SAVED_MARK + FILENAME_SUFFIX)
    )


@dataclass(frozen=True, slots=True)
class UnmarkedBackup:
    """One backup in the folder that no rule deletes: one whose name says nothing about which
    library made it, or one this library saved by hand (`saved`)."""

    name: str
    #: When it was taken: the moment its name carries.
    taken_at: int
    size_bytes: int
    #: Saved by hand by this library, rather than unmarked.
    saved: bool = False


def _unmarked_in(folder: Path, library: str) -> list[UnmarkedBackup]:
    """The backups no rule deletes in a folder, newest first, at most `MAX_UNMARKED`: the
    unmarked ones, and the ones this library saved by hand. Blocking.

    THE ONES SAVED BY HAND ARE LISTED WITH THE OTHERS. A press of Save a backup writes into this
    folder and no rule ever takes what it wrote, so without this list they would pile up where
    nothing on the screen shows them, each the size of the library's database."""
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []
    listed_here = []
    for entry in entries:
        saved = is_saved_by_hand(entry.name, library=library)
        if not saved and not is_unmarked_backup(entry.name):
            continue
        try:
            if not entry.is_file():
                continue
            size = entry.stat().st_size
        except OSError:  # pragma: no cover (a file taken away between the listing and its size)
            continue
        listed_here.append(UnmarkedBackup(entry.name, int(_taken_at(entry)), size, saved))
    listed_here.sort(key=lambda one: (one.taken_at, one.name), reverse=True)
    return listed_here[:MAX_UNMARKED]


def keep_days_from(stored: object) -> int:
    """The age rule as a whole number of days, zero for "never"; the default where nothing usable
    is stored. A negative number is "never" rather than the default: it can only mean "off"."""
    try:
        days = int(str(stored))
    except (TypeError, ValueError):
        return DEFAULT_KEEP_DAYS
    return max(0, days)


def _resolved(chosen: str) -> Path:
    """A typed folder as an absolute path (a `~` expanded); a disk read, so off the loop."""
    return Path(chosen).expanduser().resolve()


def mark_of_library(data_dir: Path) -> str:
    """The mark this library's backups carry in their names, made the first time it is asked for.
    Blocking.

    A random value kept in the library's data folder beside the folder it was made for. Not a
    digest of the path, because two computers can keep their libraries at the same path and point
    them at one shared backup folder. Not a row in the database, because a duplicate and an
    imported backup carry every row, and would carry the mark into a second library with them.

    Made again when the folder written down is not this one, so a library copied to a second place
    by hand stops sharing its original's mark. The backups made under the old mark are then left
    alone for good, which is the safe way to be wrong: rotation that cannot tell whose a file is
    keeps it.
    """
    record = data_dir / LIBRARY_MARK_FILENAME
    here = os.path.normcase(os.path.abspath(data_dir))
    try:
        written = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        written = None
    if isinstance(written, dict) and written.get("for") == here:
        kept = written.get("mark")
        if isinstance(kept, str) and _MARK.fullmatch(kept):
            return kept
    made = secrets.token_hex(LIBRARY_MARK_LENGTH // 2)
    data_dir.mkdir(parents=True, exist_ok=True)
    write_json_whole(record, {"mark": made, "for": here})
    return made


def carried_in(data_dir: Path, cache_dir: Path) -> tuple[Carried, ...]:
    """The folders a backup carries, as they are laid out in one library's two directories.

    A function of the two directories rather than of the running library, because a backup can be
    unpacked into a library that is NOT the one running (a new one being made from it) and the
    layout has to be the same one either way, or the folders would land where that library never
    looks for them.
    """
    return (
        Carried("faces/references", "Confirmed faces", data_dir / "faces" / "references", True),
        Carried("covers", "Uploaded covers", cache_dir / "covers", True),
        Carried("faces/detected", "Detected face pictures", data_dir / "faces" / "detected", False),
    )


def drop_manifest(database: Path) -> None:
    """Take the backup's own stamp out of a database that is becoming a library. Blocking."""
    connection = sqlite3.connect(database)
    try:
        connection.execute(_DISTRUST_SCHEMA)
        connection.execute(_DROP_MANIFEST)
        connection.commit()
    finally:
        connection.close()


class BackupService:
    """Snapshots, rotation and restore. One per application, held on app.state.

    It holds the database rather than a connection, because a restore closes and reopens it: a
    module handed a live connection could not put a different file underneath it.
    """

    def __init__(
        self,
        database: Database,
        settings: Settings,
        read_setting: Callable[[str], Awaitable[Any]],
        write_settings: Callable[[Viewer, dict[str, Any]], Awaitable[None]],
        *,
        workers: Workers | None = None,
        clock: Callable[[], float] = time.time,
        library: LibraryStore | None = None,
    ) -> None:
        self._db = database
        self._settings = settings
        self._read = read_setting
        self._write = write_settings
        self._workers = workers
        self._clock = clock
        # The granted folders and the libraries, which decide where a chosen backup folder may be.
        # The application's own store where it is handed one; otherwise one over the same database,
        # which is the same answer: the store keeps nothing but the handle and the settings.
        self._library = library if library is not None else LibraryStore(database, settings)
        # One piece of whole-library work at a time: any two at once copy a half-swapped library.
        # One event loop, so checking and taking it cannot interleave.
        self._exclusive = asyncio.Lock()
        self._working: str | None = None
        # Read once: the data folder cannot change under a running process.
        self._mark: str | None = None

    async def library_mark(self) -> str:
        """The mark this library's backups are named with. See `mark_of_library`."""
        if self._mark is None:
            self._mark = await asyncio.to_thread(mark_of_library, self._settings.data_dir)
        return self._mark

    @property
    def working(self) -> str | None:
        """The whole-library work running now, by its word (`BACKING_UP` and the rest), or None."""
        return self._working

    def refusal_while_busy(self) -> str | None:
        """The sentence a request would be refused with right now, or None when nothing runs."""
        return None if self._working is None else _BUSY[self._working]

    @asynccontextmanager
    async def exclusively(self, what: str) -> AsyncIterator[None]:
        """Run one piece of whole-library work, refused with `Busy` while another runs. Never
        waited for: a waiter could wait on a restore that waits for the workers to stop."""
        if self._exclusive.locked():
            raise Busy(_BUSY.get(self._working or "", _BUSY[BACKING_UP]))
        async with self._exclusive:
            # Its start and end are a settings change, so every admin's open Backup pane re-reads.
            self._working = what
            announce_now(EVERY_ADMIN, About.SETTINGS)
            try:
                yield
            finally:
                self._working = None
                announce_now(EVERY_ADMIN, About.SETTINGS)

    def now(self) -> float:
        """The clock this feature runs on. Taken from here so a test can move time."""
        return self._clock()

    async def setting(self, key: str) -> Any:
        """One of this feature's preferences, read fresh. The screen shows what the job will use."""
        return await self._read(key)

    # --- what rides along ---------------------------------------------------------------------

    def carried(self) -> tuple[Carried, ...]:
        """The folders a backup can carry beside the database, in the order they are listed.

        The confirmed faces are what somebody enrolled by naming; the covers are what somebody
        uploaded. Neither comes back from a scan. The detected faces do (a Build finds them
        again), so they are the one part that is asked for rather than taken.

        The LABEL is what a person reads on the backup screen, and "reference" is a word out of the
        machinery: what is in that folder is the faces somebody confirmed. The path keeps the old
        spelling, because it is a folder on disk in every archive ever written and renaming it would
        make an old backup unreadable.
        """
        return carried_in(self._settings.data_dir, self._settings.cache_dir)

    async def _going_in(self) -> list[Carried]:
        """The folders the next export takes: every one that always goes, and the rest as set."""
        detected = bool(await self._read(INCLUDE_DETECTED_KEY))
        return [one for one in self.carried() if one.always or detected]

    async def contents(self) -> list[dict[str, Any]]:
        """What the next backup would hold beside the database, with sizes. For the screen."""
        going = {one.name for one in await self._going_in()}
        listed: list[dict[str, Any]] = []
        for one in self.carried():
            files, size = await asyncio.to_thread(_measure, one.path)
            listed.append(
                {
                    "name": one.name,
                    "label": one.label,
                    "files": files,
                    "bytes": size,
                    "included": one.name in going,
                }
            )
        return listed

    # --- taking one --------------------------------------------------------------------------

    async def export_to(self, destination: Path) -> None:
        """Write one file to `destination`: the manifest, a consistent snapshot of the live
        database, and the folders that ride along.

        The destination must not already exist. An export that silently wrote over yesterday's
        would make the rotation below a lie, and there is no case where overwriting a backup is
        what somebody meant. The snapshot is taken beside the destination and packed from there,
        so the archive is written once, whole.
        """
        snapshot = destination.with_name(destination.name + ".snapshot")
        await self.snapshot_into(snapshot)
        try:
            await self._stamp(snapshot)
            carried = await self._going_in()
            manifest = {
                "format": FORMAT_VERSION,
                "app_version": _version_stamp(),
                "created_at": int(self._clock()),
                "carried": [one.name for one in carried],
            }
            await asyncio.to_thread(_pack, destination, snapshot, carried, manifest)
        finally:
            await asyncio.to_thread(snapshot.unlink, True)
        log.info(
            "backup.exported",
            size_bytes=await asyncio.to_thread(_size_of, destination),
            carried=[one.name for one in carried],
        )

    async def snapshot_into(self, target: Path) -> None:
        """A consistent copy of the live database at `target`, which must not exist yet.

        The one way this feature copies a database (a backup, and a library duplicated from this
        one), so both get the same guarantee: `VACUUM INTO`, never a file copy (the module
        docstring says why), and the only statement in its write block.
        """
        async with self._db.write() as connection:
            await connection.execute("VACUUM INTO ?", (str(target),))

    async def _stamp(self, path: Path) -> None:
        """Record what wrote this file, inside the file.

        Inside rather than beside: a note in a second file is separated from the backup the first
        time somebody moves one of them, and a backup that cannot say what it is has to be guessed
        at by whatever tries to restore it.
        """
        async with aiosqlite.connect(path) as connection:
            await connection.execute(_CREATE_MANIFEST)
            await connection.execute(
                _INSERT_MANIFEST, (FORMAT_VERSION, _version_stamp(), int(self._clock()))
            )
            await connection.commit()

    # --- the scheduled one -------------------------------------------------------------------

    async def destination(self) -> Path:
        """The folder scheduled backups are written to, proven writable, created if it is missing.

        Unset, it is a directory Sift owns beside the database. That works everywhere and is a
        poor backup: it is on the same disk as the thing it protects, so the failure most likely
        to cost somebody their library takes both. The screen says so and offers the picker.

        A chosen folder is confined to the folders Sift has been GIVEN (Settings > Folders), which
        is the same set the folder picker is confined to and the only places on this device Sift
        will look at directories at all. Without that, a folder arriving in a request would be a way
        to make Sift write a file anywhere it can reach. See `resolve_folder`.
        """
        return await self.resolve_folder(str(await self._read(FOLDER_KEY) or ""), ensuring=True)

    async def resolve_folder(self, chosen: str, *, ensuring: bool = False) -> Path:
        """The folder a setting names, proven usable. Empty means the one Sift owns.

        A chosen folder is a USE of a granted folder, like a library: with `ensuring` the grant
        that covers it is made here when there is none (the folder picker walked it, or the
        person typed it), so a backup folder never needs a second list of folders anywhere.
        The settings door's check passes `ensuring=False`: a check makes nothing.

        NOT INSIDE A LIBRARY. A granted folder usually holds one, and a backup written into a
        watched library is read straight back in: the backup is a `.zip`, an archive is indexed in
        place as a gallery, and with face pictures included it carries hundreds of them.
        """
        chosen = chosen.strip()
        if not chosen:
            folder = self._settings.data_dir / "backups"
            if ensuring:
                await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
            return folder

        folder = await asyncio.to_thread(_resolved, chosen)
        libraries = [Path(root.abs_path) for root in await self._library.roots()]
        if any(folder == library or library in folder.parents for library in libraries):
            raise DestinationRefused(
                "That folder is part of your library, so Sift would read its own backups back in "
                "as files. Choose a folder outside your libraries."
            )

        if not await asyncio.to_thread(folder.is_dir):
            raise DestinationRefused("That folder isn't there any more. Pick another one.")
        if not await asyncio.to_thread(is_writable, folder):
            raise DestinationRefused(
                "Sift can't write to that folder. It's read-only, or it belongs to another user."
            )
        if ensuring:
            await self._library.ensure_grant(folder)
        return folder

    async def folders_in_use(self) -> list[Path]:
        """The chosen backup folder, for the grant it uses; none when backups stay beside the data."""
        chosen = str(await self._read(FOLDER_KEY) or "").strip()
        return [await asyncio.to_thread(_resolved, chosen)] if chosen else []

    async def plan(self, *, ensuring: bool = False, pressed: bool = False) -> BackupPlan:
        """The file a backup now would write and the older ones it would then delete.

        The scheduled run is this plan carried out, and a dry run is this plan said, so the two
        cannot disagree. Without `ensuring` it writes nothing: the folder is checked, not made or
        granted. Raises `DestinationRefused` for a folder that cannot be used.

        A PRESSED run is a backup somebody made by hand: it is named as one saved by hand
        (`SAVED_MARK`), so no rule ever deletes it, and it deletes nothing. It adds no automatic
        backup to the folder, so there is nothing for it to rotate, and a press that deleted the
        oldest automatic backup would be a press with a cost nobody asked for.
        """
        if ensuring:
            folder = await self.destination()
        else:
            folder = await self.resolve_folder(str(await self._read(FOLDER_KEY) or ""))
        library = await self.library_mark()
        produced = folder / filename_for(self._clock(), library=library, saved=pressed)
        if pressed:
            return BackupPlan(file=produced, drop=())
        ours = await self._ours(folder)
        return BackupPlan(file=produced, drop=tuple(await self._stale([*ours, produced])))

    async def run_scheduled(self, *, pressed: bool = False) -> Path:
        """Take a backup into the chosen folder and drop all but the newest few.

        Returns the file it wrote. The rotation runs after the new one is on disk, never before:
        deleting first would leave a machine that fails mid-export with one fewer backup than it
        started with, which is the wrong direction to fail in. A pressed run keeps its backup as
        one saved by hand and rotates nothing (`plan`).
        """
        async with self.exclusively(BACKING_UP):
            planned = await self.plan(ensuring=True, pressed=pressed)
            await self.export_to(planned.file)
            await self._drop(planned.drop, kept=None)
        return planned.file

    async def save_now(self, by: Viewer) -> Path:
        """Save a backup, on the backup pane: one backup into the backup folder, saved by hand.

        The same file a press of the task's Run now writes (`run_scheduled` pressed): named as
        saved by hand, so no rule ever deletes it and it deletes nothing, and in the same folder
        as the automatic ones, where the pane lists it with the others no rule takes. A browser
        on another device asks for a copy of it afterwards (`left_alone_file`).

        ON HISTORY as the person's own act, with the folder it went to, never with what it holds:
        "You saved the backup from 1 October 2026 in D:\\Backups". Recorded once the file is
        whole, so the line never names a backup that is not there.
        """
        saved = await self.run_scheduled(pressed=True)
        await self.record_saved(saved, by.id)
        return saved

    async def record_saved(self, saved: Path, user_id: str) -> None:
        """Say on History that this user saved this backup, with the folder it went to: the one
        line both presses write, the pane's Save a backup and the task's Run now.

        Never with what it holds, and only once the file is whole, so the line never names a
        backup that is not there. A user removed since the press has no row the line may name, so
        nothing is said for them.
        """
        taken = moment_in_name(saved.name) or self._clock()
        async with self._db.write() as connection:
            if not await connection.execute_fetchall(_USER_THERE, (user_id,)):
                return
            # The History feed on every admin's open tab re-reads on the library bell.
            announce(EVERY_ADMIN, About.LIBRARY)
            await record_event(
                connection,
                actor=Actor.user(user_id),
                verb="saved",
                subject=LedgerSubject(kind="backup", id=saved.name, name=the_backup_from(taken)),
                payload=json.dumps({"folder": str(saved.parent)}),
            )

    async def _ours(self, folder: Path) -> list[Path]:
        """This library's automatic backups in `folder`, oldest first; none in a folder not there.

        Only files this library named are ever considered, let alone deleted: the folder belongs
        to whoever chose it and may hold anything, another library's backups included. Sorting is
        by the moment each name carries (`_by_age`), not by the spelling, which is local time.
        """
        library = await self.library_mark()
        try:
            listed = await asyncio.to_thread(_listing, folder)
        except FileNotFoundError:
            return []
        return sorted(
            (entry for entry in listed if is_backup_filename(entry.name, library=library)),
            key=_by_age,
        )

    async def _stale(self, ours: Sequence[Path]) -> list[Path]:
        """Which of these go: all but the newest `backup.keep`, and any older than `backup.keep_days`.

        TWO RULES, AND A BACKUP GOES WHEN EITHER SAYS SO: "the newest 7, and none older than 7
        days". The age is the moment the name carries, against the clock now, so the backup this
        run writes (named for now) is never one of them.
        """
        keep = int(await self._read(KEEP_KEY))
        days = keep_days_from(await self._read(KEEP_DAYS_KEY))
        ordered = sorted(ours, key=_by_age)
        stale = ordered[: max(len(ordered) - keep, 0)]
        if days > 0:
            cutoff = self._clock() - days * DAY_SECONDS
            stale += [
                entry
                for entry in ordered[len(stale) :]
                if (moment_in_name(entry.name) or 0.0) < cutoff
            ]
        return stale

    async def _drop(self, stale: Sequence[Path], *, kept: int | None) -> None:
        """Delete the backups past the number kept, each written down as it goes.

        EACH DELETION IS ON HISTORY: "Sift deleted the backup from 12 September 2026", the
        backup's own task as the actor (`VIA_BACKUP`). A backup file is the way back to a day, and
        a way back that vanished with nothing saying so is the loss a person finds out about only
        when they reach for it. Recorded after the file is gone, so the record never names a
        deletion that did not happen.
        """
        for entry in stale:
            taken = await asyncio.to_thread(_taken_at, entry)
            await asyncio.to_thread(entry.unlink, True)
            async with self._db.write() as connection:
                # The History feed on every admin's open tab re-reads on the library bell.
                announce(EVERY_ADMIN, About.LIBRARY)
                await record_event(
                    connection,
                    actor=Actor.sift(VIA_BACKUP),
                    verb="deleted",
                    subject=LedgerSubject(
                        kind="backup", id=entry.name, name=the_backup_from(taken)
                    ),
                )
        if stale:
            log.info("backup.rotated", removed=len(stale), kept=kept)

    # --- the backups no library's rule takes ---------------------------------------------------

    async def unmarked(self) -> list[UnmarkedBackup]:
        """The backups in the backup folder no rule deletes, newest first: the ones whose names
        carry no library's mark, and the ones this library saved by hand.

        Listed so a person can see why the folder holds more than the number kept, and delete
        them by hand: no rule here ever does (`_UNMARKED`, `SAVED_MARK`). None for a folder that
        cannot be used.
        """
        try:
            folder = await self.resolve_folder(str(await self._read(FOLDER_KEY) or ""))
        except DestinationRefused:
            return []
        return await asyncio.to_thread(_unmarked_in, folder, await self.library_mark())

    async def recycles(self) -> bool:
        """Whether a backup deleted from the backup folder goes to a Recycle Bin."""
        try:
            folder = await self.resolve_folder(str(await self._read(FOLDER_KEY) or ""))
        except DestinationRefused:
            return False
        return await asyncio.to_thread(recycle.has_recycle_bin, folder)

    async def left_alone_file(self, name: str) -> Path:
        """The file of one backup the list shows, by its name, for a copy handed to a browser.

        BY ITS NAME, AND ONLY A NAME THE LIST SHOWS, as a Delete is (`delete_unmarked`): anything
        else is refused as not there, so this can never hand out some other file in the folder.
        """
        listed = {one.name for one in await self.unmarked()}
        if name not in listed:
            raise NotThere("That backup isn't in the backup folder any more.")
        folder = await self.resolve_folder(str(await self._read(FOLDER_KEY) or ""))
        return folder / name

    async def delete_unmarked(self, name: str, by: Viewer) -> bool:
        """Delete one unmarked backup somebody pressed Delete on. Whether it went to a Recycle Bin.

        BY ITS NAME, AND ONLY A NAME THE LIST SHOWS: anything else is refused as not there, so this
        can never be a way to delete some other file in the folder. To the Recycle Bin where the
        folder's drive has one; deleted outright where it has none (a network share), which the
        pane's confirm says before the press. Written on History as that person's act.
        """
        listed = {one.name: one for one in await self.unmarked()}
        chosen = listed.get(name)
        if chosen is None:
            raise NotThere("That backup isn't in the backup folder any more.")
        folder = await self.resolve_folder(str(await self._read(FOLDER_KEY) or ""))
        entry = folder / chosen.name
        binned = await asyncio.to_thread(recycle.has_recycle_bin, folder)
        if binned:
            try:
                await asyncio.to_thread(recycle.to_recycle_bin, entry)
            except recycle.NoRecycleBin as refused:
                raise NotThere(
                    "Windows didn't move that backup to the Recycle Bin. Nothing was deleted."
                ) from refused
        else:
            await asyncio.to_thread(entry.unlink, True)
        async with self._db.write() as connection:
            announce(EVERY_ADMIN, About.LIBRARY)
            await record_event(
                connection,
                actor=Actor.user(by.id),
                verb="deleted",
                subject=LedgerSubject(
                    kind="backup", id=chosen.name, name=the_backup_from(chosen.taken_at)
                ),
            )
        log.info("backup.unmarked_deleted", recycled=binned)
        return binned

    async def folder_refusal(self, chosen: Any) -> str | None:
        """Why `backup.folder` may not be set to this, or None. The settings door's check.

        The SAME check the schedule's own route makes (`resolve_folder`): given, outside every
        library, there, writable. Were the general settings route to save this setting with only
        its shape checked, a library root would answer 204 there and 409 here, and the next run
        would fail with `DestinationRefused`: the exact thing the check on save exists to prevent.
        """
        try:
            await self.resolve_folder(str(chosen or ""))
        except DestinationRefused as refused:
            return str(refused)
        return None

    async def starts_on_its_own(self) -> bool:
        """Whether the backup's When lets it start without a press. The When alone decides it."""
        return str(await self._read(when_key(TASK_ID))) != WHEN_PRESS

    async def set_schedule(
        self,
        actor: Viewer,
        *,
        keep: int,
        folder: str,
        keep_days: int | None = None,
        every_days: int | None = None,
        at: str | None = None,
    ) -> None:
        """Save the preferences, having proved the folder is one Sift can write to.

        The folder is proved first and the write happens second. The other order would store a
        destination that cannot be used and report the failure afterwards, leaving a schedule that
        looks saved on the screen and fails every night in the job log instead. It is not proved
        while only a press runs the backup: nothing is about to write there, and a press proves it
        when it runs.

        How many days to keep, how often and the time of day are written only when given (an
        older client sends no days). How often and the time of day are the task's own rows on Tasks, and the Backup pane that saves the folder and the count does not draw them, so a
        save there must not put back whatever it read on arrival.

        The values themselves are written through the settings service rather than validated here,
        so what a schedule may be is declared in one place and this cannot come to disagree with
        the screen that also edits them.
        """
        if await self.starts_on_its_own():
            await self.resolve_folder(folder)
        values: dict[str, Any] = {KEEP_KEY: keep, FOLDER_KEY: folder}
        if keep_days is not None:
            values[KEEP_DAYS_KEY] = keep_days
        if every_days is not None:
            values[EVERY_DAYS_KEY] = every_days
        if at is not None:
            values[AT_KEY] = at
        await self._write(actor, values)
        # The folder just left may have been the last use of its grant; the one it moved to is
        # granted on its first use. Nothing else keeps that list.
        await self._library.release_unused_grants()

    # --- files this feature holds for the length of one request ------------------------------

    async def staged(self) -> Path:
        """A path in Sift's own directory for a file that exists only while a request runs.

        Both the export a browser is about to download and the upload a restore is about to read
        land here. In Sift's own data directory rather than a system temporary one, because these
        are database-sized and the machine's temporary space is often a fraction of the disk, and
        because a directory Sift already proved writable at boot cannot surprise it here.
        """
        staging = self._settings.data_dir / "backup-staging"
        await asyncio.to_thread(staging.mkdir, parents=True, exist_ok=True)
        return staging / f"{new_id()}{FILENAME_SUFFIX}"

    async def _discard_tree(self, path: Path) -> None:
        await asyncio.to_thread(shutil.rmtree, path, True)

    async def discard(self, path: Path) -> None:
        """Drop a staged file once the request that made it is finished with it.

        Only ever called with a path this module produced, in a directory Sift owns and rebuilds.
        """
        await asyncio.to_thread(path.unlink, True)

    # --- putting one back --------------------------------------------------------------------

    async def inspect(self, source: Path) -> dict[str, Any]:
        """What a candidate file says about itself, having proved it is a backup this build can read.

        Both refusals happen here, before anything is moved: a file that is not a backup, and a
        backup from a newer Sift. Doing the checks against the candidate rather than after adopting
        it is the whole point: there is no half-restored state to get out of.

        An archive is inspected through its manifest and its database member; a bare database from
        before the archive is inspected as it always was, and says it carries nothing.
        """
        if not await asyncio.to_thread(is_backup_archive, source):
            described = await self._inspect_database(source)
            described["carried"] = []
            return described
        manifest = await asyncio.to_thread(_manifest_of, source)
        unpacked = source.with_name(source.name + ".database")
        try:
            await asyncio.to_thread(_extract_database, source, unpacked)
            described = await self._inspect_database(unpacked)
        finally:
            await asyncio.to_thread(unpacked.unlink, True)
        known = {one.name for one in self.carried()}
        described["carried"] = [str(name) for name in manifest["carried"] if str(name) in known]
        return described

    async def _inspect_database(self, source: Path) -> dict[str, Any]:
        try:
            async with aiosqlite.connect(f"file:{source}?mode=ro", uri=True) as connection:
                await connection.execute(_DISTRUST_SCHEMA)
                connection.row_factory = aiosqlite.Row
                cursor = await connection.execute(_READ_MANIFEST)
                rows = list(await cursor.fetchall())
                await cursor.close()
                versions = await self._schema_versions(connection)
        except sqlite3.DatabaseError as broken:
            raise NotABackup(
                "That file isn't a Sift backup. Choose the .sqlite3 file an export produced."
            ) from broken

        if len(rows) != 1:
            raise NotABackup("That file isn't a Sift backup.")

        self._refuse_if_newer(versions)
        too_old = too_old_to_bring_forward(versions)
        if too_old is not None:
            raise BackupTooOld(too_old)
        return {
            "format_version": int(rows[0]["format_version"]),
            "app_version": str(rows[0]["app_version"]),
            "created_at": int(rows[0]["created_at"]),
        }

    @staticmethod
    async def _schema_versions(connection: aiosqlite.Connection) -> dict[str, int]:
        cursor = await connection.execute("SELECT component, version FROM schema_version")
        try:
            return {str(row["component"]): int(row["version"]) for row in await cursor.fetchall()}
        finally:
            await cursor.close()

    @staticmethod
    def _refuse_if_newer(versions: dict[str, int]) -> None:
        """Refuse a backup written by a Sift newer than this one.

        Two shapes of the same problem: a feature whose tables have moved on, and a feature this
        build has never heard of. Both mean the file holds things this code would not carry
        forward, and the only safe answer is to say so rather than to migrate downwards.
        """
        components = registered_components()
        for name, on_file in sorted(versions.items()):
            known = components.get(name)
            if known is None or on_file > known.version:
                raise BackupTooNew(
                    "This backup was made by a newer version of Sift than the one running. "
                    "Update Sift, then restore it again. Nothing has been changed."
                )

    async def unpack_into(self, source: Path, data_dir: Path, cache_dir: Path) -> dict[str, Any]:
        """Make a NEW library's database and folders from a backup, leaving the live one alone.

        What "Import" on the Database Switcher does with an uploaded backup: the same refusals a
        restore makes, raised by the same `inspect` before anything is written, and the same
        extraction, only into another library's directories rather than over the running one. So
        nothing is stopped and nothing is swapped; the library it makes is opened by starting Sift
        on it, which migrates an older one by the ordinary boot path, exactly as a restore does.

        Refuses a data folder that already holds a database rather than writing over it. Whatever
        is there is somebody's library. The caller owns the folders and removes them on a refusal.
        """
        manifest = await self.inspect(source)
        database = data_dir / DATABASE_MEMBER
        if await asyncio.to_thread(database.exists):
            raise FileExistsError(f"there is already a library database at {database}")
        await asyncio.to_thread(data_dir.mkdir, parents=True, exist_ok=True)
        if await asyncio.to_thread(is_backup_archive, source):
            await asyncio.to_thread(_extract_database, source, database)
        else:
            await asyncio.to_thread(shutil.copyfile, source, database)
        for one in carried_in(data_dir, cache_dir):
            if one.name in manifest["carried"]:
                await asyncio.to_thread(_extract_folder, source, one.name, one.path)
        await asyncio.to_thread(drop_manifest, database)
        log.info("backup.unpacked", from_app_version=manifest["app_version"])
        return manifest

    async def restore(self, source: Path, by: Viewer | None = None) -> dict[str, Any]:
        """Adopt a backup as the live database, then bring its schema up to date.

        Everything that can refuse has refused before a file moves; the copy is assembled beside the
        database so the swap is one rename; the database being replaced is copied aside first and
        its stale write-ahead log removed (left beside the new file SQLite would replay it); then
        the new file is renamed into place. An older backup is brought forward by the ordinary boot
        path, so there is no restore migration to drift from it.

        The unwrapped master keys held in memory are deliberately left alone. They are keyed by
        user id: restoring this install's own backup keeps the key that user already has,
        and restoring another install's leaves keys nothing looks up, so the first job that needs
        a saved site login waits for a login instead of running without one.
        """
        async with self.exclusively(RESTORING):
            return await self._restore(source, by)

    async def _restore(self, source: Path, by: Viewer | None = None) -> dict[str, Any]:
        manifest = await self.inspect(source)
        archived = await asyncio.to_thread(is_backup_archive, source)

        live = self._db.path
        incoming = live.with_name(live.name + INCOMING_SUFFIX)
        superseded = live.with_name(live.name + SUPERSEDED_SUFFIX)
        if archived:
            await asyncio.to_thread(_extract_database, source, incoming)
        else:
            await asyncio.to_thread(shutil.copyfile, source, incoming)
        # The folders, assembled beside their live counterparts before anything is stopped, so
        # every refusal an archive can raise has been raised while the application still runs.
        folders: list[tuple[Path, Path]] = []
        for one in self.carried():
            if one.name not in manifest["carried"]:
                continue
            arriving = one.path.with_name(one.path.name + INCOMING_SUFFIX)
            await self._discard_tree(arriving)
            try:
                await asyncio.to_thread(_extract_folder, source, one.name, arriving)
            except BackupError:
                await asyncio.to_thread(incoming.unlink, True)
                for _live, made in folders:
                    await self._discard_tree(made)
                await self._discard_tree(arriving)
                raise
            folders.append((one.path, arriving))

        if self._workers is not None:
            await self._workers.stop()
        await self._db.close()

        # The database being replaced is kept as the undo for this restore: copied, not moved, so
        # `live` is never absent from disk even for an instant. The rename that follows is atomic:
        # `live` is the old database right up to the moment it is the new one.
        await asyncio.to_thread(shutil.copyfile, live, superseded)
        # A stale write-ahead log belongs to the database being replaced; left beside the new file
        # SQLite would replay it over the top and hand back a mix of the two. It goes first.
        for suffix in ("-wal", "-shm"):
            await asyncio.to_thread(live.with_name(live.name + suffix).unlink, True)
        await asyncio.to_thread(os.replace, incoming, live)

        for live_folder, arriving in folders:
            await asyncio.to_thread(_swap_folder, live_folder, arriving)

        await self._db.connect()
        await self._db.initialize_schema()
        await self._db.execute(_DROP_MANIFEST)
        await self._record_restored(manifest, by)
        if self._workers is not None:
            await self._workers.start()

        log.info(
            "backup.restored", from_app_version=manifest["app_version"], carried=manifest["carried"]
        )
        return manifest

    async def _record_restored(self, manifest: dict[str, Any], by: Viewer | None) -> None:
        """Write the restore into the library that came back, so its own History says when it went
        back and to which day ("You restored this library from the backup from 12 September 2026").

        Into the RESTORED database, once it is open and brought up to date: the one being replaced
        is set aside, and a line written there would be read by nobody. The person who pressed
        Restore is named where the restored library has a row for them (its own backup); a
        backup from another install has its own users, and the act is then said as the backup
        task's (`VIA_BACKUP`) rather than as somebody it has never heard of.
        """
        known = by is not None and await self._db.fetch_one(_A_USER_HERE, (by.id,)) is not None
        async with self._db.write() as connection:
            # Every open screen was drawing the library that was replaced: the library bell has
            # each of them read the one that came back, this line with it.
            announce(EVERY_ADMIN, About.LIBRARY)
            await record_restored(
                connection,
                manifest,
                Actor.user(by.id) if known and by is not None else Actor.sift(VIA_BACKUP),
            )


async def record_restored(connection: Connection, manifest: dict[str, Any], actor: Actor) -> None:
    """The line a library says when it came back from a backup ("... restored this library from
    the backup from 12 September 2026"), on the caller's connection.

    One shape for the two doors that bring a backup back: Restore over the running library, and
    Import on the Database Switcher, which makes a new library of it (`libraries.record_origin`).
    `manifest` is what `inspect` read: the day it was taken, the version that took it, and the
    folders it carried.
    """
    await record_event(
        connection,
        actor=actor,
        verb="restored",
        subject=LedgerSubject(
            kind="backup",
            id=str(manifest["created_at"]),
            name=the_backup_from(int(manifest["created_at"])),
        ),
        payload=json.dumps(
            {"app_version": manifest["app_version"], "carried": manifest["carried"]}
        ),
    )


#: Snapshots of the database.
SERVICE: Part[BackupService] = Part("backup")
