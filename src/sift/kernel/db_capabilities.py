# SPDX-License-Identifier: AGPL-3.0-or-later
"""What this SQLite can do: the full-text and extension-loading checks a start makes."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from sift.kernel.db_base import DatabaseError
from sift.kernel.log import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class SqliteCapabilities:
    """What the SQLite this process linked can actually do."""

    version: str
    fts5: bool
    load_extension: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "fts5": self.fts5,
            "load_extension": self.load_extension,
        }


def _fts5_present(connection: sqlite3.Connection) -> bool:
    """Whether FTS5 is there, asked by building one: a compile option no version number tells."""
    try:
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(x)")
    except sqlite3.Error:
        return False
    return True


def _extension_loading_available(connection: sqlite3.Connection) -> bool:
    """Whether extensions can be loaded at all (Python or SQLite may lack it); left off."""
    try:
        connection.enable_load_extension(True)
    except (AttributeError, sqlite3.Error):
        return False
    connection.enable_load_extension(False)
    return True


def probe_sqlite() -> SqliteCapabilities:
    """Ask the library what it can do, in memory, before the data directory is opened."""
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA trusted_schema=OFF")
    try:
        return SqliteCapabilities(
            version=sqlite3.sqlite_version,
            fts5=_fts5_present(connection),
            load_extension=_extension_loading_available(connection),
        )
    finally:
        connection.close()


def check_sqlite_capabilities(
    capabilities: SqliteCapabilities | None = None,
    *,
    announce: bool = True,
) -> SqliteCapabilities:
    """Refuse to run on a SQLite that cannot do what Sift needs, and record what it can.

    A capability rather than a version: what matters is how the machine's SQLite was built. Only
    FTS5 stops a boot, since search is made of it; extension loading is recorded. Once at boot.
    `announce` is off for callers before logging is set up, or console tools.
    """
    found = capabilities if capabilities is not None else probe_sqlite()

    if not found.fts5:
        raise DatabaseError(
            f"Sift needs a SQLite built with FTS5, and the one it found ({found.version}) does "
            "not have it.\n"
            "FTS5 is the full-text index every search runs against, so without it there is no "
            "search at all.\n"
            "It is a build option and not a version, so a newer SQLite is not necessarily a fix. "
            "The container image ships one that has it."
        )

    if not announce:
        return found

    log.info(
        "sqlite.capabilities",
        version=found.version,
        fts5=found.fts5,
        load_extension=found.load_extension,
    )
    if not found.load_extension:
        log.warning(
            "sqlite.no_extension_loading",
            detail=(
                "This SQLite cannot load extensions. Everything Sift does today works without "
                "them; features that are built on one will not."
            ),
        )
    return found
