# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Theater opens as on a new install."""

from __future__ import annotations

import pytest

from sift.kernel.settings_registry import get_registered
from sift.slices.theater import LAYOUT_KEY

pytestmark = [pytest.mark.regression]


def test_theater_opens_three_across() -> None:
    setting = get_registered(LAYOUT_KEY)
    assert setting is not None
    assert setting.default == "side_by_side_by_side"
    assert setting.choice_labels is not None and setting.choices is not None
    assert (
        dict(zip(setting.choices, setting.choice_labels, strict=True))[setting.default]
        == "Grid 1x3"
    )
