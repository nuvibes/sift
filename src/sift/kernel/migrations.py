# SPDX-License-Identifier: AGPL-3.0-or-later
"""Helpers a schema step is written with: it asks what it finds rather than assuming."""

from __future__ import annotations

from sift.kernel.db import Connection


class MigrationError(RuntimeError):
    """A migration met a database it cannot safely change. The message is meant to be read."""


async def table_exists(connection: Connection, table: str) -> bool:
    """Whether a table is in the database, parameterized as the injection control."""
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
    """Whether a table's stored definition already names `value` inside a CHECK."""
    rows = list(
        await connection.execute_fetchall(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        )
    )
    if not rows or rows[0][0] is None:
        return False
    return f"'{value}'" in str(rows[0][0])


#: Rename without rewriting triggers: they would fail to reparse while the table is absent.
#: Always put back, since every migration shares the one writer connection.
_LEAVE_EVERY_OTHER_OBJECT_ALONE = "PRAGMA legacy_alter_table=ON"
_FOLLOW_THE_RENAME_EVERYWHERE = "PRAGMA legacy_alter_table=OFF"


async def rebuild_in_place(connection: Connection, statements: tuple[str, ...]) -> None:
    """Run a build-copy-drop-rename rebuild without disturbing anything else in the schema."""
    await connection.execute(_LEAVE_EVERY_OTHER_OBJECT_ALONE)
    try:
        for statement in statements:
            await connection.execute(statement)
    finally:
        await connection.execute(_FOLLOW_THE_RENAME_EVERYWHERE)


# A rebuild loses rows of a referenced table through ON DELETE, so a widening that only adds
# a value edits the stored definition and touches no row.
_WRITABLE_SCHEMA_ON = "PRAGMA writable_schema=ON"
_WRITABLE_SCHEMA_OFF = "PRAGMA writable_schema=OFF"
_STORED_DEFINITION = "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?"
_REWRITE_DEFINITION = "UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = ?"


async def widen_a_check(connection: Connection, table: str, *, was: str, now: str) -> None:
    """Replace one CHECK fragment found exactly once in a table's stored definition."""
    rows = list(await connection.execute_fetchall(_STORED_DEFINITION, (table,)))
    stored = str(rows[0][0]) if rows and rows[0][0] is not None else ""
    if stored.count(was) != 1:
        raise MigrationError(
            f"cannot widen the CHECK on {table}: this build expected to find {was!r} exactly once "
            f"in its definition and found it {stored.count(was)} times. The table is not the shape "
            "this version of Sift knows how to upgrade, so nothing has been changed."
        )

    # The schema cookie must move, or cached connections keep the old constraint.
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
