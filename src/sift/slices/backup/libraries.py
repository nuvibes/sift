# SPDX-License-Identifier: AGPL-3.0-or-later
"""Making libraries, and switching the running server from one to another.

## Where the libraries are

A LIBRARIES FOLDER that the server owns: `libraries` beside the first library's data folder. Every
library made here is `<folder>/<name>/data` and `<folder>/<name>/cache` (the same two folders
first run makes), so each one is a whole library that the desktop app, a backup or a person with a
file manager can find and recognise.

The folder is found again from INSIDE it, and that is the one subtle thing here. Running a library
made in the folder, the data folder is `<folder>/<name>/data`, and "beside the data folder" would
be a DIFFERENT place: a folder that moved every time the library did, so each library would see a
list of its own. So the folder is marked, by the small file below that also lists the libraries
kept elsewhere, and a library whose grandparent holds that mark is one of its members.

## Why a switch is a restart, and not a reopen inside this process

A library is not only its database. It is the database AND two directories: the data folder holds
the faces somebody confirmed and the log, and the cache folder holds the covers somebody uploaded
and every thumbnail. Those two paths are read from the process's settings by some seventy places,
many of them once, at start-up. Swapping only the database underneath a running server would show
one library's records with another library's confirmed faces, covers and thumbnails, and every
in-memory cache, watcher and index built at start-up would go on describing the library that was
closed. A restore can reopen in place because it replaces a library WITH ITSELF: same folders, a
different moment.

So this does what the desktop app's own switch does: stop, point at the other library, start. The
difference is who asks. The server leaves a note naming the library in its own data folder and asks
whatever supervises it to start it again (`sift.kernel.lifecycle`); the desktop app reads the note
when its backend stops for that reason and starts the new one on the folder named. That is what
makes the switch work from a browser, on any machine, and it runs every step a start runs, because
it IS a start.

Where nothing supervises the process (run by hand from a terminal), there is nothing to start it
again, and a switch is refused before anything is made or written, rather than taking the library
off the air to find out.

## What the page may name

Never a path. It names a library by the id this module handed it in the list, and the id is looked
up again in a list read fresh from the disk. A new library is named by a NAME, which is checked to be
one folder name and nothing else; the folder it goes in is the server's.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sift.kernel import lifecycle
from sift.kernel.access import Viewer
from sift.kernel.config import LIBRARIES_MARK, Settings, libraries_folder
from sift.kernel.db import (
    DATABASE_FILENAME,
    VERDICT_CURRENT,
    VERDICT_EMPTY,
    VERDICT_NEWER,
    VERDICT_OLDER,
    VERDICT_UNREADABLE,
    Connection,
    Database,
    DatabaseError,
    adopt_database,
    copy_database_aside,
    execute_blocking,
    fetch_blocking,
    inspect_database,
)
from sift.kernel.jobs import (
    JobCanceled,
    JobContext,
    JobFailedPermanently,
    JobQueue,
    JobState,
    register_handler,
)
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.paths import is_writable
from sift.kernel.vocabulary import VIA_BACKUP
from sift.kernel.vocabulary import Subject as LedgerSubject
from sift.kernel.whole_file import write_json_whole
from sift.kernel.wiring import Part
from sift.slices.backup.recycle import NoRecycleBin, to_recycle_bin
from sift.slices.backup.service import (
    DELETING,
    DUPLICATING,
    ROOM_TO_SPARE,
    SWITCHING,
    BackupService,
    Busy,
    carried_in,
    drop_manifest,
    is_backup_archive,
    record_restored,
)
from sift.slices.backup.service import free_bytes as _free_bytes

log = get_logger(__name__)

#: The mark on the libraries folder, and the list of libraries kept outside it. Written by this
#: module only, from the server's own settings, never from anything a request carried. Declared
#: in the kernel's config beside `libraries_folder`, because the device's model store is found by
#: the same rule and the kernel may not import a slice.
#: What fills a new library before it opens: handed the database file and the data folder.
Seed = Callable[[Path, Path], Awaitable[None]]


REGISTRY_FILENAME = LIBRARIES_MARK

#: The note a server leaves in its OWN data folder before it asks to be started again, naming the
#: library to start on. `SWITCH_NOTE` in the desktop app's libraries.ts, pinned by a test there.
HANDOFF_FILENAME = "library-switch.json"

#: The two folders every library holds, the same pair first run makes.
DATA_FOLDER = "data"
CACHE_FOLDER = "cache"

#: The longest a library name may be. A name is a folder, and a folder name is not prose.
MAX_NAME = 64

#: One folder name that reads the same on every system Sift runs on: letters and digits of any
#: script, spaces, and a few joining marks. Nothing that names a path (`/`, `\\`, `:`), nothing a
#: file system reserves, and it cannot start with a dot, which would hide it.
_NAME = re.compile(r"^[^\W_][\w .()'&+-]*$")

#: Names Windows gives to devices. A folder called `CON` cannot be made there, and `nul.txt` is
#: still the device, so the stem is what is compared.
_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{n}" for n in range(1, 10)}
    | {f"lpt{n}" for n in range(1, 10)}
)

#: What a library from a newer Sift is told, at every door: the same sentence the desktop app says.
NEWER_REFUSAL = (
    "That library was last opened by a newer version of Sift than this one. Update Sift, "
    "then open it again. Nothing has been changed."
)


_NO_SUPERVISOR = (
    "Nothing is watching this copy of Sift, so it can't start again on another library. Open the "
    "library the way this copy was started."
)


class LibraryError(Exception):
    """Base for the refusals this feature makes. `status` is the HTTP answer the router gives."""

    status = 409


class NoSupervisor(LibraryError):
    """Nothing would start this process again, so it cannot switch."""


class UnknownLibrary(LibraryError):
    """The id names nothing on the list."""

    status = 404


class BadName(LibraryError):
    """The name is not one folder name."""

    status = 422


class NameTaken(LibraryError):
    """A library by that name is already in the folder."""


class NotALibrary(LibraryError):
    """The file or folder is not a Sift library this build can open."""

    status = 422


class NeedsUpgrade(LibraryError):
    """The library is behind this build, and opening it upgrades it. Asked, never assumed."""


class NoRoom(LibraryError):
    """The drive the libraries folder is on has no room for the copy."""


class NotDeletable(LibraryError):
    """This library cannot be deleted from here: it is open, kept elsewhere, or not named right."""


# --- a library that is a second copy of one -------------------------------------------------------
#
# Three doors make a library out of an existing one's database: importing a backup, duplicating
# the running library, and a database file the desktop app adopts. Each way the new library's
# database arrives holding the old one's sign-ins and its unfinished work. All three go through
# `make_its_own_library` before the new library is ever opened.

#: The job that makes a duplicate: minutes of copying, so it goes through the queue with progress.
LIBRARY_DUPLICATE = "library_duplicate"

_SIGN_OUT = "DELETE FROM sessions"
_HAS_TABLE = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"

#: What a second copy does with the work the original had waiting. Canceled, not deleted: the
#: copy's Activity then says what happened to it, and a row's children are kept with it. Every one
#: of them is about the SAME files the original is still looking after, so a copy that picked them
#: up would do each one twice, and a download or a rename done twice to one file is not harmless.
#:
#: The columns it names are in the jobs table's first step (`kernel/jobs/schema.py`), and every
#: database this build agrees to open has taken that step: an older one is refused by
#: `too_old_to_bring_forward` before any door makes a library from it.
_CANCEL_UNFINISHED = (
    "UPDATE jobs SET state = 'canceled', note = ?, updated_at = ? "
    "WHERE state IN ('queued', 'running', 'blocked', 'paused')"
)
DUPLICATED_NOTE = "Canceled in the copy when this library was duplicated."
COPIED_NOTE = "Canceled when this library was made from a copy of another library."


def sign_everyone_out(database: Path) -> None:
    """Remove every sign-in from a database that is becoming a library of its own. Blocking.

    Guarded on the table, because a library with no sign-ins table has none to remove.
    """
    if fetch_blocking(database, _HAS_TABLE, ("sessions",)):
        execute_blocking(database, _SIGN_OUT)


def cancel_unfinished(database: Path, *, now: int, note: str) -> None:
    """Cancel every job a copied database still had waiting or running. Blocking."""
    if fetch_blocking(database, _HAS_TABLE, ("jobs",)):
        execute_blocking(database, _CANCEL_UNFINISHED, (note, now))


def make_its_own_library(database: Path, *, now: int, note: str) -> None:
    """Take out of a copied database what still belongs to the library it was copied from. Blocking.

    THE ONE PLACE every door that makes a library from another library's database goes through: a
    duplicate, an import on this page, and a database file the desktop app adopts
    (`--adopt-library` in `sift.main`). Its sign-ins go, so opening it signs everybody out as the
    screen promises, and its unfinished work is canceled, so the copy does not do again what the
    original is still doing. Called before the new library is ever opened.
    """
    sign_everyone_out(database)
    cancel_unfinished(database, now=now, note=note)


# --- where an imported library came from ----------------------------------------------------------
#
# An imported library's first History line says where it came from: "Sift restored this library
# from the backup from 12 September 2026", or "Sift created this library from the database file
# old.sqlite3". THE NEW LIBRARY WRITES IT, once it is open, rather than this door writing it into
# the new database here: that database may be a version behind, brought forward only by the start
# that opens it, and a line written into an older ledger through today's statement is written
# against a shape it may not have. So the import leaves a note in the new library's own data
# folder, and the start that opens it writes the line through the ordinary door, after the schema
# is brought forward and before any work runs, then removes the note (`record_origin`).

#: The note, in the NEW library's data folder: nothing else reads that folder before it opens.
ORIGIN_FILENAME = "library-origin.json"

#: What the note says the library was made from: a backup archive, or a Sift database file.
FROM_BACKUP = "backup"
FROM_DATABASE_FILE = "database_file"

#: The longest file name a line keeps. A name is a label; anything longer is not one somebody typed.
_MOST_NAME = 255

#: Characters a file name on a line never carries: the controls, which draw as nothing or as noise.
_CONTROLS = re.compile(r"[\x00-\x1f\x7f]")


def file_name_said(chosen: str | None) -> str | None:
    """The name of the file somebody chose, as a line may say it, or None where there is none.

    What a browser sends is its word, not a path Sift may use: only the last part of it is kept
    (an older browser sends the whole path, and some send one under `C:\\fakepath`), without
    control characters, and no longer than `_MOST_NAME`.
    """
    if not chosen:
        return None
    last = re.split(r"[\\/]", chosen)[-1]
    cleaned = _CONTROLS.sub("", last).strip()[:_MOST_NAME]
    return cleaned or None


def _origin_from_backup(manifest: dict[str, Any]) -> dict[str, Any]:
    """The note for a library unpacked from a backup: what `record_restored` says it with."""
    return {
        "from": FROM_BACKUP,
        "created_at": int(manifest["created_at"]),
        "app_version": str(manifest["app_version"]),
        "carried": [str(one) for one in manifest["carried"]],
    }


def _origin_from_file(name: str | None) -> dict[str, Any]:
    """The note for a library made from a Sift database file, by the name it was chosen under."""
    return {"from": FROM_DATABASE_FILE, "name": name}


def leave_origin_note(data_dir: Path, name: str | None) -> None:
    """Leave the note a library made from a database file reads at its first start (`record_origin`).

    The desktop application's own door (`--adopt-library` in `sift.main`) makes a library the same
    way the Import button does, so it leaves the same note and the new library says where it came
    from. Blocking; called right after `make_its_own_library`.
    """
    _write_json(data_dir / ORIGIN_FILENAME, _origin_from_file(name))


def _read_origin(note: Path) -> dict[str, Any] | None:
    """The note as written, or None where it cannot be read as one. Blocking."""
    try:
        written = json.loads(note.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(written, dict):
        return None
    if written.get("from") == FROM_BACKUP:
        carried = written.get("carried")
        if (
            isinstance(written.get("created_at"), int)
            and isinstance(written.get("app_version"), str)
            and isinstance(carried, list)
            and all(isinstance(one, str) for one in carried)
        ):
            return written
        return None
    if written.get("from") == FROM_DATABASE_FILE:
        name = written.get("name")
        return written if name is None or isinstance(name, str) else None
    return None


async def _record_origin(connection: Connection, origin: dict[str, Any]) -> None:
    """The line, on the caller's connection. Sift's act under Backup and restore (`VIA_BACKUP`):
    whoever pressed Import may have no row in the library it made, and the act is the task's."""
    actor = Actor.sift(VIA_BACKUP)
    if origin["from"] == FROM_BACKUP:
        await record_restored(connection, origin, actor)
        return
    name = origin.get("name")
    await record_event(
        connection,
        actor=actor,
        verb="adopted",
        subject=LedgerSubject(
            kind="database_file",
            id=name or "",
            name=f"the database file {name}" if name else "a database file",
        ),
    )


#: Folders in a cache that hold work in flight rather than pictures: segments made for one
#: playback, files part way through arriving, a job's scratch space, a download's temporary
#: folder. Left out of a duplicate's pictures; a name missed here costs some copying, never a
#: wrong library, because everything in a cache is Sift's to make again.
_IN_FLIGHT = frozenset({"transcode", "incoming", "jobs"})
_IN_FLIGHT_PREFIX = "sift-download-"

#: How many files are copied per trip off the event loop. Enough that a library of a hundred
#: thousand thumbnails is not a hundred thousand thread hand-offs; few enough that progress and a
#: Cancel are heard every second or so.
_COPY_BATCH = 200


@dataclass(frozen=True)
class CopiedFolder:
    """One folder a duplicate copies, from here to there."""

    source: Path
    target: Path


def _walk(folder: Path) -> Iterator[tuple[Path, int]]:
    """Every file under a folder with its size, never following a link or a junction. Blocking.

    `scandir`, because on Windows the size comes with the directory listing and asking for it costs
    nothing more: a cache is hundreds of thousands of files. A link is not followed: a folder
    somebody pointed somewhere else is not Sift's to copy.
    """
    if not folder.is_dir():
        return
    stack = [folder]
    while stack:
        here = stack.pop()
        try:
            entries = list(os.scandir(here))
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
                continue
            if entry.is_dir(follow_symlinks=False):
                stack.append(Path(entry.path))
            # Neither a folder nor a file is a pipe or a socket: POSIX only, and never copied,
            # since copying a pipe waits for a writer that is not coming. No Windows test makes one.
            elif entry.is_file(follow_symlinks=False):  # pragma: no branch
                yield Path(entry.path), entry.stat(follow_symlinks=False).st_size


def _size(parts: list[CopiedFolder]) -> int:
    return sum(size for part in parts for _, size in _walk(part.source))


def _database_bytes(database: Path) -> int:
    """How big a snapshot of a live database can be: the file and its write-ahead log."""
    total = 0
    for one in (database, database.with_name(database.name + "-wal")):
        try:
            total += one.stat().st_size
        except OSError:
            continue
    return total


def _copy_files(files: list[tuple[Path, Path]]) -> int:
    """Copy each (from, to), keeping modification times. Blocking. Answers the bytes copied."""
    copied = 0
    for source, target in files:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += target.stat().st_size
    return copied


@dataclass(frozen=True)
class Library:
    """One library the server knows of. `id` is what the page names it by."""

    id: str
    name: str
    data_dir: Path
    cache_dir: Path
    in_folder: bool


def library_id(data_dir: Path) -> str:
    """An opaque name for a library, stable for as long as it stays where it is.

    A digest of the path rather than the path, so what the page sends back cannot be read as, or
    edited into, a place on the disk: it only ever matches an entry in a list read fresh.
    Case-folded, because Windows is case-insensitive and one folder must not be two entries.
    """
    spelled = os.path.normcase(os.path.abspath(data_dir))
    return hashlib.sha256(spelled.encode("utf-8")).hexdigest()[:20]


def check_name(name: str) -> str:
    """The name as a folder name, or BadName. Trimmed; nothing else is changed."""
    trimmed = name.strip()
    if not trimmed or len(trimmed) > MAX_NAME:
        raise BadName(f"Give the library a name of 1 to {MAX_NAME} characters.")
    if not _NAME.match(trimmed) or trimmed.endswith("."):
        raise BadName(
            "Use letters, numbers and spaces for the name, starting with a letter or a number."
        )
    if trimmed.split(".")[0].strip().lower() in _RESERVED:
        raise BadName("That name is reserved by Windows. Choose another.")
    return trimmed


def _name_for(data_dir: Path) -> str:
    """What to call a library kept elsewhere: the folder holding its data folder, as the app does."""
    holding = data_dir.parent
    return holding.name or str(holding)


def _same(a: Path, b: Path) -> bool:
    return library_id(a) == library_id(b)


def _read_registry(folder: Path) -> list[dict[str, str]]:
    """The libraries kept elsewhere, as written. Blocking. A damaged file lists nothing."""
    try:
        written = json.loads((folder / REGISTRY_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    elsewhere = written.get("elsewhere") if isinstance(written, dict) else None
    if not isinstance(elsewhere, list):
        return []
    return [
        {"data_dir": str(one["data_dir"]), "cache_dir": str(one["cache_dir"])}
        for one in elsewhere
        if isinstance(one, dict)
        and isinstance(one.get("data_dir"), str)
        and isinstance(one.get("cache_dir"), str)
    ]


def _write_json(target: Path, value: Any) -> None:
    """Write a small JSON file whole or not at all. Blocking. See `kernel.whole_file`."""
    write_json_whole(target, value)


def _read_opening(folder: Path) -> dict[str, str] | None:
    """The library chosen to open when Sift starts, as written, or None. Blocking.

    Kept in the folder's mark beside the libraries kept elsewhere, and separately from which
    library opened LAST (the desktop app's own list): one is a choice somebody made, the other a
    record of what happened, and a switch must not quietly change the first.
    """
    try:
        written = json.loads((folder / REGISTRY_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    opening = written.get(OPENS_AT_START) if isinstance(written, dict) else None
    if (
        isinstance(opening, dict)
        and isinstance(opening.get("data_dir"), str)
        and isinstance(opening.get("cache_dir"), str)
    ):
        return {"data_dir": opening["data_dir"], "cache_dir": opening["cache_dir"]}
    return None


#: The key in the mark naming the library that opens when Sift starts. `OPENS_AT_START` in the
#: desktop app's libraries.ts, which reads it before it starts a backend; pinned by a test there.
OPENS_AT_START = "opens_at_start"

#: Leave the library that opens at start as the mark has it.
_KEEP = object()


def _mark(folder: Path, elsewhere: list[dict[str, str]], opening: object = _KEEP) -> None:
    """Make the folder if it is missing and write its mark with this list. Blocking.

    The library that opens at start is kept as written unless `opening` says otherwise: a dict of
    the two folders, or None for "whichever opened last".
    """
    folder.mkdir(parents=True, exist_ok=True)
    chosen = _read_opening(folder) if opening is _KEEP else opening
    written: dict[str, Any] = {"format": 1, "elsewhere": elsewhere}
    if chosen is not None:
        written[OPENS_AT_START] = chosen
    _write_json(folder / REGISTRY_FILENAME, written)


def _members(folder: Path) -> list[Path]:
    """The library folders in the libraries folder: every one holding a data folder. Blocking."""
    if not folder.is_dir():
        return []
    return sorted(
        (entry for entry in folder.iterdir() if entry.is_dir() and (entry / DATA_FOLDER).is_dir()),
        key=lambda entry: entry.name.lower(),
    )


#: An admin making a library, as the new library's one user. See `LibrariesService.create`.
_READ_MAKER = (
    "SELECT id, username, password_hash, pin_hash, mk_wrapped, mk_nonce, mk_kdf_salt, created_at "
    "FROM users WHERE id = ? AND role = 'admin'"
)
_WRITE_MAKER = (
    "INSERT INTO users (id, username, password_hash, pin_hash, role, mk_wrapped, mk_nonce, "
    "mk_kdf_salt, created_at) VALUES (?, ?, ?, ?, 'admin', ?, ?, ?, ?)"
)


def _write_maker(database: Path, row: tuple[Any, ...]) -> None:
    """Put the maker's row into a new library's database. Blocking."""
    execute_blocking(database, _WRITE_MAKER, row)


class LibrariesService:
    """The list, making one, and asking to be started on another. One per application."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        backup: BackupService,
        *,
        can_restart: Callable[[], bool] = lifecycle.can_restart,
        ask_to_restart: Callable[[], bool] = lifecycle.ask_to_restart,
    ) -> None:
        self._db = database
        self._settings = settings
        self._backup = backup
        self._can_restart = can_restart
        self._ask_to_restart = ask_to_restart

    @property
    def folder(self) -> Path:
        return libraries_folder(self._settings.data_dir)

    def can_switch(self) -> bool:
        """Whether anything would start this process again. Asked before anything is made."""
        return self._can_restart()

    # --- the list ------------------------------------------------------------------------------

    def _current(self) -> Library:
        data_dir = self._settings.data_dir
        return Library(
            library_id(data_dir),
            data_dir.parent.name if self._in_folder(data_dir) else _name_for(data_dir),
            data_dir,
            self._settings.cache_dir,
            self._in_folder(data_dir),
        )

    def _in_folder(self, data_dir: Path) -> bool:
        return _same(data_dir.parent.parent, self.folder) and data_dir.name == DATA_FOLDER

    def _known(self) -> list[Library]:
        """Every library, the running one first. Blocking: it reads the folder."""
        current = self._current()
        found = [current]
        for member in _members(self.folder):
            data_dir = member / DATA_FOLDER
            if not _same(data_dir, current.data_dir):
                found.append(
                    Library(
                        library_id(data_dir), member.name, data_dir, member / CACHE_FOLDER, True
                    )
                )
        for one in _read_registry(self.folder):
            data_dir = Path(one["data_dir"])
            if not any(_same(data_dir, seen.data_dir) for seen in found):
                found.append(
                    Library(
                        library_id(data_dir),
                        _name_for(data_dir),
                        data_dir,
                        Path(one["cache_dir"]),
                        False,
                    )
                )
        return found

    async def listed(self) -> list[dict[str, Any]]:
        """The list for the screen, each with what its database says about itself."""
        known = await asyncio.to_thread(self._known)
        opening = await asyncio.to_thread(_read_opening, self.folder)
        opens = library_id(Path(opening["data_dir"])) if opening is not None else None
        answer: list[dict[str, Any]] = []
        for index, one in enumerate(known):
            report = await asyncio.to_thread(inspect_database, one.data_dir / DATABASE_FILENAME)
            answer.append(
                {
                    "id": one.id,
                    "name": one.name,
                    "data_dir": str(one.data_dir),
                    "in_folder": one.in_folder,
                    "current": index == 0,
                    "verdict": VERDICT_CURRENT if index == 0 else report.verdict,
                    # Why it can't be read, where its database can say: the mark alone reads the
                    # same for a stranger's file and a library that only needs an older Sift once.
                    "detail": (
                        report.detail if index != 0 and report.verdict == VERDICT_UNREADABLE else ""
                    ),
                    "opens_at_start": one.id == opens,
                }
            )
        return answer

    # --- switching ------------------------------------------------------------------------------

    async def name_of(self, library: str) -> str | None:
        """The name a library on the list goes by, or None where the list has no such id."""
        known = await asyncio.to_thread(self._known)
        return next((one.name for one in known if one.id == library), None)

    async def open(self, library: str, *, upgrade: bool = False) -> bool:
        """Ask to be started on a library from the list. False when it is the one running.

        Refused while a backup, a restore or a duplicate is running: a restart in the middle of
        one leaves a half-written copy behind it. See `BackupService.exclusively`.
        """
        async with self._backup.exclusively(SWITCHING):
            return await self._open(library, upgrade=upgrade)

    async def _open(self, library: str, *, upgrade: bool) -> bool:
        """`open`, once nothing else is running on the library as a whole.

        Every refusal is made while this library is still up: an unknown id, nothing to restart
        this process, a library that has gone or is from a newer Sift, one that is behind and was
        not agreed to be upgraded, and a backup copy of that one that could not be made.
        """
        known = await asyncio.to_thread(self._known)
        chosen = next((one for one in known if one.id == library), None)
        if chosen is None:
            raise UnknownLibrary("Sift does not know that library. Read the list again.")
        if chosen is known[0]:
            return False
        self._require_supervisor()

        database = chosen.data_dir / DATABASE_FILENAME
        report = await asyncio.to_thread(inspect_database, database)
        if report.verdict == VERDICT_EMPTY:
            raise NotALibrary(
                "That library isn't there any more. If it's on a drive or a share, check it's "
                "connected."
            )
        if report.verdict == VERDICT_NEWER:
            raise NotALibrary(NEWER_REFUSAL)
        if report.verdict not in (VERDICT_CURRENT, VERDICT_OLDER):
            raise NotALibrary(
                report.detail or "There's no Sift library there that this copy can read."
            )
        if not await asyncio.to_thread(is_writable, chosen.data_dir):
            raise NotALibrary("Sift can't write to that library's folder, so it can't open it.")
        if report.verdict == VERDICT_OLDER:
            if not upgrade:
                raise NeedsUpgrade(
                    "That library was last opened by an older Sift. Opening it upgrades it in one "
                    "direction; Sift makes a backup copy first."
                )
            # The promise on the screen is a way back, so a copy that could not be made is a
            # refusal and not a warning: a one-way upgrade with nothing behind it is the one thing
            # this must never do quietly.
            try:
                copy = await asyncio.to_thread(copy_database_aside, database)
            except (OSError, DatabaseError) as failed:
                raise NotALibrary(
                    "Sift couldn't make a backup copy of that library, so it hasn't been opened."
                ) from failed
            log.info("libraries.backed_up", copy=str(copy))

        await self._switch(chosen)
        return True

    async def create(self, name: str, actor: Viewer, *, seed: Seed | None = None) -> Library:
        """Make an empty library and switch to it, refused while other library work runs."""
        async with self._backup.exclusively(SWITCHING):
            return await self._create(name, actor, seed=seed)

    async def _create(self, name: str, actor: Viewer, *, seed: Seed | None = None) -> Library:
        """Make an empty library in the folder, with its maker as its admin, and switch to it.

        WHY THE MAKER COMES ALONG rather than the library being left for the first-run question. A
        library with no user is claimed by whoever reaches its setup screen first (that is what
        first run IS) and a library made from a browser is, by construction, being made on a
        server other machines can reach. The person who made it would be racing the network for
        their own library. So the maker's own row is carried in: the same name, the same password,
        and the same wrapped key, which the same password unwraps. Nothing else is (no sessions,
        no saved logins, no other users), so the new library starts signed out, and every other
        first-run step (folders, what to scan) is still ahead of it.
        """
        self._require_supervisor()
        found = await self._db.fetch_one(_READ_MAKER, (actor.id,))
        if found is None:
            raise NotALibrary("Only an admin of this library can make another.")
        maker = tuple(found)
        root = await self._claim(name)
        data_dir, cache_dir = root / DATA_FOLDER, root / CACHE_FOLDER
        try:
            await asyncio.to_thread(data_dir.mkdir, parents=True)
            await asyncio.to_thread(cache_dir.mkdir, parents=True)
            fresh = Database(data_dir / DATABASE_FILENAME, readers=1)
            await fresh.connect()
            try:
                await fresh.initialize_schema()
            finally:
                await fresh.close()
            await asyncio.to_thread(_write_maker, data_dir / DATABASE_FILENAME, maker)
            # A caller's seed fills the new library before it opens (a Stash import's folders, its
            # plan and its queued run); a seed that fails takes the half-made library with it.
            if seed is not None:
                await seed(data_dir / DATABASE_FILENAME, data_dir)
        except BaseException:
            await asyncio.to_thread(shutil.rmtree, root, True)
            raise
        made = Library(library_id(data_dir), root.name, data_dir, cache_dir, True)
        log.info("libraries.created")
        await self._switch(made)
        return made

    async def import_file(self, source: Path, name: str, *, chosen: str | None = None) -> Library:
        """Make a library from an uploaded file and switch to it, refused while other work runs.

        `chosen` is the name the file had where somebody chose it (the upload's own name, not the
        staged copy's), which the new library's first line says for a database file.
        """
        async with self._backup.exclusively(SWITCHING):
            return await self._import_file(source, name, chosen)

    async def _import_file(self, source: Path, name: str, chosen: str | None = None) -> Library:
        """Make a library from an uploaded file (a backup, or a Sift database) and switch to it.

        A backup archive goes through the backup feature's own unpacking, which is what Restore
        reads it with, and brings its confirmed faces and covers along. A bare database is judged
        by the reading every other door uses and copied in by `VACUUM INTO`. Either way the upload
        is a copy somebody still holds, so the new library is not given a second one before an
        older schema is brought forward: the file they chose IS the way back.

        THE IMPORTED LIBRARY STARTS SIGNED OUT, WITH NOTHING LEFT WAITING. A backup carries the
        sign-ins that were live when it was taken, and so does a database file; left in, a browser
        still holding one of those would be signed straight in to the new library. It carries the
        work that was queued then too, about files the original still looks after. Both go
        (`make_its_own_library`).

        ITS FIRST LINE SAYS WHERE IT CAME FROM, written by the new library once it opens (see
        `record_origin`): a backup by its day, a database file by the name it was chosen under.
        """
        self._require_supervisor()
        # Judged before anything is made: a refused file must leave no libraries folder, no mark
        # and no claimed name behind, and the reading writes nothing.
        archive = await asyncio.to_thread(is_backup_archive, source)
        if not archive:
            report = await asyncio.to_thread(inspect_database, source)
            if report.verdict == VERDICT_NEWER:
                raise NotALibrary(NEWER_REFUSAL)
            if report.verdict not in (VERDICT_CURRENT, VERDICT_OLDER):
                # The reading's own sentence where it has one: a library too old to bring
                # forward IS a Sift library, and saying otherwise sends somebody the wrong way.
                raise NotALibrary(
                    report.detail or "That file isn't a Sift library or a Sift backup."
                )
        root = await self._claim(name)
        data_dir, cache_dir = root / DATA_FOLDER, root / CACHE_FOLDER
        try:
            await asyncio.to_thread(cache_dir.mkdir, parents=True)
            if archive:
                manifest = await self._backup.unpack_into(source, data_dir, cache_dir)
                origin = _origin_from_backup(manifest)
            else:
                database = await asyncio.to_thread(adopt_database, source, data_dir)
                await asyncio.to_thread(drop_manifest, database)
                origin = _origin_from_file(file_name_said(chosen))
            await asyncio.to_thread(
                make_its_own_library,
                data_dir / DATABASE_FILENAME,
                now=int(time.time()),
                note=COPIED_NOTE,
            )
            await asyncio.to_thread(_write_json, data_dir / ORIGIN_FILENAME, origin)
        except BaseException:
            await asyncio.to_thread(shutil.rmtree, root, True)
            raise
        made = Library(library_id(data_dir), root.name, data_dir, cache_dir, True)
        log.info("libraries.imported")
        await self._switch(made)
        return made

    # --- duplicating this one -----------------------------------------------------------------

    def _copied_folders(self, root: Path, *, pictures: bool) -> list[CopiedFolder]:
        """The folders a duplicate made at `root` copies, beside its database. Blocking.

        ALWAYS what a backup always carries (the faces somebody confirmed and the covers somebody
        uploaded), read from the backup's own list (`carried_in`), so a duplicate can never carry
        less than a backup does. With `pictures`, also everything Sift made that a scan makes
        again: the other face pictures, and the cache less the work in flight (`_IN_FLIGHT`).
        Never the models, which are the device's (`Settings.models_dir`), and never this library's
        backups, sign-in log, quarantine or files part way through arriving.
        """
        data, cache = self._settings.data_dir, self._settings.cache_dir
        mine = carried_in(data, cache)
        theirs = carried_in(root / DATA_FOLDER, root / CACHE_FOLDER)
        folders = [
            CopiedFolder(one.path, into.path)
            for one, into in zip(mine, theirs, strict=True)
            if one.always
        ]
        if not pictures:
            return folders
        taken = {one.path for one in mine if one.always}
        for here, there in (
            (data / "faces", root / DATA_FOLDER / "faces"),
            (cache, root / CACHE_FOLDER),
        ):
            if not here.is_dir():
                continue
            for child in sorted(here.iterdir(), key=lambda one: one.name):
                if (
                    not child.is_dir()
                    or child in taken
                    or child.name == "models"
                    or child.name in _IN_FLIGHT
                    or child.name.startswith(_IN_FLIGHT_PREFIX)
                ):
                    continue
                folders.append(CopiedFolder(child, there / child.name))
        return folders

    def _measure(self) -> dict[str, int]:
        """What a duplicate costs, measured now. Blocking: it walks the cache."""
        probe = self.folder / "measure"
        records = _database_bytes(self._db.path) + _size(
            self._copied_folders(probe, pictures=False)
        )
        everything = _database_bytes(self._db.path) + _size(
            self._copied_folders(probe, pictures=True)
        )
        return {
            "records_bytes": records,
            "pictures_bytes": everything - records,
            "free_bytes": _free_bytes(self.folder),
        }

    async def duplicate_plan(self) -> dict[str, Any]:
        """What the Duplicate form says before anything is pressed: where the copy goes, how big it
        is with and without the pictures, the room there is, and what would refuse it now."""
        measured = await asyncio.to_thread(self._measure)
        return {
            "folder": str(self.folder),
            "refusal": self._backup.refusal_while_busy(),
            **measured,
        }

    async def ask_to_duplicate(
        self, name: str, *, pictures: bool, actor: Viewer, queue: JobQueue
    ) -> str:
        """Queue a duplicate of the running library, having refused whatever can be refused now.

        An empty or taken name, a backup, restore or switch running, a duplicate already waiting,
        and a drive without room: each said here, while somebody is looking, rather than as a
        failed task later. The job checks all of them again: time passes between the two.
        """
        root = await self._free_root(name)
        refusal = self._backup.refusal_while_busy()
        if refusal is not None:
            raise Busy(refusal)
        for state in (JobState.QUEUED, JobState.RUNNING):
            waiting = await queue.list(job_type=LIBRARY_DUPLICATE, state=state, limit=1)
            if waiting.total:
                raise Busy(
                    "This library is already being duplicated. Try again when it has finished."
                )
        measured = await asyncio.to_thread(self._measure)
        need = measured["records_bytes"] + (measured["pictures_bytes"] if pictures else 0)
        if measured["free_bytes"] < need + ROOM_TO_SPARE:
            raise NoRoom(_no_room(need))
        return await queue.enqueue(
            LIBRARY_DUPLICATE,
            {"name": root.name, "pictures": pictures},
            requested_by=actor.id,
        )

    async def run_duplicate(self, context: JobContext) -> None:
        """The job: copy the running library into a new one in the libraries folder.

        YOU STAY ON THIS LIBRARY. Nothing here switches; the copy is opened from the list whenever
        somebody wants it. Refusals end the task for good, with the sentence as its error: trying a
        taken name or a full drive twice more would say the same thing three times.
        """
        name = str(context.payload.get("name", ""))
        pictures = bool(context.payload.get("pictures", True))
        try:
            async with self._backup.exclusively(DUPLICATING):
                await self._duplicate(context, name, pictures=pictures)
        except (LibraryError, Busy) as refused:
            raise JobFailedPermanently(str(refused)) from refused

    async def _duplicate(self, context: JobContext, name: str, *, pictures: bool) -> None:
        root = await self._claim(name)
        folders = await asyncio.to_thread(self._copied_folders, root, pictures=pictures)

        def listed() -> list[tuple[Path, Path, int]]:
            return [
                (source, one.target / source.relative_to(one.source), size)
                for one in folders
                for source, size in _walk(one.source)
            ]

        files = await asyncio.to_thread(listed)
        live = self._db.path
        total = await asyncio.to_thread(_database_bytes, live) + sum(size for *_, size in files)
        if await asyncio.to_thread(_free_bytes, self.folder) < total + ROOM_TO_SPARE:
            raise NoRoom(_no_room(total))

        data_dir, cache_dir = root / DATA_FOLDER, root / CACHE_FOLDER
        done = 0
        try:
            await asyncio.to_thread(data_dir.mkdir, parents=True)
            await asyncio.to_thread(cache_dir.mkdir, parents=True)
            database = data_dir / DATABASE_FILENAME
            # The backup's own consistent snapshot, then the two things a second copy of a library
            # must not carry: this one's sign-ins, and this one's unfinished work.
            await self._backup.snapshot_into(database)
            await asyncio.to_thread(
                make_its_own_library, database, now=int(time.time()), note=DUPLICATED_NOTE
            )
            done = await asyncio.to_thread(_database_bytes, database)
            await context.report_progress(min(done / max(total, 1), 0.99))
            for start in range(0, len(files), _COPY_BATCH):
                if context.stopping() == "cancel":
                    raise JobCanceled
                batch = [
                    (source, target) for source, target, _ in files[start : start + _COPY_BATCH]
                ]
                done += await asyncio.to_thread(_copy_files, batch)
                await context.report_progress(min(done / max(total, 1), 0.99))
        except BaseException:
            # Nothing half-made is left in the list: a library is either whole or not there.
            await asyncio.to_thread(shutil.rmtree, root, True)
            raise
        await context.set_progress(1.0)
        await context.set_note(f"{root.name} is ready. Open it from the list whenever you want.")
        log.info("libraries.duplicated", pictures=pictures, files=len(files), size_bytes=done)

    # --- which opens at start, deleting one, forgetting one ----------------------------------

    async def choose_opening(self, library: str | None) -> None:
        """Say which library opens when Sift starts, or None for whichever was open last.

        Written to the folder's mark, which the desktop app reads before it starts a backend. Any
        library on the list may be chosen, the open one included; one that has gone by the next
        start is passed over there for the one that was open last, so a missing drive never leaves
        Sift with nothing to open.
        """
        if library is None:
            chosen = None
        else:
            known = await asyncio.to_thread(self._known)
            found = next((one for one in known if one.id == library), None)
            if found is None:
                raise UnknownLibrary("Sift does not know that library. Read the list again.")
            chosen = {"data_dir": str(found.data_dir), "cache_dir": str(found.cache_dir)}

        def write() -> None:
            self._ensure_folder()
            _mark(self.folder, _read_registry(self.folder), chosen)

        await asyncio.to_thread(write)
        log.info("libraries.opening_chosen", chosen=chosen is not None)

    async def delete(
        self, library: str, typed: str, *, recycle: Callable[[Path], None] = to_recycle_bin
    ) -> None:
        """Move a library in the libraries folder to the Recycle Bin, by its id and its typed name.

        Refused while other work runs on the library as a whole, for the open library, for one kept
        elsewhere (which is somebody's own folder, and is taken off the list instead), and for a
        name typed wrong. What goes is the library's folder: its database, the faces confirmed in
        it, the covers uploaded to it and every picture Sift made for it. The media files it
        pointed at are somebody's own and are never touched, since none of them is in that folder.
        """
        async with self._backup.exclusively(DELETING):
            known = await asyncio.to_thread(self._known)
            chosen = next((one for one in known if one.id == library), None)
            if chosen is None:
                raise UnknownLibrary("Sift does not know that library. Read the list again.")
            if chosen is known[0]:
                raise NotDeletable(
                    "Sift can't delete the library it has open. Open another library first."
                )
            if not chosen.in_folder:
                raise NotDeletable(
                    "That library is kept in a folder of its own, so Sift won't delete it. Remove "
                    "it from this list instead."
                )
            if typed.strip() != chosen.name:
                raise NotDeletable(f"Type {chosen.name} exactly to delete it.")
            root = chosen.data_dir.parent
            try:
                await asyncio.to_thread(recycle, root)
            except NoRecycleBin as refused:
                raise NotDeletable(str(refused)) from refused
            opening = await asyncio.to_thread(_read_opening, self.folder)
            if opening is not None and _same(Path(opening["data_dir"]), chosen.data_dir):
                await self.choose_opening(None)
        log.info("libraries.deleted")

    async def forget(self, library: str) -> None:
        """Take a library kept elsewhere off the list, leaving its folder exactly as it is.

        The only way off the list for a library outside the libraries folder: its folder is
        somebody's own. The open library stays listed, since it is the one running.
        """
        known = await asyncio.to_thread(self._known)
        chosen = next((one for one in known if one.id == library), None)
        if chosen is None:
            raise UnknownLibrary("Sift does not know that library. Read the list again.")
        if chosen is known[0] or chosen.in_folder:
            raise NotDeletable(
                "Only a library kept in a folder of its own can be taken off the list."
            )

        def write() -> None:
            folder = self.folder
            kept = [
                one
                for one in _read_registry(folder)
                if not _same(Path(one["data_dir"]), chosen.data_dir)
            ]
            opening = _read_opening(folder)
            if opening is not None and _same(Path(opening["data_dir"]), chosen.data_dir):
                opening = None
            _mark(folder, kept, opening)

        await asyncio.to_thread(write)
        log.info("libraries.forgotten")

    # --- the parts --------------------------------------------------------------------------

    def _require_supervisor(self) -> None:
        if not self._can_restart():
            raise NoSupervisor(_NO_SUPERVISOR)

    async def _claim(self, name: str) -> Path:
        """The folder a new library takes, proven free. Makes the libraries folder and its mark."""
        root = await self._free_root(name)
        await asyncio.to_thread(self._ensure_folder)
        return root

    async def _free_root(self, name: str) -> Path:
        """The folder a library by this name would take, proven free. Writes nothing."""
        chosen = check_name(name)
        folder = self.folder
        if any(member.name.lower() == chosen.lower() for member in await self._listing(folder)):
            raise NameTaken("There's already a library by that name. Choose another.")
        root = folder / chosen
        if await asyncio.to_thread(root.exists):
            raise NameTaken("There's already a folder by that name. Choose another.")
        return root

    @staticmethod
    async def _listing(folder: Path) -> list[Path]:
        def read() -> list[Path]:
            return list(folder.iterdir()) if folder.is_dir() else []

        return await asyncio.to_thread(read)

    def _ensure_folder(self) -> None:
        """The libraries folder with its mark, keeping whatever list it already holds. Blocking."""
        folder = self.folder
        if not (folder / REGISTRY_FILENAME).is_file():
            _mark(folder, _read_registry(folder))

    def _remember_current(self) -> None:
        """Put the running library on the folder's list when it is kept elsewhere. Blocking.

        What makes the way BACK reachable from a browser: a library outside the folder is known
        to this server only while it is running it, so it is written down before it stops.
        """
        current = self._current()
        if current.in_folder:
            return
        folder = self.folder
        elsewhere = _read_registry(folder)
        if not any(_same(Path(one["data_dir"]), current.data_dir) for one in elsewhere):
            elsewhere.append(
                {"data_dir": str(current.data_dir), "cache_dir": str(current.cache_dir)}
            )
        _mark(folder, elsewhere)

    async def _switch(self, target: Library) -> None:
        """Leave the note and ask to be started again. The note goes if the ask is refused."""
        await asyncio.to_thread(self._remember_current)
        note = self._settings.data_dir / HANDOFF_FILENAME
        await asyncio.to_thread(
            _write_json,
            note,
            {"data_dir": str(target.data_dir), "cache_dir": str(target.cache_dir)},
        )
        if not self._ask_to_restart():
            await asyncio.to_thread(note.unlink, True)
            raise NoSupervisor(_NO_SUPERVISOR)
        log.info("libraries.switch_asked", in_folder=target.in_folder)

    async def record_origin(self) -> None:
        """Write where this library came from as its first History line, where it was just
        imported. Called once at start-up, after the schema is brought forward and before the
        workers start, so nothing the library does comes before it.

        The note goes once the line is written. A note that cannot be read is removed with a
        warning rather than kept: it would refuse the same way at every start. Written before the
        note goes, so a stop between the two says the line twice rather than never.
        """
        note = self._settings.data_dir / ORIGIN_FILENAME
        if not await asyncio.to_thread(note.exists):
            return
        origin = await asyncio.to_thread(_read_origin, note)
        if origin is None:
            log.warning("libraries.origin_note_unreadable")
        else:
            async with self._db.write() as connection:
                await _record_origin(connection, origin)
            log.info("libraries.origin_recorded", origin=origin["from"])
        await asyncio.to_thread(note.unlink, True)

    async def forget_stale_note(self) -> None:
        """Remove a switch note nothing acted on. Called once at start-up.

        A note found here was left by a stop that was never followed by a switch: a desktop app
        too old to read it, or one that was closed in the moment between. Left in place, the next
        ordinary restart of this library would carry it off to the other one.
        """
        note = self._settings.data_dir / HANDOFF_FILENAME
        if await asyncio.to_thread(note.exists):
            await asyncio.to_thread(note.unlink, True)
            log.warning("libraries.stale_switch_note_removed")


def _no_room(need: int) -> str:
    """The refusal for a drive without room, with the size in the unit somebody reads."""
    gigabytes = (need + ROOM_TO_SPARE) / 1024**3
    return (
        f"There is not enough free space for the copy. It needs about {gigabytes:.1f} GB on the "
        "drive your libraries are on. Free some space, or leave out the pictures Sift made."
    )


def register_library_handlers(libraries: LibrariesService) -> None:
    """Claim the duplicate job type. Called once, at boot, beside the other handlers."""

    async def handle(context: JobContext) -> None:
        await libraries.run_duplicate(context)

    register_handler(LIBRARY_DUPLICATE, handle, name="Duplicating library", alone=True)


#: Making libraries and switching between them.
LIBRARIES: Part[LibrariesService] = Part("libraries")
