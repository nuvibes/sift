# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a new install's Downloads settings start as, and the words the pane draws for them.

A default is what a value never written reads as, so every install that never touched one of these
takes the new answer by itself, and an install that chose keeps its choice.
"""

from __future__ import annotations

import pytest

from sift.kernel.settings_registry import get_registered, get_removed
from sift.slices.download import (
    FINISHED_MESSAGE_CHOICES,
    FINISHED_MESSAGE_KEY,
    PEOPLE_FROM_USERNAMES_KEY,
    PHOTO_SETS_REMOVED_KEY,
)
from sift.slices.download.sources import policy

pytestmark = [pytest.mark.regression]


def test_grouping_downloaded_galleries_is_no_setting_and_history_still_names_it() -> None:
    """A downloaded gallery lands as files in its folder. The switch that grouped one is gone from
    every screen, and declared removed so a History line about a change to it still reads."""
    assert get_registered(PHOTO_SETS_REMOVED_KEY) is None
    removed = get_removed(PHOTO_SETS_REMOVED_KEY)
    assert removed is not None
    assert removed.label == "Group downloaded galleries into Photo Sets"


def test_the_timeout_says_it_is_about_a_download_that_stopped_responding() -> None:
    setting = get_registered(policy.TIMEOUT_KEY)
    assert setting is not None
    assert setting.label == "Stopped responding for"
    assert setting.default == 30
    assert "stopped sending anything" in setting.help


def test_adding_creators_names_downloads_and_the_sites_it_reaches() -> None:
    """It reaches every Site whose usernames are people, PMV Haven among them, so the words
    name downloads and more than one Site rather than promising PMV Haven alone."""
    setting = get_registered(PEOPLE_FROM_USERNAMES_KEY)
    assert setting is not None
    assert "download" in setting.label.lower()
    assert "PMV Haven" in setting.help
    assert setting.disclosure is not None and "TikTok" in setting.disclosure


def test_video_quality_says_what_each_answer_picks_and_that_nothing_is_converted() -> None:
    setting = get_registered(policy.QUALITY_KEY)
    assert setting is not None
    assert setting.disclosure is not None
    for said in ("H.264", "AV1", "Nothing converts a download"):
        assert said in setting.disclosure


def test_the_finished_message_setting_starts_off_and_offers_each_or_once_for_many() -> None:
    """Off by default; the two shapes it offers, and that the sound is
    a setting of its own rather than this one's."""
    setting = get_registered(FINISHED_MESSAGE_KEY)
    assert setting is not None
    assert setting.default == "off"
    assert setting.scope == "user"
    assert setting.label == "Say when a download finishes"
    assert list(setting.choices or []) == list(FINISHED_MESSAGE_CHOICES)
    assert list(setting.choice_labels or []) == ["Off", "Each download", "Once for many"]
    assert setting.disclosure is not None and "sound is a separate setting" in setting.disclosure
