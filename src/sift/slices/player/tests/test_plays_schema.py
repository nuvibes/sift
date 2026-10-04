# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the `plays` table promises: a watch history that outlives the file, and one row a sitting.

Built on two hand-written parent tables rather than the whole schema, so the test is about this
table's keys and constraints and nothing else.
"""

from __future__ import annotations

import re
from typing import get_args

import pytest

from sift.kernel.access.constraints import MEDIA_TYPES
from sift.kernel.db import Connection, Database
from sift.slices.player import schema as player_schema
from sift.slices.player.plays import Kind, OpenedFrom, Screen

pytestmark = pytest.mark.regression

_EPOCH = 1_700_000_000

MINE = "01HX0000000000000000000901"
ASSET = "01HX0000000000000000000902"
GONE = "01HX0000000000000000000903"


async def _library(connection: Connection) -> None:
    """One user, two files, the plays table, and a sitting with each file."""
    await connection.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
    await connection.execute("CREATE TABLE assets (id TEXT PRIMARY KEY, title TEXT)")
    await player_schema.initialize_player(connection, on_disk=0)
    await connection.execute("INSERT INTO users (id, name) VALUES (?, 'admin')", (MINE,))
    for asset_id in (ASSET, GONE):
        await connection.execute("INSERT INTO assets (id) VALUES (?)", (asset_id,))
    for row_id, asset_id in (("play-kept", ASSET), ("play-orphan", GONE)):
        await connection.execute(
            "INSERT INTO plays (id, user_id, asset_id, started_at, duration_ms, made_at)"
            " VALUES (?, ?, ?, ?, 60000, ?)",
            (row_id, MINE, asset_id, _EPOCH, _EPOCH),
        )


async def _ids(connection: Connection) -> list[str]:
    found = await connection.execute_fetchall("SELECT id FROM plays ORDER BY id")
    return [str(row["id"]) for row in found]


async def test_a_sitting_outlives_the_file_it_was_about(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        await _library(connection)

        await connection.execute("DELETE FROM assets WHERE id = ?", (GONE,))

        # Both rows. The one about a file that is gone is what "you watched 240 hours this year"
        # is made of, and the file being tidied away does not unwatch it.
        assert await _ids(connection) == ["play-kept", "play-orphan"]


async def test_removing_the_user_still_takes_its_history(temp_db: Database) -> None:
    """A user removed is the deliberate forget, so that key cascades."""
    async with temp_db.write() as connection:
        await _library(connection)

        await connection.execute("DELETE FROM users WHERE id = ?", (MINE,))

        assert await _ids(connection) == []


async def test_one_sitting_of_one_file_cannot_become_two_rows(temp_db: Database) -> None:
    """The unique index, which is what makes "one row per sitting" a property rather than a habit.

    Scoped to the user and the file as well, so the three things a repeated id could otherwise
    damage are each checked: the same sitting twice is refused, the same id on a second FILE is an
    ordinary row, and the same id from a second USER is an ordinary row.
    """
    async with temp_db.write() as connection:
        await _library(connection)
        await connection.execute("INSERT INTO users (id, name) VALUES ('other', 'guest')")

        async def write(row_id: str, user_id: str, asset_id: str) -> None:
            await connection.execute(
                "INSERT INTO plays"
                " (id, user_id, asset_id, sitting, started_at, duration_ms, made_at)"
                " VALUES (?, ?, ?, 'one-sitting', ?, 1, ?)",
                (row_id, user_id, asset_id, _EPOCH, _EPOCH),
            )

        await write("first", MINE, ASSET)
        with pytest.raises(Exception, match="UNIQUE"):
            await write("second", MINE, ASSET)
        await write("other-file", MINE, GONE)
        await write("other-user", "other", ASSET)

        assert await _ids(connection) == [
            "first",
            "other-file",
            "other-user",
            "play-kept",
            "play-orphan",
        ]


async def test_a_screen_the_list_does_not_name_is_refused_by_the_column(
    temp_db: Database,
) -> None:
    async with temp_db.write() as connection:
        await _library(connection)

        with pytest.raises(Exception, match="CHECK"):
            await connection.execute("UPDATE plays SET screen = 'sideways' WHERE id = 'play-kept'")
        with pytest.raises(Exception, match="CHECK"):
            await connection.execute("UPDATE plays SET kind = 'audio' WHERE id = 'play-kept'")


def _listed(stored: str, column: str) -> tuple[str, ...]:
    """The values a column's CHECK names, read out of the table's stored definition."""
    found = re.search(rf"CHECK\s*\(\s*{column}\s+IN\s*\(([^)]*)\)", stored)
    assert found, f"no CHECK on {column}"
    return tuple(value.strip().strip("'") for value in found.group(1).split(","))


async def test_the_columns_agree_with_the_wire(temp_db: Database) -> None:
    """Each list is written twice, once for the report and once in the column's CHECK, and this is
    what keeps them one list. A value added to the report and not to the column would be a report
    the database refuses; one added to the column alone would be a value nothing sends.

    The kind is held to the library's own media types as well, since that is what it is copied from.
    """
    async with temp_db.write() as connection:
        await _library(connection)
        rows = list(
            await connection.execute_fetchall(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'plays'"
            )
        )
    stored = str(rows[0][0])

    assert _listed(stored, "screen") == get_args(Screen)
    assert _listed(stored, "opened_from") == get_args(OpenedFrom)
    assert _listed(stored, "kind") == get_args(Kind)
    assert set(get_args(Kind)) == MEDIA_TYPES


async def test_a_second_boot_leaves_the_history_alone(temp_db: Database) -> None:
    """The arm every boot after the first takes."""
    async with temp_db.write() as connection:
        await _library(connection)

        await player_schema.initialize_player(connection, on_disk=player_schema.VERSION)

        rows = list(await connection.execute_fetchall("SELECT id FROM plays ORDER BY id"))
    assert [str(row[0]) for row in rows] == ["play-kept", "play-orphan"]


def _as_version_six() -> str:
    """The table as version 6 left it: without the version 7 columns, and with the opened-from
    list before a folder and Insights were on it."""
    statement = player_schema._CREATE_PLAYS
    cut = statement.index("\n  -- Version 7.")
    return (statement[:cut].rstrip(",") + "\n)\n").replace(
        player_schema._OPENED_FROM_V7, player_schema._OPENED_FROM_V6
    )


async def test_version_seven_adds_what_a_sitting_keeps_and_reads_an_old_one_as_unrecorded(
    temp_db: Database,
) -> None:
    """A library from before version 7 gains the columns, the wider list and the names table; its
    sittings read NULL in every new column, which is "not recorded", and nothing else moves."""
    async with temp_db.write() as connection:
        await connection.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
        await connection.execute(_as_version_six())
        await connection.execute("INSERT INTO users (id, name) VALUES (?, 'admin')", (MINE,))
        await connection.execute(
            "INSERT INTO plays (id, user_id, asset_id, started_at, duration_ms, made_at, opened_from)"
            " VALUES ('old', ?, ?, ?, 60000, ?, 'library')",
            (MINE, ASSET, _EPOCH, _EPOCH),
        )

        await player_schema.initialize_player(connection, on_disk=6)
        await player_schema.initialize_player(connection, on_disk=6)

        (row,) = list(await connection.execute_fetchall("SELECT * FROM plays"))
        await connection.execute(
            "INSERT INTO plays (id, user_id, started_at, duration_ms, made_at, opened_from)"
            " VALUES ('new', ?, ?, 1, ?, 'insights')",
            (MINE, _EPOCH, _EPOCH),
        )
        names = list(await connection.execute_fetchall("SELECT * FROM play_names"))

    old = dict(row)
    assert (old["opened_from"], old["duration_ms"]) == ("library", 60000)
    for column, _ in player_schema._ADD_V7:
        assert old[column] is None, column
    assert names == []


async def test_version_six_lets_a_library_from_before_it_record_a_sitting_opened_from_a_song(
    temp_db: Database,
) -> None:
    """A library from before version 6 refuses `song` as where a file was opened from until the
    list is widened; its sittings stay as they were."""
    as_version_five = _as_version_six().replace(
        player_schema._OPENED_FROM_NOW, player_schema._OPENED_FROM_WAS
    )
    async with temp_db.write() as connection:
        await connection.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
        # nosemgrep: sift-no-string-built-sql (the test's own text of an older schema)
        await connection.execute(as_version_five)
        await connection.execute("INSERT INTO users (id, name) VALUES (?, 'admin')", (MINE,))
        await connection.execute(
            "INSERT INTO plays (id, user_id, asset_id, started_at, duration_ms, made_at, opened_from)"
            " VALUES ('old', ?, ?, ?, 60000, ?, 'collection')",
            (MINE, ASSET, _EPOCH, _EPOCH),
        )

        await player_schema.initialize_player(connection, on_disk=5)
        await connection.execute(
            "INSERT INTO plays (id, user_id, started_at, duration_ms, made_at, opened_from)"
            " VALUES ('new', ?, ?, 1, ?, 'song')",
            (MINE, _EPOCH, _EPOCH),
        )
        rows = list(
            await connection.execute_fetchall("SELECT id, opened_from FROM plays ORDER BY id")
        )

    assert [(str(row[0]), str(row[1])) for row in rows] == [("new", "song"), ("old", "collection")]
