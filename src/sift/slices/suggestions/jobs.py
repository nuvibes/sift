# SPDX-License-Identifier: AGPL-3.0-or-later
"""The folder pass, as a background job.

Asked for once a batch of imports has settled rather than once per file, for the reason the
duplicate sweep is: a claim is about a folder, and re-reading a folder after each of the two
hundred files that landed in it would be two hundred passes producing one answer.

That timing is also what attributes new arrivals. A file dropped into a folder somebody has already
answered gets its name within seconds of landing, without a second schedule to tune and without the
feature asking again, which is the failure somebody would notice first, a folder answered on
Monday asking again on Tuesday because six more files arrived.
"""

from __future__ import annotations

from sift.kernel.jobs import JobContext, register_handler
from sift.kernel.log import get_logger
from sift.slices.suggestions.service import SuggestionService

log = get_logger(__name__)

SUGGESTION_SCAN = "suggestion_scan"


async def suggestion_scan(context: JobContext, *, service: SuggestionService) -> None:
    """Re-read the folders that have moved, and file what they claim.

    Nothing here renames, moves or deletes anything. It reads rows and writes rows, and the only
    writes it makes without being asked are the two rungs of the ladder that rest on a judgement
    somebody has already made.
    """
    changed = await service.rebuild()
    await context.set_progress(1.0)
    log.info("suggestions.scan.finished", changed=changed)


def register_handlers(*, service: SuggestionService) -> None:
    register_handler(
        SUGGESTION_SCAN,
        lambda context: suggestion_scan(context, service=service),
        name="Scanning for People to suggest",
        # ONE AT A TIME. This reads every folder in the library and writes one answer, so a second
        # copy beside it repeats the first one's work (see `register_handler`).
        alone=True,
    )
