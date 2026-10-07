# SPDX-License-Identifier: AGPL-3.0-or-later
"""A number setting whose zero means something other than an amount says so in words.

A box reading "Skip files smaller than 0 MB" reads as a size, when it means there is no minimum at
all; "Lock Hidden after 0 min" reads as locking immediately, when it means never. So every whole-number
setting that accepts zero either declares `automatic_label` (the word the screen draws in place of
the zero, and the word the activity line says) or is named below as one whose zero is a real
amount. A new setting that accepts zero has to take one side or the other.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import sift.slices
from sift.kernel.settings_registry import Setting, registered_settings

pytestmark = pytest.mark.unit

#: Settings whose zero is an amount, read as one: no pause between requests, no retries, lengths
#: that must match exactly. Each is a number a person can read at face value.
ZERO_IS_AN_AMOUNT = frozenset(
    {
        "download.pace_ms",
        "download.retries",
        "stash_boxes.duration_tolerance_s",
    }
)


def _load_every_slice() -> None:
    """Import every slice, which is what fills the registry."""
    for module in pkgutil.iter_modules(sift.slices.__path__):
        importlib.import_module(f"sift.slices.{module.name}")


def _takes_zero(setting: Setting) -> bool:
    """A whole number drawn as a number box that accepts zero."""
    default = setting.default
    if isinstance(default, bool) or not isinstance(default, int):
        return False
    if setting.choices is not None or setting.unit == "%":
        return False
    return setting.minimum is None or setting.minimum <= 0


def test_every_zero_that_is_not_an_amount_has_its_word() -> None:
    _load_every_slice()
    unnamed = sorted(
        key
        for key, setting in registered_settings().items()
        if _takes_zero(setting) and not setting.automatic_label and key not in ZERO_IS_AN_AMOUNT
    )
    assert unnamed == [], (
        "these accept zero and draw it as a bare 0; declare automatic_label, or name them in "
        f"ZERO_IS_AN_AMOUNT if their zero is a real amount: {unnamed}"
    )


def test_the_amount_list_names_only_settings_that_exist_and_take_zero() -> None:
    _load_every_slice()
    settings = registered_settings()
    stale = sorted(
        key for key in ZERO_IS_AN_AMOUNT if key not in settings or not _takes_zero(settings[key])
    )
    assert stale == [], f"no longer a number that takes zero: {stale}"


def test_the_words_the_screens_draw() -> None:
    """The words chosen for these settings, so a rename is a decision rather than an accident."""
    _load_every_slice()
    words = {key: s.automatic_label for key, s in registered_settings().items()}
    assert words["download.skip_smaller_mb"] == "No minimum"
    assert words["download.skip_larger_mb"] == "No maximum"
    assert words["theater.timer_seconds"] == "No timer"
    assert words["quarantine.keep_days"] == "Never"
    assert words["search.keep_records_days"] == "Forever"
    assert words["vault.lock_after_idle_minutes"] == "Never"
    assert words["vault.app_lock_after_idle_minutes"] == "Never"
    assert words["dedup.max_duration_gap_seconds"] == "No limit"
