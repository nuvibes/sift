# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixtures for the settings tests.

The registry is emptied and re-seeded with a small, known set of settings, so a test asserts against
what it declared rather than against whatever the whole app happens to register. One per-user setting
and one global one is enough to exercise both scopes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import pytest

# The service writes every changed setting into the ledger, whose table registers itself on this
# import. Without it the file is green only when another module has imported it first.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Role, Viewer
from sift.kernel.db import Database
from sift.kernel.settings_registry import register_setting
from sift.slices.settings_hub.service import SettingsService
from sift.testing.fixtures import create_user

# A per-user preference and a global capability: the two scopes, and the shapes the tests need.
LOOP_KEY = "playback.loop_mode"
LOOP_CHOICES = ["loop_one", "loop_all", "once"]
GUEST_SAVE_KEY = "guests.can_save_to_device"
#: A per-user preference that decides what a scoped read RETURNS, which almost none of them do.
#: `vault.concealment` is the real one; this stands in for it so the rule can be tested without
#: the vault feature's whole setup. See `Setting.changes_visibility`.
CONCEALMENT_KEY = "vault.concealment"


@pytest.fixture
def registered(clean_settings_registry: None) -> Iterator[None]:
    register_setting(
        key=LOOP_KEY,
        scope="user",
        default="loop_one",
        choices=LOOP_CHOICES,
        choice_labels=("Repeat", "Play through", "Stop"),
        section="Playback",
        label="Repeat",
        help="What happens when a clip reaches the end.",
    )
    register_setting(
        key=GUEST_SAVE_KEY,
        scope="app",
        default=False,
        section="Privacy and Security",
        label="Let guests save files to their device",
        help="Turn this on to let guests keep a copy of what you share.",
    )
    register_setting(
        key=CONCEALMENT_KEY,
        scope="user",
        default="fully_gone",
        choices=["fully_gone", "placeholder"],
        choice_labels=("Leave nothing", "Leave a locked tile"),
        changes_visibility=True,
        section="Privacy and Security",
        label="How hidden files disappear",
        help="Leave nothing and hidden files are absent from every list, count and search.",
    )
    yield


@pytest.fixture
async def service(temp_db: Database, registered: None) -> SettingsService:
    await temp_db.initialize_schema()
    return SettingsService(temp_db)


@dataclass(frozen=True, slots=True)
class Users:
    admin: Viewer
    guest_a: Viewer
    guest_b: Viewer


@pytest.fixture
async def users(temp_db: Database, service: SettingsService) -> AsyncIterator[Users]:
    yield Users(
        admin=await create_user(temp_db, Role.ADMIN),
        guest_a=await create_user(temp_db, Role.GUEST),
        guest_b=await create_user(temp_db, Role.GUEST),
    )
