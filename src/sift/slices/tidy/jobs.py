# SPDX-License-Identifier: AGPL-3.0-or-later
"""The survey, as a job.

Two of the tidyings count by reading a whole directory off the disk (every picture Sift has
made, every face it has cut out) and that is seconds on a fast disk and much longer on a share.
So the count is not taken when the Maintenance screen opens. It is taken here, when somebody asks
for it, and the kernel keeps the answer with the moment it was made for the screen to read.
"""

from __future__ import annotations

from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.kernel.jobs import JobContext, register_handler
from sift.kernel.jobs.families import Family
from sift.kernel.tidy import Resources, survey_costly

TIDY_SURVEY = "tidy_survey"


async def survey(_context: JobContext, *, resources: Resources) -> None:
    """Count what every costly tidying would remove, and keep the answers."""
    await survey_costly(resources)


def register_handlers(*, database: Database, settings: Settings) -> None:
    resources = Resources(database=database, settings=settings)
    register_handler(
        TIDY_SURVEY,
        lambda context: survey(context, resources=resources),
        name="Counting leftover files",
        family=Family.OTHER,
    )
