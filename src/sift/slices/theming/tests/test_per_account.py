# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two users, two looks, and neither one reaching the other.

This is the property the whole choice rests on. Sift is a self-hosted app with guests, and the look
is personal taste rather than an instance-wide decision, so the wrong scope here would not be a
subtle bug. One person picking a green accent would turn everybody's app green, and a guest trying
to change it back would be refused, because a global setting is admin-only.

It is asserted against the real service and a real database rather than against the registration,
because "declared per-user" and "stored per-user" are two different claims and only the second one
is the one that matters when two people are logged in.
"""

from __future__ import annotations

import pytest

from sift.kernel.access import Role, Viewer
from sift.kernel.db import Database
from sift.kernel.settings_registry import SettingError
from sift.slices.settings_hub.service import SettingsService
from sift.slices.theming import (
    ACCENT_HEX_KEY,
    ACCENT_KEY,
    ACCENT_SWATCHES_KEY,
    BASE_KEY,
    FACE_DISPLAY_KEY,
)
from sift.testing.fixtures import create_user

pytestmark = [pytest.mark.regression]


@pytest.fixture
async def service(temp_db: Database) -> SettingsService:
    """The real service over a real database. No registry surgery: these settings are declared by
    importing the app, which is what actually ships."""
    import sift.main  # noqa: F401

    await temp_db.initialize_schema()
    return SettingsService(temp_db)


@pytest.fixture
async def two_people(temp_db: Database, service: SettingsService) -> tuple[Viewer, Viewer]:
    return (
        await create_user(temp_db, Role.ADMIN),
        await create_user(temp_db, Role.GUEST),
    )


async def test_one_persons_look_leaves_everybody_elses_alone(
    service: SettingsService, two_people: tuple[Viewer, Viewer]
) -> None:
    admin, guest = two_people

    await service.apply(
        admin, {BASE_KEY: "chrome", ACCENT_KEY: "magenta", FACE_DISPLAY_KEY: "geist-mono"}
    )

    assert await service.get_user(admin.id, BASE_KEY) == "chrome"
    assert await service.get_user(admin.id, ACCENT_KEY) == "magenta"
    assert await service.get_user(admin.id, FACE_DISPLAY_KEY) == "geist-mono"

    # The guest has chosen nothing, so the guest gets what a fresh install gets.
    assert await service.get_user(guest.id, BASE_KEY) == "midnight"
    assert await service.get_user(guest.id, ACCENT_KEY) == "blue"
    assert await service.get_user(guest.id, FACE_DISPLAY_KEY) == "archivo"


async def test_a_guest_may_change_their_own(
    service: SettingsService, two_people: tuple[Viewer, Viewer]
) -> None:
    """The other half of `user` scope, and the half that would be silently wrong if these were
    declared global: a guest writing an instance-wide setting is refused."""
    admin, guest = two_people

    await service.apply(guest, {ACCENT_KEY: "cyan"})

    assert await service.get_user(guest.id, ACCENT_KEY) == "cyan"
    assert await service.get_user(admin.id, ACCENT_KEY) == "blue"


async def test_a_theme_that_does_not_exist_is_refused(
    service: SettingsService, two_people: tuple[Viewer, Viewer]
) -> None:
    """A stored value no stylesheet has a rule for is the failure mode with no symptom: the page
    would simply stay on the default and nothing would say why. The write is what has to refuse it."""
    admin, _ = two_people

    with pytest.raises(SettingError):
        await service.apply(admin, {ACCENT_KEY: "chartreuse"})

    assert await service.get_user(admin.id, ACCENT_KEY) == "blue"


async def test_a_colour_that_is_not_one_is_refused_and_a_colour_is_kept(
    service: SettingsService, two_people: tuple[Viewer, Viewer]
) -> None:
    """The seventh accent is the one value with no fixed set behind it, so the shape check is the
    whole of the guard, and it is the real service that has to make it, not the browser. Stored
    lower case, so two rows differing only in the case of a letter cannot mean the same colour."""
    admin, _ = two_people

    with pytest.raises(SettingError):
        await service.apply(admin, {ACCENT_HEX_KEY: "burnt umber"})

    await service.apply(admin, {ACCENT_HEX_KEY: "#A831B7"})

    assert await service.get_user(admin.id, ACCENT_HEX_KEY) == "#a831b7"


async def test_kept_colours_are_each_persons_own_and_an_eleventh_is_refused(
    service: SettingsService, two_people: tuple[Viewer, Viewer]
) -> None:
    """The list goes through the real service the way the one colour does: stored in one spelling,
    per person, and refused whole past ten, leaving what was kept as it was."""
    admin, guest = two_people
    ten = [f"#{index:06x}" for index in range(10)]

    await service.apply(guest, {ACCENT_SWATCHES_KEY: ["#A831B7"]})
    await service.apply(admin, {ACCENT_SWATCHES_KEY: ten})

    assert await service.get_user(guest.id, ACCENT_SWATCHES_KEY) == ["#a831b7"]
    assert await service.get_user(admin.id, ACCENT_SWATCHES_KEY) == ten

    with pytest.raises(SettingError):
        await service.apply(admin, {ACCENT_SWATCHES_KEY: [*ten, "#ffffff"]})
    assert await service.get_user(admin.id, ACCENT_SWATCHES_KEY) == ten
