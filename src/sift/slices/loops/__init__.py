# SPDX-License-Identifier: AGPL-3.0-or-later
"""Loops: the twenty seconds of a video worth going back to.

A loop is a marked stretch of one file (a start, an end, a name) and not a new file. That choice
is the whole design. `media_edit` can already stream-copy a range into a new asset, so a loop COULD
have been a produced file; making it a row instead is what lets somebody mark two hundred of them,
because a file costs disk and time and has to be built before it can be looked at. Exporting one to
a real file is a button that calls the operation that already exists.

**Its visibility is not a rule, it is a join.** A loop reaches a viewer through an inner join to the
resolver's visible set, so "you cannot be shown a loop of something you cannot be shown" is true by
construction rather than true because a second rule was kept in step with the first. There is no
grant on a loop and no vault flag: hiding the file hides every loop on it, which is what somebody
hiding it meant.

**It carries its own tags and inherits its people.** A twenty-second moment is one thing while the
file around it is many, which is the point of the feature, and two answers to "who is in this"
would be a fault, so the people come from the source.

**Saving a loop must not re-arm it.** The player's A-B marks are deliberately written down
nowhere: a video that will not play past a point, with nothing on screen explaining why, is
indistinguishable from a bug. A saved loop is a catalog row somebody opens on purpose. Those are two
different things and folding them together would undo the reasoning in `loop.svelte.ts`.
"""

from __future__ import annotations

from sift.slices.loops.jobs import LOOP_STILLS, LOOP_WHOLE, register_handlers
from sift.slices.loops.router import router
from sift.slices.loops.service import SERVICE, LoopService, StillWanted

__all__ = [
    "LOOP_STILLS",
    "LOOP_WHOLE",
    "SERVICE",
    "LoopService",
    "StillWanted",
    "register_handlers",
    "router",
]
