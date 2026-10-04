# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a person's height is read on a new install."""

from __future__ import annotations

import pytest

from sift.kernel.settings_registry import get_registered
from sift.slices.records import UNITS, UNITS_KEY

pytestmark = [pytest.mark.regression]


def test_heights_are_read_in_feet_and_inches_until_somebody_chooses_centimeters() -> None:
    setting = get_registered(UNITS_KEY)
    assert setting is not None
    assert setting.default == "imperial"
    assert setting.default in UNITS
