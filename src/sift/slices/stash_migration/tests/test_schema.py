# SPDX-License-Identifier: AGPL-3.0-or-later
"""The slice's tables, brought forward from each version a library may be at."""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.kernel.db import Database
from sift.slices.stash_migration import schema

pytestmark = pytest.mark.unit


async def test_a_library_at_version_2_gains_the_groups_table_and_keeps_its_rows(
    tmp_path: Path,
) -> None:
    """Version 3 remembers which Collection each Stash group became. A library at 2 has its
    galleries remembered already; those stay, and the new table arrives empty beside them."""
    database = Database(tmp_path / "library.sqlite3", readers=1)
    await database.connect()
    try:
        async with database.write() as connection:
            await schema.initialize(connection, 0)
            await connection.execute("DROP TABLE stash_groups")
            await connection.execute(
                "INSERT INTO stash_galleries (source, stash_id, name) VALUES ('s', 1, 'Kept')"
            )
        async with database.write() as connection:
            await schema.initialize(connection, 2)
        assert schema.VERSION == 3
        kept = await database.fetch_all("SELECT name FROM stash_galleries")
        assert [row["name"] for row in kept] == ["Kept"]
        async with database.write() as connection:
            await connection.execute(
                "INSERT INTO stash_groups (source, stash_id, name) VALUES ('s', 1, 'A group')"
            )
        rows = await database.fetch_all("SELECT name, collection_id, scenes FROM stash_groups")
        assert [tuple(row) for row in rows] == [("A group", None, "[]")]
    finally:
        await database.close()


async def test_a_library_at_version_1_files_what_waits_under_sifts_own_words(
    tmp_path: Path,
) -> None:
    """Version 1 filed what waits under Stash's words (scene, image) behind a CHECK that refuses
    any other. A library at 1 is rebuilt so each row reads file or picture, its file identities
    stay with it, and the groups table arrives beside them."""
    stash_words = schema._CREATE_WAITING.replace("('file','picture')", "('scene','image')", 1)
    database = Database(tmp_path / "library.sqlite3", readers=1)
    await database.connect()
    try:
        async with database.write() as connection:
            await connection.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
            # nosemgrep: sift-no-string-built-sql (the test's own text of Stash's table)
            await connection.execute(stash_words)
            await connection.execute(schema._CREATE_FILES)
            await connection.execute(schema._CREATE_ENTITIES)
            await connection.execute(schema._CREATE_GALLERIES)
            await connection.execute(
                "INSERT INTO stash_waiting (id, kind, stash_id, read_at, label, label_key, package)"
                " VALUES ('a', 'scene', 1, 0, 'A', 'a', '{}'), ('b', 'image', 2, 0, 'B', 'b', '{}')"
            )
            await connection.execute(
                "INSERT INTO stash_waiting_files (waiting_id, position, path)"
                " VALUES ('a', 0, '/clips/a.mp4')"
            )
        async with database.write() as connection:
            await schema.initialize(connection, 1)
        kinds = await database.fetch_all("SELECT id, kind FROM stash_waiting ORDER BY id")
        assert [tuple(row) for row in kinds] == [("a", "file"), ("b", "picture")]
        files = await database.fetch_all("SELECT waiting_id, path FROM stash_waiting_files")
        assert [tuple(row) for row in files] == [("a", "/clips/a.mp4")]
        async with database.write() as connection:
            await connection.execute("DELETE FROM stash_waiting WHERE id = 'a'")
        # The child table follows the renamed parent, so a row that goes takes its files with it.
        assert await database.fetch_all("SELECT * FROM stash_waiting_files") == []
        assert await database.fetch_all("SELECT * FROM stash_groups") == []
    finally:
        await database.close()


async def test_a_library_at_the_current_version_is_left_as_it_is(tmp_path: Path) -> None:
    """A library already at the newest version runs no statement: its remembered groups and what
    waits are left exactly as they are."""
    database = Database(tmp_path / "library.sqlite3", readers=1)
    await database.connect()
    try:
        async with database.write() as connection:
            await schema.initialize(connection, 0)
            await connection.execute("DROP TABLE stash_groups")
        async with database.write() as connection:
            await schema.initialize(connection, schema.VERSION)
        tables = await database.fetch_all(
            "SELECT name FROM sqlite_master WHERE name = 'stash_groups'"
        )
        assert tables == []
    finally:
        await database.close()
