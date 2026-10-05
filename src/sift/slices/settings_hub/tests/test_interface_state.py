# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where somebody arranged the interface, kept per user: never another user's, never an undeclared
key, never a value it cannot hold."""

from __future__ import annotations

import json

import pytest

from sift.kernel.db import Database
from sift.kernel.settings_registry import SettingError
from sift.slices.settings_hub.schema import SETTINGS_VERSION, initialize_settings
from sift.slices.settings_hub.service import (
    FREQUENT_KEPT,
    MAX_INTERFACE_VALUE,
    MAX_PICK_NAME,
    MAX_PICKED_VALUE,
    SettingsService,
    UnknownSetting,
)
from sift.slices.settings_hub.tests.conftest import Users

RAIL_ORDER = "rail.order"
RAIL_HIDDEN = "rail.hidden"
PICKED_TAGS = "frequent.tag"


def picked(count: int, name: str = "portrait") -> str:
    """A record of picks in the shape the client writes, with as many entries as asked for."""
    return json.dumps([{"id": f"id{at}", "name": name, "used": 1} for at in range(count)])


async def test_a_user_that_never_arranged_anything_has_nothing_stored(
    service: SettingsService, users: Users
) -> None:
    """Nothing is stored until somebody moves something, so the shipped arrangement can change."""
    assert await service.interface(users.admin) == {}


async def test_an_arrangement_comes_back_as_it_was_written(
    service: SettingsService, users: Users
) -> None:
    await service.arrange(users.admin, {RAIL_ORDER: "browse,people,organize"})
    assert await service.interface(users.admin) == {RAIL_ORDER: "browse,people,organize"}


async def test_arranging_again_replaces_rather_than_adds(
    service: SettingsService, users: Users
) -> None:
    await service.arrange(users.admin, {RAIL_ORDER: "browse,people"})
    await service.arrange(users.admin, {RAIL_ORDER: "people,browse"})
    assert await service.interface(users.admin) == {RAIL_ORDER: "people,browse"}


async def test_one_user_never_sees_another_s(service: SettingsService, users: Users) -> None:
    """One user never reads another's rail."""
    await service.arrange(users.admin, {RAIL_ORDER: "browse,organize"})
    await service.arrange(users.guest_a, {RAIL_ORDER: "people,tags"})

    assert await service.interface(users.admin) == {RAIL_ORDER: "browse,organize"}
    assert await service.interface(users.guest_a) == {RAIL_ORDER: "people,tags"}
    assert await service.interface(users.guest_b) == {}


async def test_clearing_one_leaves_no_row_rather_than_an_empty_one(
    service: SettingsService, users: Users
) -> None:
    """Reset puts the user back where a new one starts, so the shipped arrangement reaches it."""
    await service.arrange(users.admin, {RAIL_ORDER: "browse", RAIL_HIDDEN: "tags"})
    await service.arrange(users.admin, {RAIL_HIDDEN: None})
    assert await service.interface(users.admin) == {RAIL_ORDER: "browse"}


async def test_an_empty_string_clears_it_too(service: SettingsService, users: Users) -> None:
    """A client with nothing hidden any more sends the empty list, and that is a reset rather than
    a stored empty string. Otherwise the row outlives its meaning."""
    await service.arrange(users.admin, {RAIL_HIDDEN: "tags"})
    await service.arrange(users.admin, {RAIL_HIDDEN: ""})
    assert await service.interface(users.admin) == {}


async def test_a_key_nothing_declares_is_refused(service: SettingsService, users: Users) -> None:
    """The closed list is the whole protection here. Nothing on the server reads these values, so
    an open key space would be a per-user scratchpad anybody signed in could fill."""
    with pytest.raises(UnknownSetting):
        await service.arrange(users.admin, {"something.else": "anything"})
    assert await service.interface(users.admin) == {}


@pytest.mark.parametrize(
    "value",
    [
        "browse,people;drop",
        "browse people",
        "browse,,people",
        "browse,",
        "a" * 41,
        "x" * (MAX_INTERFACE_VALUE + 1),
    ],
)
async def test_a_value_it_cannot_hold_is_refused(
    service: SettingsService, users: Users, value: str
) -> None:
    with pytest.raises(SettingError):
        await service.arrange(users.admin, {RAIL_ORDER: value})
    assert await service.interface(users.admin) == {}


async def test_a_bad_key_in_a_batch_stores_none_of_it(
    service: SettingsService, users: Users
) -> None:
    """All or none. A half-applied arrangement leaves somebody with a rail that is neither what
    they had nor what they asked for, and no way to tell which half took."""
    with pytest.raises(UnknownSetting):
        await service.arrange(users.admin, {RAIL_ORDER: "browse", "not.a.key": "x"})
    assert await service.interface(users.admin) == {}


async def test_a_bad_value_in_a_batch_stores_none_of_it(
    service: SettingsService, users: Users
) -> None:
    with pytest.raises(SettingError):
        await service.arrange(users.admin, {RAIL_ORDER: "browse", RAIL_HIDDEN: "no spaces here"})
    assert await service.interface(users.admin) == {}


async def test_the_rule_is_a_value_it_can_hold(service: SettingsService, users: Users) -> None:
    """The rail's divider rule, spelled with two hyphens, is a value it can hold."""
    await service.arrange(users.admin, {RAIL_ORDER: "browse,--,settings"})
    assert await service.interface(users.admin) == {RAIL_ORDER: "browse,--,settings"}


async def test_a_database_already_at_this_version_is_not_migrated_again(
    service: SettingsService, users: Users, temp_db: Database
) -> None:
    """A database already at this version is not migrated again: the stored arrangement survives."""
    await service.arrange(users.admin, {RAIL_ORDER: "browse,people"})

    async with temp_db.write() as connection:
        await initialize_settings(connection, SETTINGS_VERSION)

    assert await service.interface(users.admin) == {RAIL_ORDER: "browse,people"}


# --- what a picker remembers -----------------------------------------------------------------
#
# A second family of keys: a short list of things this user picked, each with the name it was
# drawn under. Each key says what it may hold.


async def test_a_picker_record_comes_back_as_it_was_written(
    service: SettingsService, users: Users
) -> None:
    value = json.dumps([{"id": "t1", "name": "golden hour", "used": 3}])
    await service.arrange(users.admin, {PICKED_TAGS: value})
    assert await service.interface(users.admin) == {PICKED_TAGS: value}


async def test_a_name_with_a_space_in_it_is_held_here_and_refused_on_the_rail(
    service: SettingsService, users: Users
) -> None:
    """A picked name with a space is held here and refused on the rail."""
    await service.arrange(users.admin, {PICKED_TAGS: picked(1, "golden hour")})
    assert PICKED_TAGS in await service.interface(users.admin)

    with pytest.raises(SettingError):
        await service.arrange(users.admin, {RAIL_ORDER: "golden hour"})


async def test_a_picker_record_longer_than_the_cap_is_refused(
    service: SettingsService, users: Users
) -> None:
    """The client trims to the same figure before it writes. This is what stops one that does not
    from turning a display preference into a per-user scratchpad."""
    await service.arrange(users.admin, {PICKED_TAGS: picked(FREQUENT_KEPT)})
    assert PICKED_TAGS in await service.interface(users.admin)

    with pytest.raises(SettingError):
        await service.arrange(users.guest_a, {PICKED_TAGS: picked(FREQUENT_KEPT + 1)})
    assert await service.interface(users.guest_a) == {}


@pytest.mark.parametrize(
    "value",
    [
        "browse,people",
        "{}",
        "[[]]",
        '[{"id": "t1", "name": "x", "used": 0}]',
        '[{"id": "t1", "name": "x", "used": true}]',
        '[{"id": "t1", "name": "x", "used": "many"}]',
        '[{"id": "sp ace", "name": "x", "used": 1}]',
        '[{"id": "t1", "name": "x", "used": 1, "extra": "no"}]',
        '[{"name": "x", "used": 1}]',
    ],
)
async def test_a_picker_value_it_cannot_hold_is_refused(
    service: SettingsService, users: Users, value: str
) -> None:
    """A picker value of the wrong shape is refused, `used: true` included (`bool` is an `int`)."""
    with pytest.raises(SettingError):
        await service.arrange(users.admin, {PICKED_TAGS: value})
    assert await service.interface(users.admin) == {}


async def test_a_name_longer_than_it_may_be_is_refused(
    service: SettingsService, users: Users
) -> None:
    with pytest.raises(SettingError):
        await service.arrange(users.admin, {PICKED_TAGS: picked(1, "x" * (MAX_PICK_NAME + 1))})
    assert await service.interface(users.admin) == {}


async def test_a_picker_record_too_long_to_hold_is_refused_before_it_is_read(
    service: SettingsService, users: Users
) -> None:
    """A picker record over the length cap is refused before it is parsed."""
    value = picked(FREQUENT_KEPT, "x" * MAX_PICK_NAME)
    assert len(value) > MAX_PICKED_VALUE, "the fixture no longer exceeds the cap it is about"

    with pytest.raises(SettingError):
        await service.arrange(users.admin, {PICKED_TAGS: value})

    assert await service.interface(users.admin) == {}


async def test_a_picker_key_for_a_kind_nothing_declares_is_refused(
    service: SettingsService, users: Users
) -> None:
    """The closed list still closes. Five kinds are registered; a sixth is a client bug or a probe."""
    with pytest.raises(UnknownSetting):
        await service.arrange(users.admin, {"frequent.folder": picked(1)})
    assert await service.interface(users.admin) == {}


CHIP_REMOVE = "confirm.chip_remove"


async def test_a_guard_somebody_turned_off_is_remembered_for_that_user(
    service: SettingsService, users: Users
) -> None:
    """A guard somebody turned off is remembered for that user, not the browser."""
    await service.arrange(users.admin, {CHIP_REMOVE: "skip"})

    assert await service.interface(users.admin) == {CHIP_REMOVE: "skip"}


async def test_clearing_the_guard_puts_the_question_back(
    service: SettingsService, users: Users
) -> None:
    """No row at all rather than a word meaning "no". "Never answered" and "answered no" are the
    same state here, so a second word for them would be a third thing to keep in step."""
    await service.arrange(users.admin, {CHIP_REMOVE: "skip"})

    await service.arrange(users.admin, {CHIP_REMOVE: ""})

    assert await service.interface(users.admin) == {}


@pytest.mark.parametrize("value", ["yes", "no", "true", "SKIP", "skip,skip"])
async def test_any_word_but_skip_is_refused_for_the_guard(
    service: SettingsService, users: Users, value: str
) -> None:
    """One word, checked. A row that would take anything is a per-user scratchpad, and nothing
    on the server reads this one closely enough to notice it had become one."""
    with pytest.raises(SettingError):
        await service.arrange(users.admin, {CHIP_REMOVE: value})
    assert await service.interface(users.admin) == {}


FACE_REMOVAL = "confirm.face_removal"


async def test_one_key_covers_both_ways_a_face_leaves_a_file(
    service: SettingsService, users: Users
) -> None:
    """Ignoring a face and removing it share one key."""
    await service.arrange(users.admin, {FACE_REMOVAL: "skip"})

    assert await service.interface(users.admin) == {FACE_REMOVAL: "skip"}


@pytest.mark.parametrize("value", ["yes", "no", "SKIP", "skip,skip"])
async def test_any_word_but_skip_is_refused_for_the_face_guard(
    service: SettingsService, users: Users, value: str
) -> None:
    """The same one word the chip guard takes, checked the same way."""
    with pytest.raises(SettingError):
        await service.arrange(users.admin, {FACE_REMOVAL: value})
    assert await service.interface(users.admin) == {}


POPOUT_EXPANDED = "popout.expanded"


@pytest.mark.parametrize("value", ["open", "shut"])
async def test_which_way_the_popout_was_left_is_remembered_for_that_account(
    service: SettingsService, users: Users, value: str
) -> None:
    """Both popout answers are stored: open is the default, so shut and open each need a word."""
    await service.arrange(users.admin, {POPOUT_EXPANDED: value})

    assert await service.interface(users.admin) == {POPOUT_EXPANDED: value}


@pytest.mark.parametrize("value", ["yes", "no", "true", "OPEN", "expanded", "open,shut", "1"])
async def test_any_word_but_open_or_shut_is_refused_for_the_popout(
    service: SettingsService, users: Users, value: str
) -> None:
    """Two words, checked. A row that would take anything is a per-user scratchpad, and nothing
    on the server reads this one closely enough to notice it had become one."""
    with pytest.raises(SettingError):
        await service.arrange(users.admin, {POPOUT_EXPANDED: value})
    assert await service.interface(users.admin) == {}


HISTORY_OPEN = "organize.history_open"


@pytest.mark.parametrize("value", ["open", "shut"])
async def test_the_withdrawn_queue_thread_key_is_refused_like_any_other_unknown_key(
    service: SettingsService, users: Users, value: str
) -> None:
    """`organize.history_open` is withdrawn and refused like any unknown key."""
    with pytest.raises(SettingError):
        await service.arrange(users.admin, {HISTORY_OPEN: value})
    assert await service.interface(users.admin) == {}


LEAVE_TO_MINI = "popout.leave_to_mini"


@pytest.mark.parametrize("value", ["on", "off"])
async def test_whether_leaving_the_popout_keeps_the_clip_going_is_remembered(
    service: SettingsService, users: Users, value: str
) -> None:
    """Both answers about leaving the popout are stored; on is the default."""
    await service.arrange(users.admin, {LEAVE_TO_MINI: value})

    assert await service.interface(users.admin) == {LEAVE_TO_MINI: value}


#: An EMPTY value is not in this list, and that is the table's own rule rather than an omission:
#: an empty string clears the row for every key here, which is the way back to the default.
@pytest.mark.parametrize("value", ["yes", "no", "true", "ON", "open", "shut", "on,off", "1"])
async def test_any_word_but_on_or_off_is_refused_for_leaving_the_popout(
    service: SettingsService, users: Users, value: str
) -> None:
    """Leaving the popout takes only its own two words; `open`/`shut` are refused."""
    with pytest.raises(SettingError):
        await service.arrange(users.admin, {LEAVE_TO_MINI: value})
    assert await service.interface(users.admin) == {}


EXPORT_WAY = "faces.export_way"


@pytest.mark.parametrize("value", ["except", "only"])
async def test_how_the_export_chooser_opens_is_remembered(
    service: SettingsService, users: Users, value: str
) -> None:
    """Both ways the export's chooser can open are stored; everyone-except is the default."""
    await service.arrange(users.admin, {EXPORT_WAY: value})

    assert await service.interface(users.admin) == {EXPORT_WAY: value}


@pytest.mark.parametrize("value", ["on", "all", "none", "Only", "except,only"])
async def test_any_word_but_except_or_only_is_refused_for_the_export_chooser(
    service: SettingsService, users: Users, value: str
) -> None:
    """The export chooser takes only its own two words."""
    with pytest.raises(SettingError):
        await service.arrange(users.admin, {EXPORT_WAY: value})
    assert await service.interface(users.admin) == {}


async def test_the_withdrawn_record_pane_key_is_refused_like_any_other_unknown_key(
    service: SettingsService, users: Users
) -> None:
    """`popout.record_tab` is withdrawn (see `service.py`) and refused like any unknown key."""
    with pytest.raises(SettingError):
        await service.arrange(users.admin, {"popout.record_tab": "media"})
    assert await service.interface(users.admin) == {}


@pytest.mark.parametrize("hint", ["organize_empty", "first_pile", "first_insights"])
async def test_the_withdrawn_hint_keys_are_refused_like_any_other_unknown_key(
    service: SettingsService, users: Users, hint: str
) -> None:
    with pytest.raises(SettingError):
        await service.arrange(users.admin, {f"path.hint.{hint}.seen": "seen"})
    assert await service.interface(users.admin) == {}
