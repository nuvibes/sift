# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read a site's watermark off the picture and file the copy under that site; off until enabled,
no model ships."""

from __future__ import annotations

from sift.kernel.jobs.quiet_hours import WHEN_WORK
from sift.kernel.jobs.schedules import PER_FILE, ScheduledTask, register_schedule
from sift.slices.watermarks import schema as schema  # registers the schema component
from sift.slices.watermarks import settings
from sift.slices.watermarks.jobs import (
    WATERMARK_FETCH_MODELS,
    WATERMARK_READ,
    read_one,
    register_handlers,
)
from sift.slices.watermarks.queue import WatermarkFilings
from sift.slices.watermarks.reader import Reader
from sift.slices.watermarks.router import router
from sift.slices.watermarks.service import FROM_WATERMARK, PRODUCT, QUEUE, SERVICE, WatermarkService
from sift.slices.watermarks.store import Store

# Registered before the task that names it.
settings.register()

#: As files arrive by default: the feature switch is the consent.
register_schedule(
    ScheduledTask(
        id="watermarks",
        title="Read watermarks",
        explain="Checks new files for a watermark, so the Sites on your files stay up to date.",
        job_type=WATERMARK_READ,
        needs_starter=True,
        when_default=WHEN_WORK,
        set_in="watermarks",
        unit=PER_FILE,
        switch=settings.ENABLED_KEY,
    )
)

__all__ = [
    "DEVICE_KEY",
    "ENABLED_KEY",
    "FROM_WATERMARK",
    "PRODUCT",
    "QUEUE",
    "READ_ON_IMPORT_KEY",
    "SERVICE",
    "WATERMARK_FETCH_MODELS",
    "WATERMARK_READ",
    "Reader",
    "Store",
    "WatermarkFilings",
    "WatermarkService",
    "read_one",
    "register_handlers",
    "router",
]

ENABLED_KEY = settings.ENABLED_KEY
READ_ON_IMPORT_KEY = settings.READ_ON_IMPORT_KEY
DEVICE_KEY = settings.DEVICE_KEY
