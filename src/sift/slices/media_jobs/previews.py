# SPDX-License-Identifier: AGPL-3.0-or-later
"""The hover clip: what plays while a pointer rests on a tile."""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path

from sift.kernel import mp4
from sift.kernel import sampling as sampler
from sift.kernel.config import Settings
from sift.kernel.content import (
    RECIPE_VERSIONS,
    Asset,
    ContentStore,
    DerivativeKind,
)
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import (
    JobContext,
)
from sift.kernel.log import get_logger, timing_hook
from sift.kernel.media import (
    Accelerator,
    resolve_decodable,
)
from sift.slices.media_jobs import ffmpeg, tuning
from sift.slices.media_jobs.job_types import _GIF, PREVIEW, ChosenShape
from sift.slices.media_jobs.shared import _asset_id, _made_for, _render, _size_of

log = get_logger(__name__)


#: Raised when a change to the ENCODER (its height, frame rate or quality in `tuning`) would make
#: every stored preview the wrong thing; the chosen shape is in the recipe and looks after itself.
#: Read from the kernel's declaration, which the lacking checks compare, and folded into `params`.
PREVIEW_RECIPE_VERSION = RECIPE_VERSIONS[DerivativeKind.PREVIEW]


def preview_recipe(shape: sampler.PreviewShape) -> dict[str, int]:
    """What a preview was built from, as the derivative's own settings.

    `derivatives` is unique on `(asset_id, kind, params)`, so previews built two ways are two rows
    and two files and a stale one is something to ask for. Milliseconds, not the shape's name.
    """
    return {
        "clip_ms": round(shape.total_seconds * 1000),
        "cut_ms": round(shape.segment_seconds * 1000),
        "v": PREVIEW_RECIPE_VERSION,
    }


async def preview(
    context: JobContext,
    *,
    settings: Settings,
    hardware: HardwareReport,
    chosen_shape: ChosenShape | None = None,
    accelerator: Accelerator | None = None,
) -> None:
    """The clip that plays while the cursor rests on a tile, built ahead of time and kept small.

    A long file is a montage of moments spread across it; a file barely longer than the clip plays
    from its beginning (`sampler.preview_segments` draws that line). A GIF previews as itself,
    capped when it is very long: it is already a short silent loop.
    """
    asset_id = _asset_id(context)
    store = context.content
    source = await resolve_decodable(store, asset_id, settings=settings)

    if not _made_for(DerivativeKind.PREVIEW, source.asset):
        # A still does not move. There is nothing to preview, and an empty derivative row would be
        # a promise of a file that is not there.
        return

    shape = sampler.preview_shape(
        await chosen_shape() if chosen_shape is not None else sampler.DEFAULT_PREVIEW_SHAPE
    )
    pieces = _pieces_of(source.asset, shape)
    params = preview_recipe(shape)

    # A fresh one where nothing was injected, with no memory between files. The shared one comes
    # from the composition root, so a fault the player meets is one this stops paying for too.
    accel = accelerator if accelerator is not None else Accelerator(hardware)
    destination = store.derivative_path(
        asset_id, DerivativeKind.PREVIEW, extension="mp4", params=params
    )

    # Which stream moves is asked only of a GIF-kind file: an animated AVIF or HEIF carries its
    # still cover as a stream ahead of its frames, while a video's cover art sits after its picture.
    stream = (
        await _moving_stream(source.path, settings=settings)
        if source.asset.media_type == _GIF
        else 0
    )

    with timing_hook("preview.encode", asset_id=asset_id, encoder=accel.encoder.value):
        await _encode_preview(
            source.path,
            destination,
            pieces=pieces,
            stream=stream,
            accelerator=accel,
            settings=settings,
            frame=(source.asset.width, source.asset.height),
        )

    size = await _size_of(destination)
    await _check_preview(
        asset_id,
        destination,
        source=source.path,
        pieces=pieces,
        size=size,
        settings=settings,
        known_ms=source.asset.video_duration_ms or None,
    )

    await store.add_derivative(
        asset_id, DerivativeKind.PREVIEW, extension="mp4", params=params, size_bytes=size
    )
    await _forget_older_previews(store, asset_id, params)


def _pieces_of(asset: Asset, shape: sampler.PreviewShape) -> tuple[sampler.Piece, ...]:
    """The moments a file's hover clip is cut from, starting where its still was cut.

    Across the PICTURE, not across the file: a montage spread over a file whose sound outlasts its
    picture would put its last pieces where there is nothing to cut. See `sampler.picture_span`.
    """
    span = sampler.picture_span(asset.duration_ms, asset.video_duration_ms)
    return from_the_still(sampler.preview_segments(span, shape), asset.still_at_ms or 0, span)


def from_the_still(
    pieces: Sequence[sampler.Piece], still_ms: int, span_ms: int
) -> tuple[sampler.Piece, ...]:
    """The hover clip's pieces, the first moved to where the tile's still was cut.

    A clip REPLACES the still in the same rectangle the moment a pointer lands, so a clip that
    starts anywhere else makes the tile jump. A single piece is moved no further than still fits
    inside the picture; a montage keeps its other pieces in order, less any the moved one overlaps.
    """
    if still_ms <= 0 or not pieces:
        return tuple(pieces)
    first = pieces[0]
    if len(pieces) == 1:
        start = max(0, min(still_ms, span_ms - first.length_ms)) if span_ms > 0 else 0
        return (sampler.Piece(start, first.length_ms),)
    moved = sampler.Piece(still_ms, first.length_ms)
    rest = tuple(
        one
        for one in pieces[1:]
        if one.start_ms >= still_ms + first.length_ms or one.start_ms + one.length_ms <= still_ms
    )
    return (moved, *rest)


async def _forget_older_previews(
    store: ContentStore, asset_id: str, params: Mapping[str, int]
) -> None:
    """Remove this file's previous hover clips, now that its replacement is recorded.

    **After the new row, never before**: until the replacement exists the old clip is the one every
    tile is playing. A file that will not unlink is left: its row is gone, and the leftover sweep
    finds a file nothing points at.
    """
    for relative in await store.forget_superseded_previews(asset_id, params):
        # Through the store, which resolves the path and refuses one that lands outside the cache.
        path = await store.derivative_at(relative)
        if path is None:
            continue
        with suppress(OSError):
            await asyncio.to_thread(path.unlink)


#: How much shorter than asked a preview may come out before it is worth saying so. Not zero: the
#: last frame of a piece can land a fortieth of a second either side of its boundary.
_PREVIEW_SHORTFALL = 0.9

#: The movie header, whose duration is the clip's running time.
_MVHD = b"mvhd"


async def _check_preview(
    asset_id: str,
    destination: Path,
    *,
    source: Path,
    pieces: Sequence[sampler.Piece],
    size: int,
    settings: Settings,
    known_ms: int | None = None,
) -> None:
    """Say so when the clip is not what was asked for. Never refuses one: a short clip still plays.

    A container that declares more running time than it holds gives a short clip and an exit of 0.
    A shortfall is measured against what the SOURCE's picture could supply: `known_ms`, the read's,
    or a probe run only on a clip that came out short.
    """
    asked_ms = sum(piece.length_ms for piece in pieces)
    seconds_of_clip = max(asked_ms, 1) / 1000
    ceiling = tuning.PREVIEW_MAX_BYTES_PER_SECOND * seconds_of_clip + tuning.PREVIEW_OVERHEAD_BYTES

    if size > ceiling:
        log.warning(
            "media.preview_oversized", asset_id=asset_id, size_bytes=size, asked_ms=asked_ms
        )

    made_ms = await asyncio.to_thread(_clip_length_ms, destination)
    if made_ms is None or made_ms >= asked_ms * _PREVIEW_SHORTFALL:
        return

    available_ms = known_ms or await _picture_length_ms(source, settings=settings)
    expected_ms = asked_ms if available_ms is None else _cuttable_ms(pieces, available_ms)
    if made_ms >= expected_ms * _PREVIEW_SHORTFALL:
        return

    log.warning(
        "media.preview_short",
        asset_id=asset_id,
        asked_ms=asked_ms,
        expected_ms=expected_ms,
        made_ms=made_ms,
        pieces=len(pieces),
    )


def _cuttable_ms(pieces: Sequence[sampler.Piece], available_ms: int) -> int:
    """How much of these moments a file holding this much picture could actually supply.

    Per piece, because a montage whose picture stops early loses its LATER pieces whole while a
    single piece simply runs out; clamping each to what is left where it starts covers both.
    """
    return sum(max(0, min(piece.length_ms, available_ms - piece.start_ms)) for piece in pieces)


def _clip_length_ms(destination: Path) -> int | None:
    """How long the clip runs, from its movie header (`mvhd`), or None if that cannot be read.

    Read from the index rather than by a tool: Sift wrote the clip. None rather than a raise: a
    question about a preview must not be able to fail the preview.
    """
    try:
        held = mp4.index_box(destination, destination.stat().st_size)
        header = None if held is None else mp4.find(held[0], held[1].body, held[1].end, _MVHD)
        if held is None or header is None:
            return None
        blob, at = held[0], header.body
        wide = blob[at] == 1
        scale, length = struct.unpack_from(
            ">IQ" if wide else ">II", blob, at + (20 if wide else 12)
        )
    except (mp4.Malformed, OSError, struct.error):
        return None
    return round(length * 1000 / scale) if scale else None


async def _picture_length_ms(source: Path, *, settings: Settings) -> int | None:
    """How much PICTURE the source holds (see `Probed.video_duration_ms`), or None if that cannot
    be read, for the reason `_clip_length_ms` gives."""
    try:
        payload = await ffmpeg.run_json(ffmpeg.probe_args(source, settings=settings), reads=source)
    except (ffmpeg.FFmpegError, ValueError, OSError):
        return None
    return ffmpeg.parse_probe(payload).video_duration_ms


async def _moving_stream(source: Path, *, settings: Settings) -> int:
    """Which of the source's video streams is the one that moves (see `Probed.picture_stream`).
    Zero when that cannot be read, for the reason `_clip_length_ms` gives."""
    try:
        payload = await ffmpeg.run_json(ffmpeg.probe_args(source, settings=settings), reads=source)
    except (ffmpeg.FFmpegError, ValueError, OSError):
        return 0
    return ffmpeg.parse_probe(payload).picture_stream


async def _encode_preview(
    source: Path,
    destination: Path,
    *,
    pieces: Sequence[sampler.Piece],
    stream: int = 0,
    accelerator: Accelerator,
    settings: Settings,
    frame: tuple[int | None, int | None] | None = None,
) -> None:
    """Encode the clip on the graphics card, and fall back to the processor if that does not work.

    A card the report names can still fail, so a failed hardware path is a processor preview, never
    none. The retry drops the hardware DECODER as well (a driver that will not encode will not
    decode); that rule is `Accelerator.run`'s, shared with the player, as is sending a `frame` too
    small for the card to the processor first.
    """

    async def render_with(encoder: ffmpeg.Encoder, decode: tuple[str, ...]) -> None:
        await _render(
            ffmpeg.preview_args(
                source,
                destination,
                pieces=pieces,
                encoder=encoder,
                device=ffmpeg.render_node() if encoder is ffmpeg.Encoder.VAAPI else None,
                decode=decode,
                stream=stream,
                settings=settings,
            ),
            destination,
            reads=source,
        )

    await accelerator.run(render_with, frame=frame)


async def rebuild_previews(
    context: JobContext,
    *,
    settings: Settings,
    hardware: HardwareReport,
    chosen_shape: ChosenShape | None = None,
) -> None:
    """Queue a fresh hover clip for every file whose one was built to a different recipe.

    Nothing else revisits a built clip (a rescan skips an unchanged file), so without this pass a
    new shape would reach only the files imported afterwards. One job per file, so the queue paces
    and caps the encodes; `dedupe` makes running it twice harmless. A file with no clip at all is
    not its business: a still, a video whose job has not run, or one previews are off for.
    """
    shape = sampler.preview_shape(
        await chosen_shape() if chosen_shape is not None else sampler.DEFAULT_PREVIEW_SHAPE
    )
    params = preview_recipe(shape)
    stale = await context.content.previews_of_another_recipe(params)
    if not stale:
        return

    log.info("preview.rebuild_start", count=len(stale), shape=shape.key)
    for index, asset_id in enumerate(stale):
        await context.raise_if_canceled()
        await context.set_progress(index / len(stale))
        await context.queue.enqueue(PREVIEW, {"asset_id": asset_id}, dedupe=True)
    log.info("preview.rebuild_queued", count=len(stale), shape=shape.key)
