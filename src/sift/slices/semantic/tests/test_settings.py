# SPDX-License-Identifier: AGPL-3.0-or-later
"""The share of the machine describing a library may take: pure arithmetic."""

from __future__ import annotations

import pytest

from sift.kernel.jobs.quiet_hours import WHEN_PRESS, WHEN_WORK
from sift.kernel.jobs.schedules import registered_schedules, when_key
from sift.kernel.settings_registry import get_registered, get_retired
from sift.slices.semantic.settings import ENABLED_KEY, describes_at_once


def test_describing_gets_what_recognition_left() -> None:
    """Eight workers, three of them recognition's, five for describing."""
    assert describes_at_once(workers=8, faces_at_once=3, faces_running=True) == 5


def test_describing_gets_the_whole_pool_when_recognition_is_off() -> None:
    """With recognition off, describing gets the whole pool."""
    assert describes_at_once(workers=8, faces_at_once=4, faces_running=False) == 8


def test_describing_never_stops_altogether() -> None:
    """Describing never stops altogether: it keeps one worker."""
    assert describes_at_once(workers=4, faces_at_once=4, faces_running=True) == 1


def test_a_share_larger_than_the_pool_does_not_go_negative() -> None:
    """A recognition share larger than the pool leaves describing one worker, never a negative cap."""
    assert describes_at_once(workers=2, faces_at_once=9, faces_running=True) == 1


def test_the_single_worker_machine_still_describes() -> None:
    assert describes_at_once(workers=1, faces_at_once=1, faces_running=True) == 1
    assert describes_at_once(workers=1, faces_at_once=0, faces_running=False) == 1


@pytest.mark.parametrize("workers", [1, 2, 4, 8, 16, 64])
def test_the_two_shares_never_ask_for_more_than_there_is(workers: int) -> None:
    """The two shares never exceed the pool, except at the floor of one each."""
    for faces_at_once in range(1, workers + 1):
        describing = describes_at_once(
            workers=workers, faces_at_once=faces_at_once, faces_running=True
        )
        assert describing >= 1
        assert faces_at_once + describing <= max(workers, faces_at_once + 1)


def test_describing_runs_as_files_arrive_once_somebody_turns_it_on() -> None:
    """Describing's When defaults to running as files arrive; a chosen When is kept."""
    task = registered_schedules()["smart-search"]
    declared = get_registered(ENABLED_KEY)

    assert task.when({}) == WHEN_WORK
    assert task.when({when_key("smart-search"): WHEN_PRESS}) == WHEN_PRESS
    assert get_retired(ENABLED_KEY) is None
    assert declared is not None and declared.default is False
