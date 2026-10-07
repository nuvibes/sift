# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the site's own mark off the picture, and filing the file under that site.

A great many files in a library of this kind carry a watermark burnt into the frame: an address
like `onlyfans.com/somebody` along the bottom, put there by the site the copy came off. Nothing in
a catalog records it, because it is not in the file's metadata and it is often not in its name
either. This reads it.

Four things about it are decisions rather than details.

**It says where a COPY came from, and never who is in it.** The mark belongs to the copy: the same
scene re-encoded by an aggregator carries that aggregator's mark or none at all. So what a read
writes is a FILING (this file came off this site, under this username) and never a person. A
username names a person and the same username is four different people on four different sites.

**Many files carry one, and they are not the ones whose names say so.** Files whose path names the
site and files with no such name carry a readable mark at much the same rate. The file name is a
poor guide to what is on the picture, in both directions, which is the whole argument for reading
the picture.

**It is off until somebody turns it on, and it ships with no model.** The two models are obtained
when the feature is enabled, from the publisher, under whatever terms that publisher sets. An
install that never switches this on pays nothing: no download, no pass, no memory.

**What is certain decides; what is not is written down.** An exact address files the file immediately,
with an Undo on the file. Anything less certain is recorded against the file and shown on its
screen, and decides nothing. There is no review queue and no card: a hint somebody has to come back
and confirm is more work than it saves.
"""

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

# The switch the task names has to be registered before the task is.
settings.register()

#: Reading watermarks, as a task. Run now is an Identify run over the library for this product
#: alone (there is no sweep of this feature's own) and the Watermarks pane draws this row. As files
#: arrive by default, for the reason recognition's task gives: the feature switch is the consent,
#: and this answer counts only once it is on.
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
