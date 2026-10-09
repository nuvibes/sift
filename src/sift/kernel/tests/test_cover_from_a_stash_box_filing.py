# SPDX-License-Identifier: AGPL-3.0-or-later
"""The catalog step that gives the people a stash-box filed a picture, where they had none.

A library written before the stash-box's filing gave a picture, as every other filing Sift makes
does, holds people drawn as letters beside files of their own. This step fills that gap for such a
library, and nothing else.
"""

from __future__ import annotations

import pytest

import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import default_covers, schema
from sift.kernel.access.catalog import by_sift, by_user, create_person_on
from sift.kernel.db import Database
from sift.kernel.vocabulary import VIA_STASH
from sift.testing.fixtures import World

pytestmark = pytest.mark.anyio


async def _cover(database: Database, person_id: str) -> tuple[str | None, str | None]:
    row = await database.fetch_one(
        "SELECT cover_asset_id, cover_upload_id FROM people WHERE id = ?", (person_id,)
    )
    assert row is not None
    return row["cover_asset_id"], row["cover_upload_id"]


async def test_the_step_gives_a_stash_box_s_people_their_first_file_and_nothing_else(
    temp_db: Database, world: World
) -> None:
    # A library at the version before the step: the rule that gives every kind its first file's
    # picture (catalog v75, `default_covers`) is not there yet, so a filing gives nothing. Its
    # files are pictures, the only kind a default cover is.
    await temp_db.execute("UPDATE assets SET media_type = 'image'")
    async with temp_db.write() as connection:
        for statement in default_covers.drop_triggers():
            await connection.execute(statement)
    async with temp_db.write() as connection:
        brought = await create_person_on(connection, "Wren Halloway", made=by_sift(VIA_STASH))
        chosen = await create_person_on(connection, "Esme Wrenfield", made=by_sift(VIA_STASH))
        by_hand = await create_person_on(connection, "Nerith Reyd", made=by_user(None))
    assert brought is not None and chosen is not None and by_hand is not None
    await temp_db.execute("UPDATE people SET cover_upload_id = 'an-upload' WHERE id = ?", (chosen,))
    for asset_id, person_id, source, decided_at in (
        (world.twin, brought, "stash_box", 20),
        (world.loose, brought, "stash_box", 10),
        (world.solo, brought, None, 1),
        (world.loose, chosen, "stash_box", 10),
        (world.loose, by_hand, None, 10),
    ):
        await temp_db.execute(
            "INSERT INTO asset_people (asset_id, person_id, source, decided_at)"
            " VALUES (?, ?, ?, ?)",
            (asset_id, person_id, source, decided_at),
        )

    async with temp_db.write() as connection:
        await schema.initialize_catalog(connection, 73)

    # The first file the stash-box filed them on, not the one somebody filed by hand earlier.
    assert await _cover(temp_db, brought) == (world.loose, None)
    # An uploaded picture is a picture: it stays, and no file is put beside it.
    assert await _cover(temp_db, chosen) == (None, "an-upload")
    # A person filed by hand only is not this step's to decide; the rule after it gives them the
    # first file they have.
    assert await _cover(temp_db, by_hand) == (world.loose, None)
