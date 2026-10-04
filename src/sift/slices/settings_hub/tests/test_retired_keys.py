# SPDX-License-Identifier: AGPL-3.0-or-later
"""A retired key is a way of asking about the setting that replaced it, never a second copy.

Read through its successor and written into it, so an old caller, an old screen and a folder's own
answer stored under the old key all reach the one stored value, and the settings screen, which
draws the successor, lists the retired key only to send an old address to the new row.
"""

from __future__ import annotations

import pytest

from sift.kernel.settings_registry import (
    SettingError,
    label_of,
    register_setting,
    retire_setting,
)
from sift.slices.settings_hub.service import SettingsService, UnknownSetting
from sift.slices.settings_hub.tests.conftest import Users

pytestmark = [pytest.mark.integration]

WHEN = "tasks.tidy.when"
OLD = "tidy.automatically"


@pytest.fixture
def retired(registered: None) -> None:
    register_setting(
        key=WHEN,
        scope="app",
        default="press",
        choices=("work", "quiet", "press"),
        choice_labels=("As soon as there is work", "In quiet hours", "Only when I press it"),
        section="Scheduled tasks",
        label="Tidy up",
        help="Tidies up.",
    )
    retire_setting(
        OLD,
        into=(WHEN,),
        read=lambda values: values[0] != "press",
        write=lambda value, current: (
            ("work" if current[0] == "press" else current[0]) if value else "press",
        ),
        why="a test's switch, answered by a When",
    )


async def test_a_retired_key_reads_and_writes_through_its_successor(
    service: SettingsService, users: Users, retired: None
) -> None:
    assert await service.get_app(OLD) is False
    await service.apply(users.admin, {OLD: True})
    assert await service.get_app(WHEN) == "work"
    assert await service.get_app(OLD) is True

    await service.apply(users.admin, {WHEN: "quiet"})
    await service.apply(users.admin, {OLD: True})
    assert await service.get_app(WHEN) == "quiet", "on keeps the quiet hours somebody chose"

    await service.apply(users.admin, {OLD: False})
    assert await service.get_app(WHEN) == "press"


async def test_a_retired_key_is_not_drawn_but_is_listed_for_an_old_address(
    service: SettingsService, users: Users, retired: None
) -> None:
    view = await service.effective(users.admin)
    keys = {entry["key"] for section in view["sections"] for entry in section["settings"]}
    assert WHEN in keys and OLD not in keys
    assert view["retired"][OLD] == [WHEN]


def test_a_retired_key_cannot_be_declared_again(retired: None) -> None:
    with pytest.raises(SettingError, match="retired"):
        register_setting(
            key=OLD,
            scope="app",
            default=True,
            section="Scheduled tasks",
            label="Tidy up automatically",
            help="Tidies up.",
        )


def test_a_retired_key_is_called_by_what_it_became(retired: None) -> None:
    """A retired key has no label of its own, and a screen can still have to name one.

    A folder's own answer is stored under the old key, so the per-folder page lists it, and read
    off the registered rows alone it would list the raw key, because a retired key has no label.
    """
    assert label_of(WHEN) == "Tidy up", "a registered key is called by its own label"
    assert label_of(OLD) == "Tidy up"
    assert label_of("nothing.declared") is None

    register_setting(
        key="tasks.sweep.when",
        scope="app",
        default="press",
        choices=("work", "quiet", "press"),
        choice_labels=("As soon as there is work", "In quiet hours", "Only when I press it"),
        section="Scheduled tasks",
        label="Sweep",
        help="Sweeps.",
    )
    register_setting(
        key="tasks.dust.when",
        scope="app",
        default="press",
        choices=("work", "quiet", "press"),
        choice_labels=("As soon as there is work", "In quiet hours", "Only when I press it"),
        section="Scheduled tasks",
        label="Dust",
        help="Dusts.",
    )
    retire_setting(
        "chores.all",
        into=(WHEN, "tasks.sweep.when", "tasks.dust.when"),
        read=lambda values: any(one != "press" for one in values),
        write=lambda value, current: tuple("work" if value else "press" for _ in current),
        why="a test's master over three Whens",
    )
    # A master over several is called by every one of them, in order, as a sentence lists them.
    assert label_of("chores.all") == "Tidy up, Sweep and Dust"


async def test_a_retired_per_user_key_reads_through_that_user_s_successor(
    service: SettingsService, users: Users, registered: None
) -> None:
    """Per user as well as per app: an old screen asking for one person's value is answered from
    that person's successor, never from anybody else's."""
    from sift.slices.settings_hub.tests.conftest import LOOP_KEY

    retire_setting(
        "playback.repeat",
        into=(LOOP_KEY,),
        read=lambda values: values[0] != "once",
        write=lambda value, current: ("loop_one" if value else "once",),
        why="a test's switch, answered by a loop mode",
    )

    await service.apply(users.guest_a, {LOOP_KEY: "once"})

    assert await service.get_user(users.guest_a.id, "playback.repeat") is False
    assert await service.get_user(users.guest_b.id, "playback.repeat") is True


async def test_a_retired_key_is_stored_when_what_it_became_is(
    service: SettingsService, users: Users, retired: None
) -> None:
    assert await service.app_is_stored(OLD) is False
    await service.apply(users.admin, {WHEN: "quiet"})
    assert await service.app_is_stored(OLD) is True


async def test_sift_writes_settings_by_their_current_keys_only(
    service: SettingsService, retired: None
) -> None:
    """Nothing of Sift's writes an old key, so one arriving at its own door is a mistake to say."""
    with pytest.raises(UnknownSetting, match="current keys only"):
        await service.apply_as_sift(
            {OLD: True}, via="benchmark", queue="settings.sift", title="t", detail="d"
        )

    assert await service.app_is_stored(WHEN) is False
