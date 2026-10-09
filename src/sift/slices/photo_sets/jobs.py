# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one pass that revisits photo sets: dissolving those Sift made under a lower floor."""

from __future__ import annotations

from sift.kernel.jobs import JobContext, register_handler
from sift.kernel.jobs.families import Family
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.kernel.photo_sets import MIN_PICTURES
from sift.kernel.vocabulary import VIA_PHOTO_SET_FLOOR
from sift.slices.photo_sets.service import PhotoSetService

log = get_logger(__name__)

DISSOLVE_UNDER_FLOOR = "photo_sets_floor"


async def dissolve_under_floor(context: JobContext, *, service: PhotoSetService) -> None:
    """Dissolve every set Sift made below `MIN_PICTURES`, through `service.delete` only."""
    wanted = await service.under_floor(MIN_PICTURES)
    for done, photo_set_id in enumerate(wanted, start=1):
        await service.delete(
            photo_set_id, actor=Actor.sift(VIA_PHOTO_SET_FLOOR), under_floor=MIN_PICTURES
        )
        if done % 25 == 0:
            await context.set_progress(done / len(wanted))
            await context.raise_if_canceled()
    await context.set_progress(1.0)
    await context.set_note(_dissolved(len(wanted)))
    log.info("photo_sets.floor", dissolved=len(wanted), floor=MIN_PICTURES)


def _dissolved(count: int) -> str:
    if count == 0:
        return f"No Photo Set Sift created has fewer than {MIN_PICTURES} photos."
    sets = "Photo Set" if count == 1 else "Photo Sets"
    return (
        f"Deleted {count} {sets} with fewer than {MIN_PICTURES} photos. The photos are unchanged."
    )


def register_handlers(*, service: PhotoSetService) -> None:
    """Claim the one job type. Called once, at boot, by whoever built the service."""
    register_handler(
        DISSOLVE_UNDER_FLOOR,
        lambda context: dissolve_under_floor(context, service=service),
        # Built from the constant, so the number on Activity cannot drift from the rule.
        name=f"Deleting Photo Sets with fewer than {MIN_PICTURES} photos",
        family=Family.OTHER,
        # One at a time: a second delete of the same set is a fault, not a no-op.
        alone=True,
    )
