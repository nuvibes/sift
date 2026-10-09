# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taking a snapshot of the database, keeping the last few, and putting one back.

A backup is a zip of a manifest, the database and the two folders Sift cannot rebuild. The
snapshot is `VACUUM INTO`, never a file copy: in WAL mode a copied file misses the newest writes.
A restore keeps the old database aside and renames the new one over it, so one is always on disk.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import time
import zipfile
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
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
from sift.kernel.vocabulary import VIA_BACKUP
from sift.kernel.vocabulary import Subject as LedgerSubject
from sift.kernel.wiring import Part
from sift.slices.backup import recycle
from sift.slices.backup.naming import (
    FILENAME_SUFFIX,
    PARTIAL_SUFFIX,
    UnmarkedBackup,
    _by_age,
    _taken_at,
    _unmarked_in,
    _version_stamp,
    filename_for,
    is_backup_filename,
    mark_of_library,
    moment_in_name,
    the_backup_from,
)

log = get_logger(__name__)

#: The preferences kept in the settings hub; a few choices need no table of their own.
EVERY_DAYS_KEY = "backup.every_days"
AT_KEY = "backup.at"
KEEP_KEY = "backup.keep"
#: Days an automatic backup is kept, beside the count kept; zero is "never".
KEEP_DAYS_KEY = "backup.keep_days"
FOLDER_KEY = "backup.folder"
#: Off by default: a Build makes them again, and they swell a backup a hundredfold.
INCLUDE_DETECTED_KEY = "backup.include_detected_faces"

#: The retired "How often", carried into `EVERY_DAYS_KEY` by the settings step.
RETIRED_SCHEDULE_KEY = "backup.schedule"

#: The task's address on the Tasks screen, and the one its When is stored under.
TASK_ID = "backup"

#: A day, in seconds: what `EVERY_DAYS_KEY` is counted in.
DAY_SECONDS = 24 * 3600
#: A ceiling: a backup more than a year apart is a mistyped number.
MAX_EVERY_DAYS = 365
#: Three in the morning: the hour a machine left on is least likely to be in use.
DEFAULT_AT = "03:00"
#: For a new library only: an upgrade that started deleting files would surprise everyone.
DEFAULT_KEEP_DAYS = 7
#: The longest a backup may be kept for, in days, short of "never": ten years.
MAX_KEEP_DAYS = 3650

#: The backup format's own version (1 a bare database, 2 the archive), not the app's.
FORMAT_VERSION = 2

#: The members every archive holds; the database also carries its own stamp (the table below).
MANIFEST_MEMBER = "manifest.json"
DATABASE_MEMBER = "sift.sqlite3"

#: The first four bytes of a zip archive, which is how a candidate is told from a bare database.
_ZIP_MAGIC = b"PK\x03\x04"

#: What tells a restore this is a Sift backup, and which version wrote it; dropped once adopted.
MANIFEST_TABLE = "backup_manifest"

# Written out rather than built: built query text is how somebody later builds it from a variable.
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

#: Whether the user who pressed Restore is a user of the library that came back.
_A_USER_HERE = "SELECT 1 FROM users WHERE id = ?"


#: On every database from outside: its views and triggers may use only harmless functions.
_DISTRUST_SCHEMA = "PRAGMA trusted_schema=OFF"


#: The undo for the last restore, replaced by the next one.
SUPERSEDED_SUFFIX = ".superseded"

#: Beside the database, so the swap is a rename within one filesystem.
INCOMING_SUFFIX = ".incoming"


class BackupError(Exception):
    """Base for the refusals this feature makes, so a router can catch one thing."""


class NotABackup(BackupError):
    """The file offered for restore is not a backup Sift wrote."""


class BackupTooNew(BackupError):
    """The backup came from a newer Sift; migrating backwards would destroy the good copy."""


class BackupTooOld(BackupError):
    """The backup came from a Sift too old for this one to bring forward; refused first."""


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

#: One sentence each, naming the work running and what to do.
_BUSY = {
    BACKING_UP: "A backup is being saved right now. Try again when it has finished.",
    RESTORING: "A backup is being restored right now. Try again when it has finished.",
    DUPLICATING: "This library is being duplicated right now. Try again when it has finished.",
    SWITCHING: "Sift is switching libraries right now.",
    DELETING: "A library is being deleted right now. Try again when it has finished.",
}


@dataclass(frozen=True)
class Carried:
    """One folder a backup carries; `name` is what a restore matches on, so it never changes."""

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
    """The worker pool, as much as a restore needs to stop and start it."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


def _size_of(path: Path) -> int:
    """The size of a file, off the event loop."""
    return path.stat().st_size


def _files_under(folder: Path) -> list[Path]:
    """Every file under a folder, sorted, off the event loop; empty for a folder not there."""
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.rglob("*") if path.is_file())


def _measure(folder: Path) -> tuple[int, int]:
    """How many files a folder holds and how many bytes, skipping one taken away meanwhile."""
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
    """Write the archive under a name no listing matches, renamed once whole; never over one."""
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
                        # Taken away since the listing (see `_measure`).
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


#: A drive filled to the last byte fails the next write anything makes.
ROOM_TO_SPARE = 512 * 1024 * 1024

#: On Windows a backslash or a colon can carry a member out of its folder.
_REFUSED_IN_NAMES = frozenset("\\:\x00")


def free_bytes(place: Path) -> int:
    """The free space on the drive `place` is, or will be, on. Blocking."""
    probe = place
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def _refuse_without_room(members: Sequence[zipfile.ZipInfo], into: Path) -> None:
    """Refuse members that would not fit on `into`'s drive; a declared size is a ceiling."""
    need = sum(member.file_size for member in members)
    if free_bytes(into) < need + ROOM_TO_SPARE:
        gigabytes = (need + ROOM_TO_SPARE) / 1024**3
        raise NoRoomToUnpack(
            f"There isn't enough free space to unpack that backup. It needs about "
            f"{gigabytes:.1f} GB. Free some space, then try again. Nothing has been changed."
        )


def _member_target(into: Path, relative: str) -> Path:
    """Where member `relative` is written, checked as text and then confined to `into`."""
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
    """Every member under `name/` written under `into`, all checked before anything is written."""
    prefix = name + "/"
    # An empty folder comes back empty, which differs from not coming back.
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
    """Put `incoming` where `live` is, renaming what was there aside as the undo."""
    superseded = live.with_name(live.name + SUPERSEDED_SUFFIX)
    if superseded.exists():
        shutil.rmtree(superseded, ignore_errors=True)
    if live.exists():
        os.replace(live, superseded)
    live.parent.mkdir(parents=True, exist_ok=True)
    os.replace(incoming, live)


def _listing(folder: Path) -> list[Path]:
    """What is in a folder, as a list, off the event loop."""
    return list(folder.iterdir())


def keep_days_from(stored: object) -> int:
    """The age rule in whole days, zero for "never" (a negative too), else the default."""
    try:
        days = int(str(stored))
    except (TypeError, ValueError):
        return DEFAULT_KEEP_DAYS
    return max(0, days)


def _resolved(chosen: str) -> Path:
    """A typed folder as an absolute path (a `~` expanded); a disk read, so off the loop."""
    return Path(chosen).expanduser().resolve()


def carried_in(data_dir: Path, cache_dir: Path) -> tuple[Carried, ...]:
    """The folders a backup carries, laid out in one library's two directories."""
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
    """Snapshots, rotation and restore; it holds the database, which a restore reopens."""

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
        # The granted folders and the libraries decide where a backup folder may be.
        self._library = library if library is not None else LibraryStore(database, settings)
        # One piece of whole-library work at a time; one event loop, so no interleaving.
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
        """Run one piece of whole-library work, refused with `Busy` while another runs."""
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
        """The folders a backup can carry; the detected faces only when asked."""
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
        """Write one archive to `destination`, which must not exist."""
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
        """A consistent copy of the live database at `target`, by `VACUUM INTO`."""
        async with self._db.write() as connection:
            await connection.execute("VACUUM INTO ?", (str(target),))

    async def _stamp(self, path: Path) -> None:
        """Record what wrote this file inside it, where it cannot be separated from it."""
        async with aiosqlite.connect(path) as connection:
            await connection.execute(_CREATE_MANIFEST)
            await connection.execute(
                _INSERT_MANIFEST, (FORMAT_VERSION, _version_stamp(), int(self._clock()))
            )
            await connection.commit()

    # --- the scheduled one -------------------------------------------------------------------

    async def destination(self) -> Path:
        """The backup folder, proven writable and made if missing; a chosen one must be granted."""
        return await self.resolve_folder(str(await self._read(FOLDER_KEY) or ""), ensuring=True)

    async def resolve_folder(self, chosen: str, *, ensuring: bool = False) -> Path:
        """The folder a setting names, proven usable and outside every library."""
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
        """The chosen backup folder, for its grant; none when backups stay beside the data."""
        chosen = str(await self._read(FOLDER_KEY) or "").strip()
        return [await asyncio.to_thread(_resolved, chosen)] if chosen else []

    async def plan(self, *, ensuring: bool = False, pressed: bool = False) -> BackupPlan:
        """The file a backup now would write and the older ones it would delete; raises if refused.

        A pressed run is saved by hand: no rule deletes it, and it deletes nothing.
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
        """Take a backup and then rotate, never before; a pressed run rotates nothing."""
        async with self.exclusively(BACKING_UP):
            planned = await self.plan(ensuring=True, pressed=pressed)
            await self.export_to(planned.file)
            await self._drop(planned.drop, kept=None)
        return planned.file

    async def save_now(self, by: Viewer) -> Path:
        """Save a backup by hand into the backup folder, and say so on History once it is whole."""
        saved = await self.run_scheduled(pressed=True)
        await self.record_saved(saved, by.id)
        return saved

    async def record_saved(self, saved: Path, user_id: str) -> None:
        """Say on History that this user saved this backup, and where."""
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
        """This library's automatic backups in `folder`, oldest first; only names it made."""
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
        """Which go: all but the newest `backup.keep`, and any older than `backup.keep_days`."""
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
        """Delete the backups past the number kept, each on History once it is gone."""
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
        """The backups in the backup folder no rule deletes, newest first."""
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
        """The file of one backup the list shows, by its name only."""
        listed = {one.name for one in await self.unmarked()}
        if name not in listed:
            raise NotThere("That backup isn't in the backup folder any more.")
        folder = await self.resolve_folder(str(await self._read(FOLDER_KEY) or ""))
        return folder / name

    async def delete_unmarked(self, name: str, by: Viewer) -> bool:
        """Delete one listed backup by name, to a Recycle Bin where there is one; True if so."""
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
        """Why `backup.folder` may not be this, or None: the same check as `resolve_folder`."""
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
        """Save the preferences, the folder proved first; values only when given."""
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
        # The folder just left may have been its grant's last use.
        await self._library.release_unused_grants()

    # --- files this feature holds for the length of one request ------------------------------

    async def staged(self) -> Path:
        """A path in Sift's own directory for a file that lives only while a request runs."""
        staging = self._settings.data_dir / "backup-staging"
        await asyncio.to_thread(staging.mkdir, parents=True, exist_ok=True)
        return staging / f"{new_id()}{FILENAME_SUFFIX}"

    async def _discard_tree(self, path: Path) -> None:
        await asyncio.to_thread(shutil.rmtree, path, True)

    async def discard(self, path: Path) -> None:
        """Drop a staged file this module made, once its request is done."""
        await asyncio.to_thread(path.unlink, True)

    # --- putting one back --------------------------------------------------------------------

    async def inspect(self, source: Path) -> dict[str, Any]:
        """What a candidate file says about itself; refused before anything moves."""
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
        """Refuse a backup written by a newer Sift: tables moved on, or a feature unknown here."""
        components = registered_components()
        for name, on_file in sorted(versions.items()):
            known = components.get(name)
            if known is None or on_file > known.version:
                raise BackupTooNew(
                    "This backup was made by a newer version of Sift than the one running. "
                    "Update Sift, then restore it again. Nothing has been changed."
                )

    async def unpack_into(self, source: Path, data_dir: Path, cache_dir: Path) -> dict[str, Any]:
        """Make a new library's database and folders from a backup, leaving the live one alone."""
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
        """Adopt a backup as the live database, then bring its schema up to date."""
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
        # Assembled before anything stops, so every refusal comes while the application runs.
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

        # Copied, not moved, so the database is never absent; the rename after is atomic.
        await asyncio.to_thread(shutil.copyfile, live, superseded)
        # A stale write-ahead log would be replayed over the new file.
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
        """Say on the restored library's History that it came back, and from which day."""
        known = by is not None and await self._db.fetch_one(_A_USER_HERE, (by.id,)) is not None
        async with self._db.write() as connection:
            # Every open screen redraws the library that came back.
            announce(EVERY_ADMIN, About.LIBRARY)
            await record_restored(
                connection,
                manifest,
                Actor.user(by.id) if known and by is not None else Actor.sift(VIA_BACKUP),
            )


async def record_restored(connection: Connection, manifest: dict[str, Any], actor: Actor) -> None:
    """The line a library says when it came back from a backup, on the caller's connection."""
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
