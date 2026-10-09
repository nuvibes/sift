# SPDX-License-Identifier: AGPL-3.0-or-later
"""A default cover is a picture, never a GIF or a video; a thing with no picture wears its letter,
and catalog step 93 gives back every default cover the rule had put on a GIF or a video."""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from sift.kernel.access import default_covers, schema
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.testing.fixtures import World
from sift.testing.logs import uncached_log

pytestmark = pytest.mark.anyio


async def _person(database: Database) -> str:
    person_id = new_id()
    await database.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (person_id, person_id)
    )
    return person_id


async def _file(database: Database, asset_id: str, person_id: str, at: int) -> None:
    await database.execute(
        "INSERT INTO asset_people (asset_id, person_id, decided_at) VALUES (?, ?, ?)",
        (asset_id, person_id, at),
    )


async def _kind(database: Database, asset_id: str, media_type: str) -> None:
    await database.execute("UPDATE assets SET media_type = ? WHERE id = ?", (media_type, asset_id))


async def _cover(database: Database, person_id: str) -> str | None:
    row = await database.fetch_one("SELECT cover_asset_id FROM people WHERE id = ?", (person_id,))
    assert row is not None
    return None if row["cover_asset_id"] is None else str(row["cover_asset_id"])


async def test_the_first_picture_is_the_cover_and_a_gif_or_a_video_never_is(
    temp_db: Database, world: World
) -> None:
    """The world's files are videos: `loose` turned into a GIF, `twin` into a picture."""
    await _kind(temp_db, world.loose, "gif")
    await _kind(temp_db, world.twin, "image")
    person, letter = await _person(temp_db), await _person(temp_db)
    await _file(temp_db, world.solo, person, 1)
    await _file(temp_db, world.loose, person, 2)
    assert await _cover(temp_db, person) is None, "a video and a GIF wear the letter"
    await _file(temp_db, world.twin, person, 3)
    assert await _cover(temp_db, person) == world.twin
    await _file(temp_db, world.solo, letter, 1)
    assert await _cover(temp_db, letter) is None


async def test_a_file_read_again_as_another_kind_moves_the_rules_pick(
    temp_db: Database, world: World
) -> None:
    for asset_id in (world.loose, world.twin):
        await _kind(temp_db, asset_id, "image")
    person = await _person(temp_db)
    await _file(temp_db, world.loose, person, 1)
    await _file(temp_db, world.twin, person, 2)
    assert await _cover(temp_db, person) == world.loose

    await _kind(temp_db, world.loose, "gif")
    assert await _cover(temp_db, person) == world.twin
    await _kind(temp_db, world.twin, "video")
    assert await _cover(temp_db, person) is None
    await _kind(temp_db, world.loose, "image")
    assert await _cover(temp_db, person) == world.loose


async def test_step_93_gives_back_a_default_on_a_gif_or_a_video_and_never_a_chosen_one(
    temp_db: Database, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A library at catalog 92 whose rule picked videos and GIFs: each such default goes to the
    first picture filed there, or the letter; a cover somebody chose stays on its video."""
    async with temp_db.write() as connection:
        for statement in default_covers.drop_triggers():
            await connection.execute(statement)
    await _kind(temp_db, world.loose, "gif")
    await _kind(temp_db, world.twin, "image")
    pictured, letter, chosen = (
        await _person(temp_db),
        await _person(temp_db),
        await _person(temp_db),
    )
    for person in (pictured, chosen):
        await _file(temp_db, world.loose, person, 1)
        await _file(temp_db, world.twin, person, 2)
    await _file(temp_db, world.solo, letter, 1)
    for person, asset_id in ((pictured, world.loose), (letter, world.solo)):
        await temp_db.execute(
            "UPDATE people SET cover_asset_id = ?, cover_by_default = ? WHERE id = ?",
            (asset_id, asset_id, person),
        )
    await temp_db.execute(
        "UPDATE people SET cover_asset_id = ?, cover_by_default = NULL WHERE id = ?",
        (world.loose, chosen),
    )
    uncached_log(monkeypatch, default_covers)

    with capture_logs() as logs:
        async with temp_db.write() as connection:
            await schema.initialize_catalog(connection, 92)

    assert await _cover(temp_db, pictured) == world.twin
    assert (await default_covers.standing(temp_db, "person", pictured)).by_default
    assert await _cover(temp_db, letter) is None
    assert await _cover(temp_db, chosen) == world.loose
    (said,) = [one for one in logs if one["event"] == "covers.default.pictures_only"]
    assert (said["person"], said["pictured"]) == (2, 1)
    async with temp_db.write() as connection:
        again = await default_covers.pictures_only(connection)
    assert set(again.values()) == {0}
