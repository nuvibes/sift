# SPDX-License-Identifier: AGPL-3.0-or-later
"""Helpers a schema step is written with.

Each component's first step creates its tables as they are at its baseline; a later step changes a
library that is already there, and these are the questions such a step has to ask of what it finds.
SQLite has no `IF NOT EXISTS` on a column, and cannot alter a CHECK at all, so a step that assumed
instead of asking would leave a library unbootable if it met one it did not expect.
"""

from __future__ import annotations

from sift.kernel.db import Connection


class MigrationError(RuntimeError):
    """A migration met a database it cannot safely change. The message is meant to be read."""


async def table_exists(connection: Connection, table: str) -> bool:
    """Whether a table is in the database.

    Parameterized against `sqlite_master` rather than formatted into a `PRAGMA`, because there is no
    ORM here and parameterization is the whole of the injection control.
    """
    rows = await connection.execute_fetchall(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    )
    return bool(rows)


async def column_exists(connection: Connection, table: str, column: str) -> bool:
    """Whether a table has a column. False when the table itself is absent."""
    rows = await connection.execute_fetchall(
        "SELECT 1 FROM pragma_table_info(?) WHERE name = ?", (table, column)
    )
    return bool(rows)


async def check_allows(connection: Connection, table: str, value: str) -> bool:
    """Whether a table's definition already names `value` inside a CHECK.

    Widening a CHECK means a rebuild, which must be safe to run twice (a step interrupted before its
    version is recorded runs again); after a rebuild the constraint allows the value and the step
    is a no-op. Read from the stored CREATE (no pragma lists constraints), matching the quoted
    literal so a column name or comment does not count. False when the table is absent.
    """
    rows = list(
        await connection.execute_fetchall(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        )
    )
    if not rows or rows[0][0] is None:
        return False
    return f"'{value}'" in str(rows[0][0])


#: Renaming a table WITHOUT SQLite rewriting every trigger and view that mentions it. The CHECK
#: rebuild (build beside, copy, DROP, rename in) leaves the name absent between DROP and RENAME, so
#: the default reparse fails on every trigger naming the table (the visibility triggers name
#: `acl_grants`) and stops the boot of an existing library. With this on, the rename touches only
#: the table; the triggers are right again once it lands, and those on the dropped table are
#: rewritten by `visibility.keep_true` later in the same boot. A connection flag that does take
#: effect inside a transaction (unlike `foreign_keys`), and always put back, since every migration
#: shares the one writer connection.
_LEAVE_EVERY_OTHER_OBJECT_ALONE = "PRAGMA legacy_alter_table=ON"
_FOLLOW_THE_RENAME_EVERYWHERE = "PRAGMA legacy_alter_table=OFF"


async def rebuild_in_place(connection: Connection, statements: tuple[str, ...]) -> None:
    """Run a build-copy-drop-rename rebuild without disturbing anything else in the schema.

    See `_LEAVE_EVERY_OTHER_OBJECT_ALONE` for what this is protecting against.
    """
    await connection.execute(_LEAVE_EVERY_OTHER_OBJECT_ALONE)
    try:
        for statement in statements:
            await connection.execute(statement)
    finally:
        await connection.execute(_FOLLOW_THE_RENAME_EVERYWHERE)


# --- widening a CHECK without moving a row --------------------------------------------------
#
# A rebuild is silent data loss for a table something REFERENCES: with foreign keys on, `DROP TABLE`
# deletes first and fires every ON DELETE action (a self-referencing `jobs` loses its children,
# `downloads.job_id` is SET NULL), and a rename rewrites REFERENCES clauses, so no order survives.
# Foreign keys cannot be turned off inside the migration's transaction. So a widening that only ADDS
# a value edits the stored `CREATE TABLE` text and touches no row; the same edit gives an added
# column the CHECK a new library's CREATE carries, after its caller confirms every stored value
# passes. Not a general schema editor: it takes the exact old and new fragments, refuses anything
# not found exactly once, and the transaction rolls back on failure.
_WRITABLE_SCHEMA_ON = "PRAGMA writable_schema=ON"
_WRITABLE_SCHEMA_OFF = "PRAGMA writable_schema=OFF"
_STORED_DEFINITION = "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?"
_REWRITE_DEFINITION = "UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = ?"


async def widen_a_check(connection: Connection, table: str, *, was: str, now: str) -> None:
    """Replace one CHECK fragment in a table's stored definition with a wider one.

    `was` and `now` are the exact text the schema module writes, legible beside the constraint. The
    fragment must appear exactly once, or the table is refused: upgrading the wrong text is worse
    than not upgrading. Callers guard on `check_allows`, so it is safe to run twice.
    """
    rows = list(await connection.execute_fetchall(_STORED_DEFINITION, (table,)))
    stored = str(rows[0][0]) if rows and rows[0][0] is not None else ""
    if stored.count(was) != 1:
        raise MigrationError(
            f"cannot widen the CHECK on {table}: this build expected to find {was!r} exactly once "
            f"in its definition and found it {stored.count(was)} times. The table is not the shape "
            "this version of Sift knows how to upgrade, so nothing has been changed."
        )

    # The schema cookie must move with the text, or connections with the schema cached keep the old
    # constraint. A pragma takes no placeholder; the value is SQLite's own integer.
    read = await connection.execute_fetchall("PRAGMA schema_version", ())
    version = int(next(iter(read))[0])
    await connection.execute(_WRITABLE_SCHEMA_ON)
    try:
        await connection.execute(_REWRITE_DEFINITION, (stored.replace(was, now), table))
        bump = f"PRAGMA schema_version = {version + 1}"
        # nosemgrep: sift-no-string-built-sql
        await connection.execute(bump)
    finally:
        await connection.execute(_WRITABLE_SCHEMA_OFF)
