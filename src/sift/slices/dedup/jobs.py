# SPDX-License-Identifier: AGPL-3.0-or-later
"""The scan, as a background job.

It runs after fingerprinting, and on demand: a fingerprint does not exist at arrival, so a near
duplicate is always imported and then flagged. Blocking would mean un-indexing a copy already in a
library folder that Sift never deletes on its own, leaving invisible orphans; one queue to clear
them hides nothing.
"""

from __future__ import annotations

from sift.kernel.jobs import BACKGROUND_PRIORITY, JobContext, register_handler
from sift.kernel.log import get_logger
from sift.slices.dedup.service import DedupService

log = get_logger(__name__)

DEDUP_SCAN = "dedup_scan"


async def dedup_scan(context: JobContext, *, service: DedupService) -> None:
    """Compare the library's fingerprints and file the pairs worth asking about.

    Whole-library, since the comparison index is built from every fingerprint anyway: once after a
    batch settles beats once per file. It reads fingerprints and writes rows, nothing else.
    """
    filed = await service.scan()
    # The run's own sentence: the Tasks row and Organize's Duplicates bar say it as the last run.
    await context.set_note(
        "Found no new pairs to review." if filed == 0 else f"Found {filed:,} new pairs to review."
    )
    await context.set_progress(1.0)
    log.info("dedup.scan.finished", filed=filed)


def register_handlers(*, service: DedupService) -> None:
    register_handler(
        DEDUP_SCAN,
        lambda context: dedup_scan(context, service=service),
        name="Scanning for duplicates",
        # One at a time: a second copy beside it repeats the same whole-library work.
        alone=True,
        # And one urgency, which `alone` needs: asked by a settle and by the button, two urgencies
        # would be two rows (a collapse joins only equal requests) and the library compared twice.
        # Background, so the press collapses onto the settle's row.
        urgency=BACKGROUND_PRIORITY,
    )
