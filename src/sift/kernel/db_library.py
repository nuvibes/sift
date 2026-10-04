# SPDX-License-Identifier: AGPL-3.0-or-later
"""Looking at a library or a database file without opening it, and copying one safely."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sift.kernel.db_base import DATABASE_FILENAME
from sift.kernel.db_schema import registered_components, too_old_to_bring_forward
from sift.kernel.when import stamp as machine_stamp

# The desktop shell asks whether a folder holds a database this build can read BEFORE it stops the
# one running, and booting is the migration, so the verdict is read here beside the rule it must
# agree with (`initialize_schema`, `too_old_to_bring_forward`), over EVERY component.

#: What a library folder turns out to be, in one word: `empty` (a new library, which may open) to
#: `unreadable` (not a Sift database, or not openable).
LibraryVerdict = str

VERDICT_EMPTY: LibraryVerdict = "empty"
VERDICT_CURRENT: LibraryVerdict = "current"
VERDICT_OLDER: LibraryVerdict = "older"
VERDICT_NEWER: LibraryVerdict = "newer"
VERDICT_UNREADABLE: LibraryVerdict = "unreadable"


@dataclass(frozen=True)
class LibraryReport:
    """What one library folder says about itself, read without starting anything."""

    #: The database file this describes, whether or not it is there.
    database: Path
    verdict: LibraryVerdict
    #: What is recorded in the file, component by component. Empty for a folder with no database.
    on_disk: dict[str, int]
    #: What this build declares, for the same components. Read from the registry, never typed.
    expected: dict[str, int]
    #: A sentence for a person, shown by every door that refuses the library, or empty.
    detail: str = ""
    #: What SQLite said when the file could not be read, for a log. Never shown to a person.
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "database": str(self.database),
            "verdict": self.verdict,
            "on_disk": dict(sorted(self.on_disk.items())),
            "expected": dict(sorted(self.expected.items())),
            "detail": self.detail,
            "error": self.error,
        }


def library_database(target: Path) -> Path:
    """The database a path names: the file itself when it ends in `.sqlite3` and is not a folder,
    else `sift.sqlite3` inside it. By NAME, since a new library's folder need not exist yet."""
    if target.suffix.lower() == ".sqlite3" and not target.is_dir():
        return target
    return target / DATABASE_FILENAME


def inspect_library(data_dir: Path) -> LibraryReport:
    """Read the schema versions recorded in a library folder, changing nothing."""
    return inspect_database(data_dir / DATABASE_FILENAME)


def inspect_database(database: Path) -> LibraryReport:
    """Read the schema versions recorded in one database file, changing nothing.

    The file form, for the switcher's "choose a database file", read by the same verdicts as a
    folder so the two doors cannot disagree. Opened `mode=ro`, so "changing nothing" is a property
    of the connection. No database, or no `schema_version` table (a first boot interrupted), is
    `empty`, never a refusal.
    """
    expected = {name: one.version for name, one in registered_components().items()}
    if not database.exists():
        return LibraryReport(database, VERDICT_EMPTY, {}, expected)

    try:
        connection = sqlite3.connect(_read_only_uri(database), uri=True)
        connection.execute("PRAGMA trusted_schema=OFF")
    except sqlite3.Error as unopenable:
        return LibraryReport(database, VERDICT_UNREADABLE, {}, expected, error=str(unopenable))

    try:
        cursor = connection.execute("SELECT component, version FROM schema_version")
        on_disk = {str(name): int(version) for name, version in cursor.fetchall()}
    except sqlite3.DatabaseError as broken:
        # "no such table" (a first boot interrupted) or "file is not a database": one class, so
        # told apart by the message.
        if "no such table" in str(broken):
            return LibraryReport(database, VERDICT_EMPTY, {}, expected)
        return LibraryReport(database, VERDICT_UNREADABLE, {}, expected, error=str(broken))
    finally:
        connection.close()

    if not on_disk:
        return LibraryReport(database, VERDICT_EMPTY, {}, expected)

    ahead = sorted(
        name
        for name, version in on_disk.items()
        if name not in expected or version > expected[name]
    )
    if ahead:
        return LibraryReport(
            database,
            VERDICT_NEWER,
            on_disk,
            expected,
            "This library was last opened by a newer version of Sift than this one.",
        )

    # Older than a baseline is a library this build cannot open at all, so it reads as one: an
    # "older" verdict would offer an upgrade that the boot then refuses.
    refused = too_old_to_bring_forward(on_disk)
    if refused is not None:
        return LibraryReport(database, VERDICT_UNREADABLE, on_disk, expected, refused)

    behind = sorted(name for name, version in on_disk.items() if version < expected[name])
    if behind:
        return LibraryReport(
            database,
            VERDICT_OLDER,
            on_disk,
            expected,
            "This library was last opened by an older version of Sift.",
        )
    return LibraryReport(database, VERDICT_CURRENT, on_disk, expected)


def _read_only_uri(database: Path) -> str:
    """A `mode=ro` URI for a database file, percent-encoded by `as_uri`, since a `#` or `?` pasted
    raw would make SQLite open a DIFFERENT file."""
    return f"{database.absolute().as_uri()}?mode=ro"


def copy_library_aside(data_dir: Path, *, now: float | None = None) -> Path:
    """Take a snapshot of a library's database, beside it, before anything upgrades it."""
    return copy_database_aside(data_dir / DATABASE_FILENAME, now=now)


def copy_database_aside(database: Path, *, now: float | None = None) -> Path:
    """Take a snapshot of one database file, beside it, before anything upgrades it.

    `VACUUM INTO`, never a file copy: in WAL mode the newest rows can sit in a side file. A dated
    plain `.sqlite3`, so the Database Switcher can open it again.
    """
    beside = database.parent
    stamp = machine_stamp(now if now is not None else time.time(), "%Y%m%d-%H%M%S")
    snapshot = beside / f"sift-before-upgrade-{stamp}.sqlite3"
    # A second attempt in the same second must not be handed the first one's file: VACUUM INTO
    # refuses a target that exists, and the refusal would read as the copy having failed.
    counter = 1
    while snapshot.exists():
        snapshot = beside / f"sift-before-upgrade-{stamp}-{counter}.sqlite3"
        counter += 1
    # Refused rather than connected to: `sqlite3.connect` on a path that is not there CREATES an
    # empty database, and the snapshot of that would be reported as a good copy of nothing.
    if not database.is_file():
        raise FileNotFoundError(f"there is no database at {database}")
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA trusted_schema=OFF")
    try:
        connection.execute("VACUUM INTO ?", (str(snapshot),))
    finally:
        connection.close()
    return snapshot


def adopt_database(source: Path, data_dir: Path) -> Path:
    """Copy a database file into a library folder as its `sift.sqlite3`, and answer where it went.

    The library is made from a COPY (`VACUUM INTO`, from a read-only connection), so the chosen file
    is never migrated, written or given a side file. A folder that already holds a database is
    refused rather than overwritten.
    """
    if not source.is_file():
        raise FileNotFoundError(f"there is no database at {source}")
    target = data_dir / DATABASE_FILENAME
    if target.exists():
        raise FileExistsError(f"there is already a library database at {target}")
    data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(_read_only_uri(source), uri=True)
    connection.execute("PRAGMA trusted_schema=OFF")
    try:
        connection.execute("VACUUM INTO ?", (str(target),))
    finally:
        connection.close()
    return target


def execute_blocking(database: Path, statement: str, params: tuple[object, ...] = ()) -> None:
    """One write statement on a database FILE, committed, blocking: for the library switcher,
    before any `Database` has opened it."""
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA trusted_schema=OFF")
    try:
        connection.execute(statement, params)
        connection.commit()
    finally:
        connection.close()


def fetch_blocking(
    database: Path, statement: str, params: tuple[object, ...] = ()
) -> list[tuple[object, ...]]:
    """Every row one read statement answers on a database FILE, blocking. See `execute_blocking`."""
    connection = sqlite3.connect(_read_only_uri(database), uri=True)
    connection.execute("PRAGMA trusted_schema=OFF")
    try:
        return [tuple(row) for row in connection.execute(statement, params).fetchall()]
    finally:
        connection.close()
