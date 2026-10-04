# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a tile carries on a new install: a quiet tile, with the word GIF and a video's length
always drawn and every other mark waiting for the pointer."""

from __future__ import annotations

import pytest

from sift.kernel.settings_registry import get_registered
from sift.slices.browse.tile_marks import (
    ALWAYS,
    DURATION_KEY,
    GIF_KEY,
    ON_HOVER,
    TILE_MARKS,
)

pytestmark = [pytest.mark.regression]


def test_only_the_gif_word_and_the_length_are_always_drawn() -> None:
    always = {DURATION_KEY, GIF_KEY}
    for mark in TILE_MARKS:
        setting = get_registered(mark.key)
        assert setting is not None
        assert setting.default == (ALWAYS if mark.key in always else ON_HOVER), mark.key
