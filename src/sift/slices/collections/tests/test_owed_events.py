# SPDX-License-Identifier: AGPL-3.0-or-later
"""Choosing a shelf's cover, read back off the record.

An act that changes what the library IS, so it is written down with the user who took it. The
column keeps no history of what it held, so without the record the picture somebody chose would be
invisible the moment the next one replaced it.
"""

from __future__ import annotations

import pytest

# For its side effect: registering the table the ledger is written to.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository
from sift.kernel.access.history_events import events_of_asset, events_of_entity
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor
from sift.slices.collections.service import CollectionService
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio


@pytest.fixture
def service(temp_db: Database, access: Repository) -> CollectionService:
    return CollectionService(temp_db, access)


async def test_a_shelfs_cover_is_on_the_shelf_and_on_the_picture(
    service: CollectionService,
    temp_db: Database,
    access: Repository,
    world: World,
    actors: Actors,
) -> None:
    """Both sides of one act. The shelf is the subject and the still is the object, so the file's
    own History says it was chosen as a cover, which it reaches through the object-side read."""
    shelf = new_id()
    await temp_db.execute(
        "INSERT INTO collections (id, name, created_at) VALUES (?, 'Shortlist', 0)", (shelf,)
    )

    assert await service.set_cover(shelf, world.solo, actor=Actor.user(actors.admin.id)) is not None

    (on_the_shelf,) = await events_of_entity(temp_db, actors.admin, "collection", shelf)
    assert on_the_shelf.verb == "edited"
    assert on_the_shelf.object is not None and on_the_shelf.object.id == world.solo
    assert [one.verb for one in await events_of_asset(temp_db, actors.admin, world.solo)] == [
        "edited"
    ]


# --- what somebody THOUGHT of a shelf -----------------------------------------------------------
#
# Not the ledger and never the ledger: a shelf hearted is one user's taste, and the feed every
# admin reads is not where that belongs. It goes in `opinions` beside the file's own, carrying the
# value the upsert is about to destroy, and `hidden_at` is the one with most to lose, because
# bringing a shelf back clears it along with the fact that it was ever hidden.


async def _thought(database: Database, subject_id: str) -> list[tuple[str, str, object, object]]:
    """Every opinion about one thing, oldest first. `ORDER BY id` because the id is a ULID and a
    wall clock can step backwards."""
    rows = await database.fetch_all(
        "SELECT subject_kind, kind, before, after FROM opinions WHERE subject_id = ? ORDER BY id",
        (subject_id,),
    )
    return [
        (str(row["subject_kind"]), str(row["kind"]), row["before"], row["after"]) for row in rows
    ]


async def test_hiding_a_shelf_and_bringing_it_back_both_leave_a_row(
    service: CollectionService, temp_db: Database, world: World, actors: Actors
) -> None:
    await service.set_vault(actors.admin, world.collection, vault=True)
    await service.set_vault(actors.admin, world.collection, vault=False)

    assert await _thought(temp_db, world.collection) == [
        ("collection", "hide", None, 1),
        ("collection", "hide", 1, 0),
    ]


async def test_hearting_a_shelf_says_what_the_heart_was(
    service: CollectionService, temp_db: Database, world: World, actors: Actors
) -> None:
    await service.set_favorite(actors.admin, world.collection, favorite=True)
    await service.set_favorite(actors.admin, world.collection, favorite=False)

    assert await _thought(temp_db, world.collection) == [
        ("collection", "favorite", None, 1),
        ("collection", "favorite", 1, 0),
    ]


async def test_rating_a_shelf_says_what_the_stars_were(
    service: CollectionService, temp_db: Database, world: World, actors: Actors
) -> None:
    await service.set_rating(actors.admin, world.collection, rating=8)
    await service.set_rating(actors.admin, world.collection, rating=None)

    assert await _thought(temp_db, world.collection) == [
        ("collection", "rating", None, 8),
        ("collection", "rating", 8, None),
    ]
