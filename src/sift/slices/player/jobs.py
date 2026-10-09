# SPDX-License-Identifier: AGPL-3.0-or-later
"""The transcode job: one segment, one at a time.

The payload carries ids, never a path; the path is resolved here, preferring the repaired copy.
"""

from __future__ import annotations

from pathlib import Path

from sift.kernel.content import ContentStore, DerivativeKind
from sift.kernel.jobs import JobContext, register_handler
from sift.kernel.log import get_logger
from sift.kernel.media import Encoder, resolve
from sift.slices.player import policy, tuning
from sift.slices.player.service import TRANSCODE, PlayerService

log = get_logger(__name__)


async def repaired_copy(store: ContentStore, asset_id: str) -> Path | None:
    """The repaired copy of this file, if cached: cutting from it avoids seeking between streams."""
    for derivative in await store.derivatives(asset_id):
        if derivative.kind is DerivativeKind.REMUX:
            return await store.derivative_at(derivative.rel_cache_path)
    return None


async def transcode(context: JobContext, *, service: PlayerService) -> None:
    """Render one segment of one asset into the cache."""
    asset_id = context.require_str("asset_id", "a transcode job needs an asset id")
    index = int(context.payload.get("index", 0))
    route = policy.Route(context.payload.get("route", policy.Route.TRANSCODE.value))
    raw_height = context.payload.get("scale_height")

    source = await resolve(context.content, asset_id)
    repaired = await repaired_copy(context.content, asset_id)

    plan = policy.Plan(
        route=route,
        reason="",
        scale_height=int(raw_height) if raw_height is not None else None,
    )

    await service.build(source.asset, repaired or source.path, index=index, plan=plan)


def register_handlers(*, service: PlayerService) -> None:
    """Claim the transcode job type. Called once, at boot, before anything can enqueue one."""

    async def handle(context: JobContext) -> None:
        await transcode(context, service=service)

    register_handler(TRANSCODE, handle, name="Transcoding")


def job_limits(encoder: Encoder = Encoder.CPU) -> dict[str, int]:
    """How many segments may be built together: one on the processor, which ffmpeg saturates,
    three on hardware, whose throughput is flat with parallel jobs."""
    if encoder is Encoder.CPU:
        return {TRANSCODE: 1}
    return {TRANSCODE: tuning.HARDWARE_SEGMENT_JOBS}
