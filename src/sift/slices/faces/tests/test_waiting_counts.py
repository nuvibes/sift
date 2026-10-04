# SPDX-License-Identifier: AGPL-3.0-or-later
"""The faces waiting in each group are a counted kind this slice registers with the kernel, and
the count is exact after every change a face can undergo."""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.kernel.access import visibility
from sift.kernel.db import Database
from sift.slices.faces.schema import FACE_BAND_KIND
from sift.slices.faces.store import Store
from sift.testing.fixtures import Actors, World, hide

pytestmark = pytest.mark.integration


async def _nothing_differs(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        found = await visibility.differences(connection)
    assert found == [], f"the stored verdict disagrees with the facts: {found[:5]}"


def test_the_slice_registered_its_kind() -> None:
    """The kind the kernel's groups wall reads is the one this slice counts, and the kernel
    brings its triggers up after this slice's tables."""
    from sift.kernel.db import registered_components

    assert any(one.kind == visibility.WAITING_FACES_KIND for one in visibility.counted())
    assert "faces" in registered_components()["visibility"].depends_on


async def test_faces_waiting_in_a_group_are_counted_exactly(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """The waiting faces of a group, per user: a group made, a face named out of it, a face
    moved to another group, a face's file concealed, and the group set aside and deleted."""
    now = 0
    for pile in ("G-one", "G-two"):
        await temp_db.execute(
            "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at)"
            " VALUES (?, 'open', x'00', 2, ?, ?)",
            (pile, now, now),
        )
    for track, asset, pile in (
        ("T-1", world.solo, "G-one"),
        ("T-2", world.twin, "G-one"),
        ("T-3", world.twin, "G-one"),
        ("T-4", world.loose, "G-two"),
    ):
        await temp_db.execute(
            "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality,"
            " pile_id, created_at) VALUES (?, ?, 0, 0, 1, 1.0, ?, ?)",
            (track, asset, pile, now),
        )
    await _nothing_differs(temp_db)
    counted = await temp_db.fetch_one(
        "SELECT permitted FROM viewer_entity_counts"
        " WHERE user_id = ? AND kind = 'pile' AND object_id = 'G-one'",
        (actors.admin.id,),
    )
    assert counted is not None and int(counted["permitted"]) == 3, "three faces, two files"
    await temp_db.execute(
        "UPDATE face_tracks SET person_id = ?, attribution = 'confirmed' WHERE id = 'T-2'",
        (world.person,),
    )
    await _nothing_differs(temp_db)
    await temp_db.execute("UPDATE face_tracks SET pile_id = 'G-two' WHERE id = 'T-1'")
    await _nothing_differs(temp_db)
    await hide(temp_db, "asset", world.twin, actors.admin.id)
    await _nothing_differs(temp_db)
    await temp_db.execute("UPDATE face_piles SET status = 'ignored' WHERE id = 'G-two'")
    await temp_db.execute("DELETE FROM face_piles WHERE id = 'G-one'")
    await _nothing_differs(temp_db)
    await temp_db.execute("DELETE FROM face_tracks WHERE id = 'T-4'")
    await _nothing_differs(temp_db)


async def test_an_ignored_duplicate_track_write_changes_nothing(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """A track written twice under one id is ignored by the engine after the BEFORE trigger has
    fired; the guard on the track's key keeps that half from running at all."""
    now = 0
    await temp_db.execute(
        "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at)"
        " VALUES ('G-one', 'open', x'00', 1, ?, ?)",
        (now, now),
    )
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality,"
        " pile_id, created_at) VALUES ('T-1', ?, 0, 0, 1, 1.0, 'G-one', ?)",
        (world.solo, now),
    )
    await _nothing_differs(temp_db)
    before = await temp_db.fetch_all(
        "SELECT user_id, kind, object_id, permitted FROM viewer_entity_counts"
        " WHERE kind = 'pile' ORDER BY user_id, object_id"
    )
    rows = await temp_db.fetch_all(
        "SELECT user_id, asset_id FROM viewer_assets WHERE asset_id = ? ORDER BY user_id",
        (world.solo,),
    )
    await temp_db.execute(
        "INSERT OR IGNORE INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in,"
        " quality, pile_id, created_at) VALUES ('T-1', ?, 0, 0, 1, 1.0, 'G-one', ?)",
        (world.solo, now),
    )
    after = await temp_db.fetch_all(
        "SELECT user_id, kind, object_id, permitted FROM viewer_entity_counts"
        " WHERE kind = 'pile' ORDER BY user_id, object_id"
    )
    assert [tuple(r) for r in after] == [tuple(r) for r in before], "the counts moved"
    still = await temp_db.fetch_all(
        "SELECT user_id, asset_id FROM viewer_assets WHERE asset_id = ? ORDER BY user_id",
        (world.solo,),
    )
    assert [tuple(r) for r in still] == [tuple(r) for r in rows], "the file's rows went"
    await _nothing_differs(temp_db)


async def test_a_persons_faces_are_counted_by_how_each_was_named(
    temp_db: Database, world: World, actors: Actors, tmp_path: Path
) -> None:
    """The People Sift can recognize wall's figures, per user: faces attached by Sift, one confirmed,
    one refused, one moved to another file's person-less state, a file concealed: the count by
    band after each, and the vault's two readings of it (shut, and revealed)."""
    assert any(one.kind == FACE_BAND_KIND for one in visibility.counted())
    store = Store(temp_db, data_dir=tmp_path)
    for track, asset in (("T-1", world.solo), ("T-2", world.twin), ("T-3", world.twin)):
        await temp_db.execute(
            "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality,"
            " person_id, attribution, created_at) VALUES (?, ?, 0, 0, 1, 1.0, ?, 'matched', 0)",
            (track, asset, world.person),
        )
    await _nothing_differs(temp_db)
    admin = actors.admin.id
    assert await store.face_bands(admin, revealed=False) == {world.person: {"matched": 3}}
    # Confirmed alone: the attribution moves and the person does not.
    await temp_db.execute("UPDATE face_tracks SET attribution = 'confirmed' WHERE id = 'T-1'")
    await _nothing_differs(temp_db)
    assert await store.face_bands(admin, revealed=False) == {
        world.person: {"matched": 2, "confirmed": 1}
    }
    # Refused: the person comes off.
    await temp_db.execute(
        "UPDATE face_tracks SET person_id = NULL, attribution = NULL WHERE id = 'T-3'"
    )
    await _nothing_differs(temp_db)
    assert await store.face_bands(admin, revealed=False) == {
        world.person: {"matched": 1, "confirmed": 1}
    }
    # The vault: shut, the concealed file's face is not counted; revealed, it is.
    await hide(temp_db, "asset", world.twin, admin)
    await _nothing_differs(temp_db)
    assert await store.face_bands(admin, revealed=False) == {world.person: {"confirmed": 1}}
    assert await store.face_bands(admin, revealed=True) == {
        world.person: {"matched": 1, "confirmed": 1}
    }
    # Every face gone: nobody is on the wall.
    await temp_db.execute("DELETE FROM face_tracks")
    await _nothing_differs(temp_db)
    assert await store.face_bands(admin, revealed=True) == {}
