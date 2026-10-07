# SPDX-License-Identifier: AGPL-3.0-or-later
"""The end of a file takes its name off the reference pictures filed from it, and nothing more.

A reference outlives the appearance it was cut from (that is the point of filing one), so the
row stays. What goes is `asset_id`, which named the file so a rescan could hand the reference on;
with the file gone it names nothing, and the gate in tests/gates/test_every_store_lets_go.py
refuses an asset id nothing clears.
"""

from __future__ import annotations

import pytest

from sift.kernel.content import backlog
from sift.kernel.db import Database
from sift.kernel.forgetting import declared_tables, registered_forgettings
from sift.slices.faces import forgetting
from sift.slices.faces import schema as faces_schema

pytestmark = pytest.mark.regression

_EPOCH = 1_700_000_000_000

_GONE = "01HX0000000000000000000750"
_KEPT = "01HX0000000000000000000751"
_PERSON = "01HX0000000000000000000752"
_TRACK = "01HX0000000000000000000753"


async def _library(database: Database) -> None:
    async with database.write() as connection:
        for statement in backlog.TABLES:
            await connection.execute(statement)
        await connection.execute(
            "CREATE TABLE assets (id TEXT PRIMARY KEY, identity TEXT NOT NULL UNIQUE, "
            "media_type TEXT NOT NULL, added_at INTEGER NOT NULL)"
        )
        await connection.execute(
            "CREATE TABLE people (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "created_at INTEGER NOT NULL)"
        )
        await faces_schema.initialize(connection, on_disk=0)
        for asset in (_GONE, _KEPT):
            await connection.execute(
                "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', ?)",
                (asset, asset, _EPOCH),
            )
        await connection.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, 'Marisol Vane', ?)",
            (_PERSON, _EPOCH),
        )
        references: tuple[tuple[str, str | None], ...] = (
            ("r-gone", _GONE),
            ("r-kept", _KEPT),
            ("r-pack", None),
        )
        for reference, filed_from in references:
            await connection.execute(
                "INSERT INTO face_references (id, person_id, crop_path, crop_digest, embedding, "
                "quality, origin, recognizer, created_at, track_id, asset_id) "
                "VALUES (?, ?, 'r.jpg', ?, X'00', 0.9, 'confirmed', 'w600k-r50', ?, ?, ?)",
                (reference, _PERSON, reference, _EPOCH, _TRACK if filed_from else None, filed_from),
            )


async def _references(database: Database) -> dict[str, tuple[object, object]]:
    rows = await database.fetch_all("SELECT id, track_id, asset_id FROM face_references")
    return {str(row["id"]): (row["track_id"], row["asset_id"]) for row in rows}


def test_the_references_table_is_somebody_s_job() -> None:
    """The registration the gate reads, under the name the fire point walks."""
    assert declared_tables()["face_references"] == forgetting.ForgetReferenceFiles.name
    assert forgetting.ForgetReferenceFiles.name in registered_forgettings()


async def test_a_deleted_file_leaves_its_references_behind_without_its_id(
    temp_db: Database,
) -> None:
    await _library(temp_db)
    await temp_db.execute("DELETE FROM assets WHERE id = ?", (_GONE,))

    entry = registered_forgettings()[forgetting.ForgetReferenceFiles.name]
    cleared = await entry.build(temp_db).forget([_GONE])

    assert cleared == 1
    # The row stays, its appearance id stays (the boot repair reads a NULL there as "orphaned by
    # a rescan" and would re-pin it elsewhere), and only the file's id goes.
    assert await _references(temp_db) == {
        "r-gone": (_TRACK, None),
        "r-kept": (_TRACK, _KEPT),
        "r-pack": (None, None),
    }


async def test_only_the_ids_handed_over_lose_their_claim(temp_db: Database) -> None:
    """The sweep clears exactly the ids it is given and no other reference's file."""
    await _library(temp_db)

    entry = registered_forgettings()[forgetting.ForgetReferenceFiles.name]
    cleared = await entry.build(temp_db).forget([_GONE])

    assert cleared == 1
    assert (await _references(temp_db))["r-kept"] == (_TRACK, _KEPT)
