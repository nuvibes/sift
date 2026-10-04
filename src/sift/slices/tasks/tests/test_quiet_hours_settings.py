# SPDX-License-Identifier: AGPL-3.0-or-later
"""Quiet hours' two clock times, checked where they arrive."""

from __future__ import annotations

import pytest

from sift.kernel.settings_registry import SettingError
from sift.slices.tasks.settings import _clock_time

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize("value", ["23:00", "00:00", "7:30", "23:59"])
def test_a_time_is_accepted_and_written_back_in_one_shape(value: str) -> None:
    stored = _clock_time(value)

    assert len(stored) == 5
    assert stored[2] == ":"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "half past six",
        "24:00",
        "12:60",
        "12",
        "12:5",
        700,
        None,
        "-1:00",
        # Characters Python calls digits and refuses to convert. `"\u00b2".isdigit()` is True while
        # `int("\u00b2")` raises, so a value checked by characters reached the conversion and came
        # back as an unhandled error instead of this refusal.
        "\u00b2:00",
        "12:\u00b2\u00b2",
        # Convertible, and still not a time somebody typed: a sign and surrounding space are things
        # `int()` reads happily and the stored shape has no room for.
        "+1:00",
        " 1:00",
        "12: 5",
    ],
)
def test_something_that_is_not_a_time_is_refused(value: object) -> None:
    """Read back by a job that runs unattended at three in the morning: a malformed value there
    would either stop the work silently or run it at the wrong time."""
    with pytest.raises(SettingError, match="expected a time"):
        _clock_time(value)
