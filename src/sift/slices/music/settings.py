# SPDX-License-Identifier: AGPL-3.0-or-later
"""The music settings: the name a folder answers with, and the AcoustID lookup's switches."""

from __future__ import annotations

from typing import Any

from sift.kernel.settings_registry import SettingError, register_setting

FINGERPRINT_KEY = "music.fingerprint"
# Retired into the music task's When; the key stays as the name the import gate and folders use.


def register() -> None:
    """Nothing left to declare: the one switch is the music task's When."""


# The lookup, read at run time so turning it off stops what waits; its key is sealed, not a setting.

LOOKUP_KEY = "music.lookup"

LOOKUP_ROUTE_KEY = "music.lookup_route"

_SECTION = "Music"


def _route(value: Any) -> str | None:
    """A tunnel's id, or None for straight out; whether it runs is asked when a lookup goes out."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise SettingError("expected a tunnel, or nothing for a direct connection")
    route = value.strip()
    if not route or route == "direct":
        return None
    if len(route) > 64 or not route.replace("-", "").replace("_", "").isalnum():
        raise SettingError("that is not a tunnel")
    return route


register_setting(
    key=LOOKUP_KEY,
    scope="app",
    default=False,
    section=_SECTION,
    label="Name songs with AcoustID",
    disclosure=(
        "Sends a fingerprint of the file's sound and its length to AcoustID, never the file. Off "
        "unless you turn it on."
    ),
    help=(
        "Sift looks up which song a full-length file uses, with your AcoustID application key. It "
        "names the song on that file and on every file that uses the same song."
    ),
)
register_setting(
    key=LOOKUP_ROUTE_KEY,
    scope="app",
    default=None,
    # The pane draws the live tunnel list; the validator decides what may be stored.
    choices=("direct",),
    choice_labels=("Direct",),
    validator=_route,
    section=_SECTION,
    label="Connect to AcoustID through",
    help="A tunnel you added under Sites, or nothing to connect directly.",
    names_a_tunnel=True,
)
