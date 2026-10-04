# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proposing Photo Sets out of a creator's loose pictures.

A library of this kind fills up with pictures that arrived one at a time and belong together in
runs: one sitting, one room, one outfit, posted over a week. A Photo Set already exists for the
three ways pictures demonstrably arrive together (a fetch, a folder, an archive) and none of
them can see a sitting that was posted piecemeal. This can, because the meaning index already holds
a description of what every picture looks like.

Four things about it are decisions rather than details.

**It proposes and does not file.** A similarity score is evidence and not a verdict, so every
grouping waits on the Shoots card until somebody agrees. The switch that turns the asking off is
off out of the box.

**The number that chooses the distance is not the one that looks decisive.** See
`clustering.DISTANCE`: single-folder purity holds across the whole range, so what picks 0.30 is
the size of the largest group rather than the purity.

**A refusal is remembered against the PICTURES, not the grouping**, because the rule is greedy and
its grouping moves. Work that comes back after being dismissed is worse than work never offered.

**And the set is made by the feature that owns Photo Sets.** Through a seam, so this slice cannot
reach into that one and there is no second way of creating a set: the downloader's galleries and
an agreed shoot go through the same derivation.
"""

from __future__ import annotations

from sift.kernel.jobs.schedules import ScheduledTask, register_schedule
from sift.slices.shoots import schema as schema  # registers the schema component
from sift.slices.shoots import settings
from sift.slices.shoots.clustering import DISTANCE, MOST, shoots
from sift.slices.shoots.jobs import SHOOTS_LOOK, register_handlers
from sift.slices.shoots.queue import ShootQueue
from sift.slices.shoots.router import router
from sift.slices.shoots.service import QUEUE, SERVICE, ShootService
from sift.slices.shoots.store import Store

#: Looking for shoots, as a task: the pass a scan asks for once it settles, and Run now. What it does
#: with a shoot it finds (create a Photo Set or wait in Organize) is the Importing pane's switch.
register_schedule(
    ScheduledTask(
        id="shoots",
        title="Find shoots",
        explain="Finds runs of one creator's photos taken together, to suggest as Photo Sets.",
        job_type=SHOOTS_LOOK,
        set_in="importing",
    )
)

__all__ = [
    "AUTO_FILE_KEY",
    "DISTANCE",
    "MOST",
    "QUEUE",
    "SERVICE",
    "SHOOTS_LOOK",
    "ShootQueue",
    "ShootService",
    "Store",
    "register_handlers",
    "router",
    "shoots",
]

AUTO_FILE_KEY = settings.AUTO_FILE_KEY

settings.register()
