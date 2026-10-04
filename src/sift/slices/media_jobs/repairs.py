# SPDX-License-Identifier: AGPL-3.0-or-later
"""The repaired copy: a file's streams copied into a container a browser can seek through."""

from __future__ import annotations

from sift.kernel.config import Settings
from sift.kernel.content import (
    DerivativeKind,
)
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import (
    JobContext,
)
from sift.kernel.log import get_logger, timing_hook
from sift.kernel.media import (
    resolve_decodable,
)
from sift.slices.media_jobs import ffmpeg, tuning
from sift.slices.media_jobs.job_types import BECAUSE_INTERLEAVE
from sift.slices.media_jobs.shared import _asset_id, _render, _size_of

log = get_logger(__name__)


async def remux(context: JobContext, *, settings: Settings, hardware: HardwareReport) -> None:
    """Build a playable copy of a file whose audio sits too far from its video.

    Only the container changes: every stream is copied untouched, and ffmpeg interleaves as it
    writes. The original is never touched; the copy is a derivative in the cache like any other.

    Two reasons ask for it. The interleave repair re-reads the gap first, since the file may have
    been replaced since it was measured; the player's remux tier (`BECAUSE_CONTAINER`) asks for
    the same whole-file copy for a container the browser cannot read, with nothing to re-measure.
    """
    asset_id = _asset_id(context)
    store = context.content
    source = await resolve_decodable(store, asset_id, settings=settings)

    because = str(context.payload.get("because") or BECAUSE_INTERLEAVE)
    gap = source.asset.interleave_gap
    if because == BECAUSE_INTERLEAVE and (gap is None or gap < tuning.MAX_INTERLEAVE_GAP_BYTES):
        log.info("remux.not_needed", asset_id=asset_id, interleave_gap=gap)
        return

    destination = store.derivative_path(asset_id, DerivativeKind.REMUX, extension="mp4")
    with timing_hook("remux.copy", asset_id=asset_id, interleave_gap=gap):
        await _render(
            ffmpeg.remux_args(source.path, destination, settings=settings),
            destination,
            reads=source.path,
        )

    await store.add_derivative(
        asset_id,
        DerivativeKind.REMUX,
        extension="mp4",
        size_bytes=await _size_of(destination),
    )
