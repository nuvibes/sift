# SPDX-License-Identifier: AGPL-3.0-or-later
"""The transcode job: one segment, one at a time.

There is one handler here and it does one thing, which is the point. The cap that matters
(never more than one ffmpeg encoding at once on the processor) is enforced by the queue rather than
by anything in this file (`job_limits` below, handed to the worker pool by
`sift/wiring/workers.py`). The measurement behind it: two concurrent transcodes do not split the
machine between them, they run *worse than sequential*, because ffmpeg already saturates the cores
it is given.

The payload carries ids and numbers, never a path. That is enforced at `enqueue` (the queue walks
the payload and raises on anything that looks like an absolute path), and the reason is worth
knowing: a path in a payload is a path in every log line, backup and diagnostics export that job
appears in, and a job that accepts a path can be pointed at any file on the machine by whoever can
enqueue one. The handler resolves the path from the asset id, through the same `resolve` the rest
of the media pipeline uses, and then prefers the repaired copy where there is one, so that every
playback path reads the same file. See `repaired_copy`.
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
    """The repaired copy of this file, if one has been built and is still in the cache.

    Cut from here for the same reason direct play is served from here. A file whose audio is
    stored far from its video is expensive to take a segment out of: ffmpeg has to reach both
    streams at a moment they sit hundreds of megabytes apart, and it does that for every segment
    rather than once. On a badly interleaved file a stream copy from the original takes about three
    times as long as the same segment, byte for byte, from the repaired copy, even on a warm SSD. A
    library on a spinning disk or a network share pays a good deal more than that for the seeking.

    Only the container differs, so the two cut into identical segments and a cache holding some of
    each is not a problem. None for almost every file, because almost no file needs a repair.
    """
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
    """How many segments may be built at once. A measurement, and it depends on what is encoding.

    **On the processor: one.** Not a tuning knob. Raising it does not make the machine do more
    work, it makes it do less, because ffmpeg already saturates the cores it is given and two of
    them then fight over the same ones. Aggregate throughput falls as jobs are added: about a tenth
    lower at two, and nearly half at three.

    **On hardware: three.** On a card rather than a processor the same measurement is flat from one
    job to four, because the encoding is not on the cores at all, so the reason the CPU limit
    exists simply is not present.

    Three rather than four. Flat to four is not a licence to saturate: the card also draws Sift's
    thumbnails and previews, some of them for other people, and a quality change mid-video only ever
    needs two segments in flight at once.
    """
    if encoder is Encoder.CPU:
        return {TRANSCODE: 1}
    return {TRANSCODE: tuning.HARDWARE_SEGMENT_JOBS}
