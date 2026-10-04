# SPDX-License-Identifier: AGPL-3.0-or-later
"""Settings: the tables, the read/write API, and the screen the app's preferences live on.

This feature owns *storing* settings and the screen that edits them. What settings *exist* is the
kernel registry's job (`sift.kernel.settings_registry`): every feature, this one included, declares
its preferences there with `register_setting`, and this screen is generated from what has been
declared. Reading a setting from another feature is done through the service held on `app.state`,
not by importing this package.

Importing this package registers the `settings` schema and two settings no one feature owns:
whether a guest is shown a Save button, which defaults off (a capability no admin has turned on
is a capability nobody has) and whether a path names the account Sift runs as.
"""

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

# A guest can always watch what an admin has shared with them. This decides whether they are
# offered Save to device. It is friction, not protection: a file a guest plays is streamed to them
# whole, so it is off by default and an admin turns it on deliberately.
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

# Read wherever a screen says where a file is (`kernel.where`). Only an admin is ever shown a full
# path, so it changes nothing for a guest. The name is taken out on the server, so it never reaches
# the page; the page draws the gap it leaves as a blur.
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
