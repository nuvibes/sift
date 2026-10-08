# SPDX-License-Identifier: AGPL-3.0-or-later
"""The adder-up: a day per piece, from where the User started, out of the way of pressed work."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import pytest

from sift.kernel.jobs import BACKGROUND_PRIORITY, WAITED_ON_PRIORITY
from sift.kernel.jobs.queue import JobQueue
from sift.slices.insights import rollup, store
from sift.slices.insights.tests.conftest import DAY, World, at

pytestmark = pytest.mark.integration

#: Three days on, at noon: the day under test and the one after it are finished.
LATER = at(12, day=DAY + timedelta(days=2))


async def _progress(world: World) -> str | None:
    reached = await store.added_up_to(world.db, world.user)
    return None if reached is None else reached.isoformat()


async def _rows(world: World) -> list[tuple[str, str, int]]:
    rows = await world.db.fetch_all(
        "SELECT day, metric, whole FROM insight_days WHERE user_id = ? AND metric = 'viewed_ms'",
        (world.user,),
    )
    return [(row["day"], row["metric"], row["whole"]) for row in rows]


async def test_it_adds_up_one_finished_day_per_piece_from_the_first_fact(world: World) -> None:
    await world.run("UPDATE users SET created_at = ?", (at(12, day=DAY + timedelta(days=1)),))
    clip = await world.add_file("video")
    await world.sit(clip, at(10), 60_000)

    assert await rollup.add_up_one_day(world.db, now=LATER) is True
    assert await _progress(world) == (DAY - timedelta(days=1)).isoformat()
    assert await rollup.add_up_one_day(world.db, now=LATER) is True
    assert await _progress(world) == DAY.isoformat()
    assert await _rows(world) == [(DAY.isoformat(), "viewed_ms", 60_000)]
    # The next day had nothing in it: one read, no rows, and the progress still moves.
    assert await rollup.add_up_one_day(world.db, now=LATER) is True
    assert await _progress(world) == (DAY + timedelta(days=1)).isoformat()
    assert await rollup.add_up_one_day(world.db, now=LATER) is False


async def test_a_day_is_not_finished_until_an_hour_past_its_midnight(world: World) -> None:
    assert rollup.last_finished_day(at(0, 30, day=DAY)) == DAY - timedelta(days=2)
    assert rollup.last_finished_day(at(1, 1, day=DAY)) == DAY - timedelta(days=1)


async def test_it_steps_aside_while_pressed_work_is_waiting(world: World) -> None:
    async def enqueue(priority: int) -> None:
        await world.run(
            "INSERT INTO jobs (id, type, state, priority, payload, created_at, updated_at)"
            " VALUES (?, 'scan', 'queued', ?, '{}', 1, 1)",
            (f"job-{priority}", priority),
        )

    # The question is the queue's own (`JobQueue.somebody_waiting`, beside its claim): the helper
    # is handed that method and asks it before every piece.
    queue = JobQueue(world.db)
    await enqueue(BACKGROUND_PRIORITY)
    assert await queue.somebody_waiting() is False
    await enqueue(WAITED_ON_PRIORITY)
    assert await queue.somebody_waiting() is True


async def test_the_loop_takes_no_piece_while_somebody_waits(world: World) -> None:
    asked: list[bool] = []
    stop = asyncio.Event()

    async def waiting() -> bool:
        asked.append(True)
        if len(asked) == 3:
            stop.set()
        return True

    await rollup.keep_the_days_added_up(
        world.db, stop, somebody_waiting=waiting, interval=0.01, catch_up=0.01
    )
    assert len(asked) == 3
    assert await _progress(world) is None


async def test_a_stale_split_is_written_down_in_an_idle_piece(world: World) -> None:
    clip = await world.add_file("video")
    await world.sit(clip, at(10), 60_000)
    stamp = await store.stamp_of(world.db, world.user)
    counted = await store.count_day(world.db.fetch_all, world.user, DAY)
    async with world.db.write() as connection:
        await store.write_day(connection, world.user, DAY, counted, stamp)
    await world.set_hidden(clip, True)
    await world.bump_stamp()

    assert await rollup.split_one_stale_day(world.db) is True
    rows = await world.db.fetch_all(
        "SELECT hidden, split_at FROM insight_days WHERE user_id = ? AND metric = 'viewed_ms'",
        (world.user,),
    )
    assert [(row["hidden"], row["split_at"]) for row in rows] == [(60_000, stamp + 1)]
    assert await rollup.split_one_stale_day(world.db) is False


async def test_a_stale_split_is_written_down_even_while_somebody_waits(world: World) -> None:
    """Pressed work holds back the adding-up, never the split: a stale split is paid for by every
    reader until it is written down, and a queue of identify jobs lasts hours."""
    clip = await world.add_file("video")
    await world.sit(clip, at(10), 60_000)
    stamp = await store.stamp_of(world.db, world.user)
    counted = await store.count_day(world.db.fetch_all, world.user, DAY)
    async with world.db.write() as connection:
        await store.write_day(connection, world.user, DAY, counted, stamp)
    await world.set_hidden(clip, True)
    await world.bump_stamp()
    asked: list[bool] = []
    stop = asyncio.Event()

    async def waiting() -> bool:
        asked.append(True)
        if len(asked) == 3:
            stop.set()
        return True

    await rollup.keep_the_days_added_up(
        world.db, stop, somebody_waiting=waiting, interval=0.01, catch_up=0.01
    )
    rows = await world.db.fetch_all(
        "SELECT hidden, split_at FROM insight_days WHERE user_id = ? AND metric = 'viewed_ms'",
        (world.user,),
    )
    assert [(row["hidden"], row["split_at"]) for row in rows] == [(60_000, stamp + 1)]
    assert await _progress(world) == DAY.isoformat()


async def test_the_makers_are_called_once_the_user_has_caught_up(world: World) -> None:
    await world.run("UPDATE users SET created_at = ?", (at(12),))
    called: list[tuple[str, str]] = []

    async def make(user_id: str, day: object) -> None:
        called.append((user_id, str(day)))

    async def broken(user_id: str, day: object) -> None:
        raise RuntimeError("a maker that fails")

    makers = (broken, make)
    while await rollup.add_up_one_day(world.db, now=LATER, after_day=makers):
        pass
    # Two days were added up (the day and the one after it); the makers ran after the last only,
    # and a failing maker did not stop the next one.
    assert called == [(world.user, (DAY + timedelta(days=1)).isoformat())]


async def test_an_idle_piece_adds_a_day_up_first_and_splits_only_once_nobody_is_behind(
    world: World,
) -> None:
    await world.run("UPDATE users SET created_at = ?", (at(12),))
    clip = await world.add_file("video")
    await world.sit(clip, at(10), 60_000)
    assert await rollup.one_piece(world.db, now=LATER) is True
    assert await _progress(world) is not None
    while await rollup.add_up_one_day(world.db, now=LATER):
        pass
    await world.set_hidden(clip, True)
    await world.bump_stamp()
    # Nobody is behind, so the piece is the stale split, and then there is nothing left.
    assert await rollup.one_piece(world.db, now=LATER) is True
    rows = await world.db.fetch_all(
        "SELECT hidden FROM insight_days WHERE user_id = ? AND metric = 'viewed_ms'",
        (world.user,),
    )
    assert [row["hidden"] for row in rows] == [60_000]
    assert await rollup.one_piece(world.db, now=LATER) is False


async def test_the_loop_takes_a_piece_while_nobody_waits(world: World) -> None:
    """A User made just now: the first piece meets them and writes down where their adding-up
    starts, and the next finds nobody behind and nothing stale, so it takes nothing."""
    await world.run("UPDATE users SET created_at = ?", (int(time.time()),))
    asked: list[bool] = []
    stop = asyncio.Event()

    async def waiting() -> bool:
        asked.append(True)
        if len(asked) == 3:
            stop.set()
        return False

    await rollup.keep_the_days_added_up(
        world.db, stop, somebody_waiting=waiting, interval=0.01, catch_up=0.01
    )
    assert await _progress(world) is not None
    assert await rollup.one_piece(world.db) is False


async def test_a_failing_piece_is_logged_and_the_helper_keeps_going(world: World) -> None:
    asked: list[bool] = []
    stop = asyncio.Event()

    async def waiting() -> bool:
        asked.append(True)
        if len(asked) == 1:
            raise RuntimeError("the queue could not be asked")
        stop.set()
        return True

    await rollup.keep_the_days_added_up(
        world.db, stop, somebody_waiting=waiting, interval=0.01, catch_up=0.01
    )
    assert len(asked) == 2


async def test_a_stop_during_the_pause_ends_the_helper_without_another_piece(
    world: World,
) -> None:
    asked: list[bool] = []

    async def waiting() -> bool:
        asked.append(True)
        return False

    stop = asyncio.Event()
    helper = asyncio.create_task(
        rollup.keep_the_days_added_up(world.db, stop, somebody_waiting=waiting, interval=60)
    )
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(helper, timeout=5)
    assert asked == []


# --- a day that waits, and a day added up again --------------------------------------------------

NEXT = DAY + timedelta(days=1)


async def _at_progress(world: World, day: str) -> None:
    async with world.db.write() as connection:
        await store.start_progress(connection, world.user, DAY.fromisoformat(day))


async def _dirty(world: World) -> str | None:
    (row,) = await world.db.fetch_all(
        "SELECT dirty_from FROM insight_progress WHERE user_id = ?", (world.user,)
    )
    return None if row["dirty_from"] is None else str(row["dirty_from"])


async def test_a_finished_day_waits_while_its_evening_is_still_going(world: World) -> None:
    """A sitting from 23:50 that goes on reporting for a day and more: the day waits at 01:40,
    and is added up as it stands once a day has passed since its end, still reporting."""
    clip = await world.add_file("video", length_ms=4 * 3_600_000)
    await _at_progress(world, (DAY - timedelta(days=1)).isoformat())
    await world.sit(clip, at(23, 50), 25 * 3_600_000)
    assert not await rollup.add_up_one_day(world.db, now=at(1, 40, NEXT))
    assert await _progress(world) == (DAY - timedelta(days=1)).isoformat()
    _, end = store.day_bounds(DAY)
    assert await rollup.add_up_one_day(world.db, now=end + rollup.WAIT_AT_MOST_SECONDS)
    assert await _progress(world) == DAY.isoformat()


async def test_a_quiet_day_is_added_up_without_waiting(world: World) -> None:
    clip = await world.add_file("video")
    await _at_progress(world, (DAY - timedelta(days=1)).isoformat())
    await world.sit(clip, at(20), 60_000)
    assert await rollup.add_up_one_day(world.db, now=at(1, 40, NEXT))
    assert await _progress(world) == DAY.isoformat()


async def test_a_sitting_reported_after_the_cut_adds_its_day_up_again(world: World) -> None:
    """A sitting written on a day already added up marks that day; one on a day not reached yet
    marks nothing. The day is added up again with it, and then nothing is left to do."""
    clip = await world.add_file("video")
    await _at_progress(world, DAY.isoformat())
    await world.sit(clip, at(10, 0, NEXT), 60_000)
    assert await _dirty(world) is None
    await world.sit(clip, at(10), 120_000)
    assert await _dirty(world) == DAY.isoformat()
    assert await rollup.one_piece(world.db, now=at(0, 30, NEXT))
    assert await _rows(world) == [(DAY.isoformat(), "viewed_ms", 120_000)]
    assert await _dirty(world) is None and await _progress(world) == DAY.isoformat()


async def test_a_mark_landing_while_a_day_is_added_up_again_is_kept(world: World) -> None:
    clip = await world.add_file("video")
    await _at_progress(world, DAY.isoformat())
    await world.sit(clip, at(10), 120_000)
    stale_mark = 0
    async with world.db.write() as connection:
        await store.write_day_again(connection, world.user, DAY, [], 0, stale_mark)
    assert await _dirty(world) == DAY.isoformat()


async def test_a_run_finishing_after_the_cut_marks_the_day_it_began(world: World) -> None:
    await _at_progress(world, DAY.isoformat())
    await world.run(
        "INSERT INTO work_runs (id, family, started_at, updated_at, worker_ms, files)"
        " VALUES ('r-1', 'scan', ?, ?, 1000, '{}')",
        (at(23), at(23)),
    )
    assert await _dirty(world) is None
    await world.run("UPDATE work_runs SET finished_at = ? WHERE id = 'r-1'", (at(2, 0, NEXT),))
    assert await _dirty(world) == DAY.isoformat()
