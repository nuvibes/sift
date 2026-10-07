# SPDX-License-Identifier: AGPL-3.0-or-later
"""The three preferences, and what the destination folder is allowed to look like.

The validator here checks shape and only shape. Whether a folder exists, is inside the media area
and can be written to are questions about a disk, they are asked at the moment a backup is written,
and the answers change between one night and the next. What is refused here is what can never be
right whatever the disk says.
"""

from __future__ import annotations

import pytest

from sift.kernel.jobs.quiet_hours import WHEN_PRESS
from sift.kernel.jobs.schedules import get_schedule, when_key
from sift.kernel.settings_registry import SettingError, Validator, get_registered
from sift.slices.backup import MAX_KEEP
from sift.slices.backup.models import MAX_KEEP as MAX_KEEP_IN_A_REQUEST
from sift.slices.backup.models import ScheduleUpdate
from sift.slices.backup.service import (
    AT_KEY,
    DEFAULT_AT,
    EVERY_DAYS_KEY,
    FOLDER_KEY,
    KEEP_KEY,
    MAX_EVERY_DAYS,
    RETIRED_SCHEDULE_KEY,
)
from sift.slices.backup.tests.conftest import a_full_path
from sift.slices.settings_hub import SettingsService
from sift.testing.fixtures import Actors


def folder_validator() -> Validator:
    declared = get_registered(FOLDER_KEY)
    assert declared is not None
    return declared.validate


def test_the_three_preferences_are_declared_where_each_one_is_answered() -> None:
    """Two of them are about the FILE and one is about WHEN, and they are filed accordingly.

    Everything that runs on a clock has one screen, so how often and the time of day are declared
    into Scheduled tasks. Where to save a backup and how many to keep are questions about the file, and they are
    answered on the pane about backups whether anything is scheduled or not.

    Asserted per key rather than over the three together, because the split IS the thing: filing all
    three together, on either screen, is exactly what this catches.
    """
    wanted = {
        EVERY_DAYS_KEY: "Scheduled tasks",
        AT_KEY: "Scheduled tasks",
        KEEP_KEY: "Backup",
        FOLDER_KEY: "Backup",
    }
    for key, section in wanted.items():
        declared = get_registered(key)
        assert declared is not None, f"{key} is not registered"
        assert declared.section == section, f"{key} is declared into {declared.section}"
        # Global, not per-user. A schedule belongs to the install; a guest has no say in it.
        assert declared.scope == "app"


def test_an_automatic_backup_waits_for_a_press_until_somebody_chooses() -> None:
    task = get_schedule("backup")
    assert task is not None
    assert task.when_default == WHEN_PRESS
    setting = get_registered(when_key("backup"))
    assert setting is not None
    assert setting.default == WHEN_PRESS


def test_how_often_is_a_count_of_days_offered_in_days_or_weeks() -> None:
    """Every N days or weeks through the one unit ladder for days; no Off, which is the When's."""
    declared = get_registered(EVERY_DAYS_KEY)
    assert declared is not None
    assert declared.choices is None
    assert declared.unit == "days"
    assert (declared.default, declared.minimum, declared.maximum) == (1, 1, MAX_EVERY_DAYS)
    bound = ScheduleUpdate.model_fields["every_days"].metadata
    assert any(getattr(one, "le", None) == MAX_EVERY_DAYS for one in bound)


async def test_the_retired_how_often_is_said_and_written_through_the_days_and_the_when(
    preferences: SettingsService, actors: Actors
) -> None:
    """An older screen or script still names the three words. Each one is a reading of the count
    of days and the When, so there is one stored answer, and choosing a cadence never arms or
    disarms the schedule: that is the When's alone."""
    when = when_key("backup")
    await preferences.apply(actors.admin, {when: "work"})

    await preferences.apply(actors.admin, {RETIRED_SCHEDULE_KEY: "weekly"})
    assert (await preferences.get_app(EVERY_DAYS_KEY), await preferences.get_app(when)) == (
        7,
        "work",
    )
    assert await preferences.get_app(RETIRED_SCHEDULE_KEY) == "weekly"

    await preferences.apply(actors.admin, {RETIRED_SCHEDULE_KEY: "daily"})
    assert (await preferences.get_app(EVERY_DAYS_KEY), await preferences.get_app(when)) == (
        1,
        "work",
    )
    assert await preferences.get_app(RETIRED_SCHEDULE_KEY) == "daily"

    # Any whole number of weeks reads as weekly, and anything else as daily.
    await preferences.apply(actors.admin, {EVERY_DAYS_KEY: 14})
    assert await preferences.get_app(RETIRED_SCHEDULE_KEY) == "weekly"
    await preferences.apply(actors.admin, {EVERY_DAYS_KEY: 3})
    assert await preferences.get_app(RETIRED_SCHEDULE_KEY) == "daily"

    await preferences.apply(actors.admin, {when: WHEN_PRESS})
    assert await preferences.get_app(RETIRED_SCHEDULE_KEY) == "off"

    with pytest.raises(SettingError, match="off, daily or weekly"):
        await preferences.apply(actors.admin, {RETIRED_SCHEDULE_KEY: "hourly"})
    assert await preferences.get_app(EVERY_DAYS_KEY) == 3


def test_the_time_of_day_is_a_clock_time_checked_where_it_arrives() -> None:
    declared = get_registered(AT_KEY)
    assert declared is not None
    assert declared.default == DEFAULT_AT
    assert declared.validate("4:30") == "04:30"
    with pytest.raises(SettingError):
        declared.validate("25:00")


def test_each_row_is_drawn_only_under_the_whens_it_means_something_under() -> None:
    """How often means nothing while only a press runs the backup; the time of day nothing in
    quiet hours, whose opening is the time."""
    task = get_schedule("backup")
    assert task is not None
    drawn = {when: task.drawn_keys({task.when_key: when}) for when in ("work", "quiet", "press")}
    assert drawn == {
        "work": (EVERY_DAYS_KEY, AT_KEY),
        "quiet": (EVERY_DAYS_KEY,),
        "press": (),
    }


def test_the_ceiling_on_how_many_to_keep_is_the_same_in_the_setting_and_in_a_request() -> None:
    """Two places check it, so they are checked against each other rather than left to agree."""
    declared = get_registered(KEEP_KEY)
    assert declared is not None
    assert declared.maximum == MAX_KEEP == MAX_KEEP_IN_A_REQUEST


def test_the_folder_help_asks_for_nothing_the_folder_does_not_need() -> None:
    """A typed folder outside every library is taken as it is, so the help never sends the
    person to add it somewhere first."""
    declared = get_registered(FOLDER_KEY)
    assert declared is not None
    assert "outside your libraries" in declared.help
    assert "Folders" not in declared.help


def test_no_folder_means_the_one_sift_owns() -> None:
    assert folder_validator()("") == ""
    assert folder_validator()("   ") == ""


def test_a_relative_folder_is_refused() -> None:
    """It would be resolved against wherever Sift happens to have been started from."""
    with pytest.raises(SettingError, match="full path"):
        folder_validator()("backups")


def test_a_folder_named_with_a_climb_is_refused() -> None:
    with pytest.raises(SettingError, match=r"\.\."):
        folder_validator()(a_full_path("media", "nas", "..", "..", "etc"))


def test_a_folder_that_is_not_text_is_refused() -> None:
    with pytest.raises(SettingError, match="expected a folder"):
        folder_validator()(7)


def test_a_folder_is_kept_as_written_apart_from_the_spaces_around_it() -> None:
    kept = a_full_path("media", "nas", "backups")
    assert folder_validator()(f"  {kept}  ") == kept
