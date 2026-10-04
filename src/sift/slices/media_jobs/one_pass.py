# SPDX-License-Identifier: AGPL-3.0-or-later
"""One read of a file for every product a Build makes of it.

A task of the Build makes several products of one file in turn (the scrub strip, the two
fingerprints, faces, meaning), and each of them asks the kernel for its moments of the file. Left
alone, each ask seeks the file afresh: two hundred seeks of a five-minute video, and over a share
two hundred round trips. This reads the file once instead, where that is cheaper, and hands the
frames to the products through the kernel's prepared-frames store, so no product knows or cares
which shape read its frames.

Which shape is decided per file. `media.choose_read_shape` weighs the moments the products want,
each priced in the file's own frames by its codec, against every frame of the file, and on a share
adds what the self-test measured for it. A long file seeks; a short one is decoded once.

Each product says what it would ask for. The requests are built from the same functions the
products read with (the same ladders, the same filters), and a test beside each product proves
the plan and the ask agree. A product whose plan drifted from its ask would not be wrong, only
unhelped: the kernel seeks for anything it was not handed.
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

from sift.kernel import media
from sift.kernel import sampling as sampler
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, DerivativeKind, perceptual
from sift.kernel.log import get_logger, timing_hook
from sift.kernel.media import (
    FileFacts,
    FrameFiles,
    FrameRequest,
    MissingAsset,
    NoReadableCopy,
    RawFrames,
    ReadRates,
    ReadShape,
    resolve_decodable,
)
from sift.slices.media_jobs import ffmpeg, tuning
from sift.slices.media_jobs.jobs import SPRITE, ShouldGenerate

log = get_logger(__name__)

_VIDEO = "video"

#: How long one decode of a file may take, as a multiple of the time the rule expected for it,
#: with a floor. A guard against a hung decoder, not a budget: the rule already chose this shape
#: because it was the quicker one.
DECODE_TIME_FLOOR_SECONDS = tuning.SUBPROCESS_TIMEOUT_SECONDS


class PlansFrames(Protocol):
    """What the reader needs of a product: which frames it would ask for, if any."""

    @property
    def frames(self) -> Callable[[FileFacts], Awaitable[Sequence[FrameRequest]]] | None: ...


async def fingerprint_frames(facts: FileFacts) -> list[FrameRequest]:
    """What the two video fingerprints read: the thirty hash frames and the twenty-five stills of
    the stash-box grid. Built from the same pieces `_grey_frames` and `_video_phash` read with."""
    if facts.media_type != _VIDEO:
        return []
    size = perceptual.HASH_FRAME_SIZE
    # Across the picture, as `_fingerprint` asks; the stash-box grid below across the file, as
    # `_video_phash` asks. See `sampler.picture_span`.
    span = sampler.picture_span(facts.duration_ms, facts.video_duration_ms)
    requests: list[FrameRequest] = [
        RawFrames(
            moments=tuple(ffmpeg.hash_frame_moment(at) for at in sampler.hash_frames(span)),
            filters=ffmpeg.hash_frame_filter(size),
            pixel_format=ffmpeg.HASH_PIXEL_FORMAT,
            frame_bytes=size * size,
        )
    ]
    if facts.duration_ms > 0:
        requests.append(
            FrameFiles(
                moments=tuple(
                    ffmpeg.stash_box_moment(at)
                    for at in perceptual.scene_frame_times(facts.duration_ms / 1000)
                ),
                filters=ffmpeg.stash_box_filter(perceptual.SCENE_SHOT_WIDTH),
                suffix=".bmp",
                output=ffmpeg.STASH_BOX_OUTPUT,
            )
        )
    return requests


def sprite_tile_frames(facts: FileFacts) -> list[FrameRequest]:
    """What the scrub strip reads: one tile per moment of the sprite ladder. The same moments,
    filter and encoding `_render_sprite` asks for."""
    if facts.media_type != _VIDEO or facts.duration_ms <= 0:
        return []
    return [
        FrameFiles(
            moments=tuple(
                ffmpeg.hash_frame_moment(at) for at in sampler.sprite_frames(facts.duration_ms)
            ),
            filters=ffmpeg.still_filter(width=tuning.SPRITE_TILE_WIDTH),
            suffix=".jpg",
            output=ffmpeg.still_output(tuning.SPRITE_QUALITY),
        )
    ]


async def picture_frames(
    facts: FileFacts, *, kind: DerivativeKind, content: ContentStore, allowed: ShouldGenerate
) -> list[FrameRequest]:
    """What one picture product would read for this file: the sprite's tiles, when the sprite is
    wanted and missing. The thumbnail is one seek and the preview is its own decode; neither is
    served from prepared frames, so for those two this is nothing."""
    if kind is not DerivativeKind.SPRITE:
        return []
    if not await allowed(SPRITE, facts.asset_id):
        return []
    if not await content.lacking_derivative(DerivativeKind.SPRITE, [facts.asset_id]):
        return []
    return sprite_tile_frames(facts)


class OnePassReader:
    """Reads one file for every product of a task, in the shape this machine does best."""

    def __init__(
        self,
        content: ContentStore,
        *,
        settings: Settings,
        rates: Callable[[Path], Awaitable[ReadRates | None]],
    ) -> None:
        self._content = content
        self._settings = settings
        self._rates = rates
        """This machine's rates for reading a file at this path, or None where it was never
        measured. Answered by the self-test's runner, through the composition root."""

    @asynccontextmanager
    async def prepared(self, asset_id: str, products: Sequence[PlansFrames]) -> AsyncIterator[None]:
        """Read the file once for these products, where that is the cheaper shape, and hand the
        frames to everything inside. Inside, the products read as they would without it."""
        requests, facts = await self._plan(asset_id, products)
        if facts is None or not requests:
            yield
            return
        rates = await self._rates(facts.path)
        moments = sum(len(one.moments) for one in requests)
        shape = media.choose_read_shape(
            moments=moments,
            duration_seconds=facts.duration_ms / 1000,
            fps=facts.fps,
            width=facts.width,
            height=facts.height,
            size_bytes=facts.size_bytes,
            codec=facts.vcodec,
            rates=rates,
        )
        log.info(
            "media_jobs.one_pass.shape",
            asset_id=asset_id,
            shape=str(shape),
            moments=moments,
            duration_ms=facts.duration_ms,
            measured=rates is not None,
        )
        if shape is not ReadShape.DECODE_ONCE:
            yield
            return
        with tempfile.TemporaryDirectory(prefix="sift-one-pass-") as workspace:
            try:
                with timing_hook("build.decode_once", asset_id=asset_id, moments=moments):
                    frames = await media.decode_once(
                        facts.path,
                        requests,
                        workspace=Path(workspace),
                        settings=self._settings,
                        time_limit=self._time_limit(facts, rates),
                    )
            except media.FFmpegError as error:
                # The products seek, as they would have without this. Nothing is lost but the
                # saving, and the reason is in the log.
                log.warning(
                    "media_jobs.one_pass.decode_refused", asset_id=asset_id, detail=str(error)
                )
                yield
                return
            with media.prepared(frames):
                yield

    async def _plan(
        self, asset_id: str, products: Sequence[PlansFrames]
    ) -> tuple[list[FrameRequest], FileFacts | None]:
        try:
            source = await resolve_decodable(self._content, asset_id, settings=self._settings)
        except (MissingAsset, NoReadableCopy):
            return [], None
        asset = source.asset
        if asset.media_type != _VIDEO:
            return [], None
        facts = FileFacts(
            asset_id=asset_id,
            path=source.path,
            media_type=asset.media_type,
            duration_ms=asset.duration_ms or 0,
            video_duration_ms=asset.video_duration_ms,
            width=asset.width or 0,
            height=asset.height or 0,
            fps=asset.fps or 0.0,
            size_bytes=source.location.size_bytes or asset.size_bytes or 0,
            vcodec=asset.vcodec,
        )
        requests: list[FrameRequest] = []
        for product in products:
            if product.frames is None:
                continue
            requests.extend(await product.frames(facts))
        return requests, facts

    @staticmethod
    def _time_limit(facts: FileFacts, rates: ReadRates | None) -> float:
        expected = 0.0
        if rates is not None and rates.decode_fps > 0:
            frames = facts.duration_ms / 1000 * (facts.fps or 30.0)
            scale = max(1.0, facts.width * facts.height / media.REFERENCE_PIXELS)
            expected = frames * scale / rates.decode_fps
        return max(DECODE_TIME_FLOOR_SECONDS, expected * 4)
