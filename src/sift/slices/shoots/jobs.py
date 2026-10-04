# SPDX-License-Identifier: AGPL-3.0-or-later
"""Looking for shoots, as a background job.

One job, because the pass is one thing: read every creator's loose pictures, ask the index for
neighbours, write down what grouped. It runs at background priority (whoever is watching
something right now always wins) and it is safe to run again from scratch, because a proposal is
replaced rather than appended to and a refusal is remembered separately.

It needs no cursor and keeps nothing in memory between runs. The next pass reads the same question
the last one did, and everything grouped, filed or refused since is out of the answer.
"""

from __future__ import annotations

from sift.kernel.jobs import BACKGROUND_PRIORITY, JobContext, register_handler
from sift.kernel.jobs.families import Family
from sift.kernel.log import get_logger
from sift.slices.shoots.service import ShootService

log = get_logger(__name__)

SHOOTS_LOOK = "shoots_look"


async def look(context: JobContext, *, service: ShootService) -> None:
    """Look for shoots across the library and write down what was found."""
    found = await service.find()
    await context.set_progress(1.0)
    log.info("shoots.job.done", creators=found.creators, shoots=found.found, filed=found.filed)


def register_handlers(*, service: ShootService) -> None:
    register_handler(
        SHOOTS_LOOK,
        lambda context: look(context, service=service),
        name="Looking for shoots",
        family=Family.OTHER,
        # ONE AT A TIME. This reads the whole library and writes one answer; several running
        # together each take most of an hour over the same files while file reads sit unclaimed.
        # See `register_handler`.
        alone=True,
        # AND ONE URGENCY. The module docstring above says this runs at background priority
        # ("whoever is watching something right now always wins"). Held at the declaration rather
        # than by each caller, that is true of every way it can be asked for, the button as well
        # as the settle, and the button's request collapses onto the settle's waiting row instead
        # of making a second run of a pass that may only have one.
        urgency=BACKGROUND_PRIORITY,
    )
