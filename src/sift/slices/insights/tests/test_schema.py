# SPDX-License-Identifier: AGPL-3.0-or-later
"""The three tables, and that every row about a User goes when the User does."""

from __future__ import annotations

import pytest

from sift.kernel.db import registered_components
from sift.slices.insights import metrics, schema, store
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


async def test_version_seven_adds_every_day_up_again_and_dates_the_recaps_before_it(
    world: World,
) -> None:
    """Version 7 corrects the statements: every day is counted again, and a recap made before it
    says so (`metrics_version` 6), so it can be made again once and only once."""
    made = await store.write_recap(world.db, world.user, "week:2026-W11", "[]")
    assert made.metrics_version == metrics.METRICS_VERSION
    await world.run("UPDATE recaps SET metrics_version = 0")
    async with world.db.write() as connection:
        await store.start_progress(connection, world.user, DAY)
        await schema.initialize(connection, 6)
    assert await store.added_up_to(world.db, world.user) is None
    (behind,) = await store.recaps_behind(world.db, world.user)
    assert behind.metrics_version == 6
    assert await store.remake_recap(world.db, world.user, behind.id, "[1]")
    assert not await store.remake_recap(world.db, world.user, behind.id, "[2]")
    again = await store.recap(world.db, world.user, behind.id)
    assert again is not None and (again.body, again.metrics_version) == ("[1]", 7)
    assert await store.recaps_behind(world.db, world.user) == []


async def _departure(world: World, asset_id: str) -> tuple[int, list[str]]:
    (row,) = await world.db.fetch_all(
        "SELECT viewers_known FROM file_departures WHERE asset_id = ?", (asset_id,)
    )
    seen = await world.db.fetch_all(
        "SELECT user_id FROM file_departure_viewers WHERE asset_id = ?", (asset_id,)
    )
    return int(row["viewers_known"]), [str(one["user_id"]) for one in seen]


async def test_a_departure_never_claims_viewers_it_could_not_read(world: World) -> None:
    """SQLite runs a delete's triggers newest first, and the verdict's own trigger takes the file's
    verdict rows away. Made newer than the departure's (as a later verdict step does), it runs
    first: the departure then says its viewers are unknown and counts for an admin, rather than
    keeping nobody. The version 7 step makes the departure's trigger the newer again, and marks
    the departures kept with nobody as unknown."""
    first = await world.add_file("video")
    second = await world.add_file("video")
    (made,) = await world.db.fetch_all(
        "SELECT sql FROM sqlite_master WHERE name = 'vis_assets_delete_before'"
    )
    await world.run("DROP TRIGGER vis_assets_delete_before")
    await world.run(str(made["sql"]))
    await world.run("DELETE FROM assets WHERE id = ?", (first,))
    assert await _departure(world, first) == (0, [])
    (ended,) = await world.db.fetch_all(
        "SELECT ended_at FROM file_departures WHERE asset_id = ?", (first,)
    )
    params = {"user": world.user, "start": ended["ended_at"], "end": ended["ended_at"] + 1}
    (removed,) = await metrics.rows_of("files_removed", world.db.fetch_all, params)
    assert removed["whole"] == 1
    await world.run("UPDATE file_departures SET viewers_known = 1 WHERE asset_id = ?", (first,))
    async with world.db.write() as connection:
        await schema.initialize(connection, 6)
    assert await _departure(world, first) == (0, [])
    await world.run("DELETE FROM assets WHERE id = ?", (second,))
    assert await _departure(world, second) == (1, [world.user])
