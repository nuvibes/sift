# SPDX-License-Identifier: AGPL-3.0-or-later
"""The player and the server must cut a file into the same number of slices.

The replay curve is stored as one row per slice of a file, and the browser is what decides which
slice a moment belongs to: it divides the playhead by the duration, multiplies by however many
slices it believes there are, and sends the index. The server writes that index straight into a row
and reads it back as a position along a fixed-width curve.

So the two numbers are one number kept in two places, which is the shape that goes wrong silently.
Nothing raises if they drift. A browser cutting a file into fifty slices against a server expecting a
hundred writes every sitting into the first half of the curve, and every curve in the library
gradually leans left: correct-looking, drawn without error, and wrong in a way no test that only
reads one side can see.

There is no way to make it one number: one is TypeScript in a Svelte component and the other is
Python in the kernel, and neither runtime can read the other's constant. So it is checked instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sift.kernel.content.user_state import HEAT_BUCKETS

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
PLAYER = REPO / "frontend" / "src" / "lib" / "components" / "player" / "Player.svelte"

#: `const HEAT_BUCKETS = 100;` in the player. Anchored to the declaration rather than searched for
#: anywhere the digits appear, so a hundred of something else in the same file cannot answer for it.
_DECLARED = re.compile(r"^\tconst HEAT_BUCKETS = (?P<count>\d+);$", re.MULTILINE)


def test_the_player_cuts_a_file_into_as_many_slices_as_the_server_stores() -> None:
    found = _DECLARED.search(PLAYER.read_text(encoding="utf-8"))
    assert found is not None, (
        "the player no longer declares HEAT_BUCKETS where this gate can read it. It is the count the"
        " replay curve's slice indexes are computed against; if it moved, move this gate with it"
        " rather than deleting the check (see the module docstring)."
    )
    assert int(found["count"]) == HEAT_BUCKETS, (
        f"the player cuts a file into {found['count']} slices and the server stores"
        f" {HEAT_BUCKETS}. Every replay curve would be written into the wrong part of itself."
    )
