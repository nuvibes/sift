# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one step a library at the faces baseline takes: two indexes a new library already has.

A library brought up through the steps before the baseline never received the index Insights adds
a day's faces up by, or the one the review list reads open groups by, while a new library gets both.
Version 34 makes them where they are missing.
"""

from __future__ import annotations

import pytest

from sift.kernel.content import backlog
from sift.kernel.db import Connection, Database
from sift.slices.faces import schema as faces_schema

pytestmark = pytest.mark.anyio

_OWED = {"ix_face_tracks_created", "ix_face_piles_status"}
_DROP_THEM = ("DROP INDEX ix_face_tracks_created", "DROP INDEX ix_face_piles_status")
_INDEXES = "SELECT name FROM sqlite_master WHERE type = 'index'"


async def _parents(connection: Connection) -> None:
    """The tables from other components the face tables name or mark into, as narrow as can be."""
    for statement in backlog.TABLES:
        await connection.execute(statement)
    await connection.execute("CREATE TABLE people (id TEXT PRIMARY KEY)")
    await connection.execute("CREATE TABLE assets (id TEXT PRIMARY KEY)")


async def _indexes(connection: Connection) -> set[str]:
    return {str(row[0]) for row in await connection.execute_fetchall(_INDEXES)}


async def test_a_new_library_has_both(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        await _parents(connection)
        await faces_schema.initialize(connection, on_disk=0)

        assert await _indexes(connection) >= _OWED


async def test_a_library_at_the_baseline_without_them_gains_them(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        await _parents(connection)
        await faces_schema.initialize(connection, on_disk=0)
        for statement in _DROP_THEM:
            await connection.execute(statement)
        assert not _OWED & await _indexes(connection)

        # The baseline itself, which is what version 34's step is written for.
        await faces_schema.initialize(connection, on_disk=33)

        assert await _indexes(connection) >= _OWED


_REASONS = {"refused_largest", "refused_blurred", "refused_turned", "refused_edge"}


_DROP_REASONS = (
    "ALTER TABLE face_scans DROP COLUMN refused_blurred",
    "ALTER TABLE face_scans DROP COLUMN refused_edge",
    "ALTER TABLE face_scans DROP COLUMN refused_largest",
    "ALTER TABLE face_scans DROP COLUMN refused_turned",
)


async def _scan_columns(connection: Connection) -> set[str]:
    return {
        str(row[1]) for row in await connection.execute_fetchall("PRAGMA table_info(face_scans)")
    }


async def test_a_library_before_35_gains_the_refusal_reasons_and_a_new_one_has_them(
    temp_db: Database,
) -> None:
    """A scan's refusals told apart by reason, and the size of the biggest face too small: NULL on
    every row already there, which the History line reads as "not known"."""
    async with temp_db.write() as connection:
        await _parents(connection)
        await faces_schema.initialize(connection, on_disk=0)
        assert await _scan_columns(connection) >= _REASONS
        # Each drop written out: the schema is data nobody types, so no statement is built.
        for statement in _DROP_REASONS:
            await connection.execute(statement)
        assert not _REASONS & await _scan_columns(connection)

        await faces_schema.initialize(connection, on_disk=34)

        assert await _scan_columns(connection) >= _REASONS


#: `face_tracks` as a library that gained `asked_by` by adding the column holds it: the shape the
#: version 36 step meets on a library older than the baseline's own CREATE.
_TRACKS_AT_35 = (
    "CREATE TABLE face_tracks (id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, started_ms INTEGER"
    " NOT NULL, ended_ms INTEGER NOT NULL, seen_in INTEGER NOT NULL, quality REAL NOT NULL,"
    " person_id TEXT, confidence REAL, attribution TEXT, pile_id TEXT, created_at INTEGER NOT NULL"
    ", attributed_at INTEGER, asked_by TEXT CHECK(asked_by IN ('match','group','undone')))"
)
_A_TRACK = (
    "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, created_at,"
    " asked_by) VALUES (?, 'a', 0, 0, 1, 1.0, 0, ?)"
)


async def test_a_library_before_36_lets_a_stash_box_ask_and_nothing_else_new(
    temp_db: Database,
) -> None:
    """The CHECK on who asked widens to the stash-box in the stored definition; a word it does
    not name is still refused, and the step taken twice changes nothing."""
    async with temp_db.write() as connection:
        await _parents(connection)
        await faces_schema.initialize(connection, on_disk=0)
        await connection.execute("DROP TABLE face_detections")
        await connection.execute("DROP TABLE face_tracks")
        await connection.execute(_TRACKS_AT_35)
        with pytest.raises(Exception, match="CHECK"):
            await connection.execute(_A_TRACK, ("t0", "box"))

        await faces_schema.initialize(connection, on_disk=35)
        await faces_schema.initialize(connection, on_disk=35)

        await connection.execute(_A_TRACK, ("t1", "box"))
        with pytest.raises(Exception, match="CHECK"):
            await connection.execute(_A_TRACK, ("t2", "somebody"))


_A_REFERENCE = (
    "INSERT INTO face_references (id, person_id, crop_digest, embedding, quality, origin,"
    " recognizer, created_at) VALUES (?, 'p', ?, x'00000000', 1.0, ?, 'r', 0)"
)


async def test_a_library_before_37_lets_a_recognized_face_be_a_reference_and_nothing_else_new(
    temp_db: Database,
) -> None:
    """The CHECK on a reference's origin widens to a face Sift recognized, in the stored
    definition; a word it does not name is still refused, and the step taken twice changes
    nothing."""
    async with temp_db.write() as connection:
        await _parents(connection)
        await connection.execute("INSERT INTO people (id) VALUES ('p')")
        await faces_schema.initialize(connection, on_disk=0)
        await connection.execute("DROP TABLE face_references")
        await connection.execute(
            faces_schema._CREATE_REFERENCES.replace(  # nosemgrep: sift-no-string-built-sql
                faces_schema._ORIGIN_NOW, faces_schema._ORIGIN_WAS
            )
        )
        with pytest.raises(Exception, match="CHECK"):
            await connection.execute(_A_REFERENCE, ("r0", "d0", "recognized"))

        await faces_schema.initialize(connection, on_disk=36)
        await faces_schema.initialize(connection, on_disk=36)

        await connection.execute(_A_REFERENCE, ("r1", "d1", "recognized"))
        with pytest.raises(Exception, match="CHECK"):
            await connection.execute(_A_REFERENCE, ("r2", "d2", "somebody"))


async def test_a_library_before_38_gains_a_declined_entry_and_twice_is_once(
    temp_db: Database,
) -> None:
    """Every older entry reads as never declined: the column comes in empty."""
    columns = "SELECT name FROM pragma_table_info('pack_entries')"
    async with temp_db.write() as connection:
        await _parents(connection)
        await faces_schema.initialize(connection, on_disk=0)
        await connection.execute("ALTER TABLE pack_entries DROP COLUMN declined_at")

        await faces_schema.initialize(connection, on_disk=37)
        await faces_schema.initialize(connection, on_disk=37)

        names = [str(row[0]) for row in await connection.execute_fetchall(columns)]
    assert names.count("declined_at") == 1


async def test_a_library_before_39_gains_an_entrys_folder_and_count_and_twice_is_once(
    temp_db: Database,
) -> None:
    """Every older entry reads as its pack's name and as a count nobody gave: both come in empty."""
    columns = "SELECT name FROM pragma_table_info('pack_entries')"
    async with temp_db.write() as connection:
        await _parents(connection)
        await faces_schema.initialize(connection, on_disk=0)
        await connection.execute("ALTER TABLE pack_entries DROP COLUMN source")
        await connection.execute("ALTER TABLE pack_entries DROP COLUMN confirmed")

        await faces_schema.initialize(connection, on_disk=38)
        await faces_schema.initialize(connection, on_disk=38)

        names = [str(row[0]) for row in await connection.execute_fetchall(columns)]
    assert (names.count("source"), names.count("confirmed")) == (1, 1)
