# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two Privacy settings this slice declares say what they do in plain words.

The profile folder's name is drawn as a blur, so its help describes the blur and never prints the
marker the page turns into one; and the guest switch keeps the caveat that it is not a lock.
"""

from __future__ import annotations

import pytest

import sift.slices.settings_hub  # noqa: F401  (registers the settings)
from sift.kernel.settings_registry import get_registered
from sift.kernel.where import HIDE_ACCOUNT_NAME_KEY, REDACTED

pytestmark = [pytest.mark.regression]


def test_hiding_the_profile_folder_is_described_as_a_blur() -> None:
    setting = get_registered(HIDE_ACCOUNT_NAME_KEY)
    assert setting is not None
    assert "blurred" in setting.help
    assert REDACTED not in setting.help
    assert setting.disclosure is not None
    assert "network share" in setting.disclosure


def test_the_profile_folder_is_shown_until_somebody_hides_it() -> None:
    setting = get_registered(HIDE_ACCOUNT_NAME_KEY)
    assert setting is not None
    assert setting.default is False


def test_the_guest_switch_says_it_is_not_a_lock() -> None:
    setting = get_registered("guests.can_save_to_device")
    assert setting is not None
    assert setting.label == "Guests can save files"
    assert setting.disclosure is not None
    assert "isn't a lock" in setting.disclosure
    # Off hides the press, and the stream still carries every byte of the file.
    assert "sent to them whole" in setting.disclosure
