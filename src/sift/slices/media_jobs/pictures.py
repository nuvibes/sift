# SPDX-License-Identifier: AGPL-3.0-or-later
"""The three pictures a file is drawn with, as the Generate row's own products."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sift.kernel.config import Settings
from sift.kernel.content import (
    ContentStore,
    DerivativeKind,
    Lack,
    VerdictProduct,
    lacks_derivative,
)
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import (
    JobContext,
)
from sift.kernel.jobs.families import AGAIN
from sift.kernel.log import get_logger
from sift.kernel.media import (
    Accelerator,
)
from sift.slices.media_jobs.job_types import PREVIEW, SPRITE, THUMBNAIL, ChosenShape, ShouldGenerate
from sift.slices.media_jobs.previews import preview
from sift.slices.media_jobs.shared import _asset_id, recording_verdicts
from sift.slices.media_jobs.sprites import sprite
from sift.slices.media_jobs.thumbnails import thumbnail

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Picture:
    """One of the three pictures a file is drawn with, as the Generate row's own product.

    The job that makes it for an arriving file, the derivative it leaves behind, and the verdict
    it is refused under. One product per picture rather than one for all three: each
    row on the Importing pane carries its own count, and a file that has no frame to cut for a
    sprite is not thereby refused a thumbnail it could have had.
    """

    key: str
    label: str
    help: str
    job_type: str
    kind: DerivativeKind
    verdict: VerdictProduct


#: The three, in the order the row lists them: the still first, because the wall needs it before
#: anything else. Each gated by its own switch, so a person who turned scrubber strips off gets
#: the other two.
PICTURES: tuple[Picture, ...] = (
    Picture(
        "thumbnails",
        "Thumbnails",
        "The still every file is drawn with on the wall.",
        THUMBNAIL,
        DerivativeKind.THUMB,
        VerdictProduct.THUMBNAILS,
    ),
    Picture(
        "previews",
        "Hover previews",
        "The clip that plays when you point at a video.",
        PREVIEW,
        DerivativeKind.PREVIEW,
        VerdictProduct.PREVIEWS,
    ),
    Picture(
        "sprites",
        "Scrubber strips",
        "The strip of frames under the player's scrubber.",
        SPRITE,
        DerivativeKind.SPRITE,
        VerdictProduct.SPRITES,
    ),
)


async def picture_lacking_among(
    content: ContentStore, asset_ids: Sequence[str], *, picture: Picture, allowed: ShouldGenerate
) -> set[str]:
    """Which of these files lack this picture, where its switch wants it."""
    if not await allowed(picture.job_type, None):
        return set()
    return await content.lacking_derivative(picture.kind, asset_ids)


async def picture_lack(*, picture: Picture, allowed: ShouldGenerate) -> Lack | None:
    """Lacking this picture, as one term of the Generate count: the same kind
    `picture_lacking_among` asks about, so the count and the pass describe one set. None while
    its switch is off."""
    if not await allowed(picture.job_type, None):
        return None
    return lacks_derivative([picture.kind])


async def picture_switched_on(*, picture: Picture, allowed: ShouldGenerate) -> bool:
    """Whether this picture is wanted for files as they arrive."""
    return await allowed(picture.job_type, None)


async def build_picture(
    context: JobContext,
    *,
    picture: Picture,
    settings: Settings,
    hardware: HardwareReport,
    allowed: ShouldGenerate,
    chosen_shape: ChosenShape | None = None,
    accelerator: Accelerator | None = None,
) -> None:
    """Make this picture for one file, where it is lacking and its switch wants it.

    The same handler an arriving file gets, for one file; the one-read shape is what lets three
    of these on one task read the file once. A file whose folder answers a switch differently is
    honoured here by the file's own id, exactly as on the way in.

    A file that already has the picture is left alone, unless the task says `AGAIN`, which only
    a person's "Run now" on that file sets: somebody looked at the picture and wants it made again,
    and skipping it would be the press doing nothing while it said it had.
    """
    asset_id = _asset_id(context)
    again = context.payload.get(AGAIN) is True

    async def one(context: JobContext) -> None:
        if not await allowed(picture.job_type, asset_id):
            return
        if not again and not await context.content.lacking_derivative(picture.kind, [asset_id]):
            return
        if picture.job_type == THUMBNAIL:
            await thumbnail(context, settings=settings, hardware=hardware, should_generate=allowed)
        elif picture.job_type == PREVIEW:
            await preview(
                context,
                settings=settings,
                hardware=hardware,
                chosen_shape=chosen_shape,
                accelerator=accelerator,
            )
        else:
            await sprite(context, settings=settings, hardware=hardware)

    # Not claimed through the table below, so the verdict is written here.
    await recording_verdicts(one, picture.verdict)(context)
