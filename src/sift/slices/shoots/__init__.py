# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proposing Photo Sets out of a creator's loose pictures; asking first is the default."""

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

#: Whether a shoot found is filed or waits in Organize is the Importing pane's switch.
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
