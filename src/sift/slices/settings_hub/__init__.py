# SPDX-License-Identifier: AGPL-3.0-or-later
"""Settings: storing preferences and the screen that edits them; the registry declares them."""

from __future__ import annotations

from sift.kernel.settings_registry import register_setting
from sift.kernel.where import HIDE_ACCOUNT_NAME_KEY
from sift.slices.settings_hub import schema
from sift.slices.settings_hub.router import router
from sift.slices.settings_hub.service import (
    SERVICE,
    ScopeForbidden,
    SettingsService,
    UnknownSetting,
)

# Friction, not protection: a file a guest plays is streamed whole, so it is off by default.
register_setting(
    key="guests.can_save_to_device",
    scope="app",
    default=False,
    section="Privacy and Security",
    label="Guests can save files",
    help=(
        "Guests can always watch what you share with them. When this is on, they can also save "
        "a copy to their own device."
    ),
    disclosure=(
        "This isn't a lock. A file a guest plays is sent to them whole, so share only what "
        "you're comfortable with a guest keeping."
    ),
)

# Only an admin sees a full path; the name is removed on the server, never just blurred.
register_setting(
    key=HIDE_ACCOUNT_NAME_KEY,
    scope="user",
    default=False,
    section="Privacy and Security",
    label="Hide my profile folder in paths",
    help=(
        "Where Sift shows a file's full location, the name of your profile folder is blurred. "
        "Your Windows sign-in name is often that folder's name."
    ),
    disclosure=(
        "Only paths inside your profile folder on this device change, since that's the only "
        "place the name appears. A file on another drive or on a network share, such as a NAS, "
        "is shown with its path as it is."
    ),
)

__all__ = [
    "SERVICE",
    "ScopeForbidden",
    "SettingsService",
    "UnknownSetting",
    "router",
    "schema",
]
