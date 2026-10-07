# SPDX-License-Identifier: AGPL-3.0-or-later
"""What this feature records about the library, and what it deliberately does not.

The table here is the resumable half: which files have been described, by which model, and when.
The numbers themselves live somewhere else, in a table type that arrives with an add-on, and the
point of this file is that **boot does not touch that table at all**. An install whose SQLite
cannot load the add-on has to start normally and lose one feature, so nothing at boot may depend on
it being there.

The model revision is stored per file for a reason that does not announce itself: numbers from two
different models are not comparable, and mixing them does not fail. It returns wrong neighbours,
quietly, forever. So a file described by a model that is no longer the configured one is stale, and
this row is how that is known without re-reading anything.
"""

from __future__ import annotations

import pytest

from sift.kernel.db import Database, IntegrityError
from sift.slices.semantic.schema import COMPONENT, VERSION, initialize

pytestmark = pytest.mark.integration


async def test_the_record_of_what_has_been_described_is_made_at_boot(temp_db: Database) -> None:
    await temp_db.initialize_schema()

    assert await temp_db.schema_version(COMPONENT) == VERSION
    assert (
        await temp_db.fetch_one("SELECT name FROM sqlite_master WHERE name = 'semantic_indexed'")
        is not None
    )


async def test_the_vector_table_is_not_made_at_boot(temp_db: Database) -> None:
    """Boot must not depend on an add-on that some machines cannot load."""
    await temp_db.initialize_schema()

    assert (
        await temp_db.fetch_one("SELECT name FROM sqlite_master WHERE name = 'semantic_frames'")
        is None
    )


async def _seed_asset(database: Database, asset_id: str) -> None:
    await database.execute(
        "INSERT INTO assets (id, identity, media_type, size_bytes, added_at) "
        "VALUES (?, ?, 'image', 1, 1700000000)",
        (asset_id, f"digest-{asset_id}"),
    )


async def _mark_described(database: Database, asset_id: str, revision: str = "r1") -> None:
    await database.execute(
        "INSERT INTO semantic_indexed (asset_id, revision, frames, indexed_at) "
        "VALUES (?, ?, 3, 1700000000)",
        (asset_id, revision),
    )


async def test_bringing_the_schema_up_a_second_time_does_nothing(temp_db: Database) -> None:
    await temp_db.initialize_schema()
    await _seed_asset(temp_db, "asset1")
    await _mark_described(temp_db, "asset1")

    await temp_db.initialize_schema()

    row = await temp_db.fetch_one("SELECT revision FROM semantic_indexed WHERE asset_id = 'asset1'")
    assert row is not None
    assert row["revision"] == "r1"


async def test_a_described_file_is_forgotten_when_the_file_is(temp_db: Database) -> None:
    """The row is about an asset. When the asset goes, a row saying it was described is a claim
    about something that no longer exists."""
    await temp_db.initialize_schema()
    await _seed_asset(temp_db, "asset1")
    await _mark_described(temp_db, "asset1")

    await temp_db.execute("DELETE FROM assets WHERE id = 'asset1'")

    assert await temp_db.fetch_one("SELECT asset_id FROM semantic_indexed") is None


async def test_a_database_already_at_this_version_is_left_alone(temp_db: Database) -> None:
    """Called directly, because the kernel does not call an initializer whose version already
    matches, so this is what would happen if a later version ever did."""
    await temp_db.initialize_schema()
    await _seed_asset(temp_db, "asset1")
    await _mark_described(temp_db, "asset1")

    async with temp_db.write() as connection:
        await initialize(connection, VERSION)

    row = await temp_db.fetch_one("SELECT revision FROM semantic_indexed WHERE asset_id = 'asset1'")
    assert row is not None


async def test_a_record_cannot_name_a_file_that_does_not_exist(temp_db: Database) -> None:
    """The index is derived from the library, so a row about nothing is not a state worth having."""
    await temp_db.initialize_schema()

    with pytest.raises(IntegrityError):
        await _mark_described(temp_db, "never-existed")


async def test_an_index_described_before_the_keys_is_keyed_when_the_library_opens(
    temp_db: Database,
) -> None:
    """The step that adds the keys fills them from the frames already held."""
    from sift.slices.semantic.store import DIMENSION, VectorStore

    await temp_db.initialize_schema()
    await VectorStore(temp_db).put("clip", [(0, [1.0] + [0.0] * (DIMENSION - 1))], revision="r1")
    async with temp_db.write() as connection:
        await connection.execute("DELETE FROM semantic_frame_keys")
        await connection.execute("DELETE FROM semantic_file_keys")
        await connection.execute("DROP TABLE semantic_files")
        await connection.execute(
            "UPDATE schema_version SET version = 2 WHERE component = ?", (COMPONENT,)
        )

    await temp_db.initialize_schema()

    assert await temp_db.schema_version(COMPONENT) == VERSION
    frames = await temp_db.fetch_all("SELECT asset_id FROM semantic_frame_keys")
    files = await temp_db.fetch_all("SELECT asset_id FROM semantic_file_keys")
    assert [row["asset_id"] for row in frames] == [row["asset_id"] for row in files] == ["clip"]


async def _counts(database: Database) -> dict[str, tuple[int, int]]:
    rows = await database.fetch_all("SELECT revision, files, frames FROM semantic_counts")
    return {str(row["revision"]): (int(row["files"]), int(row["frames"])) for row in rows}


async def _unrecorded(database: Database) -> list[str]:
    rows = await database.fetch_all("SELECT asset_id FROM semantic_unrecorded ORDER BY asset_id")
    return [str(row["asset_id"]) for row in rows]


async def _keyed(database: Database, asset_id: str, revision: str, frames: int) -> None:
    async with database.write() as connection:
        await connection.execute(
            "INSERT INTO semantic_file_keys (file, asset_id, revision) VALUES (NULL, ?, ?)",
            (asset_id, revision),
        )
        for _ in range(frames):
            await connection.execute(
                "INSERT INTO semantic_frame_keys (frame, asset_id, revision) VALUES (NULL, ?, ?)",
                (asset_id, revision),
            )


async def test_the_counts_follow_every_record_and_every_frame(temp_db: Database) -> None:
    await temp_db.initialize_schema()
    for one in ("a", "b", "c"):
        await _seed_asset(temp_db, one)
    await _mark_described(temp_db, "a")
    await _mark_described(temp_db, "b")
    await _mark_described(temp_db, "c", revision="r2")
    await _keyed(temp_db, "a", "r1", 3)

    assert await _counts(temp_db) == {"r1": (2, 3), "r2": (1, 0)}

    await temp_db.execute("UPDATE semantic_indexed SET revision = 'r2' WHERE asset_id = 'b'")
    await temp_db.execute(
        "DELETE FROM semantic_frame_keys WHERE frame = (SELECT MIN(frame) FROM semantic_frame_keys)"
    )
    await temp_db.execute("DELETE FROM semantic_indexed WHERE asset_id = 'c'")

    assert await _counts(temp_db) == {"r1": (1, 2), "r2": (1, 0)}


async def test_a_file_that_leaves_with_its_numbers_held_is_kept_as_unrecorded(
    temp_db: Database,
) -> None:
    """Its record goes with it and its numbers cannot: the one row Maintenance asks about."""
    await temp_db.initialize_schema()
    for one in ("gone", "kept", "unread"):
        await _seed_asset(temp_db, one)
    await _keyed(temp_db, "unread", "r1", 1)
    await _mark_described(temp_db, "gone")
    await _keyed(temp_db, "gone", "r1", 1)
    await _mark_described(temp_db, "kept")
    await _keyed(temp_db, "kept", "r1", 1)

    assert await _unrecorded(temp_db) == ["unread"]

    await temp_db.execute("DELETE FROM assets WHERE id = 'gone'")
    await _mark_described(temp_db, "unread")
    await temp_db.execute("DELETE FROM semantic_indexed WHERE asset_id = 'kept'")
    await temp_db.execute("DELETE FROM semantic_file_keys WHERE asset_id = 'kept'")

    assert await _unrecorded(temp_db) == ["gone"]


async def test_an_index_described_before_the_counts_is_counted_when_the_library_opens(
    temp_db: Database,
) -> None:
    await temp_db.initialize_schema()
    for one in ("a", "b"):
        await _seed_asset(temp_db, one)
    await _mark_described(temp_db, "a")
    await _keyed(temp_db, "a", "r1", 2)
    await _keyed(temp_db, "b", "r1", 1)
    async with temp_db.write() as connection:
        await connection.execute("DROP TABLE semantic_counts")
        await connection.execute("DROP TABLE semantic_unrecorded")
        await connection.execute(
            "UPDATE schema_version SET version = 4 WHERE component = ?", (COMPONENT,)
        )

    await temp_db.initialize_schema()

    assert await temp_db.schema_version(COMPONENT) == VERSION
    assert await _counts(temp_db) == {"r1": (1, 3)}
    assert await _unrecorded(temp_db) == ["b"]
