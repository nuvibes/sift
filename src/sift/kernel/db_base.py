# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the whole database layer names: the driver's types, the pragmas, the file, the one error."""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import aiosqlite

# Positional `?` or named `:name`, for the few queries that bind one value in several places.
Params = Sequence[Any] | Mapping[str, Any]

# Applied to every connection, not once at startup. `foreign_keys` in particular is per-connection:
# forget it on one and that connection silently ignores every ON DELETE CASCADE in the schema, which
# is a data-integrity bug wearing the costume of a performance setting.
PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA busy_timeout=5000",
    # Schema objects (views, triggers, CHECKs) may not reach into functions or virtual tables that
    # a crafted database file could point at something else.
    "PRAGMA trusted_schema=OFF",
    # How much of the database each connection may hold in memory, in kibibytes (negative), so 8 MB
    # whatever the page size. Per CONNECTION, and the pool is sized from the workers, so every
    # megabyte here is many on the box, and 8 buys nearly all that 64 does. `temp_store` stays
    # unset for a reader: an unbounded in-memory sort over a large library is a worse failure than
    # a slower one. The writer alone keeps its temporary tables in memory (`Database._open`).
    "PRAGMA cache_size=-8192",
)

DATABASE_FILENAME = "sift.sqlite3"

#: Marker `in_clause` expands. See its docstring.
IN_MARKER = "(?*)"

# The driver's types, aliased so the driver is named in exactly one module.
Connection = aiosqlite.Connection
Row = aiosqlite.Row

# A write refused by a constraint, aliased so a feature can name it without the driver. Most writes
# avoid it by shape (`ON CONFLICT DO NOTHING ... RETURNING`); this is for a row deleted meanwhile.
IntegrityError = sqlite3.IntegrityError
OperationalError = sqlite3.OperationalError

Initializer = Callable[[Connection, int], Awaitable[None]]


class DatabaseError(RuntimeError):
    """Raised with a message meant for the person running Sift, not a stack trace."""
