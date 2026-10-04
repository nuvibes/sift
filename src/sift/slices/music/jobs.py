# SPDX-License-Identifier: AGPL-3.0-or-later
"""The audio fingerprint as a job.

One job type, one file at a time, in the Fingerprint family, beside the near-duplicate pass and
the stash-box one, because that is what a person watching the Activity screen is waiting for and
they are the same kind of waiting. The PRODUCT it registers as belongs to Generate, which is the
other half of the same distinction: a family is what somebody watches, and a product is which of
the two runs makes it.

It is not registered as one that may only run alone. The stash-box pass is, because it reads a page
of the library and writes it: two of those read the same page. This is handed one file by
whichever pass asked for it, so two of them are two different files.
"""

from __future__ import annotations

from sift.kernel.config import Settings
from sift.kernel.jobs import JobContext, register_handler
from sift.kernel.jobs.families import Family
from sift.kernel.log import get_logger
from sift.slices.music.service import MusicService

log = get_logger(__name__)

AUDIO_FINGERPRINT = "audio_fingerprint"


async def fingerprint_one(
    context: JobContext, *, service: MusicService, settings: Settings
) -> None:
    """Give one file its audio fingerprint."""
    await service.fingerprint(context, settings=settings)
    await context.set_progress(1.0)


def register_handlers(*, service: MusicService, settings: Settings) -> None:
    register_handler(
        AUDIO_FINGERPRINT,
        lambda context: fingerprint_one(context, service=service, settings=settings),
        name="Fingerprinting music",
        family=Family.FINGERPRINT,
        counts="files with a music fingerprint",
        by_itself=True,
    )
