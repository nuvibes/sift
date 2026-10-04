# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a new install's Playback settings start as."""

from __future__ import annotations

import pytest

from sift.kernel.settings_registry import get_registered
from sift.slices.player import CACHE_MAX_GB_KEY, DWELL_PICTURES_KEY

pytestmark = [pytest.mark.regression]


def test_a_playthrough_shows_photos_for_two_seconds_and_a_gif_once() -> None:
    setting = get_registered(DWELL_PICTURES_KEY)
    assert setting is not None
    assert setting.default is True
    assert "two seconds" in setting.help
    assert "GIF plays once" in setting.help


def test_converted_copies_may_take_five_gigabytes() -> None:
    setting = get_registered(CACHE_MAX_GB_KEY)
    assert setting is not None
    assert setting.default == 5
