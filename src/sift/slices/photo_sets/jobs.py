# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one pass that revisits photo sets already made: taking a raised floor to them.

Every other way a set comes to exist runs forward, at the moment something arrives (a download,
a folder, an archive, a shoot, a post) and none of them looks back. When `MIN_PICTURES` rises,
the sets already made under the old floor are exactly the wall of near-pairs the higher floor
exists to stop, and nothing else would take it to them.

So this pass exists, and it is deliberately narrow. It dissolves only the sets Sift made (`origin`
other than `manual`) that hold fewer pictures than the floor, through the service's own door, so
each one is written down as Sift's act and its pictures are untouched: dissolving a set never
touches a file. A set somebody assembled by hand is theirs whatever its size.

Asked for at boot, once, as background work, and one at a time: on a library where the floor did
not move it reads one statement and finishes.
"""

from __future__ import annotations

from sift.kernel.jobs import JobContext, register_handler
from sift.kernel.jobs.families import Family
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.kernel.photo_sets import MIN_PICTURES
from sift.kernel.vocabulary import VIA_PHOTO_SET_FLOOR
from sift.slices.photo_sets.service import PhotoSetService

log = get_logger(__name__)

#: The pass, by the name the queue knows it by.
DISSOLVE_UNDER_FLOOR = "photo_sets_floor"


async def dissolve_under_floor(context: JobContext, *, service: PhotoSetService) -> None:
    """Dissolve every set Sift made that holds fewer than `MIN_PICTURES` pictures.

    Through `service.delete`, never a statement of its own: that door records the deletion under
    Sift's name and tells every open screen, and a second path to the same table would be a second
    chance to do one of those and not the other.
    """
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
        # Delete, not Remove: each set stops existing (`service.delete`), though no photo is touched.
        # Built from the constant, so the number on Activity cannot drift from the rule.
        name=f"Deleting Photo Sets with fewer than {MIN_PICTURES} photos",
        family=Family.OTHER,
        # One at a time: two of it would read the same list and each try to delete what the
        # other already has, and the second delete of a set is a fault rather than a no-op.
        alone=True,
    )
