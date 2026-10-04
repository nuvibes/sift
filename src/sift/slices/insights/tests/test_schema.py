# SPDX-License-Identifier: AGPL-3.0-or-later
"""The three tables, and that every row about a User goes when the User does."""

from __future__ import annotations

import pytest

from sift.kernel.db import registered_components
from sift.slices.insights import schema, store
from sift.slices.insights.store import DayRow
from sift.slices.insights.tests.conftest import DAY, World

pytestmark = pytest.mark.integration


def test_the_component_is_registered_after_the_users() -> None:
    component = registered_components()[schema.COMPONENT]
    assert component.version == schema.VERSION and "identity" in component.depends_on


async def test_a_user_removed_takes_every_insights_row_with_them(world: World) -> None:
    async with world.db.write() as connection:
        await store.write_day(
            connection, world.user, DAY, [DayRow(DAY.isoformat(), "sittings", "", 3, 1)], 0
        )
    await store.write_recap(world.db, world.user, "week:2026-W11", "[]")
    await world.run("DELETE FROM users WHERE id = ?", (world.user,))
    left = await world.db.fetch_all(
        "SELECT (SELECT COUNT(*) FROM insight_days) + (SELECT COUNT(*) FROM insight_progress)"
        " + (SELECT COUNT(*) FROM recaps) AS n"
    )
    assert left[0]["n"] == 0


async def test_a_database_already_at_this_version_is_left_as_it_is(world: World) -> None:
    async with world.db.write() as connection:
        await store.start_progress(connection, world.user, DAY)
        await schema.initialize(connection, schema.VERSION)
    assert await store.added_up_to(world.db, world.user) == DAY


async def test_version_three_adds_every_day_up_again(world: World) -> None:
    """Version 3 keyed `sittings:file` by kind, so a library from before forgets how far each User
    was added up and the helper counts every day again from the first."""
    async with world.db.write() as connection:
        await store.start_progress(connection, world.user, DAY)
        await schema.initialize(connection, 2)
    assert await store.added_up_to(world.db, world.user) is None


async def test_version_four_adds_every_day_up_again(world: World) -> None:
    """Version 4 shares a sitting's time between the hours it ran through and counts the files
    rated each day, so every day is counted again by the audited statements."""
    async with world.db.write() as connection:
        await store.start_progress(connection, world.user, DAY)
        await schema.initialize(connection, 3)
    assert await store.added_up_to(world.db, world.user) is None


async def test_version_five_adds_every_day_up_again(world: World) -> None:
    """Version 5 adds the time spent on each song's files (`viewed_ms:song`), a figure every day
    can hold, so every day is counted again with it."""
    async with world.db.write() as connection:
        await store.start_progress(connection, world.user, DAY)
        await schema.initialize(connection, 4)
    assert await store.added_up_to(world.db, world.user) is None
