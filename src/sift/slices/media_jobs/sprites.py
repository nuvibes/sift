# SPDX-License-Identifier: AGPL-3.0-or-later
"""The scrub strip: the frames a scrubber shows while it is dragged, tiled into one sheet."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

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
    moments_to_files,
    resolve_decodable,
)
from sift.slices.media_jobs import ffmpeg, tuning
from sift.slices.media_jobs.job_types import _GIF
from sift.slices.media_jobs.shared import _asset_id, _made_for, _render, _size_of

log = get_logger(__name__)


async def sprite(context: JobContext, *, settings: Settings, hardware: HardwareReport) -> None:
    """The strip of frames a scrubber shows while it is dragged, built as the file arrives.

    Two passes: seek out each frame, then tile the results into one sheet. One pass with a filter
    that picks frames as they go by would decode the entire video to keep thirty of them.
    """
    asset_id = _asset_id(context)
    store = context.content
    source = await resolve_decodable(store, asset_id, settings=settings)
    duration_ms = source.asset.duration_ms or 0
    # A still, or a file with no running time, has nothing to scrub through.
    if not _made_for(DerivativeKind.SPRITE, source.asset):
        return

    # The sprite's own rate, denser than the fingerprint's thirty frames (see
    # `sampler.sprite_frames`): a scrubber needs more than one tile every few minutes.
    timestamps = sampler.sprite_frames(duration_ms)
    columns, rows = ffmpeg.sprite_grid(len(timestamps))
    destination = store.derivative_path(
        asset_id,
        DerivativeKind.SPRITE,
        extension="jpg",
        params={"columns": columns, "rows": rows, "tile_width": tuning.SPRITE_TILE_WIDTH},
    )

    with timing_hook("sprite.render", asset_id=asset_id, frames=len(timestamps)):
        await _render_sprite(
            source.path,
            destination,
            timestamps=timestamps,
            columns=columns,
            rows=rows,
            settings=settings,
            context=context,
            # A GIF cannot seek cheaply, so its tiles come from one decode rather than one seek each.
            single_decode=source.asset.media_type == _GIF,
        )

    await store.add_derivative(
        asset_id,
        DerivativeKind.SPRITE,
        extension="jpg",
        params={"columns": columns, "rows": rows, "tile_width": tuning.SPRITE_TILE_WIDTH},
        size_bytes=await _size_of(destination),
    )


async def _render_sprite(
    source: Path,
    destination: Path,
    *,
    timestamps: Sequence[int],
    columns: int,
    rows: int,
    settings: Settings,
    context: JobContext,
    single_decode: bool = False,
) -> None:
    with tempfile.TemporaryDirectory(prefix="sift-sprite-") as workspace:
        staging = Path(workspace)
        if single_decode:
            await _decode_sprite_tiles(
                source, staging, count=len(timestamps), settings=settings, context=context
            )
        else:
            await _seek_sprite_tiles(
                source, staging, timestamps=timestamps, settings=settings, context=context
            )

        # THE GAP IS FILLED BEFORE THE SHEET IS BUILT. A moment that produced no tile takes the
        # nearest tile that did, so tile N stays at moment N and the sheet keeps the grid the
        # derivative's path and stored `params` were chosen with, which is what a scrubber slices by.
        await asyncio.to_thread(_fill_gaps, staging, len(timestamps))
        produced = await asyncio.to_thread(_renumber, staging)
        if not produced:
            raise ffmpeg.FFmpegError("no frames could be read from this file")

        # Through _render, so a re-run killed mid-write never leaves a truncated sheet in place.
        await _render(
            ffmpeg.tile_args(
                str(staging / "tile-%04d.jpg"),
                destination,
                columns=columns,
                rows=rows,
                settings=settings,
            ),
            destination,
        )


async def _seek_sprite_tiles(
    source: Path,
    staging: Path,
    *,
    timestamps: Sequence[int],
    settings: Settings,
    context: JobContext,
) -> None:
    """Every tile from as few processes as the command line allows (one, for most files).

    The kernel names each moment as its own input and each tile as its own output, byte-identical
    to the one-tile command, and reads a refused chunk one moment at a time so one bad moment
    costs one tile.
    """
    await context.raise_if_canceled()
    tiles = await moments_to_files(
        source,
        [ffmpeg.hash_frame_moment(timestamp) for timestamp in timestamps],
        into=staging,
        suffix=".jpg",
        filters=ffmpeg.still_filter(width=tuning.SPRITE_TILE_WIDTH),
        output=ffmpeg.still_output(tuning.SPRITE_QUALITY),
        settings=settings,
        time_limit=tuning.SUBPROCESS_TIMEOUT_SECONDS,
    )
    for timestamp, tile in zip(timestamps, tiles, strict=True):
        if tile is None:
            # One moment that cannot be read is a missing tile, not a lost strip; logged with the
            # moment so a file losing many is visible. A file with no tiles at all still fails.
            log.info("sprite.tile_unreadable", asset_id=_asset_id(context), at_ms=timestamp)


async def _decode_sprite_tiles(
    source: Path, staging: Path, *, count: int, settings: Settings, context: JobContext
) -> None:
    """The sprite tiles of a file that cannot seek cheaply (a GIF) from one decode, not one seek
    per tile.

    The whole file is decoded once into a scratch subfolder, then up to `count` frames are taken
    evenly by position and moved out as the tiles. The picks are distinct, so moving them out
    cannot ask for a frame already moved, and `_renumber` only globs the staging folder itself.
    """
    scratch = staging / "frames"
    await asyncio.to_thread(scratch.mkdir)
    await context.raise_if_canceled()
    await ffmpeg.run(
        ffmpeg.all_tiles_args(
            source,
            scratch / "frame-%04d.jpg",
            width=tuning.SPRITE_TILE_WIDTH,
            quality=tuning.SPRITE_QUALITY,
            settings=settings,
        ),
        reads=source,
    )

    frames = sorted(await asyncio.to_thread(lambda: list(scratch.glob("*.jpg"))))
    if not frames:
        return  # _renumber finds nothing and the caller raises "no frames could be read"
    take = min(count, len(frames))
    for position in range(take):
        frame = frames[position * len(frames) // take]
        await asyncio.to_thread(frame.rename, staging / f"{position:04d}.jpg")


def _fill_gaps(staging: Path, wanted: int) -> None:
    """Give every position from 0 to `wanted` a tile, copying the nearest one that came out.

    ffmpeg's sequence reader stops at the first gap in the numbering, and closing a gap up would
    move every later tile onto the wrong moment. Nothing at all came out is left alone: the caller
    raises "no frames could be read".
    """
    tiles = {int(one.stem): one for one in staging.glob("*.jpg") if one.stem.isdigit()}
    if not tiles:
        return
    for position in range(wanted):
        if position in tiles:
            continue
        nearest = min(tiles, key=lambda have: (abs(have - position), have))
        shutil.copyfile(tiles[nearest], staging / f"{position:04d}.jpg")


def _renumber(staging: Path) -> int:
    """Name the tiles that are there `tile-NNNN`, in order, and say how many there were.

    ffmpeg's sequence reader needs names without a gap; `_fill_gaps` makes sure there is none.
    """
    produced = sorted(staging.glob("*.jpg"))
    for position, frame in enumerate(produced):
        frame.rename(staging / f"tile-{position:04d}.jpg")
    return len(produced)
