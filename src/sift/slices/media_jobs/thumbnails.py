# SPDX-License-Identifier: AGPL-3.0-or-later
"""The still the grid draws, chosen by what the frame shows, and the still of one loop."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sift.kernel import media
from sift.kernel import sampling as sampler
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
from sift.slices.media_jobs.job_types import _GIF, _IMAGE, PREVIEW, THUMBNAIL
from sift.slices.media_jobs.shared import Unusable, _asset_id, _at_ms, _made_for, _render, _size_of

log = get_logger(__name__)


#: The side of the grey square a candidate still's brightness is read from. Sixteen by sixteen is
#: enough to tell a black frame, a fade and a picture apart, and small enough to cost nothing.
STILL_LEVEL_SIZE = 16


#: A frame whose mean brightness (0 to 255) is under this is black: a fade from black, a slate, a
#: blank lead-in. Black frames sit at 0 to 2, and even a dark picture (a night scene) above this.
BLACK_BELOW = 12.0


#: A frame whose brightness varies less than this across it is flat: one colour, whatever colour,
#: which is a fade to white, a blank card or a frame of nothing. A picture varies by tens.
FLAT_BELOW = 4.0


#: A frame dim AND low in contrast at once is a fade on its way in or out: brighter than black,
#: and still showing nothing somebody would pick a tile by.
FADE_DIM_BELOW = 40.0


FADE_FLAT_BELOW = 10.0


#: Where a still is tried, as fractions of the picture's length, after the first frame. Spread
#: across the file rather than clustered at its start, because a video that opens on a long slate,
#: a title card or a fade has nothing at a fixed few seconds either.
STILL_FRACTIONS = (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.65, 0.8)


@dataclass(frozen=True, slots=True)
class StillLevels:
    """How bright a candidate still is, and how much that brightness varies across it."""

    mean: float
    spread: float

    @property
    def worth_showing(self) -> bool:
        """Whether this frame is a picture rather than black, a fade or one flat colour."""
        if self.mean < BLACK_BELOW or self.spread < FLAT_BELOW:
            return False
        return not (self.mean < FADE_DIM_BELOW and self.spread < FADE_FLAT_BELOW)


def still_levels(frame: bytes) -> StillLevels:
    """The mean and the spread (standard deviation) of a grey frame's pixels."""
    if not frame:
        return StillLevels(mean=0.0, spread=0.0)
    count = len(frame)
    mean = sum(frame) / count
    spread = (sum((one - mean) ** 2 for one in frame) / count) ** 0.5
    return StillLevels(mean=mean, spread=spread)


def still_level_filter() -> str:
    """How a candidate is shaped to be measured: squashed to a small grey square by area."""
    return f"scale={STILL_LEVEL_SIZE}:{STILL_LEVEL_SIZE}:flags=area,format=gray"


def still_candidates(span_ms: int) -> tuple[int, ...]:
    """Where a still is tried after the first frame, in the order they are tried. Nothing for a
    file with no length to spread across."""
    if span_ms <= 0:
        return ()
    moments: list[int] = []
    for fraction in STILL_FRACTIONS:
        at = round(span_ms * fraction)
        if 0 < at < span_ms and at not in moments:
            moments.append(at)
    return tuple(moments)


def _choose(cut: Sequence[tuple[int, media.CutStill]]) -> tuple[int, media.CutStill] | None:
    """The first candidate worth showing, in the order they were tried; where none is, the one
    that shows the most (the widest spread of brightness), so a file that is dark throughout still
    gets its least dark frame rather than its first."""
    if not cut:
        return None
    for at, still in cut:
        if still_levels(still.levels).worth_showing:
            return at, still
    return max(cut, key=lambda one: still_levels(one[1].levels).spread)


async def _cut_stills(
    source: Path, moments: Sequence[int], *, into: Path, settings: Settings
) -> list[tuple[int, media.CutStill]]:
    """These moments of `source` as tile stills and their brightness, from one process."""
    cut = await media.moments_to_stills(
        source,
        [ffmpeg.hash_frame_moment(at) for at in moments],
        into=into,
        still_filters=ffmpeg.still_filter(height=tuning.THUMBNAIL_HEIGHT),
        still_output=ffmpeg.still_output(tuning.THUMBNAIL_QUALITY),
        level_filters=still_level_filter(),
        level_bytes=STILL_LEVEL_SIZE * STILL_LEVEL_SIZE,
        settings=settings,
        time_limit=tuning.SUBPROCESS_TIMEOUT_SECONDS,
    )
    return [(at, one) for at, one in zip(moments, cut, strict=True) if one is not None]


async def thumbnail(context: JobContext, *, settings: Settings, hardware: HardwareReport) -> None:
    """The still the grid draws, chosen by what the frame shows.

    The first frame is cut with a few grey pixels beside it, from one read; if it is black, a fade
    or one flat colour, moments spread across the whole picture are cut the same way from one more
    read, and the first worth showing is kept (the least dark, where none is). The moment is
    stored (`still_at_ms`) and the hover clip starts there, so the tile does not jump under a
    pointer. A photograph is its one frame, unmeasured.
    """
    asset_id = _asset_id(context)
    store = context.content
    source = await resolve_decodable(store, asset_id, settings=settings)
    asset = source.asset

    destination = store.derivative_path(asset_id, DerivativeKind.THUMB, extension="jpg")
    span = sampler.picture_span(asset.duration_ms, asset.video_duration_ms)
    chosen_at = 0
    with timing_hook("thumbnail.render", asset_id=asset_id):
        if asset.media_type == _IMAGE or span <= 0:
            await _render(
                ffmpeg.thumbnail_args(source.path, destination, timestamp_ms=0, settings=settings),
                destination,
                reads=source.path,
            )
        else:
            chosen_at = await _cut_by_content(source.path, destination, span, settings=settings)

    await store.add_derivative(
        asset_id,
        DerivativeKind.THUMB,
        extension="jpg",
        size_bytes=await _size_of(destination),
    )
    await store.record_still_moment(asset_id, chosen_at)
    # A hover clip built from another moment than this still's starts somewhere else, so the tile
    # would jump under a pointer: it is built again from here. Only when the moment MOVED and a
    # clip exists; a clip not built yet reads the moment when it is.
    if (
        chosen_at != (asset.still_at_ms or 0)
        and _made_for(DerivativeKind.PREVIEW, asset)
        and asset.media_type != _GIF
        and not await store.lacking_derivative(DerivativeKind.PREVIEW, [asset_id])
    ):
        await context.queue.enqueue(PREVIEW, {"asset_id": asset_id}, dedupe=True)


async def _cut_by_content(source: Path, destination: Path, span: int, *, settings: Settings) -> int:
    """Cut the tile's still from the first moment worth showing, into `destination`. The moment."""
    with tempfile.TemporaryDirectory(prefix="sift-still-") as workspace:
        first = Path(workspace) / "first"
        rest = Path(workspace) / "rest"
        await asyncio.to_thread(first.mkdir)
        await asyncio.to_thread(rest.mkdir)
        cut = await _cut_stills(source, [0], into=first, settings=settings)
        if not (cut and still_levels(cut[0][1].levels).worth_showing):
            cut += await _cut_stills(source, still_candidates(span), into=rest, settings=settings)
        chosen = _choose(cut)
        if chosen is None:
            raise Unusable(
                "ffmpeg read the file but produced no image from it \u2014 it has no frame that "
                "can be decoded, which usually means the file is truncated or is not the kind of "
                "media it claims to be"
            )
        at, still = chosen
        if at != 0:
            log.info("thumbnail.first_frame_refused", source=str(source), chosen_ms=at)
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        # Staged beside the target and moved into place, so the cache never holds half a still.
        staging = destination.with_name(f".{destination.stem}.partial{destination.suffix}")
        try:
            await asyncio.to_thread(shutil.copyfile, still.still, staging)
            await asyncio.to_thread(shutil.move, staging, destination)
        finally:
            await asyncio.to_thread(staging.unlink, True)
        return at


async def loop_thumbnail(
    context: JobContext, *, settings: Settings, hardware: HardwareReport
) -> None:
    """The still for one loop of a video, cut at the moment the loop begins.

    **The moment is half of the cache key.** `derivatives` is unique on `(asset_id, kind, params)`
    and `params` is canonical JSON, so stills at two moments are two rows and two files, and
    running this twice for one loop writes the same key twice, which the unique index absorbs.

    The moment is not checked here: only a saved loop enqueues this, and its ends were checked
    against the file's running time when it was written.
    """
    asset_id = _asset_id(context)
    at_ms = _at_ms(context)
    store = context.content
    source = await resolve_decodable(store, asset_id, settings=settings)

    params = {"at_ms": at_ms}
    destination = store.derivative_path(
        asset_id, DerivativeKind.THUMB, extension="jpg", params=params
    )
    with timing_hook("loop_thumbnail.render", asset_id=asset_id):
        await _render(
            ffmpeg.thumbnail_args(source.path, destination, timestamp_ms=at_ms, settings=settings),
            destination,
            reads=source.path,
        )

    await store.add_derivative(
        asset_id,
        DerivativeKind.THUMB,
        extension="jpg",
        params=params,
        size_bytes=await _size_of(destination),
    )


async def rebuild_thumbnails(
    context: JobContext, *, settings: Settings, hardware: HardwareReport
) -> None:
    """Queue a fresh tile for every file in the library that could have one.

    A job rather than a route's walk, so the query that finds the work sits with the job and the
    queue paces what it hands out. `dedupe` on each tile, so running it twice is harmless.
    """
    del settings, hardware  # every handler in the table takes them; this one has no use for them
    assets = await context.content.thumbnailable()
    if not assets:
        return
    log.info("thumbnail.rebuild_start", count=len(assets))
    for index, asset_id in enumerate(assets):
        await context.raise_if_canceled()
        await context.set_progress(index / len(assets))
        await context.queue.enqueue(THUMBNAIL, {"asset_id": asset_id}, dedupe=True)
    await context.set_progress(1.0)
    await context.set_note(f"{len(assets):,} files queued.")
    log.info("thumbnail.rebuild_queued", count=len(assets))
