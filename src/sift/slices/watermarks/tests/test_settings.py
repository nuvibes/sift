# SPDX-License-Identifier: AGPL-3.0-or-later
"""When reading watermarks runs, and the switch in front of it."""

from __future__ import annotations

import pytest

from sift.kernel.jobs.quiet_hours import WHEN_PRESS, WHEN_WORK
from sift.kernel.jobs.schedules import registered_schedules, when_key
from sift.kernel.settings_registry import get_registered, get_retired
from sift.slices.watermarks.settings import ENABLED_KEY

pytestmark = pytest.mark.unit


def test_reading_runs_as_files_arrive_once_somebody_turns_it_on() -> None:
    """The When a value never written reads as. The switch in front of it starts off and is never
    read through the When; a When somebody chose is stored and stays what they chose."""
    task = registered_schedules()["watermarks"]
    declared = get_registered(ENABLED_KEY)

    assert task.when({}) == WHEN_WORK
    assert task.when({when_key("watermarks"): WHEN_PRESS}) == WHEN_PRESS
    assert get_retired(ENABLED_KEY) is None
    assert declared is not None and declared.default is False
