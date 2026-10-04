# SPDX-License-Identifier: AGPL-3.0-or-later
"""Choosing a Photo Set's cover, read back off the record. See the shelf's twin next door."""

from __future__ import annotations

import pytest

# For its side effect: registering the table the ledger is written to.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository
from sift.kernel.access.history_events import events_of_asset, events_of_entity
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.slices.photo_sets.service import PhotoSetService
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio


@pytest.fixture
def service(temp_db: Database, access: Repository) -> PhotoSetService:
    return PhotoSetService(temp_db, access)


async def test_a_sets_cover_is_on_the_set_and_on_the_picture(
    service: PhotoSetService,
    temp_db: Database,
    access: Repository,
    world: World,
    actors: Actors,
) -> None:
    made = await service.create("A Shoot")

    assert (
        await service.set_cover(made.id, world.solo, actor=Actor.user(actors.admin.id)) is not None
    )

    (on_the_set,) = await events_of_entity(temp_db, actors.admin, "photo_set", made.id)
    assert on_the_set.verb == "edited"
    assert on_the_set.object is not None and on_the_set.object.id == world.solo
    assert [one.verb for one in await events_of_asset(temp_db, actors.admin, world.solo)] == [
        "edited"
    ]


# --- what somebody THOUGHT of a Photo Set -------------------------------------------------------
#
# Not the ledger: a set hearted is one user's taste and the feed every admin reads is not where
# that belongs. It goes in `opinions`, carrying the value the upsert is about to destroy, and the
# hide is the one with most to lose, because bringing a set back clears `hidden_at` along with the
# fact that it was ever hidden.


async def _thought(database: Database, subject_id: str) -> list[tuple[str, str, object, object]]:
    """Every opinion about one thing, oldest first. `ORDER BY id` because the id is a ULID and a
    machine's wall clock can step backwards."""
    rows = await database.fetch_all(
        "SELECT subject_kind, kind, before, after FROM opinions WHERE subject_id = ? ORDER BY id",
        (subject_id,),
    )
    return [
        (str(row["subject_kind"]), str(row["kind"]), row["before"], row["after"]) for row in rows
    ]


async def test_hiding_a_set_and_bringing_it_back_both_leave_a_row(
    service: PhotoSetService, temp_db: Database, world: World, actors: Actors
) -> None:
    await service.set_vault(actors.admin, world.photo_set, vault=True)
    await service.set_vault(actors.admin, world.photo_set, vault=False)

    assert await _thought(temp_db, world.photo_set) == [
        ("photo_set", "hide", None, 1),
        ("photo_set", "hide", 1, 0),
    ]


async def test_hearting_a_set_says_what_the_heart_was(
    service: PhotoSetService, temp_db: Database, world: World, actors: Actors
) -> None:
    await service.set_favorite(actors.admin, world.photo_set, favorite=True)
    await service.set_favorite(actors.admin, world.photo_set, favorite=False)

    assert await _thought(temp_db, world.photo_set) == [
        ("photo_set", "favorite", None, 1),
        ("photo_set", "favorite", 1, 0),
    ]


async def test_rating_a_set_says_what_the_stars_were(
    service: PhotoSetService, temp_db: Database, world: World, actors: Actors
) -> None:
    await service.set_rating(actors.admin, world.photo_set, rating=3)
    await service.set_rating(actors.admin, world.photo_set, rating=10)

    assert await _thought(temp_db, world.photo_set) == [
        ("photo_set", "rating", None, 3),
        ("photo_set", "rating", 3, 10),
    ]
