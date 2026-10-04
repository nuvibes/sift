# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Editing settings say what their numbers are and what the app calls the thing they make.

Each compression size starts at one of Discord's upload limits, and its help line says which one.
The number in that sentence is read from the preset's own default, so the two cannot disagree.
"""

from __future__ import annotations

import pytest

import sift.slices.media_edit  # noqa: F401  (registers the settings)
from sift.kernel.settings_registry import get_registered, get_retired
from sift.slices.media_edit.settings import (
    DEFAULTS,
    GIF_FORMAT_KEY,
    KEY_FOR_PRESET,
    RETIRED_GIF_FORMAT_KEY,
    Preset,
    said_size,
)

pytestmark = [pytest.mark.regression]


def _help(preset: Preset) -> str:
    setting = get_registered(KEY_FOR_PRESET[preset])
    assert setting is not None
    return setting.help


def test_the_sizes_start_at_discords_limits() -> None:
    assert DEFAULTS == {
        Preset.SMALL: 10,
        Preset.STANDARD: 50,
        Preset.LARGE: 100,
        Preset.VERY_LARGE: 1024,
    }


def test_very_large_says_one_gigabyte() -> None:
    assert _help(Preset.VERY_LARGE) == "Starts at 1 GB, Discord's limit with Nitro."


@pytest.mark.parametrize(
    ("preset", "whose"),
    [
        (Preset.SMALL, "without Nitro"),
        (Preset.STANDARD, "Nitro Basic, or at boost level 2"),
        (Preset.LARGE, "Discord's limit at boost level 3"),
        (Preset.VERY_LARGE, "with Nitro."),
    ],
)
def test_each_size_says_whose_limit_it_starts_at(preset: Preset, whose: str) -> None:
    said = _help(preset)
    assert said.startswith(f"Starts at {said_size(DEFAULTS[preset])}, Discord's ")
    assert whose in said


def test_no_two_sizes_share_a_help_line() -> None:
    assert len({_help(preset) for preset in KEY_FOR_PRESET}) == len(KEY_FOR_PRESET)


def test_the_format_setting_calls_the_result_a_gif() -> None:
    setting = get_registered(GIF_FORMAT_KEY)
    assert setting is not None
    assert setting.label == "Save GIFs as"
    assert "animation" not in setting.help.lower()


def test_the_format_is_stored_under_its_gif_word_and_the_old_one_reads_through() -> None:
    assert GIF_FORMAT_KEY == "edit.gif_format"
    retired = get_retired(RETIRED_GIF_FORMAT_KEY)
    assert retired is not None
    assert retired.into == (GIF_FORMAT_KEY,)
    assert retired.read(("webp",)) == "webp"
    assert retired.write("avif", ("webp",)) == ("avif",)
