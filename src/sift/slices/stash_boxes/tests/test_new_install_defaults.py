# SPDX-License-Identifier: AGPL-3.0-or-later
"""What stash-box enrichment starts as on a new install, once somebody turns it on."""

from __future__ import annotations

import pytest

from sift.kernel.jobs.quiet_hours import WHEN_PRESS
from sift.kernel.jobs.schedules import get_schedule, when_key
from sift.kernel.settings_registry import get_registered
from sift.slices.stash_boxes import AUTO_APPLY_KEY
from sift.slices.stash_boxes.settings import INVENTABLE, SHOW_EVERY_FIELD_KEY, invent_key

pytestmark = [pytest.mark.regression]


def test_lookups_wait_for_a_press_until_somebody_chooses_otherwise() -> None:
    task = get_schedule("enrichment")
    assert task is not None
    assert task.when_default == WHEN_PRESS
    setting = get_registered(when_key("enrichment"))
    assert setting is not None
    assert setting.default == WHEN_PRESS


def test_exact_matches_are_confirmed_and_new_people_sites_and_tags_are_created() -> None:
    confirm = get_registered(AUTO_APPLY_KEY)
    assert confirm is not None
    assert confirm.default is True
    for subject in INVENTABLE:
        invent = get_registered(invent_key(subject))
        assert invent is not None
        assert invent.default is True, subject


def test_a_record_shows_every_field_until_somebody_turns_it_off() -> None:
    every = get_registered(SHOW_EVERY_FIELD_KEY)
    assert every is not None
    assert every.default is True
