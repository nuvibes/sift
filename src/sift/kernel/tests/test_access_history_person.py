# SPDX-License-Identifier: AGPL-3.0-or-later
"""A person a stash-box made names the box, and a person's thread draws without the face tables."""

from __future__ import annotations

import pytest

# Imported for its side effect: registering the stash-boxes' tables.
import sift.slices.stash_boxes.schema  # noqa: F401
from sift.kernel.access.history import Actor
from sift.kernel.access.history_person import history_count_of_person, history_of_person
from sift.kernel.db import Database
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio

BOX = "01HX0000000000000000000901"


async def test_a_person_a_box_made_is_said_to_be_made_by_that_box(
    temp_db: Database, world: World, actors: Actors
) -> None:
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, 'StashDB', ?, 0)",
        (BOX, "https://stash-box.invalid/graphql"),
    )
    await temp_db.execute(
        "UPDATE people SET created_by_kind = 'box', created_by_box_id = ? WHERE id = ?",
        (BOX, world.person),
    )
    # A process that never registered the faces feature has no references to read.
    await temp_db.execute("DROP TABLE IF EXISTS face_references")

    events = await history_of_person(temp_db, actors.admin, world.person)

    assert [one.actor_name for one in events if one.actor is Actor.STASH_BOX] == ["StashDB"]
    assert await history_count_of_person(temp_db, actors.admin, world.person) == len(events)
