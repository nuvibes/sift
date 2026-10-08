# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stored counts move once per write: a change of many rows records what it touches and the
counts follow before the write commits, exactly; a large share's counts follow its press."""

from __future__ import annotations

from pathlib import Path

from sift.kernel.access import visibility, visibility_settled
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.testing.fixture_library import FixtureLibrary, fixture_library
from sift.testing.fixtures import Actors, World
from sift.testing.library import hidden_row

_COUNTER = (
    "CREATE TABLE folds_seen (step TEXT NOT NULL)",
    "CREATE TRIGGER folds_counted INSTEAD OF INSERT ON visibility_step_fold"
    " BEGIN INSERT INTO folds_seen (step) VALUES ('fold'); END",
)


async def _counting_folds(database: Database) -> None:
    async with database.write() as connection:
        for statement in _COUNTER:
            await connection.execute(statement)


async def _folds(database: Database) -> int:
    row = await database.fetch_one("SELECT COUNT(*) AS n FROM folds_seen")
    assert row is not None
    return int(row["n"])


async def _differences(database: Database) -> list[tuple[str, str, str | None, int | None]]:
    async with database.write() as connection:
        return await visibility.differences(connection)


async def _owed(database: Database) -> int:
    row = await database.fetch_one("SELECT COUNT(*) AS n FROM visibility_owed")
    assert row is not None
    return int(row["n"])


async def test_a_change_of_many_rows_moves_the_counts_once(
    temp_db: Database, world: World, actors: Actors
) -> None:
    await _counting_folds(temp_db)
    tag = new_id()
    async with temp_db.write() as connection:
        await connection.execute(
            "INSERT INTO tags (id, name, created_at) VALUES (?, ?, 0)", (tag, "dusk")
        )
        await connection.execute(
            "INSERT INTO asset_tags (asset_id, tag_id) SELECT id, ? FROM assets", (tag,)
        )
        # A hide inside the same write: its file is recorded once, with what the counts held.
        await connection.execute(*hidden_row("asset", world.solo, actors.guest.id))
    assert await _folds(temp_db) == 1
    assert await _owed(temp_db) == 0
    assert await _differences(temp_db) == []
    # A table the verdict does not read: its kinds alone are read, and still once per write.
    await temp_db.execute(
        "INSERT INTO loops (id, asset_id, start_ms, end_ms, created_at)"
        " SELECT lower(hex(randomblob(16))), id, 0, 1000, 0 FROM assets"
    )
    assert await _folds(temp_db) == 2
    assert await _differences(temp_db) == []
    await temp_db.execute("DELETE FROM assets WHERE id = ?", (world.twin,))
    await temp_db.execute("DELETE FROM asset_tags WHERE tag_id = ?", (tag,))
    assert await _differences(temp_db) == []


async def test_a_write_that_touches_nothing_moves_nothing(temp_db: Database, world: World) -> None:
    await _counting_folds(temp_db)
    await temp_db.execute("UPDATE users SET role = role WHERE 0")
    assert await _folds(temp_db) == 0


async def _library(tmp_path: Path) -> tuple[FixtureLibrary, Database, str, str]:
    """A library with a root one guest does not see, of more files than a share counts in its
    press: the guest and the root."""
    lib = await fixture_library(6_000, 7, tmp_path / "library.sqlite3")
    database = Database(lib.path, readers=1)
    await database.connect()
    await database.initialize_schema()
    guest = lib.guests[-1]
    unseen = await database.fetch_one(
        "SELECT l.root_id, COUNT(DISTINCT l.asset_id) AS n FROM asset_locations l"
        " WHERE NOT EXISTS (SELECT 1 FROM viewer_assets v WHERE v.user_id = ?"
        " AND v.asset_id = l.asset_id) GROUP BY l.root_id ORDER BY n DESC LIMIT 1",
        (guest,),
    )
    assert unseen is not None and unseen["n"] >= visibility_settled.OWED_FROM
    return lib, database, guest, str(unseen["root_id"])


async def _share(database: Database, root: str, guest: str) -> None:
    await database.execute(
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)"
        " VALUES (?, 'root', ?, ?, 'share', 0)",
        (new_id(), root, guest),
    )


async def test_a_large_share_owes_its_counts_and_they_follow_exactly(tmp_path: Path) -> None:
    lib, database, guest, root = await _library(tmp_path)
    try:
        await _share(database, root, guest)
        owed = await _owed(database)
        assert owed >= visibility_settled.OWED_FROM
        # The rows are the press's: only counts and totals wait.
        waiting = {what for what, *_ in await _differences(database)}
        assert waiting and waiting <= {
            "count missing",
            "count extra",
            "pair missing",
            "pair extra",
            "stats",
        }
        # Writes meanwhile: a file of the share tagged, another hidden, a third gone.
        files = [
            str(row["asset_id"])
            for row in await database.fetch_all(
                "SELECT asset_id FROM visibility_owed WHERE owed = 1 ORDER BY asset_id LIMIT 3"
            )
        ]
        await database.execute(
            "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (files[0], lib.tag)
        )
        await database.execute(*hidden_row("asset", files[1], guest))
        await database.execute("DELETE FROM assets WHERE id = ?", (files[2],))
        assert await _owed(database) < owed
        async with database.write() as connection:
            while await visibility_settled.fold_owed(connection, 500):
                pass
        assert await _owed(database) == 0
        assert await _differences(database) == []
    finally:
        await database.close()


async def test_a_share_taken_back_before_its_fold_moves_nothing(tmp_path: Path) -> None:
    _lib, database, guest, root = await _library(tmp_path)
    try:
        before = await database.fetch_all(
            "SELECT * FROM viewer_entity_counts WHERE user_id = ? ORDER BY kind, object_id",
            (guest,),
        )
        await _share(database, root, guest)
        await database.execute(
            "DELETE FROM acl_grants WHERE object_type = 'root' AND object_id = ?"
            " AND subject_user_id = ?",
            (root, guest),
        )
        # The boot folds what a stop left owed.
        async with database.write() as connection:
            await visibility.keep_true(connection)
        assert await _owed(database) == 0
        after = await database.fetch_all(
            "SELECT * FROM viewer_entity_counts WHERE user_id = ? ORDER BY kind, object_id",
            (guest,),
        )
        assert [tuple(row) for row in after] == [tuple(row) for row in before]
        assert await _differences(database) == []
    finally:
        await database.close()


async def test_a_library_at_version_seventeen_moves_its_counts_once_per_write(
    temp_db: Database, world: World
) -> None:
    """The version 18 step: the tables made and the triggers rewritten, nothing stored moved."""
    async with temp_db.write() as connection:
        await connection.execute("DROP TABLE visibility_members")
        await connection.execute("DROP TABLE visibility_owed")
        await visibility.initialize(connection, 17)
        await visibility.keep_true(connection)
    triggers = await temp_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'vis_%'"
    )
    assert {str(row["name"]) for row in triggers} == {
        name for name, _table, _ddl in visibility.triggers()
    }
    await temp_db.execute(
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (world.twin, world.tag)
    )
    assert await _differences(temp_db) == []


async def test_a_library_at_version_eighteen_keeps_a_moved_pairs_size_beside_it(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """The version 19 step: the scratch of moved pairs made again with the size and time each
    moves, and the triggers rewritten to read them, so a hide's counts still follow exactly."""
    async with temp_db.write() as connection:
        await connection.execute("DROP TABLE visibility_moved")
        await connection.execute(
            "CREATE TABLE visibility_moved (asset_id TEXT NOT NULL REFERENCES assets(id),"
            " user_id TEXT NOT NULL REFERENCES users(id), dn INTEGER NOT NULL,"
            " dc INTEGER NOT NULL, read INTEGER NOT NULL, PRIMARY KEY (asset_id, user_id))"
            " WITHOUT ROWID"
        )
        await visibility.initialize(connection, 18)
        await visibility.keep_true(connection)
    columns = await temp_db.fetch_all("SELECT name FROM pragma_table_info('visibility_moved')")
    assert {"size", "time"} <= {str(row["name"]) for row in columns}
    await temp_db.execute(*hidden_row("asset", world.twin, actors.admin.id))
    assert await _differences(temp_db) == []


async def test_a_file_one_touch_read_in_part_moves_whole_when_its_answer_moves(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """A Loop cut and its file hidden in one write: the Loop's touch reads only the kinds over
    `loops`, so the rest of the file is read when the answer moves, and every count follows."""
    async with temp_db.write() as connection:
        await connection.execute(
            "INSERT INTO loops (id, asset_id, start_ms, end_ms, name, created_by, created_at)"
            " VALUES (?, ?, 0, 1000, 'dusk', ?, 0)",
            (new_id(), world.solo, actors.admin.id),
        )
        await connection.execute(*hidden_row("asset", world.solo, actors.admin.id))
    assert await _owed(temp_db) == 0
    assert await _differences(temp_db) == []
