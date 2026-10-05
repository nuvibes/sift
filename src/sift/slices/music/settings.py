# SPDX-License-Identifier: AGPL-3.0-or-later
"""The music task's When, and the name a folder answers with.

OFF out of the box: the music task's When is "Only when I press it" (`music/__init__.py`), and an
install that had it at "As soon as there is work" is moved there once (settings v11). A library is
never read for its music on its own: reading a file's sound decodes the whole track, a few seconds a
file, which over a library is hours nobody asked for. The card on Organize asks, a press runs it
whatever this says, and a folder can say yes for itself.

`music.fingerprint` stays as the name a FOLDER answers with. It is in the list a folder may answer
differently (`ImportPolicy.overridable`), drawn on the Importing pane's per-folder rows, and a file
arriving through Sift into a folder that said yes is fingerprinted at staging while it is still on
this device's own disk (`slices/music/landing.py`). A folder that says nothing follows the library,
which is off.
"""

from __future__ import annotations

from typing import Any

from sift.kernel.settings_registry import SettingError, register_setting

FINGERPRINT_KEY = "music.fingerprint"
# RETIRED into the When of the music task (`tasks.music.when`). The key stays as the name the import
# gate and a folder's own answer use; the composition root retires it
# (`settings_registry.retire_setting`), so it declares nothing here any more.


def register() -> None:
    """Nothing left to declare: the one switch is the music task's When. Kept as the slice's hook."""


# --- SONG-NAMES: the online lookup (AcoustID) -----------------------------------------------------
#
# Drawn on the Music pane beside the fingerprint task's When: one feature, one door. It has a
# stash-box's shape of disclosure, because it is the same kind of sender. The switch says whether
# anything may be sent at all; WHEN it is sent is the lookup task's own When (`lookup.LOOKUP_TASK`),
# which is Only when I press it until somebody chooses otherwise.
# Read at RUN time by the lookup task (`slices/music/lookup.py`), never only when it is queued, so
# turning it off stops what is already waiting. The KEY is not a setting: it is sealed, and a
# setting is a value every admin screen draws and every settings write may change. See
# `NameStore.key_id` for where its id is kept.

#: Whether a file's sound fingerprint may be sent to AcoustID to name its song.
LOOKUP_KEY = "music.lookup"

#: Which way the lookup goes out: a tunnel's id, or None for straight out.
LOOKUP_ROUTE_KEY = "music.lookup_route"

#: The section the Music pane draws: the lookup and the fingerprints are one feature.
_SECTION = "Music"


def _route(value: Any) -> str | None:
    """A tunnel's id, or None for straight out. Whether the tunnel exists and is running is asked at
    the moment a lookup goes out (the route refuses then, in words), because that answer changes."""
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
    # The pane draws the real menu (every tunnel, read live); this declares the shape and the one
    # choice that is always there. The validator, not the list, decides what may be stored.
    choices=("direct",),
    choice_labels=("Direct",),
    validator=_route,
    section=_SECTION,
    label="Connect to AcoustID through",
    help="A tunnel you added under Sites, or nothing to connect directly.",
    names_a_tunnel=True,
)
