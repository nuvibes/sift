# SPDX-License-Identifier: AGPL-3.0-or-later
"""A start checks for an update once: the scheduled run moved to now, never a second row beside it.

On a new library the schedule's own run of the check already falls due at once, so a start that
queued a check of its own would leave two rows due at the same moment, and both would run. Read
through the real scheduler and the real queue, because the fault is in what the two leave waiting
together.
"""

from __future__ import annotations

import pytest

import sift.main  # noqa: F401 (every task's schedule and setting is declared by it)
from sift.kernel import settings_registry
from sift.kernel.db import Database
from sift.kernel.jobs import JobContext, JobQueue, register_handler
from sift.kernel.jobs.clock import TaskClock
from sift.slices import update_notify
from sift.testing.fixtures import FakeClock
from sift.wiring.tasks import check_for_an_update_at_start

pytestmark = pytest.mark.integration

HOUR = 3600


async def _declared(key: str) -> object:
    """Every setting at its declared default: a new install."""
    declared = settings_registry.get_registered(key)
    return None if declared is None else declared.default


async def _day_range() -> tuple[str, str]:
    return "23:00", "07:00"


async def _nothing(context: JobContext) -> None:
    return None


async def _waiting_checks(queue: JobQueue) -> list[int | None]:
    return [one.run_after for one in await queue.waiting_unpressed(update_notify.UPDATE_CHECK)]


async def test_a_start_leaves_one_update_check_waiting_and_it_is_due_now(
    temp_db: Database, fake_clock: FakeClock
) -> None:
    await temp_db.initialize_schema()
    register_handler(update_notify.UPDATE_CHECK, _nothing, name="Checking for a new version")
    queue = JobQueue(temp_db, clock=fake_clock.now)
    clock = TaskClock(queue, read=_declared, quiet_range=_day_range, clock=fake_clock.now)
    now = int(fake_clock.now())

    # A new library: the schedule's first run, placed as a boot places it, is already due.
    await clock.reschedule("update-check")
    assert await _waiting_checks(queue) == [now]
    await check_for_an_update_at_start(clock)
    assert await _waiting_checks(queue) == [now]

    # A check an hour ago puts the schedule's run hours away; a start still checks now, once.
    await clock.ensure("update-check", since=now - HOUR, move=True)
    assert await _waiting_checks(queue) == [now - HOUR + update_notify.CHECK_INTERVAL_SECONDS]
    await check_for_an_update_at_start(clock)
    assert await _waiting_checks(queue) == [now]
